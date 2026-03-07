"""
Router tests for /api/v1/pipelines.
"""

import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import (
    CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS, NO_ROLE_HEADERS,
    PIPELINE_SPEC, CHANGE_SPEC,
)

_PIPELINE_RESPONSE = {
    "name": "test-pipeline",
    "spec": PIPELINE_SPEC,
    "pr_url": "https://github.com/test/repo/pull/3",
}

_HISTORY = [
    {
        "release_id": "release-001",
        "stage": "dev",
        "status": "success",
        "timestamp": "2026-03-07T10:00:00+00:00",
        "pr_url": "https://github.com/test/repo/pull/4",
        "chart_version": "1.0.0",
    }
]

_TEST_RESULTS = {
    "release_id": "release-001",
    "passed": 10,
    "failed": 0,
    "test_cases": [],
    "raw_output": None,
}


# ---------------------------------------------------------------------------
# GET /api/v1/pipelines
# ---------------------------------------------------------------------------

def test_list_pipelines_all_roles_allowed(client):
    with patch(
        "gitopsgui.api.routers.pipelines.PipelineService.list_pipelines",
        new=AsyncMock(return_value=[_PIPELINE_RESPONSE]),
    ):
        for headers in (CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS):
            r = client.get("/api/v1/pipelines", headers=headers)
            assert r.status_code == 200


def test_list_pipelines_no_role_rejected(client):
    r = client.get("/api/v1/pipelines", headers=NO_ROLE_HEADERS)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# POST /api/v1/pipelines — build_manager only
# ---------------------------------------------------------------------------

def test_create_pipeline_build_manager_allowed(client):
    with patch(
        "gitopsgui.api.routers.pipelines.PipelineService.create_pipeline",
        new=AsyncMock(return_value=_PIPELINE_RESPONSE),
    ):
        r = client.post("/api/v1/pipelines", json=PIPELINE_SPEC, headers=BUILD_MGR_HEADERS)
    assert r.status_code == 202


def test_create_pipeline_cluster_operator_rejected(client):
    r = client.post("/api/v1/pipelines", json=PIPELINE_SPEC, headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 403


def test_create_pipeline_senior_dev_rejected(client):
    r = client.post("/api/v1/pipelines", json=PIPELINE_SPEC, headers=SENIOR_DEV_HEADERS)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/v1/pipelines/{name}/changes — build_manager only
# ---------------------------------------------------------------------------

def test_add_change_build_manager_allowed(client):
    with patch(
        "gitopsgui.api.routers.pipelines.PipelineService.create_change",
        new=AsyncMock(return_value=_PIPELINE_RESPONSE),
    ):
        r = client.post(
            "/api/v1/pipelines/test-pipeline/changes",
            json=CHANGE_SPEC,
            headers=BUILD_MGR_HEADERS,
        )
    assert r.status_code == 202


def test_add_change_cluster_operator_rejected(client):
    r = client.post(
        "/api/v1/pipelines/test-pipeline/changes",
        json=CHANGE_SPEC,
        headers=CLUSTER_OP_HEADERS,
    )
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v1/pipelines/{name}/history
# ---------------------------------------------------------------------------

def test_get_history_all_roles_allowed(client):
    with patch(
        "gitopsgui.api.routers.pipelines.PipelineService.get_history",
        new=AsyncMock(return_value=_HISTORY),
    ):
        for headers in (CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS):
            r = client.get("/api/v1/pipelines/test-pipeline/history", headers=headers)
            assert r.status_code == 200
            assert r.json()[0]["release_id"] == "release-001"


# ---------------------------------------------------------------------------
# GET /api/v1/pipelines/{name}/history/{id}/tests
# ---------------------------------------------------------------------------

def test_get_test_results(client):
    with patch(
        "gitopsgui.api.routers.pipelines.PipelineService.get_test_results",
        new=AsyncMock(return_value=_TEST_RESULTS),
    ):
        r = client.get(
            "/api/v1/pipelines/test-pipeline/history/release-001/tests",
            headers=BUILD_MGR_HEADERS,
        )
    assert r.status_code == 200
    assert r.json()["passed"] == 10
    assert r.json()["failed"] == 0


# ---------------------------------------------------------------------------
# POST /api/v1/pipelines/{name}/promote — build_manager only
# ---------------------------------------------------------------------------

def test_promote_build_manager_allowed(client):
    with patch(
        "gitopsgui.api.routers.pipelines.PipelineService.promote",
        new=AsyncMock(return_value=_PIPELINE_RESPONSE),
    ):
        r = client.post(
            "/api/v1/pipelines/test-pipeline/promote",
            json={"target_stage": "ete"},
            headers=BUILD_MGR_HEADERS,
        )
    assert r.status_code == 202


def test_promote_senior_dev_rejected(client):
    r = client.post(
        "/api/v1/pipelines/test-pipeline/promote",
        json={"target_stage": "ete"},
        headers=SENIOR_DEV_HEADERS,
    )
    assert r.status_code == 403
