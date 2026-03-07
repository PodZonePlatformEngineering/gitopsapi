"""
Router tests for /api/v1/clusters — role enforcement + response shape.
"""

import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import (
    CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS, NO_ROLE_HEADERS,
    CLUSTER_SPEC,
)

_CLUSTER_RESPONSE = {
    "name": "test-cluster",
    "spec": CLUSTER_SPEC,
    "status": None,
    "pr_url": "https://github.com/test/repo/pull/1",
}

_LIST_RESPONSE = [_CLUSTER_RESPONSE]


# ---------------------------------------------------------------------------
# GET /api/v1/clusters — all roles allowed
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("headers", [CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS])
def test_list_clusters_allowed_roles(client, headers):
    with patch(
        "gitopsgui.api.routers.clusters.ClusterService.list_clusters",
        new=AsyncMock(return_value=_LIST_RESPONSE),
    ):
        r = client.get("/api/v1/clusters", headers=headers)
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_list_clusters_no_role_rejected(client):
    r = client.get("/api/v1/clusters", headers=NO_ROLE_HEADERS)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# GET /api/v1/clusters/{name}
# ---------------------------------------------------------------------------

def test_get_cluster_returns_cluster(client):
    with patch(
        "gitopsgui.api.routers.clusters.ClusterService.get_cluster",
        new=AsyncMock(return_value=_CLUSTER_RESPONSE),
    ):
        r = client.get("/api/v1/clusters/test-cluster", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 200
    assert r.json()["name"] == "test-cluster"


def test_get_cluster_not_found(client):
    with patch(
        "gitopsgui.api.routers.clusters.ClusterService.get_cluster",
        new=AsyncMock(return_value=None),
    ):
        r = client.get("/api/v1/clusters/missing", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/v1/clusters — cluster_operator only
# ---------------------------------------------------------------------------

def test_provision_cluster_allowed(client):
    with patch(
        "gitopsgui.api.routers.clusters.ClusterService.create_cluster",
        new=AsyncMock(return_value=_CLUSTER_RESPONSE),
    ):
        r = client.post("/api/v1/clusters", json=CLUSTER_SPEC, headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 202
    assert r.json()["pr_url"] is not None


def test_provision_cluster_build_manager_rejected(client):
    r = client.post("/api/v1/clusters", json=CLUSTER_SPEC, headers=BUILD_MGR_HEADERS)
    assert r.status_code == 403


def test_provision_cluster_senior_dev_rejected(client):
    r = client.post("/api/v1/clusters", json=CLUSTER_SPEC, headers=SENIOR_DEV_HEADERS)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# PATCH /api/v1/clusters/{name} — cluster_operator only
# ---------------------------------------------------------------------------

def test_update_cluster_allowed(client):
    with patch(
        "gitopsgui.api.routers.clusters.ClusterService.update_cluster",
        new=AsyncMock(return_value=_CLUSTER_RESPONSE),
    ):
        r = client.patch("/api/v1/clusters/test-cluster", json=CLUSTER_SPEC, headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 202


def test_update_cluster_build_manager_rejected(client):
    r = client.patch("/api/v1/clusters/test-cluster", json=CLUSTER_SPEC, headers=BUILD_MGR_HEADERS)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v1/clusters/{name}/kubeconfig — role-gated
# ---------------------------------------------------------------------------

def test_get_kubeconfig_cluster_operator_allowed(client):
    with patch(
        "gitopsgui.api.routers.clusters.KubeconfigService.get_kubeconfig",
        new=AsyncMock(return_value="apiVersion: v1\nkind: Config\n"),
    ):
        r = client.get("/api/v1/clusters/test-cluster/kubeconfig", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 200
    assert "kubeconfig" in r.headers.get("content-disposition", "")


def test_get_kubeconfig_no_role_rejected(client):
    r = client.get("/api/v1/clusters/test-cluster/kubeconfig", headers=NO_ROLE_HEADERS)
    assert r.status_code == 401
