"""
Unit tests for ClusterService — mocks GitService and GitHubService.
"""

import pytest
import httpx
import yaml as _yaml
from unittest.mock import AsyncMock, MagicMock, patch


# ---------------------------------------------------------------------------
# T-035 (CC-175) — module-wide autouse fixture
# ---------------------------------------------------------------------------
# retrieve_age_key makes K8s API calls in production. All tests in this module
# should use the stub to avoid any dependency on a live cluster. Tests that
# specifically test the ValueError/422 path monkeypatch retrieve_age_key directly.

@pytest.fixture(autouse=True)
def _stub_retrieve_age_key(monkeypatch):
    """Stub retrieve_age_key for all tests — returns a safe placeholder key."""
    monkeypatch.setattr(
        "gitopsgui.services.cluster_service.retrieve_age_key",
        lambda ref: "AGE-SECRET-KEY-1FAKESTUBTESTKEY",
    )

from gitopsgui.models.cluster import (
    ClusterSpec, ClusterDimensions, PlatformSpec, TalosTemplateSpec,
    IngressConnectorSpec, TokenSecretRef, ClusterChartSpec, StorageSpec,
    NetworkSpec,
)
from gitopsgui.services.cluster_service import (
    ClusterService, _render_values, _render_kustomization, _render_cluster_yaml,
    _set_kustomization_suspended, _remove_kustomization,
    _render_cloudflared_yaml, _render_cloudflared_apps_kustomization,
    _render_cloudflared_flux_kustomization,
    _render_piraeus_kustomization,
    fetch_static_inline_manifests, GATEWAY_API_VERSION,
    classify_cluster_changes, ChangeCategory, _dims_hash,
    _STATIC_INLINE_MANIFESTS, _PIRAEUS_INFRA_PATH,
    _build_cilium_helm_args, generate_cilium_manifest,
    CILIUM_HELM_DEFAULTS,
    retrieve_age_key, generate_fluxinstance_manifest, generate_sops_secret_manifest,
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


def test_render_values_hostname_included():
    spec = _SPEC.model_copy(update={"hostname": ["git.podzone.cloud", "login.podzone.cloud"]})
    out = _render_values(spec)
    assert "hostname" in out
    assert "git.podzone.cloud" in out
    assert "login.podzone.cloud" in out


def test_render_values_hostname_omitted_when_empty():
    out = _render_values(_SPEC)
    assert "hostname" not in out


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


# ---------------------------------------------------------------------------
# IngressConnectorSpec model
# ---------------------------------------------------------------------------

def test_ingress_connector_defaults():
    ic = IngressConnectorSpec()
    assert ic.enabled is True
    assert ic.type == "cloudflare-tunnel"
    assert ic.replicas == 2
    assert ic.namespace == "cloudflared"
    assert ic.token_secret_ref.name == "cloudflare-tunnel-token"
    assert ic.token_secret_ref.key == "token"


def test_token_secret_ref_custom():
    ref = TokenSecretRef(name="my-secret", key="api-token")
    assert ref.name == "my-secret"
    assert ref.key == "api-token"


# ---------------------------------------------------------------------------
# _render_values — ingress_connector roundtrip
# ---------------------------------------------------------------------------

def test_render_values_ingress_connector_included():
    ic = IngressConnectorSpec(
        tunnel_id="71e24b2a-94c2-4064-bf4e-137150356331",
        token_secret_ref=TokenSecretRef(name="cloudflare-tunnel-token"),
    )
    spec = _SPEC.model_copy(update={"ingress_connector": ic})
    out = _render_values(spec)
    assert "ingress_connector:" in out
    assert "cloudflare-tunnel-token" in out
    assert "71e24b2a-94c2-4064-bf4e-137150356331" in out


def test_render_values_ingress_connector_omitted_when_none():
    out = _render_values(_SPEC)
    assert "ingress_connector" not in out


# ---------------------------------------------------------------------------
# _render_cloudflared_yaml
# ---------------------------------------------------------------------------

def test_render_cloudflared_yaml_contains_helmrelease():
    ic = IngressConnectorSpec()
    out = _render_cloudflared_yaml(ic)
    assert "HelmRelease" in out
    assert "cloudflared" in out
    assert "cloudflare-tunnel-remote" in out


def test_render_cloudflared_yaml_uses_token_secret_ref():
    ic = IngressConnectorSpec(
        token_secret_ref=TokenSecretRef(name="my-tunnel-token", key="token")
    )
    out = _render_cloudflared_yaml(ic)
    assert "my-tunnel-token" in out
    assert "cloudflare.tunnel_token" in out


def test_render_cloudflared_yaml_uses_namespace():
    ic = IngressConnectorSpec(namespace="custom-ns")
    out = _render_cloudflared_yaml(ic)
    assert "name: custom-ns" in out


def test_render_cloudflared_yaml_includes_helmrepository():
    ic = IngressConnectorSpec()
    out = _render_cloudflared_yaml(ic)
    assert "HelmRepository" in out
    assert "cloudflare.github.io/helm-charts" in out


def test_render_cloudflared_apps_kustomization_references_yaml():
    out = _render_cloudflared_apps_kustomization()
    assert "cloudflared.yaml" in out


def test_render_cloudflared_flux_kustomization_references_cluster():
    out = _render_cloudflared_flux_kustomization("my-cluster")
    assert "my-cluster-apps" in out
    assert "gitops/gitops-apps/cloudflared" in out
    assert "sops-age" in out


# ---------------------------------------------------------------------------
# wire_ingress_connector
# ---------------------------------------------------------------------------

async def test_wire_ingress_connector_raises_if_cluster_not_found():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(side_effect=FileNotFoundError("not found"))
    import pytest
    with pytest.raises(FileNotFoundError):
        await svc.wire_ingress_connector("missing")


async def test_wire_ingress_connector_raises_if_no_connector():
    import yaml as _yaml
    raw = _yaml.dump({
        "cluster": {"name": "test-cluster"},
        "network": {"ip_ranges": ["10.0.0.0/24"]},
        "vip": "10.0.0.1",
        "sops_secret_ref": "sops-key",
        "dimensions": {"control_plane_count": 1, "worker_count": 1,
                       "cpu_per_node": 4, "memory_gb_per_node": 8, "boot_volume_gb": 50},
    })
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=raw)
    import pytest
    with pytest.raises(ValueError, match="no ingress_connector"):
        await svc.wire_ingress_connector("test-cluster")


async def test_wire_ingress_connector_opens_two_prs():
    import yaml as _yaml
    from unittest.mock import patch, AsyncMock as AM

    ic_data = {
        "enabled": True,
        "type": "cloudflare-tunnel",
        "tunnel_id": "abc-123",
        "replicas": 2,
        "namespace": "cloudflared",
        "token_secret_ref": {"name": "cloudflare-tunnel-token", "key": "token"},
    }
    raw = _yaml.dump({
        "cluster": {"name": "test-cluster"},
        "network": {"ip_ranges": ["10.0.0.0/24"]},
        "vip": "10.0.0.1",
        "sops_secret_ref": "sops-key",
        "dimensions": {"control_plane_count": 1, "worker_count": 1,
                       "cpu_per_node": 4, "memory_gb_per_node": 8, "boot_volume_gb": 50},
        "ingress_connector": ic_data,
    })

    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=raw)

    mock_git_apps = AsyncMock()
    mock_git_apps.read_file = AsyncMock(return_value="existing: content\n")
    mock_git_infra = AsyncMock()
    mock_git_infra.read_file = AsyncMock(return_value="existing: content\n")
    mock_gh_apps = AsyncMock()
    mock_gh_apps.create_pr = AsyncMock(return_value="https://github.com/test/apps/pull/1")
    mock_gh_infra = AsyncMock()
    mock_gh_infra.create_pr = AsyncMock(return_value="https://github.com/test/infra/pull/2")

    with (
        patch("gitopsgui.services.cluster_service.repo_router.git_for_apps", return_value=mock_git_apps),
        patch("gitopsgui.services.cluster_service.repo_router.git_for_infra", return_value=mock_git_infra),
        patch("gitopsgui.services.cluster_service.repo_router.github_for_apps", return_value=mock_gh_apps),
        patch("gitopsgui.services.cluster_service.repo_router.github_for_infra", return_value=mock_gh_infra),
    ):
        result = await svc.wire_ingress_connector("test-cluster")

    assert result.apps_pr_url == "https://github.com/test/apps/pull/1"
    assert result.infra_pr_url == "https://github.com/test/infra/pull/2"
    assert result.name == "test-cluster"
    assert mock_git_apps.write_file.call_count == 2  # cloudflared.yaml + kustomization.yaml
    assert mock_gh_apps.create_pr.call_count == 1
    assert mock_gh_infra.create_pr.call_count == 1


# ---------------------------------------------------------------------------
# ClusterService.wire_storage_classes
# ---------------------------------------------------------------------------

def _storage_raw_yaml(caps: dict) -> str:
    import yaml as _yaml
    return _yaml.dump({
        "cluster": {"name": "test-cluster"},
        "network": {"ip_ranges": ["10.0.0.0/24"]},
        "vip": "10.0.0.1",
        "sops_secret_ref": "sops-key",
        "dimensions": {"control_plane_count": 1, "worker_count": 2,
                       "cpu_per_node": 4, "memory_gb_per_node": 8, "boot_volume_gb": 50},
        "platform": {
            "name": "venus", "type": "proxmox", "endpoint": "https://192.168.4.50:8006",
            "nodes": ["venus"], "credentials_ref": "capmox",
            "bridge": "vmbr0", "capabilities": caps,
        },
    })


async def test_wire_storage_classes_raises_if_cluster_not_found():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(side_effect=FileNotFoundError("not found"))
    import pytest
    with pytest.raises(FileNotFoundError):
        await svc.wire_storage_classes("missing")


async def test_wire_storage_classes_raises_if_no_backends():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=_storage_raw_yaml(
        {"nfs": False, "iscsi": False, "s3": False}
    ))
    import pytest
    with pytest.raises(ValueError, match="no NFS or iSCSI"):
        await svc.wire_storage_classes("test-cluster")


async def test_wire_storage_classes_raises_if_nfs_server_missing():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=_storage_raw_yaml(
        {"nfs": True, "nfs_server": None, "iscsi": False}
    ))
    import pytest
    with pytest.raises(ValueError, match="nfs_server is required"):
        await svc.wire_storage_classes("test-cluster")


async def test_wire_storage_classes_nfs_opens_infra_pr():
    from unittest.mock import patch, AsyncMock as AM
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=_storage_raw_yaml(
        {"nfs": True, "nfs_server": "192.168.4.51", "iscsi": False}
    ))
    mock_git_infra = AsyncMock()
    mock_git_infra.read_file = AsyncMock(return_value="existing: infra\n")
    mock_gh_infra = AsyncMock()
    mock_gh_infra.create_pr = AsyncMock(return_value="https://github.com/test/infra/pull/5")

    with (
        patch("gitopsgui.services.cluster_service.repo_router.git_for_infra", return_value=mock_git_infra),
        patch("gitopsgui.services.cluster_service.repo_router.github_for_infra", return_value=mock_gh_infra),
    ):
        result = await svc.wire_storage_classes("test-cluster")

    assert result.infra_pr_url == "https://github.com/test/infra/pull/5"
    assert result.backends == ["nfs"]
    assert result.name == "test-cluster"
    # nfs manifest + kustomization.yaml + infrastructure.yaml append = 3 writes
    assert mock_git_infra.write_file.call_count == 3


async def test_wire_storage_classes_iscsi_and_nfs_writes_both_manifests():
    from unittest.mock import patch, AsyncMock as AM
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=_storage_raw_yaml({
        "nfs": True, "nfs_server": "192.168.4.51",
        "iscsi": True, "iscsi_server": "192.168.4.51",
    }))
    mock_git_infra = AsyncMock()
    mock_git_infra.read_file = AsyncMock(return_value="existing: infra\n")
    mock_gh_infra = AsyncMock()
    mock_gh_infra.create_pr = AsyncMock(return_value="https://github.com/test/infra/pull/6")

    with (
        patch("gitopsgui.services.cluster_service.repo_router.git_for_infra", return_value=mock_git_infra),
        patch("gitopsgui.services.cluster_service.repo_router.github_for_infra", return_value=mock_gh_infra),
    ):
        result = await svc.wire_storage_classes("test-cluster")

    assert set(result.backends) == {"nfs", "iscsi"}
    # nfs manifest + iscsi manifest + kustomization.yaml + infrastructure.yaml = 4 writes
    assert mock_git_infra.write_file.call_count == 4


def test_render_democratic_csi_nfs_yaml_contains_server():
    from gitopsgui.services.cluster_service import _render_democratic_csi_nfs_yaml
    out = _render_democratic_csi_nfs_yaml("192.168.4.51")
    assert "192.168.4.51" in out
    assert "nfs-saturn" in out
    assert "zfs-generic-nfs" in out
    assert "HelmRelease" in out


def test_render_democratic_csi_iscsi_yaml_contains_server():
    from gitopsgui.services.cluster_service import _render_democratic_csi_iscsi_yaml
    out = _render_democratic_csi_iscsi_yaml("192.168.4.51")
    assert "192.168.4.51" in out
    assert "iscsi-saturn" in out
    assert "zfs-generic-iscsi" in out
    assert "HelmRelease" in out


def test_render_storage_classes_kustomization_lists_backends():
    from gitopsgui.services.cluster_service import _render_storage_classes_kustomization
    out = _render_storage_classes_kustomization(["nfs", "iscsi"])
    assert "democratic-csi-nfs.yaml" in out
    assert "democratic-csi-iscsi.yaml" in out


def test_render_storage_classes_flux_kustomization():
    from gitopsgui.services.cluster_service import _render_storage_classes_flux_kustomization
    out = _render_storage_classes_flux_kustomization("mycluster")
    assert "name: storage-classes" in out
    assert "00-prerequisites" in out


# ---------------------------------------------------------------------------
# ClusterService.bootstrap_cluster — CC-053b
# ---------------------------------------------------------------------------

async def test_bootstrap_cluster_raises_if_cluster_not_found():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(side_effect=FileNotFoundError("not found"))
    from gitopsgui.models.deploy_key import ClusterBootstrapRequest
    with pytest.raises(FileNotFoundError):
        await svc.bootstrap_cluster("missing", ClusterBootstrapRequest())


async def test_bootstrap_cluster_returns_response():
    import yaml as _yaml
    from unittest.mock import patch, AsyncMock as AM
    from gitopsgui.models.deploy_key import ClusterBootstrapRequest
    from gitopsgui.models.sops import SOPSBootstrapResponse
    from gitopsgui.models.deploy_key import GitAccessResponse

    raw = _yaml.dump({
        "cluster": {"name": "test-cluster"},
        "network": {"ip_ranges": ["10.0.0.0/24"]},
        "vip": "10.0.0.1",
        "sops_secret_ref": "sops-key",
        "dimensions": {"control_plane_count": 1, "worker_count": 1,
                       "cpu_per_node": 4, "memory_gb_per_node": 8, "boot_volume_gb": 50},
    })

    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=raw)

    mock_sops_result = SOPSBootstrapResponse(
        cluster_name="test-cluster",
        sops_public_key="age1testpub",
        encrypted_key_path="sops-keys/test-cluster.agekey.enc",
        secret_created=False,
        sops_yaml_committed=True,
        mgmt_pr_url="https://github.com/org/management-infra/pull/5",
    )
    mock_infra_result = GitAccessResponse(
        repo_name="test-cluster-infra",
        github_key_id=11,
        secret_name="flux-test-cluster-infra-key",
        gitrepository_created=False,
    )
    mock_apps_result = GitAccessResponse(
        repo_name="test-cluster-apps",
        github_key_id=22,
        secret_name="flux-test-cluster-apps-key",
        gitrepository_created=False,
    )

    mock_sops_svc = AsyncMock()
    mock_sops_svc.sops_bootstrap = AsyncMock(return_value=mock_sops_result)
    mock_dks = AsyncMock()
    mock_dks.configure_repository_access = AsyncMock(
        side_effect=[mock_infra_result, mock_apps_result]
    )

    with patch("gitopsgui.services.deploy_key_service.SKIP_K8S", True):
        result = await svc.bootstrap_cluster(
            "test-cluster",
            ClusterBootstrapRequest(),
            _sops_svc=mock_sops_svc,
            _deploy_key_svc=mock_dks,
        )

    assert result.cluster_name == "test-cluster"
    assert result.sops_public_key == "age1testpub"
    assert result.sops_mgmt_pr_url == "https://github.com/org/management-infra/pull/5"
    assert result.infra_key_id == 11
    assert result.apps_key_id == 22
    assert result.secrets_created is False
    assert mock_dks.configure_repository_access.call_count == 2


# ---------------------------------------------------------------------------
# T-020: Change classification + rolling update semantics
# ---------------------------------------------------------------------------

_SPEC_BASE = ClusterSpec(
    name="test-cluster",
    platform=_PLATFORM,
    vip="192.168.1.100",
    ip_range="192.168.1.101-192.168.1.107",
    dimensions=ClusterDimensions(
        control_plane_count=1, worker_count=2,
        cpu_per_node=4, memory_gb_per_node=16, boot_volume_gb=50,
    ),
    managed_gitops=False,
    gitops_repo_url="https://github.com/test/repo",
    sops_secret_ref="sops-key",
    kubernetes_version="v1.34.2",
)


def test_classify_mutable_change():
    new = _SPEC_BASE.model_copy(update={"extra_manifests": ["http://host/cilium.yaml"]})
    result = classify_cluster_changes(_SPEC_BASE, new)
    assert result.category == ChangeCategory.MUTABLE
    assert result.machine_template_hash is None


def test_classify_rolling_kubernetes_version():
    new = _SPEC_BASE.model_copy(update={"kubernetes_version": "v1.35.0"})
    result = classify_cluster_changes(_SPEC_BASE, new)
    assert result.category == ChangeCategory.ROLLING
    assert "kubernetes_version" in result.changed_fields
    assert result.machine_template_hash is None


def test_classify_rolling_talos_image():
    new = _SPEC_BASE.model_copy(update={"talos_image": "factory.talos.dev/installer/abc123:v1.9.5"})
    result = classify_cluster_changes(_SPEC_BASE, new)
    assert result.category == ChangeCategory.ROLLING
    assert "talos_image" in result.changed_fields


def test_classify_immutable_cpu_change():
    new_dims = ClusterDimensions(control_plane_count=1, worker_count=2, cpu_per_node=8, memory_gb_per_node=16, boot_volume_gb=50)
    new = _SPEC_BASE.model_copy(update={"dimensions": new_dims})
    result = classify_cluster_changes(_SPEC_BASE, new)
    assert result.category == ChangeCategory.IMMUTABLE_TEMPLATE
    assert "cpu_per_node" in result.changed_fields
    assert result.machine_template_hash is not None
    assert len(result.machine_template_hash) == 8


def test_classify_immutable_memory_change():
    new_dims = ClusterDimensions(control_plane_count=1, worker_count=2, cpu_per_node=4, memory_gb_per_node=32, boot_volume_gb=50)
    new = _SPEC_BASE.model_copy(update={"dimensions": new_dims})
    result = classify_cluster_changes(_SPEC_BASE, new)
    assert result.category == ChangeCategory.IMMUTABLE_TEMPLATE
    assert result.machine_template_hash is not None


def test_classify_prohibited_name_change():
    new = _SPEC_BASE.model_copy(update={"name": "renamed-cluster"})
    result = classify_cluster_changes(_SPEC_BASE, new)
    assert result.category == ChangeCategory.PROHIBITED
    assert "name" in result.changed_fields
    assert result.machine_template_hash is None


def test_classify_prohibited_ip_range_change():
    new = _SPEC_BASE.model_copy(update={"ip_range": "192.168.2.101-192.168.2.107"})
    result = classify_cluster_changes(_SPEC_BASE, new)
    assert result.category == ChangeCategory.PROHIBITED
    assert "ip_range" in result.changed_fields


def test_dims_hash_stable():
    h1 = _dims_hash(_SPEC_BASE)
    h2 = _dims_hash(_SPEC_BASE)
    assert h1 == h2
    assert len(h1) == 8


def test_dims_hash_changes_with_cpu():
    new_dims = ClusterDimensions(control_plane_count=1, worker_count=2, cpu_per_node=8, memory_gb_per_node=16, boot_volume_gb=50)
    new = _SPEC_BASE.model_copy(update={"dimensions": new_dims})
    assert _dims_hash(_SPEC_BASE) != _dims_hash(new)


def test_render_values_includes_kubernetes_version():
    spec = _SPEC_BASE.model_copy(update={"kubernetes_version": "v1.34.2"})
    rendered = _render_values(spec)
    assert "kubernetes_version: v1.34.2" in rendered


def test_render_values_includes_talos_image():
    spec = _SPEC_BASE.model_copy(update={"talos_image": "factory.talos.dev/nocloud-installer/abc:v1.9.5"})
    rendered = _render_values(spec)
    assert "image: factory.talos.dev/nocloud-installer/abc:v1.9.5" in rendered


def test_render_values_machine_template_suffix_on_cat1():
    rendered = _render_values(_SPEC_BASE, machine_template_hash="abc12345")
    assert "machine_template_suffix: controlplane-abc12345" in rendered
    assert "machine_template_suffix: worker-abc12345" in rendered


def test_render_values_no_machine_template_suffix_by_default():
    rendered = _render_values(_SPEC_BASE)
    assert "machine_template_suffix" not in rendered


def test_render_values_controlplane_dimensions():
    cp_dims = ClusterDimensions(control_plane_count=3, worker_count=0, cpu_per_node=4, memory_gb_per_node=8, boot_volume_gb=40)
    spec = _SPEC_BASE.model_copy(update={"controlplane_dimensions": cp_dims})
    rendered = _render_values(spec)
    assert "controlplane_dimensions" in rendered


def test_classify_controlplane_dimensions_immutable():
    cp_dims = ClusterDimensions(control_plane_count=3, worker_count=0, cpu_per_node=8, memory_gb_per_node=32, boot_volume_gb=40)
    new = _SPEC_BASE.model_copy(update={"controlplane_dimensions": cp_dims})
    result = classify_cluster_changes(_SPEC_BASE, new)
    assert result.category == ChangeCategory.IMMUTABLE_TEMPLATE


@pytest.mark.asyncio
async def test_update_cluster_cat4_raises_422():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._gh = AsyncMock()

    existing_response = MagicMock()
    existing_response.spec = _SPEC_BASE
    svc.get_cluster = AsyncMock(return_value=existing_response)

    new = _SPEC_BASE.model_copy(update={"name": "renamed"})

    from fastapi import HTTPException
    with pytest.raises(HTTPException) as exc:
        await svc.update_cluster("test-cluster", new)
    assert exc.value.status_code == 422
    assert "name" in exc.value.detail


@pytest.mark.asyncio
async def test_update_cluster_cat1_includes_hash_in_pr():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._gh = AsyncMock(return_value="https://github.com/test/pr/1")

    existing_response = MagicMock()
    existing_response.spec = _SPEC_BASE
    svc.get_cluster = AsyncMock(return_value=existing_response)

    new_dims = ClusterDimensions(control_plane_count=1, worker_count=2, cpu_per_node=8, memory_gb_per_node=16, boot_volume_gb=50)
    new = _SPEC_BASE.model_copy(update={"dimensions": new_dims})

    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/pr/1")
    await svc.update_cluster("test-cluster", new)

    written_values = svc._git.write_file.call_args[0][1]
    assert "machine_template_suffix" in written_values

    pr_kwargs = svc._gh.create_pr.call_args[1]
    assert "Cat 1" in pr_kwargs["title"]
    assert "Cat 1" in pr_kwargs["body"]


@pytest.mark.asyncio
async def test_update_cluster_cat3_warns_in_pr():
    svc = ClusterService()
    svc._git = AsyncMock()

    existing_response = MagicMock()
    existing_response.spec = _SPEC_BASE
    svc.get_cluster = AsyncMock(return_value=existing_response)

    new = _SPEC_BASE.model_copy(update={"kubernetes_version": "v1.35.0"})
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/pr/1")
    await svc.update_cluster("test-cluster", new)

    pr_kwargs = svc._gh.create_pr.call_args[1]
    assert "Cat 3" in pr_kwargs["title"]
    assert "Rolling" in pr_kwargs["body"] or "rolling" in pr_kwargs["body"]


# ---------------------------------------------------------------------------
# ClusterService.wire_gateway
# ---------------------------------------------------------------------------

def _gateway_raw_yaml(hostname=None, internal_hosts=None):
    import yaml as _yaml
    return _yaml.dump({
        "cluster": {"name": "test-cluster"},
        "network": {"ip_ranges": ["10.0.0.0/24"]},
        "vip": "10.0.0.1",
        "sops_secret_ref": "sops-key",
        "dimensions": {"control_plane_count": 1, "worker_count": 1,
                       "cpu_per_node": 4, "memory_gb_per_node": 8, "boot_volume_gb": 50},
        **({"hostname": hostname} if hostname else {}),
        **({"internal_hosts": internal_hosts} if internal_hosts else {}),
    })


async def test_wire_gateway_raises_if_cluster_not_found():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(side_effect=FileNotFoundError("not found"))
    import pytest
    with pytest.raises(FileNotFoundError):
        await svc.wire_gateway("missing")


async def test_wire_gateway_raises_if_no_hosts():
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=_gateway_raw_yaml())
    import pytest
    with pytest.raises(ValueError, match="no hostname or internal_hosts"):
        await svc.wire_gateway("test-cluster")


async def test_wire_gateway_public_only_opens_infra_pr():
    from unittest.mock import patch
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=_gateway_raw_yaml(
        hostname=["ollama.podzone.cloud", "qdrant.podzone.cloud"]
    ))
    mock_git_infra = AsyncMock()
    mock_git_infra.read_file = AsyncMock(return_value="existing: infra\n")
    mock_gh_infra = AsyncMock()
    mock_gh_infra.create_pr = AsyncMock(return_value="https://github.com/test/infra/pull/7")

    with (
        patch("gitopsgui.services.cluster_service.repo_router.git_for_infra", return_value=mock_git_infra),
        patch("gitopsgui.services.cluster_service.repo_router.github_for_infra", return_value=mock_gh_infra),
    ):
        result = await svc.wire_gateway("test-cluster")

    assert result.infra_pr_url == "https://github.com/test/infra/pull/7"
    assert result.public_hosts == ["ollama.podzone.cloud", "qdrant.podzone.cloud"]
    assert result.internal_hosts == []
    # gateway.yaml + kustomization.yaml + infrastructure.yaml = 3 writes
    assert mock_git_infra.write_file.call_count == 3


async def test_wire_gateway_internal_hosts_writes_cert_resources():
    from unittest.mock import patch
    from gitopsgui.services.cluster_service import _render_gateway_yaml
    out = _render_gateway_yaml([], ["storage.internal.podzone.net"])
    assert "lets-encrypt-dns01" in out
    assert "Certificate" in out
    assert "*.internal.podzone.net" in out
    assert "HTTPS" in out


def test_render_gateway_yaml_http_listener():
    from gitopsgui.services.cluster_service import _render_gateway_yaml
    out = _render_gateway_yaml(["ollama.podzone.cloud"], [])
    assert "ollama-podzone-cloud-http" in out
    assert "port: 80" in out
    assert "HTTP" in out
    assert "ClusterIssuer" not in out
    assert "Certificate" not in out


def test_render_gateway_yaml_https_listener():
    from gitopsgui.services.cluster_service import _render_gateway_yaml
    out = _render_gateway_yaml([], ["storage.internal.podzone.net"])
    assert "storage-internal-podzone-net-https" in out
    assert "port: 443" in out
    assert "internal-wildcard-tls" in out


def test_render_gateway_flux_kustomization():
    from gitopsgui.services.cluster_service import _render_gateway_flux_kustomization
    out = _render_gateway_flux_kustomization("mycluster")
    assert "name: gateway" in out
    assert "00-manifests" in out


# ---------------------------------------------------------------------------
# StorageSpec model and rendering (T-025)
# ---------------------------------------------------------------------------

def test_storage_omitted_when_none():
    """When storage is None, no storage: key in rendered values."""
    out = _render_values(_SPEC)
    assert "storage:" not in out


def test_storage_linstor_disabled_rendered():
    """storage.internal_linstor=False renders storage block."""
    from gitopsgui.models.cluster import StorageSpec
    spec = _SPEC.model_copy(update={"storage": StorageSpec(internal_linstor=False)})
    out = _render_values(spec)
    assert "storage:" in out
    assert "internal_linstor: false" in out


def test_storage_linstor_with_disk_rendered():
    """storage.internal_linstor=True with linstor_disk_gb renders full storage block."""
    from gitopsgui.models.cluster import StorageSpec
    spec = _SPEC.model_copy(update={"storage": StorageSpec(internal_linstor=True, linstor_disk_gb=50)})
    out = _render_values(spec)
    assert "storage:" in out
    assert "internal_linstor: true" in out
    assert "linstor_disk_gb: 50" in out


def test_storage_linstor_disk_omitted_when_none():
    """When storage.linstor_disk_gb is None, storage block has no linstor_disk_gb key."""
    import yaml
    from gitopsgui.models.cluster import StorageSpec
    spec = _SPEC.model_copy(update={"storage": StorageSpec(internal_linstor=False)})
    parsed = yaml.safe_load(_render_values(spec))
    assert "linstor_disk_gb" not in parsed.get("storage", {})


def test_storage_emptydir_adds_to_boot_volume():
    """emptydir_gb is added to worker boot_volume_size in rendered values."""
    import yaml
    from gitopsgui.models.cluster import StorageSpec
    spec = _SPEC.model_copy(update={"storage": StorageSpec(emptydir_gb=30)})
    parsed = yaml.safe_load(_render_values(spec))
    assert parsed["worker"]["boot_volume_size"] == spec.dimensions.boot_volume_gb + 30


def test_classify_storage_linstor_change_is_cat1():
    """Changing storage.internal_linstor is Cat 1 (machine template change)."""
    from gitopsgui.models.cluster import StorageSpec
    existing = _SPEC_BASE
    new = _SPEC_BASE.model_copy(update={"storage": StorageSpec(internal_linstor=True)})
    result = classify_cluster_changes(existing, new)
    assert result.category == ChangeCategory.IMMUTABLE_TEMPLATE
    assert "storage.internal_linstor" in result.changed_fields


def test_classify_storage_linstor_disk_change_is_cat1():
    """Changing storage.linstor_disk_gb is Cat 1 (machine template change)."""
    from gitopsgui.models.cluster import StorageSpec
    existing = _SPEC_BASE.model_copy(update={"storage": StorageSpec(internal_linstor=True, linstor_disk_gb=20)})
    new = _SPEC_BASE.model_copy(update={"storage": StorageSpec(internal_linstor=True, linstor_disk_gb=50)})
    result = classify_cluster_changes(existing, new)
    assert result.category == ChangeCategory.IMMUTABLE_TEMPLATE
    assert "storage.linstor_disk_gb" in result.changed_fields


def test_classify_storage_emptydir_change_is_cat1():
    """Changing storage.emptydir_gb is Cat 1 (boot disk resize)."""
    from gitopsgui.models.cluster import StorageSpec
    existing = _SPEC_BASE.model_copy(update={"storage": StorageSpec(emptydir_gb=0)})
    new = _SPEC_BASE.model_copy(update={"storage": StorageSpec(emptydir_gb=30)})
    result = classify_cluster_changes(existing, new)
    assert result.category == ChangeCategory.IMMUTABLE_TEMPLATE
    assert "storage.emptydir_gb" in result.changed_fields


def test_classify_storage_unchanged_is_cat2():
    """Same storage spec is Cat 2 (no template change)."""
    from gitopsgui.models.cluster import StorageSpec
    existing = _SPEC_BASE.model_copy(update={"storage": StorageSpec(internal_linstor=True, linstor_disk_gb=20)})
    new = _SPEC_BASE.model_copy(update={"storage": StorageSpec(internal_linstor=True, linstor_disk_gb=20)})
    result = classify_cluster_changes(existing, new)
    assert result.category == ChangeCategory.MUTABLE


def test_dims_hash_differs_for_storage_changes():
    """_dims_hash produces different values when storage fields change."""
    from gitopsgui.models.cluster import StorageSpec
    spec_with = _SPEC.model_copy(update={"storage": StorageSpec(internal_linstor=True, linstor_disk_gb=20)})
    spec_without = _SPEC.model_copy(update={"storage": StorageSpec(internal_linstor=False)})
    assert _dims_hash(spec_with) != _dims_hash(spec_without)


def test_dims_hash_differs_for_emptydir():
    """_dims_hash produces different values when emptydir_gb changes."""
    from gitopsgui.models.cluster import StorageSpec
    spec_with = _SPEC.model_copy(update={"storage": StorageSpec(emptydir_gb=30)})
    spec_without = _SPEC.model_copy(update={"storage": StorageSpec(emptydir_gb=0)})
    assert _dims_hash(spec_with) != _dims_hash(spec_without)


def test_musings_cluster_storage_disabled():
    """musings test cluster: single node, no linstor, no emptydir headroom."""
    from gitopsgui.models.cluster import StorageSpec
    musings = ClusterSpec(
        name="musings",
        vip="192.168.4.230",
        ip_range="192.168.4.231-192.168.4.233",
        dimensions=ClusterDimensions(
            control_plane_count=1, worker_count=0,
            cpu_per_node=2, memory_gb_per_node=4, boot_volume_gb=10,
        ),
        allow_scheduling_on_control_planes=True,
        storage=StorageSpec(internal_linstor=False),
        sops_secret_ref="sops-age",
    )
    import yaml
    parsed = yaml.safe_load(_render_values(musings))
    assert parsed["storage"]["internal_linstor"] is False
    assert "linstor_disk_gb" not in parsed["storage"]


# ---------------------------------------------------------------------------
# CC-166 — ClusterChartSpec roundtrip
# ---------------------------------------------------------------------------

def test_render_values_cluster_chart_written():
    """cluster_chart section is written into values when set on ClusterSpec."""
    spec = _SPEC.model_copy(update={
        "cluster_chart": ClusterChartSpec(
            id="a1b2c3d4-0000-0000-0000-000000000001",
            version="0.1.39",
            type="proxmox-talos",
        ),
    })
    import yaml
    parsed = yaml.safe_load(_render_values(spec))
    assert "cluster_chart" in parsed
    assert parsed["cluster_chart"]["id"] == "a1b2c3d4-0000-0000-0000-000000000001"
    assert parsed["cluster_chart"]["version"] == "0.1.39"
    assert parsed["cluster_chart"]["type"] == "proxmox-talos"


def test_render_values_cluster_chart_omitted_when_none():
    """cluster_chart section is absent when ClusterSpec.cluster_chart is None."""
    import yaml
    parsed = yaml.safe_load(_render_values(_SPEC))
    assert "cluster_chart" not in parsed


async def test_get_cluster_roundtrips_cluster_chart():
    """ClusterSpec.cluster_chart survives a _render_values → get_cluster roundtrip."""
    original = _SPEC.model_copy(update={
        "cluster_chart": ClusterChartSpec(
            id="a1b2c3d4-0000-0000-0000-000000000002",
            version="0.1.39",
            type="proxmox-talos",
        ),
    })
    rendered = _render_values(original)

    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=rendered)

    result = await svc.get_cluster("test-cluster")
    assert result is not None
    assert result.spec.cluster_chart is not None
    assert result.spec.cluster_chart.id == "a1b2c3d4-0000-0000-0000-000000000002"
    assert result.spec.cluster_chart.version == "0.1.39"
    assert result.spec.cluster_chart.type == "proxmox-talos"


# ---------------------------------------------------------------------------
# CC-177: ClusterSpec gap closure — cni, machine_install_disk, talos_version, cert_sans
# ---------------------------------------------------------------------------

def test_render_values_cni_written_when_set():
    """_render_values writes top-level 'cni' key when spec.cni is set."""
    import yaml
    spec = _SPEC.model_copy(update={"cni": "cilium"})
    parsed = yaml.safe_load(_render_values(spec))
    assert parsed["cni"] == "cilium"


def test_render_values_cni_written_when_empty_string():
    """_render_values writes cni: '' when spec.cni is set to empty string (explicit opt-out)."""
    import yaml
    spec = _SPEC.model_copy(update={"cni": ""})
    parsed = yaml.safe_load(_render_values(spec))
    assert "cni" in parsed
    assert parsed["cni"] == ""


def test_render_values_cni_omitted_when_none():
    """_render_values does not write 'cni' key when spec.cni is None."""
    import yaml
    parsed = yaml.safe_load(_render_values(_SPEC))
    assert "cni" not in parsed


def test_render_values_machine_install_disk_written():
    """_render_values writes machine.installDisk when spec.machine_install_disk is set."""
    import yaml
    spec = _SPEC.model_copy(update={"machine_install_disk": "/dev/sda"})
    parsed = yaml.safe_load(_render_values(spec))
    assert parsed["machine"]["installDisk"] == "/dev/sda"


def test_render_values_machine_install_disk_omitted_when_none():
    """_render_values does not write machine.installDisk when spec.machine_install_disk is None."""
    import yaml
    parsed = yaml.safe_load(_render_values(_SPEC))
    assert "machine" not in parsed


def test_render_values_talos_version_written_to_cluster_block():
    """_render_values writes cluster.talos_version when spec.talos_version is set."""
    import yaml
    spec = _SPEC.model_copy(update={"talos_version": "v1.12"})
    parsed = yaml.safe_load(_render_values(spec))
    assert parsed["cluster"]["talos_version"] == "v1.12"


def test_render_values_talos_version_written_as_roundtrip():
    """_render_values also writes top-level talos_version for roundtrip metadata."""
    import yaml
    spec = _SPEC.model_copy(update={"talos_version": "v1.12"})
    parsed = yaml.safe_load(_render_values(spec))
    assert parsed["talos_version"] == "v1.12"


def test_render_values_talos_version_omitted_when_none():
    """_render_values does not write talos_version when spec.talos_version is None."""
    import yaml
    parsed = yaml.safe_load(_render_values(_SPEC))
    assert "talos_version" not in parsed


def test_render_values_cert_sans_written_to_network_block():
    """_render_values writes network.certSANs when spec.cert_sans is set and non-empty."""
    import yaml
    spec = _SPEC.model_copy(update={"cert_sans": ["192.168.4.190", "k8s.internal.example.com"]})
    parsed = yaml.safe_load(_render_values(spec))
    assert parsed["network"]["certSANs"] == ["192.168.4.190", "k8s.internal.example.com"]


def test_render_values_cert_sans_omitted_when_none():
    """_render_values does not write network.certSANs when spec.cert_sans is None."""
    import yaml
    parsed = yaml.safe_load(_render_values(_SPEC))
    assert "certSANs" not in parsed.get("network", {})


def test_render_values_cert_sans_omitted_when_empty_list():
    """_render_values does not write network.certSANs when spec.cert_sans is an empty list."""
    import yaml
    spec = _SPEC.model_copy(update={"cert_sans": []})
    parsed = yaml.safe_load(_render_values(spec))
    assert "certSANs" not in parsed.get("network", {})


def test_render_values_all_gap_fields_roundtrip():
    """All four CC-177 fields together render and can be re-parsed correctly."""
    import yaml
    spec = _SPEC.model_copy(update={
        "cni": "cilium",
        "machine_install_disk": "/dev/vda",
        "talos_version": "v1.12",
        "cert_sans": ["192.168.4.190"],
    })
    parsed = yaml.safe_load(_render_values(spec))
    assert parsed["cni"] == "cilium"
    assert parsed["machine"]["installDisk"] == "/dev/vda"
    assert parsed["cluster"]["talos_version"] == "v1.12"
    assert parsed["talos_version"] == "v1.12"
    assert parsed["network"]["certSANs"] == ["192.168.4.190"]


def test_classify_cni_change_is_prohibited():
    """Changing cni on a live cluster is Cat 4 (immutable at provision time)."""
    base = _SPEC_BASE.model_copy(update={"cni": ""})
    new = _SPEC_BASE.model_copy(update={"cni": "cilium"})
    result = classify_cluster_changes(base, new)
    assert result.category == ChangeCategory.PROHIBITED
    assert "cni" in result.changed_fields


def test_classify_cert_sans_change_is_prohibited():
    """Changing cert_sans on a live cluster is Cat 4 (baked into TLS cert at bootstrap)."""
    base = _SPEC_BASE.model_copy(update={"cert_sans": ["192.168.4.190"]})
    new = _SPEC_BASE.model_copy(update={"cert_sans": ["192.168.4.191"]})
    result = classify_cluster_changes(base, new)
    assert result.category == ChangeCategory.PROHIBITED
    assert "cert_sans" in result.changed_fields


def test_classify_cert_sans_none_to_set_is_prohibited():
    """Adding cert_sans to a cluster that had none is also Cat 4."""
    base = _SPEC_BASE.model_copy(update={"cert_sans": None})
    new = _SPEC_BASE.model_copy(update={"cert_sans": ["192.168.4.190"]})
    result = classify_cluster_changes(base, new)
    assert result.category == ChangeCategory.PROHIBITED
    assert "cert_sans" in result.changed_fields


# ---------------------------------------------------------------------------
# T-036 (CC-176) — fetch_static_inline_manifests
# ---------------------------------------------------------------------------

_FAKE_MANIFEST_CONTENT = "# fake manifest\napiVersion: v1\nkind: Namespace\n"


def _make_mock_response(text: str, status_code: int = 200):
    """Build a minimal httpx.Response-like mock."""
    mock = MagicMock(spec=httpx.Response)
    mock.status_code = status_code
    mock.text = text
    if status_code >= 400:
        mock.raise_for_status.side_effect = httpx.HTTPStatusError(
            f"HTTP {status_code}",
            request=MagicMock(url="https://example.com/manifest.yaml"),
            response=mock,
        )
    else:
        mock.raise_for_status.return_value = None
    return mock


def test_fetch_static_inline_manifests_returns_all_three():
    """All three manifests returned with correct names and non-empty contents."""
    with patch("gitopsgui.services.cluster_service.httpx.get") as mock_get:
        mock_get.return_value = _make_mock_response(_FAKE_MANIFEST_CONTENT)
        result = fetch_static_inline_manifests()
    assert len(result) == 3
    names = [m["name"] for m in result]
    assert "kubelet-serving-cert-approver" in names
    assert "metrics-server" in names
    assert "gateway-api" in names


def test_fetch_static_inline_manifests_contents_populated():
    """Each returned entry has non-empty 'contents'."""
    with patch("gitopsgui.services.cluster_service.httpx.get") as mock_get:
        mock_get.return_value = _make_mock_response(_FAKE_MANIFEST_CONTENT)
        result = fetch_static_inline_manifests()
    for m in result:
        assert "contents" in m
        assert m["contents"] == _FAKE_MANIFEST_CONTENT


def test_fetch_static_inline_manifests_raises_on_404():
    """HTTP 404 on any manifest raises httpx.HTTPStatusError — halts provisioning."""
    with patch("gitopsgui.services.cluster_service.httpx.get") as mock_get:
        mock_get.return_value = _make_mock_response("", status_code=404)
        with pytest.raises(httpx.HTTPStatusError):
            fetch_static_inline_manifests()


def test_fetch_static_inline_manifests_follows_redirects():
    """httpx.get is called with follow_redirects=True and a timeout."""
    with patch("gitopsgui.services.cluster_service.httpx.get") as mock_get:
        mock_get.return_value = _make_mock_response(_FAKE_MANIFEST_CONTENT)
        fetch_static_inline_manifests()
    for call in mock_get.call_args_list:
        assert call.kwargs.get("follow_redirects") is True
        assert call.kwargs.get("timeout") is not None


def test_gateway_api_version_pin():
    """The gateway-api URL contains the pinned version constant, not 'latest'."""
    gateway_entry = next(m for m in _STATIC_INLINE_MANIFESTS if m["name"] == "gateway-api")
    assert GATEWAY_API_VERSION in gateway_entry["url"]
    assert "latest" not in gateway_entry["url"].lower()


def test_render_values_includes_inline_manifests_when_provided():
    """When inline_manifests passed, 'inlineManifests' key appears in rendered values."""
    manifests = [
        {"name": "kubelet-serving-cert-approver", "contents": "# manifest"},
        {"name": "metrics-server", "contents": "# manifest"},
        {"name": "gateway-api", "contents": "# manifest"},
    ]
    out = _render_values(_SPEC, inline_manifests=manifests)
    parsed = _yaml.safe_load(out)
    assert "inlineManifests" in parsed
    assert len(parsed["inlineManifests"]) == 3
    names = [m["name"] for m in parsed["inlineManifests"]]
    assert "kubelet-serving-cert-approver" in names
    assert "metrics-server" in names
    assert "gateway-api" in names


def test_render_values_no_inline_manifests_when_not_provided():
    """When inline_manifests not passed, 'inlineManifests' key absent from rendered values."""
    out = _render_values(_SPEC)
    parsed = _yaml.safe_load(out)
    assert "inlineManifests" not in parsed


def test_render_values_empty_inline_manifests_written():
    """Passing empty list writes inlineManifests: [] to values (valid cluster-chart value)."""
    out = _render_values(_SPEC, inline_manifests=[])
    parsed = _yaml.safe_load(out)
    assert "inlineManifests" in parsed
    assert parsed["inlineManifests"] == []


# ---------------------------------------------------------------------------
# T-033 (CC-173) — InlineManifest redaction on read
# ---------------------------------------------------------------------------

async def test_get_cluster_does_not_expose_inline_manifest_contents():
    """GET /clusters/{name} must not return inlineManifest contents — only names."""
    import yaml
    values_with_inline = yaml.dump({
        "cluster": {"name": "test-cluster"},
        "network": {"ip_ranges": ["192.168.1.0/24"]},
        "controlplane": {"machine_count": 1},
        "worker": {"machine_count": 1},
        "dimensions": {
            "control_plane_count": 1,
            "worker_count": 1,
            "cpu_per_node": 4,
            "memory_gb_per_node": 8,
            "boot_volume_gb": 50,
        },
        "vip": "192.168.1.100",
        "sops_secret_ref": "sops-key",
        "inlineManifests": [
            {"name": "kubelet-serving-cert-approver", "contents": "---\napiVersion: v1\n# SENSITIVE"},
            {"name": "sops-age", "contents": "---\napiVersion: v1\nstringData:\n  age.agekey: AGE-SECRET-KEY-1..."},
        ],
    })
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=values_with_inline)

    result = await svc.get_cluster("test-cluster")

    assert result is not None
    # Contents must not be exposed — only names surfaced via inline_manifest_names
    assert "AGE-SECRET-KEY" not in str(result)
    assert "SENSITIVE" not in str(result)
    # Names should be surfaced
    assert "kubelet-serving-cert-approver" in result.spec.inline_manifest_names
    assert "sops-age" in result.spec.inline_manifest_names


def test_cluster_spec_inline_manifest_names_defaults_empty():
    """inline_manifest_names defaults to empty list — backward compatible with existing specs."""
    spec = ClusterSpec(
        name="test",
        vip="10.0.0.1",
        ip_range="10.0.0.2-10.0.0.5",
        dimensions=ClusterDimensions(),
        sops_secret_ref="key",
    )
    assert spec.inline_manifest_names == []


# ---------------------------------------------------------------------------
# T-034 (CC-174) — piraeus-operator kustomization
# ---------------------------------------------------------------------------

def test_render_piraeus_kustomization_contains_kustomization_cr():
    """Rendered manifest includes a Flux Kustomization CR."""
    out = _render_piraeus_kustomization("mycluster")
    assert "kind: Kustomization" in out
    assert "name: piraeus-operator" in out


def test_render_piraeus_kustomization_contains_gitrepository_cr():
    """Rendered manifest includes a Flux GitRepository CR."""
    out = _render_piraeus_kustomization("mycluster")
    assert "kind: GitRepository" in out
    assert "piraeusdatastore/piraeus-operator" in out


def test_render_piraeus_kustomization_flux_system_namespace():
    """Both resources are in flux-system namespace."""
    out = _render_piraeus_kustomization("mycluster")
    assert out.count("namespace: flux-system") == 2


async def test_create_cluster_generates_piraeus_when_linstor_true():
    """create_cluster writes piraeus kustomization to {cluster}-infra when storage.internal_linstor=True."""
    spec_with_linstor = _SPEC.model_copy(update={
        "storage": StorageSpec(internal_linstor=True),
    })

    infra_git_mock = AsyncMock()
    infra_git_mock.create_branch = AsyncMock()
    infra_git_mock.write_file = AsyncMock()
    infra_git_mock.commit = AsyncMock(return_value="sha")
    infra_git_mock.push = AsyncMock()

    infra_gh_mock = AsyncMock()
    infra_gh_mock.create_pr = AsyncMock(return_value="https://github.com/test/infra-pr/1")

    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/1")

    with patch("gitopsgui.services.cluster_service.fetch_static_inline_manifests") as mock_fetch, \
         patch("gitopsgui.services.cluster_service.repo_router.git_for_infra", return_value=infra_git_mock), \
         patch("gitopsgui.services.cluster_service.repo_router.github_for_infra", return_value=infra_gh_mock):
        mock_fetch.return_value = []
        result = await svc.create_cluster(spec_with_linstor)

    # piraeus kustomization written to infra repo
    infra_git_mock.write_file.assert_called_once()
    written_path, written_content = infra_git_mock.write_file.call_args.args
    assert _PIRAEUS_INFRA_PATH in written_path
    assert "piraeus-operator.yaml" in written_path
    assert "kind: Kustomization" in written_content
    assert "piraeusdatastore" in written_content

    # PR opened on infra repo
    infra_gh_mock.create_pr.assert_called_once()
    assert result.pr_url is not None


async def test_create_cluster_no_piraeus_when_linstor_false():
    """create_cluster does NOT write piraeus kustomization when storage.internal_linstor=False."""
    spec_no_linstor = _SPEC.model_copy(update={
        "storage": StorageSpec(internal_linstor=False),
    })

    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/1")

    with patch("gitopsgui.services.cluster_service.fetch_static_inline_manifests") as mock_fetch, \
         patch("gitopsgui.services.cluster_service.repo_router") as mock_router:
        mock_fetch.return_value = []
        result = await svc.create_cluster(spec_no_linstor)

    # repo_router infra methods NOT called (no piraeus branch)
    mock_router.git_for_infra.assert_not_called()
    mock_router.github_for_infra.assert_not_called()
    assert result.pr_url is not None


async def test_create_cluster_no_piraeus_when_no_storage():
    """create_cluster does NOT write piraeus kustomization when storage=None."""
    spec_no_storage = _SPEC.model_copy(update={"storage": None})

    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/1")

    with patch("gitopsgui.services.cluster_service.fetch_static_inline_manifests") as mock_fetch, \
         patch("gitopsgui.services.cluster_service.repo_router") as mock_router:
        mock_fetch.return_value = []
        result = await svc.create_cluster(spec_no_storage)

    mock_router.git_for_infra.assert_not_called()
    assert result.pr_url is not None


async def test_create_cluster_static_inline_manifests_in_values():
    """create_cluster writes inlineManifests from fetch_static_inline_manifests into the values file."""
    fake_manifests = [
        {"name": "kubelet-serving-cert-approver", "contents": "# ksa\n"},
        {"name": "metrics-server", "contents": "# ms\n"},
        {"name": "gateway-api", "contents": "# gw\n"},
    ]

    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha")
    svc._git.push = AsyncMock()
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/1")

    with patch("gitopsgui.services.cluster_service.fetch_static_inline_manifests") as mock_fetch, \
         patch("gitopsgui.services.cluster_service.repo_router"):
        mock_fetch.return_value = fake_manifests
        await svc.create_cluster(_SPEC)

    # Find the values file write call
    import yaml
    values_call = next(
        (c for c in svc._git.write_file.call_args_list if "values" in c.args[0]),
        None,
    )
    assert values_call is not None
    written_values = yaml.safe_load(values_call.args[1])
    assert "inlineManifests" in written_values
    names = [m["name"] for m in written_values["inlineManifests"]]
    assert "kubelet-serving-cert-approver" in names
    assert "metrics-server" in names
    assert "gateway-api" in names


# ---------------------------------------------------------------------------
# CC-178: NetworkSpec — _render_values, classify_cluster_changes, get_cluster
# ---------------------------------------------------------------------------

from gitopsgui.models.cluster import NetworkSpec


def _make_spec_with_network(type="flannel", vip="192.168.1.100", ip_range="192.168.1.0/24",
                             cert_sans=None, **kwargs):
    """Build a ClusterSpec with an explicit NetworkSpec (bypasses migration validator)."""
    n = NetworkSpec(
        id="test-net-id",
        type=type,
        vip=vip,
        ip_range=ip_range,
        cert_sans=cert_sans,
        **kwargs,
    )
    return ClusterSpec(
        name="test-cluster",
        network=n,
        dimensions=ClusterDimensions(control_plane_count=1, worker_count=1),
        managed_gitops=False,
        gitops_repo_url="https://github.com/test/repo",
        sops_secret_ref="sops-key",
    )


def test_render_values_network_vip_written():
    """_render_values writes vip from spec.network.vip when network is present."""
    import yaml
    spec = _make_spec_with_network(vip="10.99.0.1")
    parsed = yaml.safe_load(_render_values(spec))
    assert parsed["vip"] == "10.99.0.1"


def test_render_values_network_ip_ranges_written():
    """_render_values writes network.ip_ranges from spec.network.ip_range."""
    import yaml
    spec = _make_spec_with_network(ip_range="10.99.0.0/24")
    parsed = yaml.safe_load(_render_values(spec))
    assert parsed["network"]["ip_ranges"] == ["10.99.0.0/24"]


def test_render_values_network_endpoint_ip_written():
    """_render_values writes network.endpoint_ip from spec.network.vip."""
    import yaml
    spec = _make_spec_with_network(vip="10.99.0.1")
    parsed = yaml.safe_load(_render_values(spec))
    assert parsed["network"]["endpoint_ip"] == "10.99.0.1"


def test_render_values_cni_cilium_when_network_type_cilium():
    """_render_values writes cni='cilium' when spec.network.type='cilium'."""
    import yaml
    spec = _make_spec_with_network(type="cilium")
    parsed = yaml.safe_load(_render_values(spec))
    assert parsed["cni"] == "cilium"


def test_render_values_no_cni_when_network_type_flannel():
    """_render_values does not write cni key when spec.network.type='flannel' and spec.cni is None."""
    import yaml
    spec = _make_spec_with_network(type="flannel")
    parsed = yaml.safe_load(_render_values(spec))
    assert "cni" not in parsed


def test_render_values_proxy_disabled_not_set():
    """_render_values never sets proxy.disabled — derived from cni=='cilium' in cluster-chart."""
    import yaml
    spec = _make_spec_with_network(type="cilium")
    parsed = yaml.safe_load(_render_values(spec))
    assert "proxy" not in parsed


def test_render_values_network_cert_sans_written_when_set():
    """_render_values writes network.certSANs from spec.network.cert_sans."""
    import yaml
    spec = _make_spec_with_network(cert_sans=["192.168.1.100", "k8s.example.com"])
    parsed = yaml.safe_load(_render_values(spec))
    assert parsed["network"]["certSANs"] == ["192.168.1.100", "k8s.example.com"]


def test_render_values_network_cert_sans_omitted_when_none():
    """_render_values does not write certSANs when spec.network.cert_sans is None."""
    import yaml
    spec = _make_spec_with_network(cert_sans=None)
    parsed = yaml.safe_load(_render_values(spec))
    assert "certSANs" not in parsed.get("network", {})


def test_render_values_network_spec_roundtrip_block_written():
    """_render_values writes 'network_spec' roundtrip block for get_cluster reconstruction."""
    import yaml
    spec = _make_spec_with_network(type="cilium", vip="10.0.0.5", ip_range="10.0.0.0/24")
    parsed = yaml.safe_load(_render_values(spec))
    assert "network_spec" in parsed
    assert parsed["network_spec"]["type"] == "cilium"
    assert parsed["network_spec"]["vip"] == "10.0.0.5"
    assert parsed["network_spec"]["ip_range"] == "10.0.0.0/24"


def test_classify_network_type_change_is_prohibited():
    """Changing network.type (CNI) is Cat 4 — cluster reprovisioning required."""
    base = _make_spec_with_network(type="flannel")
    new = _make_spec_with_network(type="cilium")
    result = classify_cluster_changes(base, new)
    assert result.category == ChangeCategory.PROHIBITED
    assert "network.type" in result.changed_fields


def test_classify_network_vip_change_is_prohibited():
    """Changing network.vip is Cat 4."""
    base = _make_spec_with_network(vip="10.0.0.1")
    new = _make_spec_with_network(vip="10.0.0.2")
    result = classify_cluster_changes(base, new)
    assert result.category == ChangeCategory.PROHIBITED
    assert "network.vip" in result.changed_fields


def test_classify_network_ip_range_change_is_prohibited():
    """Changing network.ip_range is Cat 4."""
    base = _make_spec_with_network(ip_range="10.0.0.0/24")
    new = _make_spec_with_network(ip_range="10.0.1.0/24")
    result = classify_cluster_changes(base, new)
    assert result.category == ChangeCategory.PROHIBITED
    assert "network.ip_range" in result.changed_fields


def test_classify_network_cilium_version_change_is_cat1():
    """Changing network.cilium_version is Cat 1 (new InlineManifest → new MachineTemplate)."""
    base = _make_spec_with_network(cilium_version="1.17.4")
    new = _make_spec_with_network(cilium_version="1.18.0")
    result = classify_cluster_changes(base, new)
    assert result.category == ChangeCategory.IMMUTABLE_TEMPLATE
    assert "network.cilium_version" in result.changed_fields


def test_classify_network_capability_flag_change_is_cat1():
    """Changing any Cilium capability flag is Cat 1."""
    base = _make_spec_with_network(hubble_relay=False)
    new = _make_spec_with_network(hubble_relay=True)
    result = classify_cluster_changes(base, new)
    assert result.category == ChangeCategory.IMMUTABLE_TEMPLATE
    assert "network.hubble_relay" in result.changed_fields


def test_classify_network_gateway_api_alpn_change_is_cat1():
    """gateway_api_alpn flag change is Cat 1."""
    base = _make_spec_with_network(gateway_api_alpn=False)
    new = _make_spec_with_network(gateway_api_alpn=True)
    result = classify_cluster_changes(base, new)
    assert result.category == ChangeCategory.IMMUTABLE_TEMPLATE
    assert "network.gateway_api_alpn" in result.changed_fields


@pytest.mark.asyncio
async def test_get_cluster_reconstructs_network_spec_from_roundtrip():
    """get_cluster reads 'network_spec' block from values and reconstructs NetworkSpec."""
    import yaml as _yaml
    raw = _yaml.dump({
        "cluster": {"name": "test-cluster"},
        "network": {"ip_ranges": ["10.0.0.0/24"], "endpoint_ip": "10.0.0.1"},
        "vip": "10.0.0.1",
        "sops_secret_ref": "sops-key",
        "dimensions": {"control_plane_count": 1, "worker_count": 1,
                       "cpu_per_node": 4, "memory_gb_per_node": 8, "boot_volume_gb": 50},
        "network_spec": {
            "id": "fixed-net-id",
            "type": "cilium",
            "vip": "10.0.0.1",
            "ip_range": "10.0.0.0/24",
            "cilium_version": "1.17.4",
            "kube_proxy_replacement": True,
            "ingress_controller": True,
            "ingress_controller_lb_mode": "shared",
            "ingress_controller_default": True,
            "l2_load_balancer": True,
            "l2_lease_duration": "3s",
            "l2_lease_renew_deadline": "1s",
            "l2_lease_retry_period": "200ms",
            "l7_proxy": True,
            "gateway_api": True,
            "gateway_api_alpn": False,
            "gateway_api_app_protocol": False,
            "hubble_relay": False,
            "hubble_ui": False,
        },
    })
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=raw)
    result = await svc.get_cluster("test-cluster")
    assert result is not None
    assert result.spec.network is not None
    assert result.spec.network.id == "fixed-net-id"
    assert result.spec.network.type == "cilium"
    assert result.spec.network.vip == "10.0.0.1"
    assert result.spec.network.ip_range == "10.0.0.0/24"


@pytest.mark.asyncio
async def test_get_cluster_derives_network_from_values_when_no_roundtrip():
    """get_cluster derives NetworkSpec from cluster-chart keys when network_spec is absent."""
    import yaml as _yaml
    raw = _yaml.dump({
        "cluster": {"name": "test-cluster"},
        "network": {"ip_ranges": ["192.168.1.0/24"]},
        "vip": "192.168.1.1",
        "cni": "cilium",
        "sops_secret_ref": "sops-key",
        "dimensions": {"control_plane_count": 1, "worker_count": 1,
                       "cpu_per_node": 4, "memory_gb_per_node": 8, "boot_volume_gb": 50},
    })
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.read_file = AsyncMock(return_value=raw)
    result = await svc.get_cluster("test-cluster")
    assert result is not None
    assert result.spec.network is not None
    assert result.spec.network.vip == "192.168.1.1"
    assert result.spec.network.ip_range == "192.168.1.0/24"
    assert result.spec.network.type == "cilium"


# ---------------------------------------------------------------------------
# T-039 (CC-179) — Cilium InlineManifest generation
# ---------------------------------------------------------------------------

import subprocess as _subprocess


def _make_network(**overrides) -> NetworkSpec:
    """Create a NetworkSpec with cilium type and test defaults."""
    defaults = dict(
        id="test-net-id",
        type="cilium",
        vip="192.168.1.100",
        ip_range="192.168.1.0/24",
    )
    defaults.update(overrides)
    return NetworkSpec(**defaults)


@pytest.fixture
def mock_helm_success(monkeypatch):
    def fake_run(cmd, **kwargs):
        mock_result = MagicMock()
        mock_result.returncode = 0
        mock_result.stdout = "# cilium manifest\napiVersion: apps/v1\nkind: DaemonSet\n"
        mock_result.stderr = ""
        return mock_result
    monkeypatch.setattr("gitopsgui.services.cluster_service.subprocess.run", fake_run)


@pytest.fixture
def mock_helm_failure(monkeypatch):
    def fake_run(cmd, **kwargs):
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stdout = ""
        mock_result.stderr = "Error: chart not found"
        return mock_result
    monkeypatch.setattr("gitopsgui.services.cluster_service.subprocess.run", fake_run)


# --- _build_cilium_helm_args ---

def test_build_cilium_helm_args_includes_gateway_api_when_enabled():
    network = _make_network(gateway_api=True)
    args = _build_cilium_helm_args(network)
    assert "--set" in args
    idx = args.index("gatewayAPI.enabled=true")
    assert args[idx - 1] == "--set"


def test_build_cilium_helm_args_omits_hubble_relay_when_false():
    network = _make_network(hubble_relay=False)
    args = _build_cilium_helm_args(network)
    assert "hubble.relay.enabled=true" not in args


def test_build_cilium_helm_args_includes_hubble_relay_when_true():
    network = _make_network(hubble_relay=True)
    args = _build_cilium_helm_args(network)
    assert "hubble.relay.enabled=true" in args


def test_build_cilium_helm_args_includes_all_defaults():
    """All CILIUM_HELM_DEFAULTS keys must appear as --set args."""
    network = _make_network()
    args = _build_cilium_helm_args(network)
    for key, value in CILIUM_HELM_DEFAULTS.items():
        assert f"{key}={value}" in args


def test_build_cilium_helm_args_includes_kube_proxy_replacement_by_default():
    network = _make_network(kube_proxy_replacement=True)
    args = _build_cilium_helm_args(network)
    assert "kubeProxyReplacement=true" in args


def test_build_cilium_helm_args_omits_kube_proxy_replacement_when_false():
    network = _make_network(kube_proxy_replacement=False)
    args = _build_cilium_helm_args(network)
    assert "kubeProxyReplacement=true" not in args


def test_build_cilium_helm_args_includes_l2_announcements_when_enabled():
    network = _make_network(l2_load_balancer=True)
    args = _build_cilium_helm_args(network)
    assert "l2announcements.enabled=true" in args


def test_build_cilium_helm_args_includes_l7_proxy_when_enabled():
    network = _make_network(l7_proxy=True)
    args = _build_cilium_helm_args(network)
    assert "l7Proxy=true" in args


def test_build_cilium_helm_args_includes_ingress_controller_when_enabled():
    network = _make_network(ingress_controller=True)
    args = _build_cilium_helm_args(network)
    assert "ingressController.enabled=true" in args


def test_build_cilium_helm_args_gateway_api_alpn_when_set():
    network = _make_network(gateway_api=True, gateway_api_alpn=True)
    args = _build_cilium_helm_args(network)
    assert "gatewayAPI.enableAlpn=true" in args


def test_build_cilium_helm_args_no_gateway_api_alpn_when_not_set():
    network = _make_network(gateway_api=True, gateway_api_alpn=False)
    args = _build_cilium_helm_args(network)
    assert "gatewayAPI.enableAlpn=true" not in args


# --- generate_cilium_manifest ---

def test_generate_cilium_manifest_returns_yaml_string(mock_helm_success):
    network = _make_network()
    result = generate_cilium_manifest(network)
    assert isinstance(result, str)
    assert len(result) > 0
    assert "apiVersion" in result


def test_generate_cilium_manifest_lb_pool_crs_appended_when_set(mock_helm_success):
    network = _make_network(lb_pool_start="192.168.1.200", lb_pool_stop="192.168.1.250")
    result = generate_cilium_manifest(network)
    assert "CiliumLoadBalancerIPPool" in result
    assert "CiliumL2AnnouncementPolicy" in result
    assert "192.168.1.200" in result
    assert "192.168.1.250" in result


def test_generate_cilium_manifest_lb_pool_crs_absent_when_not_set(mock_helm_success):
    network = _make_network(lb_pool_start=None, lb_pool_stop=None)
    result = generate_cilium_manifest(network)
    assert "CiliumLoadBalancerIPPool" not in result
    assert "CiliumL2AnnouncementPolicy" not in result


def test_generate_cilium_manifest_raises_on_helm_failure(mock_helm_failure):
    network = _make_network()
    with pytest.raises(RuntimeError, match="helm template failed"):
        generate_cilium_manifest(network)


# --- integration: cilium manifest in _render_values / create_cluster ---

def test_render_values_includes_inline_manifests_with_cilium():
    """When inline_manifests list contains cilium, it appears in rendered values."""
    cilium_inline = [
        {"name": "kubelet-serving-cert-approver", "contents": "# approver\n"},
        {"name": "metrics-server", "contents": "# metrics\n"},
        {"name": "gateway-api", "contents": "# gateway\n"},
        {"name": "cilium", "contents": "# cilium manifest\n"},
    ]
    spec = _SPEC.model_copy(update={
        "network": _make_network(),
    })
    out = _render_values(spec, inline_manifests=cilium_inline)
    assert "cilium" in out
    assert "inlineManifests" in out


@pytest.mark.asyncio
async def test_create_cluster_includes_cilium_in_inline_manifests(monkeypatch):
    """When network.type="cilium", cilium InlineManifest is appended after static manifests."""
    # Mock fetch_static_inline_manifests
    static_manifests = [
        {"name": "kubelet-serving-cert-approver", "contents": "# approver\n"},
        {"name": "metrics-server", "contents": "# metrics\n"},
        {"name": "gateway-api", "contents": "# gateway\n"},
    ]
    monkeypatch.setattr(
        "gitopsgui.services.cluster_service.fetch_static_inline_manifests",
        lambda: static_manifests,
    )
    # Mock generate_cilium_manifest
    monkeypatch.setattr(
        "gitopsgui.services.cluster_service.generate_cilium_manifest",
        lambda network: "# cilium manifest\napiVersion: apps/v1\nkind: DaemonSet\n",
    )

    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha123")
    svc._git.push = AsyncMock()
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/1")

    spec = _SPEC.model_copy(update={"network": _make_network(type="cilium")})
    await svc.create_cluster(spec)

    # Verify values file was written with cilium in inlineManifests
    written_values = None
    for call in svc._git.write_file.call_args_list:
        path = call.args[0]
        if "values" in path:
            written_values = call.args[1]
            break
    assert written_values is not None
    assert "cilium" in written_values
    assert "inlineManifests" in written_values


@pytest.mark.asyncio
async def test_create_cluster_no_cilium_when_flannel(monkeypatch):
    """When network.type="flannel" (or no network), cilium is NOT in inlineManifests."""
    static_manifests = [
        {"name": "kubelet-serving-cert-approver", "contents": "# approver\n"},
        {"name": "metrics-server", "contents": "# metrics\n"},
        {"name": "gateway-api", "contents": "# gateway\n"},
    ]
    monkeypatch.setattr(
        "gitopsgui.services.cluster_service.fetch_static_inline_manifests",
        lambda: static_manifests,
    )

    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha123")
    svc._git.push = AsyncMock()
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/1")

    # Use _SPEC which has no network set (will migrate to flannel via model_validator)
    await svc.create_cluster(_SPEC)

    written_values = None
    for call in svc._git.write_file.call_args_list:
        path = call.args[0]
        if "values" in path:
            written_values = call.args[1]
            break
    assert written_values is not None
    # cilium inline manifest name should NOT be in values when flannel
    import yaml as _yaml_check
    data = _yaml_check.safe_load(written_values)
    inline_names = [m["name"] for m in data.get("inlineManifests", [])]
    assert "cilium" not in inline_names


# ---------------------------------------------------------------------------
# T-035 (CC-175) — fluxinstance + sops-age InlineManifests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=False)
def skip_k8s(monkeypatch):
    """Set GITOPS_SKIP_K8S=1 to prevent real K8s calls in all T-035 tests."""
    monkeypatch.setenv("GITOPS_SKIP_K8S", "1")
    # Also patch the module-level flag so already-imported code sees it
    monkeypatch.setattr("gitopsgui.services.cluster_service._SKIP_K8S", True)


# --- generate_fluxinstance_manifest ---

def test_generate_fluxinstance_manifest_piraeus_storage_class(skip_k8s):
    """storage.internal_linstor=True → storageClass piraeus-datastore."""
    spec = _SPEC.model_copy(update={
        "storage": StorageSpec(internal_linstor=True),
        "gitops_repo_url": "https://github.com/test/test-cluster-infra",
    })
    out = generate_fluxinstance_manifest(spec)
    assert "piraeus-datastore" in out
    assert "FluxInstance" in out


def test_generate_fluxinstance_manifest_standard_storage_class(skip_k8s):
    """storage.internal_linstor=False → storageClass standard."""
    spec = _SPEC.model_copy(update={
        "storage": StorageSpec(internal_linstor=False),
        "gitops_repo_url": "https://github.com/test/test-cluster-infra",
    })
    out = generate_fluxinstance_manifest(spec)
    assert "class: standard" in out


def test_generate_fluxinstance_manifest_no_storage_uses_standard(skip_k8s):
    """storage=None → storageClass standard."""
    spec = _SPEC.model_copy(update={
        "storage": None,
        "gitops_repo_url": "https://github.com/test/test-cluster-infra",
    })
    out = generate_fluxinstance_manifest(spec)
    assert "class: standard" in out


def test_generate_fluxinstance_manifest_uses_gitops_repo_url(skip_k8s):
    """gitops_repo_url set → appears in manifest url field."""
    spec = _SPEC.model_copy(update={
        "gitops_repo_url": "https://github.com/custom-org/custom-infra",
        "storage": None,
    })
    out = generate_fluxinstance_manifest(spec)
    assert "https://github.com/custom-org/custom-infra" in out


def test_generate_fluxinstance_manifest_constructs_url_from_cluster_name(skip_k8s):
    """gitops_repo_url=None → URL constructed from cluster name."""
    spec = _SPEC.model_copy(update={
        "gitops_repo_url": None,
        "storage": None,
        "managed_gitops": False,
    })
    out = generate_fluxinstance_manifest(spec)
    assert "test-cluster-infra" in out
    assert "PodZonePlatformEngineering" in out


def test_generate_fluxinstance_manifest_contains_flux_system_namespace(skip_k8s):
    """FluxInstance must be in flux-system namespace."""
    spec = _SPEC.model_copy(update={"storage": None, "gitops_repo_url": "https://github.com/x/y"})
    out = generate_fluxinstance_manifest(spec)
    assert "namespace: flux-system" in out


def test_generate_fluxinstance_manifest_contains_sops_decryption_patch(skip_k8s):
    """FluxInstance includes kustomize patch for SOPS decryption."""
    spec = _SPEC.model_copy(update={"storage": None, "gitops_repo_url": "https://github.com/x/y"})
    out = generate_fluxinstance_manifest(spec)
    assert "sops-age" in out
    assert "decryption" in out


# --- generate_sops_secret_manifest ---

def test_generate_sops_secret_manifest_contains_age_key(skip_k8s):
    """SOPS secret manifest wraps age key in stringData.age.agekey."""
    test_key = "AGE-SECRET-KEY-1TESTKEY"
    out = generate_sops_secret_manifest(test_key)
    assert test_key in out
    assert "age.agekey" in out
    assert "stringData" in out


def test_generate_sops_secret_manifest_is_flux_system(skip_k8s):
    """SOPS secret manifest targets flux-system namespace."""
    out = generate_sops_secret_manifest("AGE-SECRET-KEY-1FAKE")
    assert "namespace: flux-system" in out
    assert "name: sops-age" in out


def test_generate_sops_secret_manifest_valid_yaml(skip_k8s):
    """SOPS secret manifest is valid YAML."""
    import yaml as _y
    out = generate_sops_secret_manifest("AGE-SECRET-KEY-1FAKE")
    docs = list(_y.safe_load_all(out))
    assert any(d for d in docs if d and d.get("kind") == "Secret")


# --- retrieve_age_key ---

def test_retrieve_age_key_returns_stub_with_skip_k8s(skip_k8s):
    """With GITOPS_SKIP_K8S=1, retrieve_age_key returns the stub key."""
    result = retrieve_age_key("sops-age-testcluster")
    assert result.startswith("AGE-SECRET-KEY")


def test_retrieve_age_key_stub_does_not_expose_real_key(skip_k8s):
    """Stub key returned must be a recognisable placeholder, not a real secret."""
    result = retrieve_age_key("sops-age-testcluster")
    # Stub contains FAKESTUB — confirms it's the test placeholder
    assert "FAKE" in result or "STUB" in result or "TEST" in result


# --- create_cluster integration ---

def _make_svc_with_mocks():
    """Return a ClusterService with all external I/O mocked."""
    svc = ClusterService()
    svc._git = AsyncMock()
    svc._git.create_branch = AsyncMock()
    svc._git.write_file = AsyncMock()
    svc._git.commit = AsyncMock(return_value="sha123")
    svc._git.push = AsyncMock()
    svc._gh = AsyncMock()
    svc._gh.create_pr = AsyncMock(return_value="https://github.com/test/repo/pull/99")
    return svc


def _extract_written_values(svc) -> str:
    """Pull the values YAML from mock write_file calls."""
    for call in svc._git.write_file.call_args_list:
        path = call.args[0]
        if "values" in path:
            return call.args[1]
    return ""


@pytest.mark.asyncio
async def test_create_cluster_fluxinstance_always_present(monkeypatch, skip_k8s):
    """fluxinstance InlineManifest is always appended in create_cluster()."""
    monkeypatch.setattr(
        "gitopsgui.services.cluster_service.fetch_static_inline_manifests",
        lambda: [],
    )
    spec = _SPEC.model_copy(update={"sops_secret_ref": ""})  # no SOPS key
    svc = _make_svc_with_mocks()
    await svc.create_cluster(spec)

    values = _extract_written_values(svc)
    assert "fluxinstance" in values


@pytest.mark.asyncio
async def test_create_cluster_sops_age_present_when_sops_secret_ref_set(monkeypatch, skip_k8s):
    """sops-age InlineManifest is appended when sops_secret_ref is set."""
    monkeypatch.setattr(
        "gitopsgui.services.cluster_service.fetch_static_inline_manifests",
        lambda: [],
    )
    spec = _SPEC.model_copy(update={"sops_secret_ref": "sops-age-testcluster"})
    svc = _make_svc_with_mocks()
    await svc.create_cluster(spec)

    values = _extract_written_values(svc)
    data = _yaml.safe_load(values)
    inline_names = [m["name"] for m in data.get("inlineManifests", [])]
    assert "sops-age" in inline_names


@pytest.mark.asyncio
async def test_create_cluster_sops_age_absent_when_no_sops_secret_ref(monkeypatch, skip_k8s):
    """sops-age InlineManifest is NOT appended when sops_secret_ref is empty/None."""
    monkeypatch.setattr(
        "gitopsgui.services.cluster_service.fetch_static_inline_manifests",
        lambda: [],
    )
    spec = _SPEC.model_copy(update={"sops_secret_ref": ""})
    svc = _make_svc_with_mocks()
    await svc.create_cluster(spec)

    values = _extract_written_values(svc)
    data = _yaml.safe_load(values)
    inline_names = [m["name"] for m in data.get("inlineManifests", [])]
    assert "sops-age" not in inline_names


@pytest.mark.asyncio
async def test_create_cluster_422_when_sops_secret_not_found(monkeypatch, skip_k8s):
    """create_cluster raises HTTPException(422) when sops_secret_ref Secret is absent."""
    from fastapi import HTTPException
    monkeypatch.setattr(
        "gitopsgui.services.cluster_service.fetch_static_inline_manifests",
        lambda: [],
    )
    # Monkeypatch retrieve_age_key to simulate Secret not found
    monkeypatch.setattr(
        "gitopsgui.services.cluster_service.retrieve_age_key",
        lambda ref: (_ for _ in ()).throw(
            ValueError(f"SOPS Secret '{ref}' not found in namespace 'gitopsapi'.")
        ),
    )
    spec = _SPEC.model_copy(update={"sops_secret_ref": "sops-age-missing"})
    svc = _make_svc_with_mocks()
    with pytest.raises(HTTPException) as exc_info:
        await svc.create_cluster(spec)
    assert exc_info.value.status_code == 422
    assert "sops-age-missing" in exc_info.value.detail


@pytest.mark.asyncio
async def test_create_cluster_inline_manifest_ordering(monkeypatch, skip_k8s):
    """InlineManifests appear in the correct order: static → fluxinstance → sops-age."""
    static = [
        {"name": "kubelet-serving-cert-approver", "contents": "# ksa\n"},
        {"name": "metrics-server", "contents": "# ms\n"},
        {"name": "gateway-api", "contents": "# gw\n"},
    ]
    monkeypatch.setattr(
        "gitopsgui.services.cluster_service.fetch_static_inline_manifests",
        lambda: static,
    )
    spec = _SPEC.model_copy(update={"sops_secret_ref": "sops-age-testcluster"})
    svc = _make_svc_with_mocks()
    await svc.create_cluster(spec)

    values = _extract_written_values(svc)
    data = _yaml.safe_load(values)
    inline_names = [m["name"] for m in data.get("inlineManifests", [])]
    # Ordering: static manifests, then fluxinstance, then sops-age
    assert inline_names.index("kubelet-serving-cert-approver") < inline_names.index("fluxinstance")
    assert inline_names.index("metrics-server") < inline_names.index("fluxinstance")
    assert inline_names.index("gateway-api") < inline_names.index("fluxinstance")


# ---------------------------------------------------------------------------
# PROJ-003/T-029 — Cluster chart constant overrides via monkeypatch
# ---------------------------------------------------------------------------

import gitopsgui.services.cluster_service as cs


def test_cluster_chart_version_default_is_0139():
    """Default CLUSTER_CHART_VERSION must be 0.1.39 (type-approved baseline)."""
    # This reads the module-level constant directly; no monkeypatching needed.
    assert cs.CLUSTER_CHART_VERSION == "0.1.39"


def test_cluster_chart_version_override_reflected_in_render(monkeypatch):
    """Overriding CLUSTER_CHART_VERSION is reflected in the rendered cluster YAML."""
    monkeypatch.setattr(cs, "CLUSTER_CHART_VERSION", "9.9.9")
    out = cs._render_cluster_yaml("my-cluster")
    assert "9.9.9" in out


def test_cluster_chart_repo_name_override_reflected_in_render(monkeypatch):
    """Overriding CLUSTER_CHART_REPO_NAME changes HelmRepository name and sourceRef in render."""
    monkeypatch.setattr(cs, "CLUSTER_CHART_REPO_NAME", "custom-chart-repo")
    out = cs._render_cluster_yaml("my-cluster")
    # HelmRepository metadata.name and HelmRelease sourceRef.name both use the constant
    assert out.count("custom-chart-repo") == 2


def test_cluster_chart_repo_url_override_reflected_in_render(monkeypatch):
    """Overriding CLUSTER_CHART_REPO_URL changes HelmRepository spec.url in render."""
    monkeypatch.setattr(cs, "CLUSTER_CHART_REPO_URL", "https://charts.example.com/custom")
    out = cs._render_cluster_yaml("my-cluster")
    assert "https://charts.example.com/custom" in out
