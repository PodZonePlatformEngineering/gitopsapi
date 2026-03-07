"""
Router tests for /api/v1/prs — including stage-based approval role enforcement.
"""

import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS, NO_ROLE_HEADERS


def _make_pr(pr_number: int, stage: str, approvals_satisfied: bool = False) -> dict:
    return {
        "pr_number": pr_number,
        "title": f"PR #{pr_number}",
        "state": "open",
        "labels": [f"stage:{stage}", "promotion"],
        "stage": stage,
        "resource_type": "promotion",
        "diff_url": "https://github.com/test/repo/pull/1.diff",
        "reviews": [],
        "required_approvers": [],
        "approvals_satisfied": approvals_satisfied,
        "pr_url": f"https://github.com/test/repo/pull/{pr_number}",
    }


# ---------------------------------------------------------------------------
# GET /api/v1/prs — all three roles
# ---------------------------------------------------------------------------

def test_list_prs_all_roles_allowed(client):
    with patch(
        "gitopsgui.api.routers.prs.GitHubService.list_prs",
        new=AsyncMock(return_value=[_make_pr(1, "dev")]),
    ):
        for headers in (CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS):
            r = client.get("/api/v1/prs", headers=headers)
            assert r.status_code == 200


def test_list_prs_no_role_rejected(client):
    r = client.get("/api/v1/prs", headers=NO_ROLE_HEADERS)
    assert r.status_code == 401


def test_list_prs_label_filter_passed(client):
    with patch(
        "gitopsgui.api.routers.prs.GitHubService.list_prs",
        new=AsyncMock(return_value=[]),
    ) as mock:
        client.get("/api/v1/prs?label=stage:dev", headers=BUILD_MGR_HEADERS)
        mock.assert_called_once_with(state="open", label="stage:dev")


# ---------------------------------------------------------------------------
# GET /api/v1/prs/{pr_number}
# ---------------------------------------------------------------------------

def test_get_pr_found(client):
    with patch(
        "gitopsgui.api.routers.prs.GitHubService.get_pr",
        new=AsyncMock(return_value=_make_pr(42, "ete")),
    ):
        r = client.get("/api/v1/prs/42", headers=BUILD_MGR_HEADERS)
    assert r.status_code == 200
    assert r.json()["pr_number"] == 42


def test_get_pr_not_found(client):
    with patch(
        "gitopsgui.api.routers.prs.GitHubService.get_pr",
        new=AsyncMock(return_value=None),
    ):
        r = client.get("/api/v1/prs/999", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/prs/{pr_number}/approve — stage-based role enforcement
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("stage,approving_role,expected_status", [
    ("dev",        "build_manager",    204),
    ("ete",        "build_manager",    204),
    ("production", "build_manager",    204),
    ("production", "cluster_operator", 204),
    ("dev",        "cluster_operator", 403),  # cluster_operator cannot approve dev PRs
    ("ete",        "cluster_operator", 403),  # cluster_operator cannot approve ete PRs
])
def test_approve_pr_stage_role_enforcement(client, stage, approving_role, expected_status):
    role_headers = {
        "build_manager":    BUILD_MGR_HEADERS,
        "cluster_operator": CLUSTER_OP_HEADERS,
    }
    with patch(
        "gitopsgui.api.routers.prs.GitHubService.get_pr",
        new=AsyncMock(return_value=_make_pr(1, stage)),
    ), patch(
        "gitopsgui.api.routers.prs.GitHubService.approve_pr",
        new=AsyncMock(),
    ):
        r = client.post("/api/v1/prs/1/approve", headers=role_headers[approving_role])
    assert r.status_code == expected_status


def test_approve_pr_senior_dev_rejected(client):
    """Senior developers cannot approve PRs at any stage."""
    with patch(
        "gitopsgui.api.routers.prs.GitHubService.get_pr",
        new=AsyncMock(return_value=_make_pr(1, "dev")),
    ):
        r = client.post("/api/v1/prs/1/approve", headers=SENIOR_DEV_HEADERS)
    assert r.status_code == 403


def test_approve_pr_not_found(client):
    with patch(
        "gitopsgui.api.routers.prs.GitHubService.get_pr",
        new=AsyncMock(return_value=None),
    ):
        r = client.post("/api/v1/prs/999/approve", headers=BUILD_MGR_HEADERS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/prs/{pr_number}/merge
# ---------------------------------------------------------------------------

def test_merge_pr_approvals_satisfied(client):
    with patch(
        "gitopsgui.api.routers.prs.GitHubService.get_pr",
        new=AsyncMock(return_value=_make_pr(1, "dev", approvals_satisfied=True)),
    ), patch(
        "gitopsgui.api.routers.prs.GitHubService.merge_pr",
        new=AsyncMock(),
    ):
        r = client.post("/api/v1/prs/1/merge", headers=BUILD_MGR_HEADERS)
    assert r.status_code == 204


def test_merge_pr_approvals_not_satisfied(client):
    with patch(
        "gitopsgui.api.routers.prs.GitHubService.get_pr",
        new=AsyncMock(return_value=_make_pr(1, "dev", approvals_satisfied=False)),
    ):
        r = client.post("/api/v1/prs/1/merge", headers=BUILD_MGR_HEADERS)
    assert r.status_code == 409


def test_merge_pr_senior_dev_rejected(client):
    r = client.post("/api/v1/prs/1/merge", headers=SENIOR_DEV_HEADERS)
    assert r.status_code == 403
