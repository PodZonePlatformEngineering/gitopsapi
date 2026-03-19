"""
Pydantic model validation tests for cluster models.
"""

import pytest
from pydantic import ValidationError
from gitopsgui.models.cluster import ClusterSpec, ClusterDimensions, ClusterResponse, PlatformSpec


def test_cluster_spec_defaults():
    spec = ClusterSpec(
        name="my-cluster",
        vip="192.168.1.0",
        ip_range="192.168.1.1-192.168.1.7",
        dimensions=ClusterDimensions(),
        gitops_repo_url="https://github.com/test/repo",
        sops_secret_ref="sops-key",
    )
    assert spec.dimensions.control_plane_count == 3
    assert spec.dimensions.worker_count == 3


def test_cluster_spec_missing_required_field():
    with pytest.raises(ValidationError):
        ClusterSpec(
            vip="192.168.1.0",
            ip_range="192.168.1.1-192.168.1.7",
            dimensions=ClusterDimensions(),
            gitops_repo_url="https://github.com/test/repo",
            sops_secret_ref="sops-key",
            # name missing
        )


def test_platform_spec_defaults_type_proxmox():
    p = PlatformSpec(name="erectus", endpoint="https://192.168.1.201:8006", nodes=["erectus"])
    assert p.type == "proxmox"


def test_platform_spec_multi_node():
    p = PlatformSpec(name="pve-cluster", endpoint="https://192.168.4.1:8006", nodes=["pve1", "pve2", "pve3"])
    assert len(p.nodes) == 3


def test_cluster_spec_with_platform():
    platform = PlatformSpec(name="erectus", endpoint="https://192.168.1.201:8006", nodes=["erectus"])
    spec = ClusterSpec(
        name="my-cluster",
        platform=platform,
        vip="192.168.1.0",
        ip_range="192.168.1.1-192.168.1.7",
        dimensions=ClusterDimensions(),
        sops_secret_ref="sops-key",
    )
    assert spec.platform.name == "erectus"
    assert spec.platform.type == "proxmox"


def test_cluster_spec_platform_optional():
    spec = ClusterSpec(
        name="external-cluster",
        vip="192.168.1.0",
        ip_range="192.168.1.1-192.168.1.7",
        dimensions=ClusterDimensions(),
        managed_gitops=False,
        gitops_repo_url="git@github.com:org/external-infra.git",
        sops_secret_ref="sops-key",
    )
    assert spec.platform is None


def test_cluster_response_optional_fields():
    from gitopsgui.models.cluster import ClusterDimensions
    spec = ClusterSpec(
        name="c",
        vip="10.0.0.0",
        ip_range="10.0.0.1-10.0.0.7",
        dimensions=ClusterDimensions(),
        gitops_repo_url="https://github.com/test/repo",
        sops_secret_ref="key",
    )
    response = ClusterResponse(name="c", spec=spec)
    assert response.status is None
    assert response.pr_url is None


def test_cluster_dimensions_custom_values():
    d = ClusterDimensions(control_plane_count=3, worker_count=5, cpu_per_node=8, memory_gb_per_node=32)
    assert d.cpu_per_node == 8
    assert d.memory_gb_per_node == 32


def test_allow_scheduling_on_control_planes_defaults_false():
    spec = ClusterSpec(
        name="cp-only",
        vip="10.0.0.1",
        ip_range="10.0.0.2-10.0.0.5",
        dimensions=ClusterDimensions(control_plane_count=1, worker_count=0),
        sops_secret_ref="sops-key",
    )
    assert spec.allow_scheduling_on_control_planes is False


def test_allow_scheduling_on_control_planes_can_be_set():
    spec = ClusterSpec(
        name="cp-only",
        vip="10.0.0.1",
        ip_range="10.0.0.2-10.0.0.5",
        dimensions=ClusterDimensions(control_plane_count=1, worker_count=0),
        sops_secret_ref="sops-key",
        allow_scheduling_on_control_planes=True,
    )
    assert spec.allow_scheduling_on_control_planes is True
