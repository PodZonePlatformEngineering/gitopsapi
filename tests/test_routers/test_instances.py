"""
Router tests for POST /api/v1/instances/self/sops-bootstrap (CC-187).
"""

import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS, NO_ROLE_HEADERS


@pytest.fixture(autouse=True)
def skip_k8s(monkeypatch):
    monkeypatch.setenv("GITOPS_SKIP_K8S", "1")


# ---------------------------------------------------------------------------
# POST /api/v1/instances/self/sops-bootstrap
# ---------------------------------------------------------------------------

def test_sops_bootstrap_returns_200_with_public_key(client):
    with patch(
        "gitopsgui.api.routers.instances.InstanceSopsService.bootstrap",
        new=AsyncMock(return_value="age1fakepublickey"),
    ):
        r = client.post("/api/v1/instances/self/sops-bootstrap", headers=CLUSTER_OP_HEADERS)

    assert r.status_code == 200
    body = r.json()
    assert body["public_key"] == "age1fakepublickey"
    assert body["secret_name"] == "gitopsapi-sops-age"
    assert "namespace" in body
    assert "message" in body


def test_sops_bootstrap_requires_cluster_operator(client):
    with patch(
        "gitopsgui.api.routers.instances.InstanceSopsService.bootstrap",
        new=AsyncMock(return_value="age1fakepublickey"),
    ):
        r = client.post("/api/v1/instances/self/sops-bootstrap", headers=BUILD_MGR_HEADERS)
    assert r.status_code == 403


def test_sops_bootstrap_rejects_unauthenticated(no_auth_client):
    r = no_auth_client.post("/api/v1/instances/self/sops-bootstrap", headers=NO_ROLE_HEADERS)
    assert r.status_code == 401


def test_sops_bootstrap_returns_500_on_k8s_error(client):
    with patch(
        "gitopsgui.api.routers.instances.InstanceSopsService.bootstrap",
        new=AsyncMock(side_effect=RuntimeError("K8s API unavailable")),
    ):
        r = client.post("/api/v1/instances/self/sops-bootstrap", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 500
