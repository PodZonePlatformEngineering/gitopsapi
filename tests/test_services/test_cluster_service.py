"""
Unit tests for ClusterService — mocks GitService and GitHubService.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gitopsgui.models.cluster import ClusterSpec, ClusterDimensions, PlatformSpec, TalosTemplateSpec
from gitopsgui.services.cluster_service import (
    ClusterService, _render_values, _render_kustomization, _render_cluster_yaml,
    _set_kustomization_suspended, _remove_kustomization,
)


_PLATFORM = PlatformSpec(
    name="test-hypervisor",
    endpoint="https://192.168.1.10:8006",
    nodes=["test-hypervisor"],
)

_SPEC = ClusterSpec(
    name="test-cluster",
    platform=_PLATFORM,
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


def test_render_values_platform_included():
    out = _render_values(_SPEC)
    assert "test-hypervisor" in out
    assert "https://192.168.1.10:8006" in out
    assert "proxmox" in out


def test_render_values_external_hosts_included():
    spec = _SPEC.model_copy(update={"external_hosts": ["git.podzone.cloud", "login.podzone.cloud"]})
    out = _render_values(spec)
    assert "external_hosts" in out
    assert "git.podzone.cloud" in out
    assert "login.podzone.cloud" in out


def test_render_values_external_hosts_omitted_when_empty():
    out = _render_values(_SPEC)
    assert "external_hosts" not in out


def test_render_values_platform_omitted_when_none():
    spec = _SPEC.model_copy(update={"platform": None})
    out = _render_values(spec)
    assert "platform:" not in out


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


# ---------------------------------------------------------------------------
# clusters.yaml helpers
# ---------------------------------------------------------------------------

_CLUSTERS_YAML = """\
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: testcluster-cluster
  namespace: flux-system
spec:
  interval: 10m
  path: ./gitops/cluster-charts/testcluster
  prune: true
---
apiVersion: kustomize.toolkit.fluxcd.io/v1
kind: Kustomization
metadata:
  name: other-cluster
  namespace: flux-system
spec:
  interval: 10m
  path: ./gitops/cluster-charts/other
  prune: true
"""


def test_set_kustomization_suspended_adds_suspend():
    result = _set_kustomization_suspended(_CLUSTERS_YAML, "testcluster")
    assert "suspend: true" in result
    # Only patched the target — other cluster unaffected
    other_idx = result.index("name: other-cluster")
    other_block = result[other_idx:]
    assert "suspend: true" not in other_block


def test_set_kustomization_suspended_noop_on_unknown():
    result = _set_kustomization_suspended(_CLUSTERS_YAML, "nonexistent")
    assert result == _CLUSTERS_YAML


def test_remove_kustomization_removes_target():
    result = _remove_kustomization(_CLUSTERS_YAML, "testcluster")
    assert "name: testcluster-cluster" not in result
    assert "name: other-cluster" in result


def test_remove_kustomization_noop_on_unknown():
    result = _remove_kustomization(_CLUSTERS_YAML, "nonexistent")
    assert result == _CLUSTERS_YAML


# ---------------------------------------------------------------------------
# suspend_cluster
# ---------------------------------------------------------------------------

async def test_suspend_cluster_creates_pr():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=_CLUSTERS_YAML)
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/pr/10")

    result = await svc.suspend_cluster("testcluster")

    svc._git.write_file.assert_called_once()
    written = svc._git.write_file.call_args.args[1]
    assert "suspend: true" in written
    assert result.pr_url == "https://github.com/test/pr/10"


# ---------------------------------------------------------------------------
# decommission_cluster
# ---------------------------------------------------------------------------

async def test_decommission_cluster_creates_pr_and_archives():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.delete_file = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=_CLUSTERS_YAML)
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh = AsyncMock()
    svc._gh.archive_repo = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/pr/11")

    result = await svc.decommission_cluster("testcluster")

    assert svc._git.delete_file.call_count == 4  # values + cluster.yaml + kustomization + kustomizeconfig
    written_content = svc._git.write_file.call_args.args[1]
    assert "name: testcluster-cluster" not in written_content
    assert svc._gh.archive_repo.call_count == 2
    archived_names = {c.args[0] for c in svc._gh.archive_repo.call_args_list}
    assert archived_names == {"testcluster-infra", "testcluster-apps"}
    assert result.archived_repos == ["testcluster-infra", "testcluster-apps"]
    assert result.pr_url == "https://github.com/test/pr/11"


# ---------------------------------------------------------------------------
# TalosTemplateSpec
# ---------------------------------------------------------------------------

def test_talos_template_spec_defaults():
    t = TalosTemplateSpec()
    assert t.name == "0-talos-template"
    assert t.version == "v1.9.5"
    assert t.vmid == 100
    assert t.node is None


def test_talos_template_spec_custom_values():
    t = TalosTemplateSpec(name="my-template", version="v1.10.0", vmid=200, node="pve2")
    assert t.name == "my-template"
    assert t.version == "v1.10.0"
    assert t.vmid == 200
    assert t.node == "pve2"


# ---------------------------------------------------------------------------
# _render_values — TalosTemplateSpec integration
# ---------------------------------------------------------------------------

def test_render_values_uses_talos_template_node_for_sourcenode():
    """When talos_template.node is set it is used as proxmox.template.sourcenode."""
    platform = PlatformSpec(
        name="test-hypervisor",
        endpoint="https://192.168.1.10:8006",
        nodes=["node1", "node2"],
        talos_template=TalosTemplateSpec(node="node2"),
    )
    spec = _SPEC.model_copy(update={"platform": platform})
    out = _render_values(spec)
    assert "sourcenode: node2" in out


def test_render_values_falls_back_to_nodes0_when_talos_template_node_is_none():
    """When talos_template.node is None, proxmox.template.sourcenode defaults to nodes[0]."""
    platform = PlatformSpec(
        name="test-hypervisor",
        endpoint="https://192.168.1.10:8006",
        nodes=["primary-node"],
        talos_template=TalosTemplateSpec(node=None),
    )
    spec = _SPEC.model_copy(update={"platform": platform})
    out = _render_values(spec)
    assert "sourcenode: primary-node" in out


def test_render_values_uses_talos_template_vmid():
    """proxmox.template.template_vmid comes from talos_template.vmid."""
    platform = PlatformSpec(
        name="test-hypervisor",
        endpoint="https://192.168.1.10:8006",
        nodes=["test-hypervisor"],
        talos_template=TalosTemplateSpec(vmid=999),
    )
    spec = _SPEC.model_copy(update={"platform": platform})
    out = _render_values(spec)
    assert "template_vmid: 999" in out


def test_render_values_includes_talos_template_name_and_version_in_platform_block():
    """platform roundtrip block includes talos_template.name and talos_template.version."""
    platform = PlatformSpec(
        name="test-hypervisor",
        endpoint="https://192.168.1.10:8006",
        nodes=["test-hypervisor"],
        talos_template=TalosTemplateSpec(name="my-talos-tmpl", version="v1.10.1"),
    )
    spec = _SPEC.model_copy(update={"platform": platform})
    out = _render_values(spec)
    assert "my-talos-tmpl" in out
    assert "v1.10.1" in out


def test_render_values_platform_block_has_talos_template_subkey():
    """The platform: block in values YAML must contain a talos_template: sub-block."""
    out = _render_values(_SPEC)
    assert "talos_template:" in out
