"""
Tests for JWT/role extraction and require_role dependency enforcement.
"""

import os
import pytest
from unittest.mock import patch
from fastapi import FastAPI
from fastapi.testclient import TestClient

from gitopsgui.api.auth import _extract_caller, require_role, CallerInfo


# ---------------------------------------------------------------------------
# _extract_caller
# ---------------------------------------------------------------------------

def _make_caller(user: str, groups: str) -> CallerInfo:
    """Call _extract_caller with explicit header values."""
    return _extract_caller(
        x_forwarded_user=user,
        x_auth_request_groups=groups,
    )


def test_extract_cluster_operator():
    caller = _make_caller("alice", "cluster-operators")
    assert caller.role == "cluster_operator"
    assert caller.username == "alice"


def test_extract_build_manager():
    caller = _make_caller("bob", "build-managers")
    assert caller.role == "build_manager"
    assert caller.username == "bob"


def test_extract_senior_developer():
    caller = _make_caller("carol", "senior-developers")
    assert caller.role == "senior_developer"
    assert caller.username == "carol"


def test_extract_first_group_wins():
    """If a user is in multiple groups, the first recognised one wins."""
    caller = _make_caller("dave", "cluster-operators,build-managers")
    assert caller.role == "cluster_operator"


def test_extract_unknown_group_falls_back_to_dev_role(monkeypatch):
    monkeypatch.setenv("GITOPSGUI_DEV_ROLE", "build_manager")
    caller = _make_caller("eve", "unknown-group")
    assert caller.role == "build_manager"


def test_extract_no_group_no_dev_role_raises(monkeypatch):
    monkeypatch.delenv("GITOPSGUI_DEV_ROLE", raising=False)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc_info:
        _make_caller("frank", "")
    assert exc_info.value.status_code == 401


# ---------------------------------------------------------------------------
# require_role — via a minimal FastAPI test app
# ---------------------------------------------------------------------------

_app = FastAPI()

@_app.get("/operator-only")
def operator_only(_=require_role("cluster_operator")):
    return {"ok": True}

@_app.get("/manager-or-operator")
def manager_or_operator(_=require_role("cluster_operator", "build_manager")):
    return {"ok": True}

@_app.get("/all-roles")
def all_roles(_=require_role("cluster_operator", "build_manager", "senior_developer")):
    return {"ok": True}

_tc = TestClient(_app, raise_server_exceptions=False)


def _h(group: str, user: str = "u") -> dict:
    return {"X-Forwarded-User": user, "X-Auth-Request-Groups": group}


def test_require_role_allows_correct_role():
    r = _tc.get("/operator-only", headers=_h("cluster-operators"))
    assert r.status_code == 200


def test_require_role_rejects_wrong_role():
    r = _tc.get("/operator-only", headers=_h("build-managers"))
    assert r.status_code == 403


def test_require_role_allows_any_of_listed():
    assert _tc.get("/manager-or-operator", headers=_h("cluster-operators")).status_code == 200
    assert _tc.get("/manager-or-operator", headers=_h("build-managers")).status_code == 200


def test_require_role_rejects_unlisted():
    r = _tc.get("/manager-or-operator", headers=_h("senior-developers"))
    assert r.status_code == 403


def test_require_role_all_three_allowed():
    for group in ("cluster-operators", "build-managers", "senior-developers"):
        assert _tc.get("/all-roles", headers=_h(group)).status_code == 200


def test_require_role_returns_caller_info():
    """Dependency should return CallerInfo (username + role) to the handler."""
    app2 = FastAPI()

    @app2.get("/whoami")
    def whoami(caller: CallerInfo = require_role("build_manager")):
        return {"username": caller.username, "role": caller.role}

    with TestClient(app2) as tc:
        r = tc.get("/whoami", headers=_h("build-managers", user="alice"))
        assert r.json() == {"username": "alice", "role": "build_manager"}
