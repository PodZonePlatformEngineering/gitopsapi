"""
Unit tests for HypervisorService — uses GITOPS_SKIP_K8S=1 in-memory path.
"""

import pytest
from unittest.mock import AsyncMock, patch

import gitopsgui.services.hypervisor_service as hs_module
from gitopsgui.models.hypervisor import HypervisorSpec
from gitopsgui.services.hypervisor_service import HypervisorService


_SPEC = HypervisorSpec(
    name="mercury",
    endpoint="https://192.168.4.52:8006/",
    host_ip="192.168.4.52",
)

_SPEC_2 = HypervisorSpec(
    name="venus",
    endpoint="https://192.168.4.53:8006/",
    host_ip="192.168.4.53",
)


@pytest.fixture(autouse=True)
def skip_k8s(monkeypatch):
    monkeypatch.setenv("GITOPS_SKIP_K8S", "1")


@pytest.fixture(autouse=True)
def clear_store():
    hs_module._local_store.clear()
    yield
    hs_module._local_store.clear()


def _svc() -> HypervisorService:
    return HypervisorService()


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_returns_response():
    svc = _svc()
    result = await svc.create(_SPEC)
    assert result.name == "mercury"
    assert result.endpoint == "https://192.168.4.52:8006/"
    assert result.host_ip == "192.168.4.52"
    assert result.type == "proxmox"
    assert result.bridge == "vmbr0"
    assert result.default_storage_pool == "local-lvm"


@pytest.mark.asyncio
async def test_create_duplicate_raises_value_error():
    svc = _svc()
    await svc.create(_SPEC)
    with pytest.raises(ValueError, match="already exists"):
        await svc.create(_SPEC)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_list_empty():
    result = await _svc().list()
    assert result.items == []


@pytest.mark.asyncio
async def test_list_after_two_creates():
    svc = _svc()
    await svc.create(_SPEC)
    await svc.create(_SPEC_2)
    result = await svc.list()
    names = {h.name for h in result.items}
    assert names == {"mercury", "venus"}


# ---------------------------------------------------------------------------
# get
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_get_existing():
    svc = _svc()
    await svc.create(_SPEC)
    result = await svc.get("mercury")
    assert result is not None
    assert result.name == "mercury"
    assert result.endpoint == "https://192.168.4.52:8006/"


@pytest.mark.asyncio
async def test_get_missing_returns_none():
    result = await _svc().get("nonexistent")
    assert result is None


# ---------------------------------------------------------------------------
# update
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_existing():
    svc = _svc()
    await svc.create(_SPEC)
    updated_spec = HypervisorSpec(
        name="mercury",
        endpoint="https://freyr:8008/",
        host_ip="192.168.4.52",
        bridge="vmbr1",
    )
    result = await svc.update("mercury", updated_spec)
    assert result.endpoint == "https://freyr:8008/"
    assert result.bridge == "vmbr1"


@pytest.mark.asyncio
async def test_update_missing_raises_file_not_found():
    with pytest.raises(FileNotFoundError, match="not found"):
        await _svc().update("ghost", _SPEC)


@pytest.mark.asyncio
async def test_update_name_mismatch_raises_value_error():
    svc = _svc()
    await svc.create(_SPEC)
    wrong_name_spec = HypervisorSpec(
        name="venus",
        endpoint="https://192.168.4.52:8006/",
        host_ip="192.168.4.52",
    )
    with pytest.raises(ValueError, match="Cannot rename"):
        await svc.update("mercury", wrong_name_spec)


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_delete_existing():
    svc = _svc()
    await svc.create(_SPEC)
    await svc.delete("mercury")
    assert await svc.get("mercury") is None


@pytest.mark.asyncio
async def test_delete_missing_raises_file_not_found():
    with pytest.raises(FileNotFoundError, match="not found"):
        await _svc().delete("ghost")


# ---------------------------------------------------------------------------
# get_ssh_context
# ---------------------------------------------------------------------------

_SPEC_WITH_SSH = HypervisorSpec(
    name="mercury",
    endpoint="https://192.168.4.52:8006/",
    host_ip="192.168.4.52",
    ssh_credentials_ref="mercury-root",
)


@pytest.mark.asyncio
async def test_get_ssh_context_returns_dict():
    svc = _svc()
    await svc.create(_SPEC_WITH_SSH)
    ctx = await svc.get_ssh_context("mercury")
    assert ctx == {"host_ip": "192.168.4.52", "ssh_credentials_ref": "mercury-root"}


@pytest.mark.asyncio
async def test_get_ssh_context_missing_raises_file_not_found():
    with pytest.raises(FileNotFoundError, match="not found"):
        await _svc().get_ssh_context("ghost")


@pytest.mark.asyncio
async def test_get_ssh_context_no_credentials_ref_raises_value_error():
    svc = _svc()
    await svc.create(_SPEC)  # _SPEC has no ssh_credentials_ref
    with pytest.raises(ValueError, match="no ssh_credentials_ref"):
        await svc.get_ssh_context("mercury")


# ---------------------------------------------------------------------------
# run_audit
# ---------------------------------------------------------------------------

MOCK_AUDIT_RESULT = {
    "bridges": ["vmbr0", "vmbr1"],
    "storage_pools": ["zfs-pool-01", "ceph-pool-01"],
    "template_vms": ["talos-v1.12.6"],
    "proxmox_nodes": ["mercury"],
    "last_audited": "2026-04-11T10:00:00Z",
}


@pytest.mark.asyncio
async def test_run_audit_populates_audit_fields():
    svc = _svc()
    await svc.create(_SPEC_WITH_SSH)
    with patch(
        "gitopsgui.services.egg_script_service.EggScriptService.audit",
        new=AsyncMock(return_value=MOCK_AUDIT_RESULT),
    ):
        result = await svc.run_audit("mercury")
    assert result.audit.bridges == ["vmbr0", "vmbr1"]
    assert result.audit.storage_pools == ["zfs-pool-01", "ceph-pool-01"]
    assert result.audit.template_vms == ["talos-v1.12.6"]
    assert result.audit.proxmox_nodes == ["mercury"]
    assert result.audit.last_audited == "2026-04-11T10:00:00Z"


@pytest.mark.asyncio
async def test_run_audit_persists_to_store():
    svc = _svc()
    await svc.create(_SPEC_WITH_SSH)
    with patch(
        "gitopsgui.services.egg_script_service.EggScriptService.audit",
        new=AsyncMock(return_value=MOCK_AUDIT_RESULT),
    ):
        await svc.run_audit("mercury")
    fetched = await svc.get("mercury")
    assert fetched.audit.last_audited == "2026-04-11T10:00:00Z"
    assert fetched.audit.bridges == ["vmbr0", "vmbr1"]


@pytest.mark.asyncio
async def test_run_audit_missing_hypervisor_raises_file_not_found():
    with pytest.raises(FileNotFoundError, match="not found"):
        await _svc().run_audit("ghost")


@pytest.mark.asyncio
async def test_run_audit_no_ssh_credentials_ref_raises_value_error():
    svc = _svc()
    await svc.create(_SPEC)  # _SPEC has no ssh_credentials_ref
    with pytest.raises(ValueError, match="no ssh_credentials_ref"):
        await svc.run_audit("mercury")


@pytest.mark.asyncio
async def test_run_audit_partial_result_defaults_to_empty():
    svc = _svc()
    await svc.create(_SPEC_WITH_SSH)
    with patch(
        "gitopsgui.services.egg_script_service.EggScriptService.audit",
        new=AsyncMock(return_value={"last_audited": "2026-04-11T10:00:00Z"}),
    ):
        result = await svc.run_audit("mercury")
    assert result.audit.bridges == []
    assert result.audit.storage_pools == []
    assert result.audit.template_vms == []
    assert result.audit.proxmox_nodes == []
    assert result.audit.last_audited == "2026-04-11T10:00:00Z"
