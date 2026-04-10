"""
Unit tests for EggScriptService.

All tests use GITOPS_SKIP_K8S=1 and GITOPS_SKIP_SSH=1.
HypervisorService.get_ssh_context is patched to return a fixed context.
SSHOrchestrationService responses are controlled via _mock_execute_response.
No SSH connections or K8s API calls are made.
"""
import json

import pytest
from unittest.mock import AsyncMock

import gitopsgui.services.ssh_orchestration_service as ssh_svc
from gitopsgui.models.ssh_result import SSHResult
from gitopsgui.services.egg_script_service import EggScriptError, EggScriptService
from gitopsgui.services.hypervisor_service import HypervisorService

_FAKE_CTX = {"host_ip": "192.168.4.52", "ssh_credentials_ref": "mercury-root"}


@pytest.fixture(autouse=True)
def skip_all(monkeypatch):
    monkeypatch.setenv("GITOPS_SKIP_K8S", "1")
    monkeypatch.setenv("GITOPS_SKIP_SSH", "1")


@pytest.fixture(autouse=True)
def reset_mock(monkeypatch):
    monkeypatch.setattr(ssh_svc, "_mock_execute_response", None)
    yield
    monkeypatch.setattr(ssh_svc, "_mock_execute_response", None)


@pytest.fixture(autouse=True)
def patch_ctx(monkeypatch):
    monkeypatch.setattr(
        HypervisorService,
        "get_ssh_context",
        AsyncMock(return_value=_FAKE_CTX),
    )


def _svc() -> EggScriptService:
    return EggScriptService()


def _ok(stdout: str) -> SSHResult:
    return SSHResult(host="192.168.4.52", command="cmd", stdout=stdout, stderr="", exit_code=0)


def _fail(stderr: str = "error") -> SSHResult:
    return SSHResult(host="192.168.4.52", command="cmd", stdout="", stderr=stderr, exit_code=1)


# ---------------------------------------------------------------------------
# audit()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_audit_returns_parsed_dict(monkeypatch):
    payload = {
        "bridges": ["vmbr0", "vmbr1"],
        "storage_pools": ["zfs-pool-01"],
        "template_vms": ["talos-v1.12.6"],
        "proxmox_nodes": ["mercury"],
        "last_audited": "2026-04-10T12:00:00Z",
    }
    monkeypatch.setattr(ssh_svc, "_mock_execute_response", _ok(json.dumps(payload)))
    result = await _svc().audit("mercury")
    assert result["bridges"] == ["vmbr0", "vmbr1"]
    assert result["storage_pools"] == ["zfs-pool-01"]
    assert result["template_vms"] == ["talos-v1.12.6"]
    assert result["proxmox_nodes"] == ["mercury"]
    assert "last_audited" in result


@pytest.mark.asyncio
async def test_audit_nonzero_exit_raises_egg_script_error(monkeypatch):
    monkeypatch.setattr(ssh_svc, "_mock_execute_response", _fail("pvesh not found"))
    with pytest.raises(EggScriptError):
        await _svc().audit("mercury")


# ---------------------------------------------------------------------------
# create_template()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_template_skipped_response(monkeypatch):
    payload = {"status": "skipped", "reason": "template talos-v1.12.6 already exists", "vmid": 9000}
    monkeypatch.setattr(ssh_svc, "_mock_execute_response", _ok(json.dumps(payload)))
    result = await _svc().create_template("mercury", {
        "TALOS_VERSION": "v1.12.6",
        "TALOS_SCHEMA_ID": "abc123",
        "VMID": "9000",
        "STORAGE": "zfs-pool-01",
        "BRIDGE": "vmbr0",
    })
    assert result["status"] == "skipped"
    assert result["vmid"] == 9000


@pytest.mark.asyncio
async def test_create_template_created_response(monkeypatch):
    payload = {"status": "created", "template": "talos-v1.12.6", "vmid": 9000}
    monkeypatch.setattr(ssh_svc, "_mock_execute_response", _ok(json.dumps(payload)))
    result = await _svc().create_template("mercury", {
        "TALOS_VERSION": "v1.12.6",
        "TALOS_SCHEMA_ID": "abc123",
        "VMID": "9000",
        "STORAGE": "zfs-pool-01",
        "BRIDGE": "vmbr0",
    })
    assert result["status"] == "created"
    assert result["template"] == "talos-v1.12.6"
    assert result["vmid"] == 9000


# ---------------------------------------------------------------------------
# provision_cluster()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_provision_cluster_success(monkeypatch):
    payload = {
        "status": "provisioned",
        "cluster": "mercury-management",
        "vip": "192.168.4.150",
        "kubeconfig_path": "/tmp/mercury-management.kubeconfig",
    }
    monkeypatch.setattr(ssh_svc, "_mock_execute_response", _ok(json.dumps(payload)))
    result = await _svc().provision_cluster("mercury", {
        "CLUSTER_NAME": "mercury-management",
        "VIP": "192.168.4.150",
        "TEMPLATE_VMID": "9000",
        "NEW_VMID": "100",
        "STORAGE": "zfs-pool-01",
        "BRIDGE": "vmbr0",
        "CPU": "4",
        "MEMORY_MB": "8192",
        "DISK_GB": "50",
        "TALOS_VERSION": "v1.12.6",
        "TALOS_SCHEMA_ID": "abc123",
        "K8S_VERSION": "v1.34.6",
        "INSTALL_DISK": "/dev/vda",
    })
    assert result["status"] == "provisioned"
    assert result["cluster"] == "mercury-management"
    assert result["vip"] == "192.168.4.150"
    assert "kubeconfig_path" in result


@pytest.mark.asyncio
async def test_provision_cluster_nonzero_exit_raises(monkeypatch):
    monkeypatch.setattr(ssh_svc, "_mock_execute_response", _fail("talosctl not found"))
    with pytest.raises(EggScriptError):
        await _svc().provision_cluster("mercury", {"CLUSTER_NAME": "mercury-management"})


# ---------------------------------------------------------------------------
# download_kubeconfig()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_kubeconfig_returns_bytes():
    # GITOPS_SKIP_SSH=1 → SSHOrchestrationService.download returns b""
    result = await _svc().download_kubeconfig("mercury", "/tmp/mercury-management.kubeconfig")
    assert isinstance(result, bytes)


# ---------------------------------------------------------------------------
# platform_install()
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_platform_install_success(monkeypatch):
    payload = {"status": "complete", "flux": "installed", "capi": "installed", "capmox": "installed"}
    monkeypatch.setattr(ssh_svc, "_mock_execute_response", _ok(json.dumps(payload)))
    result = await _svc().platform_install("mercury", {
        "KUBECONFIG_PATH": "/tmp/mercury-management.kubeconfig",
        "CLUSTER_CHART_REPO_URL": "oci://ghcr.io/podzoneplatformengineering/cluster-chart",
        "CLUSTER_CHART_VERSION": "0.1.40",
    })
    assert result["status"] == "complete"
    assert result["flux"] == "installed"
    assert result["capi"] == "installed"
    assert result["capmox"] == "installed"


# ---------------------------------------------------------------------------
# Script file existence
# ---------------------------------------------------------------------------

def test_egg_audit_script_exists_and_nonempty():
    from gitopsgui.services.egg_script_service import _SCRIPT_DIR
    p = _SCRIPT_DIR / "egg-audit.sh"
    assert p.exists(), f"Missing: {p}"
    assert p.stat().st_size > 0


def test_egg_template_script_exists_and_nonempty():
    from gitopsgui.services.egg_script_service import _SCRIPT_DIR
    p = _SCRIPT_DIR / "egg-template.sh"
    assert p.exists(), f"Missing: {p}"
    assert p.stat().st_size > 0


def test_egg_provision_script_exists_and_nonempty():
    from gitopsgui.services.egg_script_service import _SCRIPT_DIR
    p = _SCRIPT_DIR / "egg-provision.sh"
    assert p.exists(), f"Missing: {p}"
    assert p.stat().st_size > 0


def test_egg_platform_install_script_exists_and_nonempty():
    from gitopsgui.services.egg_script_service import _SCRIPT_DIR
    p = _SCRIPT_DIR / "egg-platform-install.sh"
    assert p.exists(), f"Missing: {p}"
    assert p.stat().st_size > 0
