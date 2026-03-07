"""
Pydantic model validation tests for cluster models.
"""

import pytest
from pydantic import ValidationError
from gitopsgui.models.cluster import ClusterSpec, ClusterDimensions, ClusterResponse


def test_cluster_spec_defaults():
    spec = ClusterSpec(
        name="my-cluster",
        platform="proxmox",
        ip_range="192.168.1.0/24",
        dimensions=ClusterDimensions(),
        gitops_repo_url="https://github.com/test/repo",
        sops_secret_ref="sops-key",
    )
    assert spec.dimensions.control_plane_count == 3
    assert spec.dimensions.worker_count == 3


def test_cluster_spec_missing_required_field():
    with pytest.raises(ValidationError):
        ClusterSpec(
            platform="proxmox",
            ip_range="192.168.1.0/24",
            dimensions=ClusterDimensions(),
            gitops_repo_url="https://github.com/test/repo",
            sops_secret_ref="sops-key",
            # name missing
        )


def test_cluster_response_optional_fields():
    from gitopsgui.models.cluster import ClusterDimensions
    spec = ClusterSpec(
        name="c",
        platform="proxmox",
        ip_range="10.0.0.0/24",
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
