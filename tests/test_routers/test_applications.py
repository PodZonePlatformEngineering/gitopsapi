"""
Router tests for /api/v1/applications.
"""

import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import (
    CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS, NO_ROLE_HEADERS,
    APP_SPEC,
)

_APP_RESPONSE = {
    "name": "test-app",
    "spec": APP_SPEC,
    "status": None,
    "pr_url": "https://github.com/test/repo/pull/2",
}


def test_list_applications_all_roles_allowed(client):
    with patch(
        "gitopsgui.api.routers.applications.AppService.list_applications",
        new=AsyncMock(return_value=[_APP_RESPONSE]),
    ):
        for headers in (CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS):
            r = client.get("/api/v1/applications", headers=headers)
            assert r.status_code == 200


def test_list_applications_no_role_rejected(client):
    r = client.get("/api/v1/applications", headers=NO_ROLE_HEADERS)
    assert r.status_code == 401


def test_get_application_found(client):
    with patch(
        "gitopsgui.api.routers.applications.AppService.get_application",
        new=AsyncMock(return_value=_APP_RESPONSE),
    ):
        r = client.get("/api/v1/applications/test-app", headers=BUILD_MGR_HEADERS)
    assert r.status_code == 200
    assert r.json()["name"] == "test-app"


def test_get_application_not_found(client):
    with patch(
        "gitopsgui.api.routers.applications.AppService.get_application",
        new=AsyncMock(return_value=None),
    ):
        r = client.get("/api/v1/applications/missing", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 404


def test_add_application_cluster_operator_allowed(client):
    with patch(
        "gitopsgui.api.routers.applications.AppService.create_application",
        new=AsyncMock(return_value=_APP_RESPONSE),
    ):
        r = client.post("/api/v1/applications", json=APP_SPEC, headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 202
    assert r.json()["pr_url"] is not None


def test_add_application_build_manager_rejected(client):
    r = client.post("/api/v1/applications", json=APP_SPEC, headers=BUILD_MGR_HEADERS)
    assert r.status_code == 403


def test_add_application_senior_dev_rejected(client):
    r = client.post("/api/v1/applications", json=APP_SPEC, headers=SENIOR_DEV_HEADERS)
    assert r.status_code == 403
