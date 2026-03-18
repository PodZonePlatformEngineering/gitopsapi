"""
Unit tests for ClusterService — mocks GitService and GitHubService.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gitopsgui.models.cluster import ClusterSpec, ClusterDimensions
from gitopsgui.services.cluster_service import (
    ClusterService, _render_values, _render_kustomization, _render_cluster_yaml
)


_SPEC = ClusterSpec(
    name="test-cluster",
    platform="proxmox",
    vip="192.168.1.100",
    ip_range="192.168.1.101-192.168.1.107",
    dimensions=ClusterDimensions(control_plane_count=1, worker_count=1),
    managed_gitops=False,  # external repo — skips GitHub repo creation in create_cluster
    gitops_repo_url="https://github.com/test/repo",
    sops_secret_ref="sops-key",
)


# ---------------------------------------------------------------------------
# Render helpers
# ---------------------------------------------------------------------------

def test_render_values_contains_name():
    out = _render_values(_SPEC)
    assert "test-cluster" in out
    assert "192.168.1.100" in out


def test_render_values_cluster_chart_fields():
    """cluster/network/controlplane/worker keys must be present for cluster-chart consumption."""
    out = _render_values(_SPEC)
    assert "cluster:" in out
    assert "network:" in out
    assert "controlplane:" in out
    assert "worker:" in out
    assert "ip_ranges:" in out
    assert "endpoint_ip:" in out


def test_render_values_extra_manifests_included():
    spec = _SPEC.model_copy(update={"extra_manifests": [
        "http://192.168.4.1/cilium.yaml",
        "http://192.168.4.1/flux.yaml",
    ]})
    out = _render_values(spec)
    assert "extra_manifests" in out
    assert "cilium.yaml" in out


def test_render_values_no_extra_manifests_omitted():
    out = _render_values(_SPEC)
    assert "extra_manifests" not in out


def test_render_values_allow_scheduling_default_omitted():
    out = _render_values(_SPEC)
    # False is stored as roundtrip field but controlplane key must not appear
    assert "allow_scheduling_on_control_planes: true" not in out


def test_render_values_allow_scheduling_when_enabled():
    spec = _SPEC.model_copy(update={"allow_scheduling_on_control_planes": True})
    out = _render_values(spec)
    assert "allow_scheduling_on_control_planes: true" in out


def test_render_values_worker_count_zero_preserved():
    spec = _SPEC.model_copy(update={
        "allow_scheduling_on_control_planes": True,
        "dimensions": ClusterDimensions(control_plane_count=1, worker_count=0),
    })
    out = _render_values(spec)
    assert "worker_count: 0" in out


def test_render_kustomization_references_name():
    out = _render_kustomization("my-cluster")
    assert "my-cluster.yaml" in out
    assert "my-cluster-values" in out


def test_render_kustomization_includes_namespace():
    out = _render_kustomization("my-cluster")
    assert "namespace: my-cluster" in out


def test_render_kustomization_includes_proxmox_secret():
    out = _render_kustomization("my-cluster")
    assert "proxmox-secret.yaml" in out


def test_render_kustomization_no_flux_system_namespace_in_configmap():
    out = _render_kustomization("my-cluster")
    assert "namespace: flux-system" not in out


def test_render_cluster_yaml_contains_helmrelease():
    out = _render_cluster_yaml("my-cluster")
    assert "HelmRelease" in out
    assert "my-cluster" in out


# ---------------------------------------------------------------------------
# get_cluster — reads from repo
# ---------------------------------------------------------------------------

async def test_get_cluster_returns_none_if_not_found():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(side_effect=FileNotFoundError("not found"))
    result = await svc.get_cluster("missing")
    assert result is None


async def test_get_cluster_parses_values_yaml():
    import yaml
    raw_values = yaml.dump({
        "cluster": {"name": "test-cluster"},
        "network": {"ip_ranges": ["192.168.1.0/24"]},
        "controlplane": {"machine_count": 1},
        "worker": {"machine_count": 1},
    })
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=raw_values)
    result = await svc.get_cluster("test-cluster")
    assert result is not None
    assert result.name == "test-cluster"
    assert result.spec.ip_range == "192.168.1.0/24"


# ---------------------------------------------------------------------------
# create_cluster — branches, writes files, opens PR
# ---------------------------------------------------------------------------

async def test_create_cluster_opens_pr():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha123")
    svc._git.push = AsyncMock()
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/1")

    result = await svc.create_cluster(_SPEC)

    svc._git.create_branch.assert_called_once()
    assert svc._git.write_file.call_count == 4  # values + cluster.yaml + kustomization + kustomizeconfig
    svc._gh.create_pr.assert_called_once()
    assert result.pr_url == "https://github.com/test/repo/pull/1"


async def test_create_cluster_pr_labels_include_cluster_and_stage():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/pr/1")

    await svc.create_cluster(_SPEC)

    call_kwargs = svc._gh.create_pr.call_args
    labels = call_kwargs.kwargs.get("labels") or call_kwargs.args[3]
    assert "cluster" in labels
    assert "stage:production" in labels
