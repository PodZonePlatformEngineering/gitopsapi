"""
Unit tests for ClusterService — mocks GitService and GitHubService.
"""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from gitopsgui.models.cluster import ClusterSpec, ClusterDimensions
from gitopsgui.services.cluster_service import (
    ClusterService, _render_values, _render_kustomization, _render_cluster_yaml
)


_SPEC = ClusterSpec(
    name="test-cluster",
    platform="proxmox",
    ip_range="192.168.1.100-192.168.1.110",
    dimensions=ClusterDimensions(control_plane_count=1, worker_count=1),
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


def test_render_kustomization_references_name():
    out = _render_kustomization("my-cluster")
    assert "my-cluster.yaml" in out
    assert "my-cluster-values" in out


def test_render_cluster_yaml_contains_helmrelease():
    out = _render_cluster_yaml("my-cluster")
    assert "HelmRelease" in out
    assert "my-cluster" in out


# ---------------------------------------------------------------------------
# get_cluster — reads from repo
# ---------------------------------------------------------------------------

def test_get_cluster_returns_none_if_not_found():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(side_effect=FileNotFoundError("not found"))
    result = asyncio.get_event_loop().run_until_complete(svc.get_cluster("missing"))
    assert result is None


def test_get_cluster_parses_values_yaml():
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
    result = asyncio.get_event_loop().run_until_complete(svc.get_cluster("test-cluster"))
    assert result is not None
    assert result.name == "test-cluster"
    assert result.spec.ip_range == "192.168.1.0/24"


# ---------------------------------------------------------------------------
# create_cluster — branches, writes files, opens PR
# ---------------------------------------------------------------------------

def test_create_cluster_opens_pr():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha123")
    svc._git.push = AsyncMock()
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/1")

    result = asyncio.get_event_loop().run_until_complete(svc.create_cluster(_SPEC))

    svc._git.create_branch.assert_called_once()
    assert svc._git.write_file.call_count == 4  # values + cluster.yaml + kustomization + kustomizeconfig
    svc._gh.create_pr.assert_called_once()
    assert result.pr_url == "https://github.com/test/repo/pull/1"


def test_create_cluster_pr_labels_include_cluster_and_stage():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/pr/1")

    asyncio.get_event_loop().run_until_complete(svc.create_cluster(_SPEC))

    call_kwargs = svc._gh.create_pr.call_args
    labels = call_kwargs.kwargs.get("labels") or call_kwargs.args[3]
    assert "cluster" in labels
    assert "stage:production" in labels
