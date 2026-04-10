"""
Router tests for /api/v1/hypervisors — role enforcement + response shape.
"""

import pytest
from unittest.mock import AsyncMock, patch

import gitopsgui.services.hypervisor_service as hs_module
from tests.conftest import CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS
from gitopsgui.models.hypervisor import HypervisorAuditData, HypervisorListResponse, HypervisorResponse

_SPEC_PAYLOAD = {
    "name": "mercury",
    "endpoint": "https://192.168.4.52:8006/",
    "host_ip": "192.168.4.52",
}

_RESPONSE = HypervisorResponse(
    name="mercury",
    endpoint="https://192.168.4.52:8006/",
    host_ip="192.168.4.52",
)

_LIST_RESPONSE = HypervisorListResponse(items=[_RESPONSE])


@pytest.fixture(autouse=True)
def skip_k8s(monkeypatch):
    monkeypatch.setenv("GITOPS_SKIP_K8S", "1")


@pytest.fixture(autouse=True)
def clear_store():
    hs_module._local_store.clear()
    yield
    hs_module._local_store.clear()


# ---------------------------------------------------------------------------
# POST /api/v1/hypervisors
# ---------------------------------------------------------------------------

def test_create_hypervisor_returns_201(client):
    with patch(
        "gitopsgui.api.routers.hypervisors.HypervisorService.create",
        new=AsyncMock(return_value=_RESPONSE),
    ):
        r = client.post("/api/v1/hypervisors", json=_SPEC_PAYLOAD, headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 201
    assert r.json()["name"] == "mercury"


def test_create_hypervisor_duplicate_returns_409(client):
    with patch(
        "gitopsgui.api.routers.hypervisors.HypervisorService.create",
        new=AsyncMock(side_effect=ValueError("already exists")),
    ):
        r = client.post("/api/v1/hypervisors", json=_SPEC_PAYLOAD, headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 409


def test_create_hypervisor_build_manager_rejected(client):
    r = client.post("/api/v1/hypervisors", json=_SPEC_PAYLOAD, headers=BUILD_MGR_HEADERS)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# GET /api/v1/hypervisors
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("headers", [CLUSTER_OP_HEADERS, BUILD_MGR_HEADERS, SENIOR_DEV_HEADERS])
def test_list_hypervisors_allowed_roles(client, headers):
    with patch(
        "gitopsgui.api.routers.hypervisors.HypervisorService.list",
        new=AsyncMock(return_value=_LIST_RESPONSE),
    ):
        r = client.get("/api/v1/hypervisors", headers=headers)
    assert r.status_code == 200
    assert "items" in r.json()


# ---------------------------------------------------------------------------
# GET /api/v1/hypervisors/{name}
# ---------------------------------------------------------------------------

def test_get_hypervisor_returns_200(client):
    with patch(
        "gitopsgui.api.routers.hypervisors.HypervisorService.get",
        new=AsyncMock(return_value=_RESPONSE),
    ):
        r = client.get("/api/v1/hypervisors/mercury", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 200
    assert r.json()["name"] == "mercury"


def test_get_hypervisor_missing_returns_404(client):
    with patch(
        "gitopsgui.api.routers.hypervisors.HypervisorService.get",
        new=AsyncMock(return_value=None),
    ):
        r = client.get("/api/v1/hypervisors/ghost", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/v1/hypervisors/{name}
# ---------------------------------------------------------------------------

def test_update_hypervisor_returns_200(client):
    with patch(
        "gitopsgui.api.routers.hypervisors.HypervisorService.update",
        new=AsyncMock(return_value=_RESPONSE),
    ):
        r = client.patch("/api/v1/hypervisors/mercury", json=_SPEC_PAYLOAD, headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 200
    assert r.json()["name"] == "mercury"


def test_update_hypervisor_missing_returns_404(client):
    with patch(
        "gitopsgui.api.routers.hypervisors.HypervisorService.update",
        new=AsyncMock(side_effect=FileNotFoundError("not found")),
    ):
        r = client.patch("/api/v1/hypervisors/ghost", json=_SPEC_PAYLOAD, headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 404


def test_update_hypervisor_name_mismatch_returns_422(client):
    with patch(
        "gitopsgui.api.routers.hypervisors.HypervisorService.update",
        new=AsyncMock(side_effect=ValueError("Cannot rename")),
    ):
        r = client.patch("/api/v1/hypervisors/mercury", json=_SPEC_PAYLOAD, headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 422


def test_update_hypervisor_build_manager_rejected(client):
    r = client.patch("/api/v1/hypervisors/mercury", json=_SPEC_PAYLOAD, headers=BUILD_MGR_HEADERS)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# DELETE /api/v1/hypervisors/{name}
# ---------------------------------------------------------------------------

def test_delete_hypervisor_returns_204(client):
    with patch(
        "gitopsgui.api.routers.hypervisors.HypervisorService.delete",
        new=AsyncMock(return_value=None),
    ):
        r = client.delete("/api/v1/hypervisors/mercury", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 204


def test_delete_hypervisor_missing_returns_404(client):
    with patch(
        "gitopsgui.api.routers.hypervisors.HypervisorService.delete",
        new=AsyncMock(side_effect=FileNotFoundError("not found")),
    ):
        r = client.delete("/api/v1/hypervisors/ghost", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 404


def test_delete_hypervisor_build_manager_rejected(client):
    r = client.delete("/api/v1/hypervisors/mercury", headers=BUILD_MGR_HEADERS)
    assert r.status_code == 403


# ---------------------------------------------------------------------------
# POST /api/v1/hypervisors/{name}/audit
# ---------------------------------------------------------------------------

_AUDIT_RESPONSE = HypervisorResponse(
    name="mercury",
    endpoint="https://192.168.4.52:8006/",
    host_ip="192.168.4.52",
    ssh_credentials_ref="mercury-root",
    audit=HypervisorAuditData(
        bridges=["vmbr0", "vmbr1"],
        storage_pools=["zfs-pool-01", "ceph-pool-01"],
        template_vms=["talos-v1.12.6"],
        proxmox_nodes=["mercury"],
        last_audited="2026-04-11T10:00:00Z",
    ),
)


def test_run_audit_returns_200_with_audit_fields(client):
    with patch(
        "gitopsgui.api.routers.hypervisors.HypervisorService.run_audit",
        new=AsyncMock(return_value=_AUDIT_RESPONSE),
    ):
        r = client.post("/api/v1/hypervisors/mercury/audit", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["name"] == "mercury"
    assert body["audit"]["bridges"] == ["vmbr0", "vmbr1"]
    assert body["audit"]["last_audited"] == "2026-04-11T10:00:00Z"


def test_run_audit_missing_hypervisor_returns_404(client):
    with patch(
        "gitopsgui.api.routers.hypervisors.HypervisorService.run_audit",
        new=AsyncMock(side_effect=FileNotFoundError("not found")),
    ):
        r = client.post("/api/v1/hypervisors/ghost/audit", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 404


def test_run_audit_no_ssh_credentials_ref_returns_422(client):
    with patch(
        "gitopsgui.api.routers.hypervisors.HypervisorService.run_audit",
        new=AsyncMock(side_effect=ValueError("no ssh_credentials_ref")),
    ):
        r = client.post("/api/v1/hypervisors/mercury/audit", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 422


def test_run_audit_build_manager_rejected(client):
    r = client.post("/api/v1/hypervisors/mercury/audit", headers=BUILD_MGR_HEADERS)
    assert r.status_code == 403
