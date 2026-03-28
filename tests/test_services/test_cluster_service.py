"""
Unit tests for ClusterService — mocks GitService and GitHubService.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from gitopsgui.models.cluster import (
    ClusterSpec, ClusterDimensions, PlatformSpec, TalosTemplateSpec,
    IngressConnectorSpec, TokenSecretRef,
)
from gitopsgui.services.cluster_service import (
    ClusterService, _render_values, _render_kustomization, _render_cluster_yaml,
    _set_kustomization_suspended, _remove_kustomization,
    _render_cloudflared_yaml, _render_cloudflared_apps_kustomization,
    _render_cloudflared_flux_kustomization,
    classify_cluster_changes, ChangeCategory, _dims_hash,
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
# StorageSpec model and rendering (T-025)
# ---------------------------------------------------------------------------

def test_storage_omitted_when_none():
    """When storage is None, no storage: key in rendered values."""
    out = _render_values(_SPEC)
    assert "storage:" not in out


def test_storage_disabled_rendered():
    """storage.enabled=False renders storage block with enabled: false."""
    from gitopsgui.models.cluster import StorageSpec
    spec = _SPEC.model_copy(update={"storage": StorageSpec(enabled=False)})
    out = _render_values(spec)
    assert "storage:" in out
    assert "enabled: false" in out


def test_storage_enabled_with_size_rendered():
    """storage.enabled=True with size renders full storage block."""
    from gitopsgui.models.cluster import StorageSpec
    spec = _SPEC.model_copy(update={"storage": StorageSpec(enabled=True, size=50)})
    out = _render_values(spec)
    assert "storage:" in out
    assert "enabled: true" in out
    assert "size: 50" in out


def test_storage_size_omitted_when_none():
    """When storage.size is None, storage block has no size key."""
    import yaml
    from gitopsgui.models.cluster import StorageSpec
    spec = _SPEC.model_copy(update={"storage": StorageSpec(enabled=False)})
    parsed = yaml.safe_load(_render_values(spec))
    assert "size" not in parsed.get("storage", {})


def test_classify_storage_enabled_change_is_cat1():
    """Changing storage.enabled is Cat 1 (machine template change)."""
    from gitopsgui.models.cluster import StorageSpec
    existing = _SPEC_BASE
    new = _SPEC_BASE.model_copy(update={"storage": StorageSpec(enabled=False)})
    result = classify_cluster_changes(existing, new)
    assert result.category == ChangeCategory.IMMUTABLE_TEMPLATE
    assert "storage.enabled" in result.changed_fields


def test_classify_storage_size_change_is_cat1():
    """Changing storage.size is Cat 1 (machine template change)."""
    from gitopsgui.models.cluster import StorageSpec
    existing = _SPEC_BASE.model_copy(update={"storage": StorageSpec(enabled=True, size=20)})
    new = _SPEC_BASE.model_copy(update={"storage": StorageSpec(enabled=True, size=50)})
    result = classify_cluster_changes(existing, new)
    assert result.category == ChangeCategory.IMMUTABLE_TEMPLATE
    assert "storage.size" in result.changed_fields


def test_classify_storage_unchanged_is_cat2():
    """Same storage spec is Cat 2 (no template change)."""
    from gitopsgui.models.cluster import StorageSpec
    existing = _SPEC_BASE.model_copy(update={"storage": StorageSpec(enabled=True, size=20)})
    new = _SPEC_BASE.model_copy(update={"storage": StorageSpec(enabled=True, size=20)})
    result = classify_cluster_changes(existing, new)
    assert result.category == ChangeCategory.MUTABLE


def test_dims_hash_differs_for_storage_enabled_vs_disabled():
    """_dims_hash produces different values when storage.enabled changes."""
    from gitopsgui.models.cluster import StorageSpec
    spec_with = _SPEC.model_copy(update={"storage": StorageSpec(enabled=True, size=20)})
    spec_without = _SPEC.model_copy(update={"storage": StorageSpec(enabled=False)})
    assert _dims_hash(spec_with) != _dims_hash(spec_without)


def test_musings_cluster_storage_disabled():
    """musings test cluster: single node, storage disabled, no size rendered."""
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
        storage=StorageSpec(enabled=False),
        sops_secret_ref="sops-age",
    )
    import yaml
    parsed = yaml.safe_load(_render_values(musings))
    assert parsed["storage"]["enabled"] is False
    assert "size" not in parsed["storage"]
