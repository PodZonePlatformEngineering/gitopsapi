"""
Model tests for HypervisorSpec, HypervisorAuditData, HypervisorResponse.
"""

import pytest
from gitopsgui.models.hypervisor import (
    HypervisorAuditData,
    HypervisorSpec,
    HypervisorResponse,
)


def _minimal_spec() -> HypervisorSpec:
    return HypervisorSpec(
        name="mercury",
        endpoint="https://192.168.4.52:8006/",
        host_ip="192.168.4.52",
    )


# ---------------------------------------------------------------------------
# HypervisorAuditData defaults
# ---------------------------------------------------------------------------

def test_audit_data_defaults():
    audit = HypervisorAuditData()
    assert audit.bridges == []
    assert audit.storage_pools == []
    assert audit.template_vms == []
    assert audit.proxmox_nodes == []
    assert audit.last_audited is None


# ---------------------------------------------------------------------------
# HypervisorSpec defaults
# ---------------------------------------------------------------------------

def test_spec_defaults():
    spec = _minimal_spec()
    assert spec.type == "proxmox"
    assert spec.bridge == "vmbr0"
    assert spec.default_storage_pool == "local-lvm"
    assert spec.credentials_ref == "capmox-manager-credentials"
    assert spec.ssh_credentials_ref is None
    assert spec.idrac_ip is None
    assert spec.idrac_credentials_ref is None
    assert spec.nodes == []
    assert spec.audit.bridges == []


# ---------------------------------------------------------------------------
# HypervisorResponse roundtrip
# ---------------------------------------------------------------------------

def test_response_roundtrip():
    spec = HypervisorSpec(
        name="venus",
        endpoint="https://192.168.4.53:8006/",
        host_ip="192.168.4.53",
        nodes=["venus"],
        bridge="vmbr1",
        default_storage_pool="zfs-pool-01",
        audit=HypervisorAuditData(
            bridges=["vmbr0", "vmbr1"],
            storage_pools=["zfs-pool-01"],
            template_vms=["talos-v1.12.6"],
            last_audited="2026-04-10T00:00:00Z",
        ),
    )
    resp = HypervisorResponse(**spec.model_dump())
    json_str = resp.model_dump_json()
    roundtripped = HypervisorResponse.model_validate_json(json_str)
    assert roundtripped.name == "venus"
    assert roundtripped.nodes == ["venus"]
    assert roundtripped.audit.bridges == ["vmbr0", "vmbr1"]
    assert roundtripped.audit.last_audited == "2026-04-10T00:00:00Z"
