"""
Pydantic model validation tests for cluster models.
"""

import pytest
from pydantic import ValidationError
from gitopsgui.models.cluster import ClusterSpec, ClusterDimensions, ClusterResponse, PlatformSpec, NetworkSpec, RegistryMirrorSpec


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


# ---------------------------------------------------------------------------
# CC-178: NetworkSpec model tests
# ---------------------------------------------------------------------------

def test_network_spec_defaults():
    n = NetworkSpec(id="abc-123", vip="10.0.0.1", ip_range="10.0.0.0/24")
    assert n.type == "flannel"
    assert n.dns_domain == "cluster.local"
    assert n.cilium_version == "1.19.2"
    assert n.kube_proxy_replacement is True
    assert n.ingress_controller is True
    assert n.l2_load_balancer is True
    assert n.l7_proxy is True
    assert n.gateway_api is True
    assert n.gateway_api_alpn is False
    assert n.gateway_api_app_protocol is False
    assert n.hubble_relay is False
    assert n.hubble_ui is False


def test_network_spec_cilium_type():
    n = NetworkSpec(id="abc-123", type="cilium", vip="10.0.0.1", ip_range="10.0.0.0/24")
    assert n.type == "cilium"


def test_network_spec_optional_fields_default_none():
    n = NetworkSpec(id="abc-123", vip="10.0.0.1", ip_range="10.0.0.0/24")
    assert n.lb_pool_start is None
    assert n.lb_pool_stop is None
    assert n.cert_sans is None
    assert n.pod_cidr is None
    assert n.service_cidr is None


def test_cluster_spec_migration_validator_creates_network():
    """Migration validator constructs NetworkSpec from legacy vip/ip_range fields."""
    spec = ClusterSpec(
        name="test-cluster",
        vip="192.168.1.100",
        ip_range="192.168.1.0/24",
        dimensions=ClusterDimensions(),
        sops_secret_ref="sops-key",
    )
    assert spec.network is not None
    assert spec.network.vip == "192.168.1.100"
    assert spec.network.ip_range == "192.168.1.0/24"
    assert spec.network.type == "flannel"


def test_cluster_spec_migration_validator_uses_cni_as_type():
    """Migration validator maps cni='cilium' → network.type='cilium'."""
    spec = ClusterSpec(
        name="test-cluster",
        vip="192.168.1.100",
        ip_range="192.168.1.0/24",
        cni="cilium",
        dimensions=ClusterDimensions(),
        sops_secret_ref="sops-key",
    )
    assert spec.network is not None
    assert spec.network.type == "cilium"


def test_cluster_spec_migration_validator_preserves_cert_sans():
    """Migration validator copies cert_sans into network.cert_sans."""
    spec = ClusterSpec(
        name="test-cluster",
        vip="192.168.1.100",
        ip_range="192.168.1.0/24",
        cert_sans=["192.168.1.100", "k8s.example.com"],
        dimensions=ClusterDimensions(),
        sops_secret_ref="sops-key",
    )
    assert spec.network is not None
    assert spec.network.cert_sans == ["192.168.1.100", "k8s.example.com"]


def test_cluster_spec_migration_validator_no_network_when_no_vip_or_ip_range():
    """Migration validator does NOT create network when neither vip nor ip_range is set."""
    spec = ClusterSpec(
        name="test-cluster",
        dimensions=ClusterDimensions(),
        managed_gitops=False,
        gitops_repo_url="git@github.com:org/repo.git",
        sops_secret_ref="sops-key",
    )
    assert spec.network is None


def test_cluster_spec_network_not_overwritten_if_present():
    """When network is explicitly provided, migration validator does not overwrite it."""
    n = NetworkSpec(id="fixed-id", type="cilium", vip="10.1.2.3", ip_range="10.1.2.0/24")
    spec = ClusterSpec(
        name="test-cluster",
        network=n,
        dimensions=ClusterDimensions(),
        sops_secret_ref="sops-key",
    )
    assert spec.network.id == "fixed-id"
    assert spec.network.type == "cilium"
    assert spec.network.vip == "10.1.2.3"


def test_cluster_spec_migration_validator_generates_uuid_id():
    """Migration validator assigns a UUID id to the constructed NetworkSpec."""
    import re
    spec = ClusterSpec(
        name="test-cluster",
        vip="192.168.1.100",
        ip_range="192.168.1.0/24",
        dimensions=ClusterDimensions(),
        sops_secret_ref="sops-key",
    )
    uuid_pattern = re.compile(
        r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
    )
    assert uuid_pattern.match(spec.network.id) is not None


# ---------------------------------------------------------------------------
# CC-147: RegistryMirrorSpec + ClusterSpec new fields
# ---------------------------------------------------------------------------

def test_registry_mirror_spec_defaults():
    """RegistryMirrorSpec.override_path defaults to True."""
    m = RegistryMirrorSpec(registry="docker.io", endpoints=["http://nexus.local/proxy-dockerhub"])
    assert m.override_path is True


def test_registry_mirror_spec_override_path_can_be_false():
    m = RegistryMirrorSpec(registry="gcr.io", endpoints=["http://nexus.local/proxy-gcr"], override_path=False)
    assert m.override_path is False


def test_cluster_spec_registry_mirrors_defaults_empty():
    """ClusterSpec.registry_mirrors defaults to an empty list."""
    spec = ClusterSpec(
        name="test-cluster",
        vip="192.168.1.100",
        ip_range="192.168.1.0/24",
        dimensions=ClusterDimensions(),
        sops_secret_ref="sops-key",
    )
    assert spec.registry_mirrors == []


def test_cluster_spec_registry_mirrors_accepts_entries():
    mirror = RegistryMirrorSpec(registry="docker.io", endpoints=["http://nexus.local/proxy-dockerhub"])
    spec = ClusterSpec(
        name="test-cluster",
        vip="192.168.1.100",
        ip_range="192.168.1.0/24",
        dimensions=ClusterDimensions(),
        sops_secret_ref="sops-key",
        registry_mirrors=[mirror],
    )
    assert len(spec.registry_mirrors) == 1
    assert spec.registry_mirrors[0].registry == "docker.io"


def test_cluster_spec_observability_agent_defaults_empty():
    """ClusterSpec.observability_agent defaults to ''."""
    spec = ClusterSpec(
        name="test-cluster",
        vip="192.168.1.100",
        ip_range="192.168.1.0/24",
        dimensions=ClusterDimensions(),
        sops_secret_ref="sops-key",
    )
    assert spec.observability_agent == ""


def test_cluster_spec_observability_agent_can_be_set():
    spec = ClusterSpec(
        name="test-cluster",
        vip="192.168.1.100",
        ip_range="192.168.1.0/24",
        dimensions=ClusterDimensions(),
        sops_secret_ref="sops-key",
        observability_agent="fluentbit",
    )
    assert spec.observability_agent == "fluentbit"
