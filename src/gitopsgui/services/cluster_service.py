"""
GITGUI-005 — Cluster object reader/writer.

Gitops repo layout:
  clusters/<name>/                          — Flux kustomization wiring
  gitops/cluster-charts/<name>/<name>-values.yaml  — cluster-chart Helm values

Writes go via feature branch + PR labelled 'cluster' + stage label.
"""

import hashlib
import json
import os
import textwrap
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import IntEnum
from typing import List, Optional

import httpx
import yaml

from ..models.cluster import (
    ClusterSpec, ClusterResponse, ClusterStatus,
    ClusterSuspendResponse, ClusterDecommissionResponse,
    IngressConnectorSpec, IngressConnectorResponse,
    StorageClassesResponse, GatewayWireResponse,
    PlatformSpec, StorageSpec, ClusterChartSpec, NetworkSpec,
)
from ..models.deploy_key import ClusterBootstrapRequest, ClusterBootstrapResponse
from .git_service import GitService
from .github_service import GitHubService
from . import repo_router

CLUSTER_CHART_REPO_URL = os.environ.get("GITOPS_CLUSTER_CHART_REPO_URL", "")
CLUSTER_CHART_REPO_NAME = os.environ.get("GITOPS_CLUSTER_CHART_REPO_NAME", "cluster-charts")
CLUSTER_CHART_VERSION = os.environ.get("GITOPS_CLUSTER_CHART_VERSION", "0.1.20")

_CLUSTER_CHARTS_BASE = "gitops/cluster-charts"
_CLUSTERS_BASE = "clusters"
_MGMT_CLUSTERS_PATH = "clusters/management/clusters.yaml"

# Reviewers determined by target (cluster changes always need cluster_operator approval)
_CLUSTER_REVIEWERS: List[str] = []  # populated from env/config at runtime
_CLUSTER_STAGE_LABEL = "stage:production"


# ---------------------------------------------------------------------------
# T-036 (CC-176) — Static InlineManifests
# ---------------------------------------------------------------------------
# These three manifests are fetched at cluster provision time and embedded as
# inlineManifests in the cluster-chart values. They are not cluster-specific
# (no templating required) and are required on every cluster.
#
# Why inlineManifests rather than extraManifests (URLs)?
# InlineManifests do not require outbound network access at cluster boot. The
# content is fetched here (where outbound access is available) and baked into
# the CAPI MachineConfig. This removes external URL dependencies from the
# cluster bootstrap path.

# gateway-api is pinned to v1.3.0 (standard channel).
# When gateway-api releases a new version, bump GATEWAY_API_VERSION here and
# update the URL below. Do NOT silently follow 'latest'.
GATEWAY_API_VERSION = "v1.3.0"

_STATIC_INLINE_MANIFESTS = [
    {
        "name": "kubelet-serving-cert-approver",
        "url": (
            "https://raw.githubusercontent.com/alex1989hu/kubelet-serving-cert-approver"
            "/main/deploy/standalone-install.yaml"
        ),
    },
    {
        "name": "metrics-server",
        "url": (
            "https://github.com/kubernetes-sigs/metrics-server"
            "/releases/latest/download/components.yaml"
        ),
    },
    {
        "name": "gateway-api",
        # Pinned to GATEWAY_API_VERSION — update pin when gateway-api releases a new version.
        "url": (
            f"https://github.com/kubernetes-sigs/gateway-api"
            f"/releases/download/{GATEWAY_API_VERSION}/standard-install.yaml"
        ),
    },
]


def fetch_static_inline_manifests() -> list:
    """Fetch the three static inline manifests required on every cluster.

    Fetches kubelet-serving-cert-approver, metrics-server, and gateway-api
    from their upstream URLs. Raises httpx.HTTPStatusError (wraps as 424) on
    any fetch failure — all three are required; partial provisioning is not
    permitted.

    Returns:
        list[dict]: Each entry has 'name' and 'contents' keys, ready for
        insertion into the cluster-chart values inlineManifests list.
    """
    result = []
    for m in _STATIC_INLINE_MANIFESTS:
        response = httpx.get(m["url"], follow_redirects=True, timeout=30)
        response.raise_for_status()
        result.append({"name": m["name"], "contents": response.text})
    return result


# ---------------------------------------------------------------------------
# Change classification — Cat 1–4
# ---------------------------------------------------------------------------
#
# Cat 1 — Immutable machine template fields: require new ProxmoxMachineTemplate
#          (hash-based suffix via cluster-chart machine_template_suffix value).
#          All other field changes may be combined in the same PR.
# Cat 2 — Mutable cluster fields: updated in-place via values.yaml PR.
# Cat 3 — Rolling update fields: kubernetes_version, talos_image.
#          Triggers node-by-node replacement; accepted but PR body warns operator.
# Cat 4 — Prohibited on live cluster: name, ip_range, platform.type change.
#          Rejected with HTTP 422.

class ChangeCategory(IntEnum):
    MUTABLE = 2
    ROLLING = 3
    IMMUTABLE_TEMPLATE = 1
    PROHIBITED = 4


# Fields that map to ProxmoxMachineTemplate (immutable in CAPI)
_IMMUTABLE_DIMS = {"cpu_per_node", "memory_gb_per_node", "boot_volume_gb"}
_IMMUTABLE_PLATFORM = {"type", "talos_template"}  # talos_template.vmid / .node


@dataclass
class ChangeClassification:
    category: ChangeCategory
    changed_fields: List[str] = field(default_factory=list)
    machine_template_hash: Optional[str] = None  # set for Cat 1; used as machine_template_suffix in values


def _dims_hash(spec: "ClusterSpec") -> str:
    """Stable hash of the immutable dimension fields for machine template naming."""
    cp_dims = spec.controlplane_dimensions or spec.dimensions
    payload = {
        "worker_cpu": spec.dimensions.cpu_per_node,
        "worker_mem": spec.dimensions.memory_gb_per_node,
        "worker_disk": spec.dimensions.boot_volume_gb + (spec.storage.emptydir_gb if spec.storage else 0),
        "cp_cpu": cp_dims.cpu_per_node,
        "cp_mem": cp_dims.memory_gb_per_node,
        "cp_disk": cp_dims.boot_volume_gb,
        "talos_vmid": spec.platform.talos_template.vmid if spec.platform else None,
        "talos_node": (
            spec.platform.talos_template.node or (spec.platform.nodes[0] if spec.platform else None)
        ) if spec.platform else None,
        "internal_linstor": spec.storage.internal_linstor if spec.storage else False,
        "linstor_disk_gb": spec.storage.linstor_disk_gb if spec.storage else None,
        "emptydir_gb": spec.storage.emptydir_gb if spec.storage else 0,
    }
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return digest[:8]


def classify_cluster_changes(existing: "ClusterSpec", new: "ClusterSpec") -> ChangeClassification:
    """Classify the changes from existing to new ClusterSpec into a single category.

    Returns the highest-severity category found across all changed fields.
    Cat 4 > Cat 1 > Cat 3 > Cat 2.
    """
    changed: List[str] = []
    category = ChangeCategory.MUTABLE

    # Cat 4: prohibited changes
    if existing.name != new.name:
        changed.append("name")
        category = ChangeCategory.PROHIBITED
    if existing.ip_range != new.ip_range:
        changed.append("ip_range")
        category = ChangeCategory.PROHIBITED
    if (existing.platform and new.platform) and existing.platform.type != new.platform.type:
        changed.append("platform.type")
        category = ChangeCategory.PROHIBITED
    # CC-177: cni and cert_sans are immutable at provision time (legacy fields — not removed yet)
    if existing.cni != new.cni:
        changed.append("cni")
        category = ChangeCategory.PROHIBITED
    if existing.cert_sans != new.cert_sans:
        changed.append("cert_sans")
        category = ChangeCategory.PROHIBITED

    # CC-178: NetworkSpec Cat 4 checks — parallel to legacy field checks above
    if existing.network and new.network:
        if existing.network.type != new.network.type:
            changed.append("network.type")      # Cat 4
            category = ChangeCategory.PROHIBITED
        if existing.network.vip != new.network.vip:
            changed.append("network.vip")       # Cat 4
            category = ChangeCategory.PROHIBITED
        if existing.network.ip_range != new.network.ip_range:
            changed.append("network.ip_range")  # Cat 4
            category = ChangeCategory.PROHIBITED
        if existing.network.cert_sans != new.network.cert_sans:
            changed.append("network.cert_sans") # Cat 4
            category = ChangeCategory.PROHIBITED

    if category == ChangeCategory.PROHIBITED:
        return ChangeClassification(category=category, changed_fields=changed)

    # Cat 1: immutable machine template fields
    def _dims_changed(a, b) -> List[str]:
        if a is None and b is None:
            return []
        a = a or existing.dimensions
        b = b or new.dimensions
        return [f for f in _IMMUTABLE_DIMS if getattr(a, f) != getattr(b, f)]

    immutable_changes = (
        _dims_changed(existing.dimensions, new.dimensions)
        + [f"controlplane_dimensions.{f}" for f in _dims_changed(
            existing.controlplane_dimensions, new.controlplane_dimensions
        )]
    )
    if existing.platform and new.platform:
        old_t, new_t = existing.platform.talos_template, new.platform.talos_template
        if old_t.vmid != new_t.vmid:
            immutable_changes.append("platform.talos_template.vmid")
        old_node = old_t.node or existing.platform.nodes[0]
        new_node = new_t.node or new.platform.nodes[0]
        if old_node != new_node:
            immutable_changes.append("platform.talos_template.node")

    # Cat 1: storage spec changes require new machine template (disk config changes)
    old_s = existing.storage
    new_s = new.storage
    if (old_s.internal_linstor if old_s else False) != (new_s.internal_linstor if new_s else False):
        immutable_changes.append("storage.internal_linstor")
    if (old_s.linstor_disk_gb if old_s else None) != (new_s.linstor_disk_gb if new_s else None):
        immutable_changes.append("storage.linstor_disk_gb")
    if (old_s.emptydir_gb if old_s else 0) != (new_s.emptydir_gb if new_s else 0):
        immutable_changes.append("storage.emptydir_gb")

    # CC-178: NetworkSpec Cat 1 checks — Cilium version + capability flags.
    # All changes trigger a new InlineManifest → new MachineTemplate → rolling replacement.
    if existing.network and new.network:
        if existing.network.cilium_version != new.network.cilium_version:
            immutable_changes.append("network.cilium_version")
        _cilium_flags = [
            "kube_proxy_replacement", "ingress_controller", "l2_load_balancer",
            "l7_proxy", "gateway_api", "gateway_api_alpn", "gateway_api_app_protocol",
            "hubble_relay", "hubble_ui",
        ]
        for flag in _cilium_flags:
            if getattr(existing.network, flag) != getattr(new.network, flag):
                immutable_changes.append(f"network.{flag}")

    if immutable_changes:
        changed.extend(immutable_changes)
        category = ChangeCategory.IMMUTABLE_TEMPLATE

    # Cat 3: rolling update fields (node-by-node OS/extension update)
    rolling_changes = []
    if existing.kubernetes_version != new.kubernetes_version:
        rolling_changes.append("kubernetes_version")
    if existing.talos_image != new.talos_image:
        rolling_changes.append("talos_image")
    # iSCSI capability toggle requires iscsi-tools Talos extension (rolling node update)
    old_iscsi = existing.platform.capabilities.iscsi if existing.platform else False
    new_iscsi = new.platform.capabilities.iscsi if new.platform else False
    if old_iscsi != new_iscsi:
        rolling_changes.append("platform.capabilities.iscsi")
    if rolling_changes:
        changed.extend(rolling_changes)
        if category == ChangeCategory.MUTABLE:
            category = ChangeCategory.ROLLING

    # Cat 2: everything else (mutable) — no action needed beyond a values PR

    hash_val = _dims_hash(new) if category == ChangeCategory.IMMUTABLE_TEMPLATE else None
    return ChangeClassification(
        category=category,
        changed_fields=changed,
        machine_template_hash=hash_val,
    )


# ---------------------------------------------------------------------------
# clusters.yaml helpers
# ---------------------------------------------------------------------------

def _set_kustomization_suspended(content: str, cluster_name: str) -> str:
    """Insert suspend: true into the named Kustomization's spec block."""
    kust_name = f"{cluster_name}-cluster"
    parts = content.split("\n---")
    result = []
    for part in parts:
        if f"name: {kust_name}" in part and "kind: Kustomization" in part:
            part = part.replace("spec:\n", "spec:\n  suspend: true\n", 1)
        result.append(part)
    return "\n---".join(result)


def _remove_kustomization(content: str, cluster_name: str) -> str:
    """Remove the YAML document for cluster_name-cluster from a multi-doc file."""
    kust_name = f"{cluster_name}-cluster"
    parts = content.split("\n---")
    kept = [
        p for p in parts
        if not (f"name: {kust_name}" in p and "kind: Kustomization" in p)
    ]
    return "\n---".join(kept)


def _cluster_values_path(name: str) -> str:
    return f"{_CLUSTER_CHARTS_BASE}/{name}/{name}-values.yaml"


def _cluster_yaml_path(name: str) -> str:
    return f"{_CLUSTER_CHARTS_BASE}/{name}/{name}.yaml"


def _kustomization_path(name: str) -> str:
    return f"{_CLUSTER_CHARTS_BASE}/{name}/kustomization.yaml"


def _kustomizeconfig_path(name: str) -> str:
    return f"{_CLUSTER_CHARTS_BASE}/{name}/kustomizeconfig.yaml"


def _render_values(
    spec: ClusterSpec,
    machine_template_hash: Optional[str] = None,
    inline_manifests: Optional[list] = None,
) -> str:
    """Render cluster-chart values YAML.

    Matches the schema used by actual cluster-chart values files:
      cluster.name, network.ip_ranges, controlplane.*, worker.machine_count
    Top-level GitOpsAPI metadata fields (platform, vip, etc.) are also stored
    here for roundtrip fidelity when the API reads back a cluster spec.

    machine_template_hash: if set (Cat 1 change), written as
      controlplane.machine_template_suffix and worker.machine_template_suffix
      so the cluster-chart generates ProxmoxMachineTemplates with new names.

    inline_manifests: list of {name, contents} dicts (T-036/CC-176).
      When provided, written as 'inlineManifests' to the values file.
      Static public manifests (kubelet-serving-cert-approver, metrics-server,
      gateway-api) are safe to commit; sensitive manifests (sops-age, fluxinstance)
      are T-035 scope and must NOT be passed here.
    """
    cp_dims = spec.controlplane_dimensions or spec.dimensions

    # Resolve effective VIP and ip_range from NetworkSpec (preferred) or legacy fields (fallback).
    # CC-178: spec.network supersedes the legacy top-level vip/ip_range fields.
    if spec.network:
        _effective_vip = spec.network.vip
        _effective_ip_range = spec.network.ip_range
    else:
        _effective_vip = spec.vip or ""
        _effective_ip_range = spec.ip_range or ""

    # cluster-chart consumed fields
    data: dict = {
        "cluster": {"name": spec.name},
        "network": {"ip_ranges": [_effective_ip_range]},
        "controlplane": {
            "endpoint_ip": _effective_vip,
            "machine_count": cp_dims.control_plane_count,
            "num_cores": cp_dims.cpu_per_node,
            "num_sockets": 1,
            "memory_mib": cp_dims.memory_gb_per_node * 1024,
            "boot_volume_size": cp_dims.boot_volume_gb,
        },
        "worker": {
            "machine_count": spec.dimensions.worker_count,
            "num_cores": spec.dimensions.cpu_per_node,
            "num_sockets": 1,
            "memory_mib": spec.dimensions.memory_gb_per_node * 1024,
            "boot_volume_size": spec.dimensions.boot_volume_gb + (spec.storage.emptydir_gb if spec.storage else 0),
        },
    }
    if spec.extra_manifests:
        data["controlplane"]["extra_manifests"] = spec.extra_manifests
    if spec.allow_scheduling_on_control_planes:
        data["controlplane"]["allow_scheduling_on_control_planes"] = True
    if spec.kubernetes_version:
        data["cluster"]["kubernetes_version"] = spec.kubernetes_version
    if spec.talos_image:
        data["cluster"]["image"] = spec.talos_image
    if spec.hostname:
        data["cluster"]["hostname"] = spec.hostname
    if spec.internal_hosts:
        data["cluster"]["internalhost"] = spec.internal_hosts
    if machine_template_hash:
        data["controlplane"]["machine_template_suffix"] = f"controlplane-{machine_template_hash}"
        data["worker"]["machine_template_suffix"] = f"worker-{machine_template_hash}"

    # proxmox: section consumed by cluster-chart templates
    if spec.platform and spec.platform.type == "proxmox":
        template_node = spec.platform.talos_template.node or spec.platform.nodes[0]
        data["proxmox"] = {
            "allowed_nodes": spec.platform.nodes,
            "template": {
                "sourcenode": template_node,
                "template_vmid": spec.platform.talos_template.vmid,
            },
            "vm": {"bridge": spec.platform.bridge},
        }

    # GitOpsAPI metadata (roundtrip fields — not consumed by cluster-chart)
    if spec.platform:
        data["platform"] = {
            "name": spec.platform.name,
            "type": spec.platform.type,
            "endpoint": spec.platform.endpoint,
            "nodes": spec.platform.nodes,
            "talos_template": {
                "name": spec.platform.talos_template.name,
                "version": spec.platform.talos_template.version,
                "vmid": spec.platform.talos_template.vmid,
                "node": spec.platform.talos_template.node,
            },
            "credentials_ref": spec.platform.credentials_ref,
            "bridge": spec.platform.bridge,
            "capabilities": {
                "nfs": spec.platform.capabilities.nfs,
                "nfs_server": spec.platform.capabilities.nfs_server,
                "iscsi": spec.platform.capabilities.iscsi,
                "iscsi_server": spec.platform.capabilities.iscsi_server,
                "s3": spec.platform.capabilities.s3,
                "s3_endpoint": spec.platform.capabilities.s3_endpoint,
            },
        }
    data["vip"] = _effective_vip
    if spec.gitops_repo_url:
        data["gitops_repo_url"] = spec.gitops_repo_url
    data["sops_secret_ref"] = spec.sops_secret_ref
    data["allow_scheduling_on_control_planes"] = spec.allow_scheduling_on_control_planes
    if spec.storage is not None:
        storage_data: dict = {
            "internal_linstor": spec.storage.internal_linstor,
            "emptydir_gb": spec.storage.emptydir_gb,
        }
        if spec.storage.linstor_disk_gb is not None:
            storage_data["linstor_disk_gb"] = spec.storage.linstor_disk_gb
        data["storage"] = storage_data

    if spec.ingress_connector:
        ic = spec.ingress_connector
        data["ingress_connector"] = {
            "enabled": ic.enabled,
            "type": ic.type,
            "tunnel_id": ic.tunnel_id,
            "replicas": ic.replicas,
            "namespace": ic.namespace,
            "token_secret_ref": {
                "name": ic.token_secret_ref.name,
                "key": ic.token_secret_ref.key,
            },
        }
    data["dimensions"] = {
        "control_plane_count": spec.dimensions.control_plane_count,
        "worker_count": spec.dimensions.worker_count,
        "cpu_per_node": spec.dimensions.cpu_per_node,
        "memory_gb_per_node": spec.dimensions.memory_gb_per_node,
        "boot_volume_gb": spec.dimensions.boot_volume_gb,
    }
    if spec.controlplane_dimensions:
        data["controlplane_dimensions"] = {
            "control_plane_count": spec.controlplane_dimensions.control_plane_count,
            "worker_count": spec.controlplane_dimensions.worker_count,
            "cpu_per_node": spec.controlplane_dimensions.cpu_per_node,
            "memory_gb_per_node": spec.controlplane_dimensions.memory_gb_per_node,
            "boot_volume_gb": spec.controlplane_dimensions.boot_volume_gb,
        }
    if spec.kubernetes_version:
        data["kubernetes_version"] = spec.kubernetes_version
    if spec.talos_image:
        data["talos_image"] = spec.talos_image
    if spec.cluster_chart:
        data["cluster_chart"] = {
            "id": spec.cluster_chart.id,
            "version": spec.cluster_chart.version,
            "type": spec.cluster_chart.type,
        }

    # CC-178: NetworkSpec — network capability fields (preferred path).
    # When spec.network is present, read CNI type and certSANs from it.
    # The legacy fallback (spec.cni, spec.cert_sans) applies for values files
    # that have not yet been migrated to the NetworkSpec format.
    if spec.network:
        n = spec.network
        # endpoint_ip and ip_ranges already written above via _effective_vip/_effective_ip_range
        data.setdefault("network", {})["endpoint_ip"] = n.vip
        if n.cert_sans:
            data.setdefault("network", {})["certSANs"] = n.cert_sans
        if n.type == "cilium":
            data["cni"] = "cilium"
            # proxy.disabled is derived from cni=="cilium" in the cluster-chart template; not set here.
        # Write NetworkSpec as roundtrip metadata so get_cluster can reconstruct it.
        data["network_spec"] = {
            "id": n.id,
            "type": n.type,
            "vip": n.vip,
            "ip_range": n.ip_range,
            "lb_pool_start": n.lb_pool_start,
            "lb_pool_stop": n.lb_pool_stop,
            "cert_sans": n.cert_sans,
            "dns_domain": n.dns_domain,
            "pod_cidr": n.pod_cidr,
            "service_cidr": n.service_cidr,
            "cilium_version": n.cilium_version,
            "kube_proxy_replacement": n.kube_proxy_replacement,
            "ingress_controller": n.ingress_controller,
            "ingress_controller_lb_mode": n.ingress_controller_lb_mode,
            "ingress_controller_default": n.ingress_controller_default,
            "l2_load_balancer": n.l2_load_balancer,
            "l2_lease_duration": n.l2_lease_duration,
            "l2_lease_renew_deadline": n.l2_lease_renew_deadline,
            "l2_lease_retry_period": n.l2_lease_retry_period,
            "l7_proxy": n.l7_proxy,
            "gateway_api": n.gateway_api,
            "gateway_api_alpn": n.gateway_api_alpn,
            "gateway_api_app_protocol": n.gateway_api_app_protocol,
            "hubble_relay": n.hubble_relay,
            "hubble_ui": n.hubble_ui,
        }
    # CC-177: legacy cni and cert_sans fields — always written when explicitly set.
    # These apply even when spec.network is present, because model_copy() does not re-run
    # validators so spec.cni and spec.cert_sans may differ from network.type / network.cert_sans.
    # spec.cni / spec.cert_sans act as explicit overrides that take precedence.
    if spec.cni is not None:
        data["cni"] = spec.cni
    if spec.cert_sans:
        data.setdefault("network", {})["certSANs"] = spec.cert_sans

    # CC-177: remaining cluster-chart gap fields (machine, talos_version)
    if spec.machine_install_disk:
        data.setdefault("machine", {})["installDisk"] = spec.machine_install_disk
    if spec.talos_version:
        data.setdefault("cluster", {})["talos_version"] = spec.talos_version
        data["talos_version"] = spec.talos_version  # roundtrip metadata

    # T-036 (CC-176) — InlineManifests: written when provided by the caller.
    # The caller fetches manifests before calling _render_values and passes them in.
    # NOTE: inlineManifests written here go into the gitops values file and are therefore
    # committed to the gitops repo. Static public manifests (kubelet-serving-cert-approver,
    # metrics-server, gateway-api) are safe to commit. Sensitive entries (sops-age, fluxinstance)
    # are handled by T-035 and must never be committed; the caller is responsible for passing
    # only non-sensitive entries here (or an empty list for roundtrip metadata).
    if inline_manifests is not None:
        data["inlineManifests"] = inline_manifests

    return yaml.dump(data, default_flow_style=False)


def _render_cluster_yaml(name: str) -> str:
    return textwrap.dedent(f"""\
        apiVersion: v1
        kind: Namespace
        metadata:
          name: {name}
        ---
        apiVersion: source.toolkit.fluxcd.io/v1beta2
        kind: HelmRepository
        metadata:
          name: {CLUSTER_CHART_REPO_NAME}
          namespace: flux-system
        spec:
          interval: 10m0s
          url: {CLUSTER_CHART_REPO_URL}
        ---
        apiVersion: helm.toolkit.fluxcd.io/v2beta2
        kind: HelmRelease
        metadata:
          name: {name}
          namespace: flux-system
        spec:
          targetNamespace: {name}
          chart:
            spec:
              chart: cluster-chart
              sourceRef:
                kind: HelmRepository
                name: {CLUSTER_CHART_REPO_NAME}
              version: {CLUSTER_CHART_VERSION}
          valuesFrom:
            - kind: ConfigMap
              name: {name}-values
          interval: 10m0s
    """)


def _render_kustomization(name: str) -> str:
    return textwrap.dedent(f"""\
        apiVersion: kustomize.config.k8s.io/v1beta1
        kind: Kustomization
        namespace: {name}
        resources:
          - {name}.yaml
          - proxmox-secret.yaml
        configMapGenerator:
          - name: {name}-values
            files:
              - values.yaml={name}-values.yaml
        configurations:
          - kustomizeconfig.yaml
    """)


_KUSTOMIZECONFIG = textwrap.dedent("""\
    nameReference:
    - kind: ConfigMap
      version: v1
      fieldSpecs:
      - path: spec/valuesFrom/name
        kind: HelmRelease
""")

_CLOUDFLARED_APPS_PATH = "gitops/gitops-apps/cloudflared"


def _render_cloudflared_yaml(connector: IngressConnectorSpec) -> str:
    """Render cloudflared HelmRelease + HelmRepository + Namespace for the cluster apps repo."""
    return textwrap.dedent(f"""\
        ---
        apiVersion: v1
        kind: Namespace
        metadata:
          name: {connector.namespace}
        ---
        apiVersion: source.toolkit.fluxcd.io/v1
        kind: HelmRepository
        metadata:
          name: cloudflare
          namespace: flux-system
        spec:
          interval: 24h
          url: https://cloudflare.github.io/helm-charts
        ---
        apiVersion: helm.toolkit.fluxcd.io/v2
        kind: HelmRelease
        metadata:
          name: cloudflared
          namespace: {connector.namespace}
        spec:
          interval: 30m
          chart:
            spec:
              chart: cloudflare-tunnel-remote
              version: "0.1.2"
              sourceRef:
                kind: HelmRepository
                name: cloudflare
                namespace: flux-system
          valuesFrom:
            - kind: Secret
              name: {connector.token_secret_ref.name}
              valuesKey: {connector.token_secret_ref.key}
              targetPath: cloudflare.tunnel_token
    """)


def _render_cloudflared_apps_kustomization() -> str:
    """Render kustomization.yaml for the cloudflared app directory."""
    return textwrap.dedent("""\
        apiVersion: kustomize.config.k8s.io/v1beta1
        kind: Kustomization
        resources:
          - cloudflared.yaml
    """)


def _render_cloudflared_flux_kustomization(cluster_name: str) -> str:
    """Render the Flux Kustomization entry to append to {cluster}-infra/{name}-apps.yaml."""
    return textwrap.dedent(f"""\
        ---
        apiVersion: kustomize.toolkit.fluxcd.io/v1
        kind: Kustomization
        metadata:
          name: cloudflared
          namespace: flux-system
        spec:
          interval: 1h
          retryInterval: 1m
          timeout: 5m
          sourceRef:
            kind: GitRepository
            name: {cluster_name}-apps
          path: ./{_CLOUDFLARED_APPS_PATH}
          prune: true
          decryption:
            provider: sops
            secretRef:
              name: sops-age
    """)


# ---------------------------------------------------------------------------
# Storage classes — democratic-csi NFS / iSCSI
# ---------------------------------------------------------------------------

_DEMOCRATIC_CSI_CHART_URL = "https://democratic-csi.github.io/charts/"
_STORAGE_CLASSES_INFRA_PATH = "gitops/gitops-infra/storage-classes"


def _render_democratic_csi_nfs_yaml(server: str) -> str:
    """HelmRepository + HelmRelease for democratic-csi NFS StorageClass.

    Requires a pre-existing Secret `democratic-csi-ssh` in namespace `democratic-csi`
    containing the SSH private key for ZFS host access.
    """
    return textwrap.dedent(f"""\
        ---
        apiVersion: source.toolkit.fluxcd.io/v1beta2
        kind: HelmRepository
        metadata:
          name: democratic-csi
          namespace: flux-system
        spec:
          interval: 1h
          url: {_DEMOCRATIC_CSI_CHART_URL}
        ---
        apiVersion: v1
        kind: Namespace
        metadata:
          name: democratic-csi
        ---
        apiVersion: helm.toolkit.fluxcd.io/v2beta2
        kind: HelmRelease
        metadata:
          name: democratic-csi-nfs
          namespace: flux-system
        spec:
          targetNamespace: democratic-csi
          interval: 1h
          chart:
            spec:
              chart: democratic-csi
              sourceRef:
                kind: HelmRepository
                name: democratic-csi
              version: ">=0.14.0"
          values:
            csiDriver:
              name: org.democratic-csi.nfs-saturn
            driver:
              config:
                driver: zfs-generic-nfs
                sshConnection:
                  host: {server}
                  port: 22
                  username: root
                  privateKeySecret:
                    name: democratic-csi-ssh
                    namespace: democratic-csi
                zfs:
                  datasetParentName: pool1/nfs
                  detachedSnapshotsDatasetParentName: pool1/nfs-snapshots
                  datasetEnableQuotas: true
                  datasetEnableReservation: false
                  datasetPermissionsMode: "0777"
                  datasetPermissionsUser: 0
                  datasetPermissionsGroup: 0
                nfs:
                  shareHost: {server}
                  shareAlldirs: false
                  shareAllowedHosts: []
                  shareMaprootUser: root
                  shareMaprootGroup: wheel
                  shareMapallUser: ""
                  shareMapallGroup: ""
            storageClasses:
              - name: nfs-saturn
                defaultClass: false
                reclaimPolicy: Delete
                volumeBindingMode: Immediate
                allowVolumeExpansion: true
                parameters:
                  fsType: nfs
                mountOptions:
                  - noatime
                  - nfsvers=4
            volumeSnapshotClasses:
              - name: nfs-saturn
                deletionPolicy: Delete
    """)


def _render_democratic_csi_iscsi_yaml(server: str) -> str:
    """HelmRepository + HelmRelease for democratic-csi iSCSI StorageClass.

    Requires:
    - Pre-existing Secret `democratic-csi-ssh` in namespace `democratic-csi`
    - Talos iscsi-tools machine extension enabled on worker nodes (Cat 3 change)
    """
    return textwrap.dedent(f"""\
        ---
        apiVersion: source.toolkit.fluxcd.io/v1beta2
        kind: HelmRepository
        metadata:
          name: democratic-csi
          namespace: flux-system
        spec:
          interval: 1h
          url: {_DEMOCRATIC_CSI_CHART_URL}
        ---
        apiVersion: v1
        kind: Namespace
        metadata:
          name: democratic-csi
        ---
        apiVersion: helm.toolkit.fluxcd.io/v2beta2
        kind: HelmRelease
        metadata:
          name: democratic-csi-iscsi
          namespace: flux-system
        spec:
          targetNamespace: democratic-csi
          interval: 1h
          chart:
            spec:
              chart: democratic-csi
              sourceRef:
                kind: HelmRepository
                name: democratic-csi
              version: ">=0.14.0"
          values:
            csiDriver:
              name: org.democratic-csi.iscsi-saturn
            driver:
              config:
                driver: zfs-generic-iscsi
                sshConnection:
                  host: {server}
                  port: 22
                  username: root
                  privateKeySecret:
                    name: democratic-csi-ssh
                    namespace: democratic-csi
                zfs:
                  datasetParentName: pool1/iscsi
                  detachedSnapshotsDatasetParentName: pool1/iscsi-snapshots
                  datasetEnableQuotas: true
                  datasetEnableReservation: false
                  datasetPermissionsMode: "0770"
                iscsi:
                  targetPortal: "{server}:3260"
                  namePrefix: iqn.2026-03.cloud.podzone:saturn-
                  nameSuffix: ""
                  targetGroups:
                    - targetGroupPortalGroup: 1
                      targetGroupInitiatorGroup: 1
                      targetGroupAuthType: None
                  extentInsecureTpc: true
                  extentDisablePhysicalBlocksize: true
                  extentBlocksize: 512
            storageClasses:
              - name: iscsi-saturn
                defaultClass: false
                reclaimPolicy: Retain
                volumeBindingMode: Immediate
                allowVolumeExpansion: true
                parameters:
                  fsType: ext4
            volumeSnapshotClasses:
              - name: iscsi-saturn
                deletionPolicy: Retain
    """)


def _render_storage_classes_kustomization(backends: List[str]) -> str:
    """kustomization.yaml listing the active storage class manifests."""
    resources = "\n".join(f"  - democratic-csi-{b}.yaml" for b in backends)
    return textwrap.dedent(f"""\
        apiVersion: kustomize.config.k8s.io/v1beta1
        kind: Kustomization
        resources:
        {resources}
    """)


def _render_storage_classes_flux_kustomization(cluster_name: str) -> str:
    """Flux Kustomization entry to append to {cluster}-infra/clusters/{name}/infrastructure.yaml."""
    return textwrap.dedent(f"""\
        ---
        apiVersion: kustomize.toolkit.fluxcd.io/v1
        kind: Kustomization
        metadata:
          name: storage-classes
          namespace: flux-system
        spec:
          interval: 1h
          retryInterval: 1m
          timeout: 10m
          sourceRef:
            kind: GitRepository
            name: flux-system
          path: ./{_STORAGE_CLASSES_INFRA_PATH}
          prune: true
          dependsOn:
            - name: 00-prerequisites
    """)


# ---------------------------------------------------------------------------
# T-034 (CC-174) — piraeus-operator gitops Kustomization
# ---------------------------------------------------------------------------
# piraeus-operator deploys Linstor in-cluster distributed storage (DRBD).
# Generated when storage.internal_linstor == True at cluster creation time.
# Written to {cluster}-infra as a Flux Kustomization + GitRepository pair.

_PIRAEUS_INFRA_PATH = "gitops/gitops-apps/piraeus-operator"
# Using 'latest' tag for dev/ETE. For production, pin to a specific release tag.
_PIRAEUS_RELEASE_URL = "https://github.com/piraeusdatastore/piraeus-operator"


def _render_piraeus_kustomization(cluster_name: str) -> str:
    """Flux Kustomization + GitRepository for piraeus-operator.

    Written to {cluster}-infra when storage.internal_linstor is True.
    The GitRepository tracks the piraeus-operator release repo; the
    Kustomization applies the upstream release manifest path.
    """
    return textwrap.dedent(f"""\
        ---
        apiVersion: kustomize.toolkit.fluxcd.io/v1
        kind: Kustomization
        metadata:
          name: piraeus-operator
          namespace: flux-system
        spec:
          interval: 1h
          path: ./
          prune: true
          sourceRef:
            kind: GitRepository
            name: piraeus-operator
          postBuild:
            substitute: {{}}
        ---
        apiVersion: source.toolkit.fluxcd.io/v1
        kind: GitRepository
        metadata:
          name: piraeus-operator
          namespace: flux-system
        spec:
          interval: 1h
          url: {_PIRAEUS_RELEASE_URL}
          ref:
            tag: latest
    """)


# ---------------------------------------------------------------------------
# Gateway — GatewayClass, Gateway listeners, ClusterIssuer, Certificate
# ---------------------------------------------------------------------------

_GATEWAY_INFRA_PATH = "gitops/gitops-infra/gateway"
_INTERNAL_WILDCARD_DOMAIN = "*.internal.podzone.net"
_LETSENCRYPT_EMAIL = "martinjcolley@gmail.com"


def _listener_name(hostname: str, suffix: str) -> str:
    """Sanitise a hostname into a valid Gateway listener name."""
    return hostname.replace(".", "-").replace("_", "-") + "-" + suffix


def _render_gateway_yaml(public_hosts: List[str], internal_hosts: List[str]) -> str:
    """GatewayClass + Gateway listeners + optional ClusterIssuer + Certificate."""
    lines = [textwrap.dedent("""\
        ---
        apiVersion: gateway.networking.k8s.io/v1beta1
        kind: GatewayClass
        metadata:
          name: cilium
        spec:
          controllerName: io.cilium/gateway-controller
        ---
        apiVersion: gateway.networking.k8s.io/v1
        kind: Gateway
        metadata:
          name: gateway
          namespace: kube-system
        spec:
          gatewayClassName: cilium
          listeners:
    """)]

    for h in public_hosts:
        lines.append(textwrap.dedent(f"""\
            \
          - hostname: {h}
            name: {_listener_name(h, "http")}
            port: 80
            protocol: HTTP
            allowedRoutes:
              namespaces:
                from: All
        """))

    for h in internal_hosts:
        lines.append(textwrap.dedent(f"""\
            \
          - hostname: {h}
            name: {_listener_name(h, "https")}
            port: 443
            protocol: HTTPS
            tls:
              mode: Terminate
              certificateRefs:
                - name: internal-wildcard-tls
                  namespace: kube-system
            allowedRoutes:
              namespaces:
                from: All
        """))

    if internal_hosts:
        lines.append(textwrap.dedent(f"""\
            ---
            # DNS-01 ClusterIssuer — issues wildcard cert for {_INTERNAL_WILDCARD_DOMAIN}
            # Individual service names not exposed in Cloudflare DNS.
            apiVersion: cert-manager.io/v1
            kind: ClusterIssuer
            metadata:
              name: lets-encrypt-dns01
            spec:
              acme:
                email: {_LETSENCRYPT_EMAIL}
                server: https://acme-v02.api.letsencrypt.org/directory
                privateKeySecretRef:
                  name: letsencrypt-dns01-key
                solvers:
                - dns01:
                    cloudflare:
                      apiTokenSecretRef:
                        name: cloudflare-api-token
                        key: api-token
            ---
            apiVersion: cert-manager.io/v1
            kind: Certificate
            metadata:
              name: internal-wildcard-tls
              namespace: kube-system
            spec:
              secretName: internal-wildcard-tls
              issuerRef:
                name: lets-encrypt-dns01
                kind: ClusterIssuer
              dnsNames:
                - "{_INTERNAL_WILDCARD_DOMAIN}"
        """))

    return "\n".join(lines)


def _render_gateway_kustomization() -> str:
    return textwrap.dedent("""\
        apiVersion: kustomize.config.k8s.io/v1beta1
        kind: Kustomization
        resources:
          - gateway.yaml
    """)


def _render_gateway_flux_kustomization(cluster_name: str) -> str:
    """Flux Kustomization entry to append to {cluster}-infra/clusters/{name}/infrastructure.yaml."""
    return textwrap.dedent(f"""\
        ---
        apiVersion: kustomize.toolkit.fluxcd.io/v1
        kind: Kustomization
        metadata:
          name: gateway
          namespace: flux-system
        spec:
          interval: 1h
          retryInterval: 1m
          timeout: 5m
          sourceRef:
            kind: GitRepository
            name: flux-system
          path: ./{_GATEWAY_INFRA_PATH}
          prune: true
          dependsOn:
            - name: 00-manifests
    """)


class ClusterService:
    def __init__(self):
        self._git = GitService()
        self._gh = GitHubService()

    async def list_clusters(self) -> List[ClusterResponse]:
        names = await self._git.list_dir(_CLUSTER_CHARTS_BASE)
        results = []
        for name in names:
            cluster = await self.get_cluster(name)
            if cluster:
                results.append(cluster)
        return results

    async def get_cluster(self, name: str) -> Optional[ClusterResponse]:
        try:
            raw = await self._git.read_file(_cluster_values_path(name))
            data = yaml.safe_load(raw)
        except FileNotFoundError:
            return None

        raw_platform = data.get("platform")
        platform = PlatformSpec(**raw_platform) if isinstance(raw_platform, dict) else None

        raw_ic = data.get("ingress_connector")
        ingress_connector = IngressConnectorSpec(**raw_ic) if isinstance(raw_ic, dict) else None

        raw_storage = data.get("storage")
        storage = StorageSpec(**raw_storage) if isinstance(raw_storage, dict) else None

        raw_cluster_chart = data.get("cluster_chart")
        cluster_chart = ClusterChartSpec(**raw_cluster_chart) if isinstance(raw_cluster_chart, dict) else None

        # CC-178: reconstruct NetworkSpec.
        # Preferred: use the 'network_spec' roundtrip block written by _render_values.
        # Fallback: derive from cluster-chart values keys (ip_ranges, endpoint_ip, cni, certSANs).
        raw_network_spec = data.get("network_spec")
        if isinstance(raw_network_spec, dict):
            network = NetworkSpec(**raw_network_spec)
        else:
            # Derive from cluster-chart values (backward compat for values files pre-CC-178)
            raw_net = data.get("network", {})
            _ip_ranges = raw_net.get("ip_ranges", [""])
            _ip_range = _ip_ranges[0] if _ip_ranges else ""
            _vip = data.get("vip", "")
            _cni_str = data.get("cni", "")
            _cni_type = "cilium" if _cni_str == "cilium" else "flannel"
            _cert_sans = raw_net.get("certSANs")
            if _vip or _ip_range:
                from uuid import uuid4
                network = NetworkSpec(
                    id=str(uuid4()),
                    type=_cni_type,
                    vip=_vip,
                    ip_range=_ip_range,
                    cert_sans=_cert_sans,
                )
            else:
                network = None

        # T-033 (CC-173) — InlineManifest redaction on read.
        # The values file in git may contain 'inlineManifests' with full manifest contents
        # (kubelet-serving-cert-approver, metrics-server, gateway-api from T-036, and
        # potentially sops-age/fluxinstance from T-035 which are sensitive).
        # We extract only the 'name' of each entry and discard 'contents'.
        # This ensures that sensitive material (SOPS keys, Flux bootstrap config) is never
        # returned via the API — only the manifest inventory is surfaced.
        raw_inline = data.get("inlineManifests", [])
        inline_names = [
            m["name"] for m in raw_inline
            if isinstance(m, dict) and "name" in m
        ]

        spec = ClusterSpec(
            name=name,
            platform=platform,
            vip=data.get("vip", ""),
            ip_range=data.get("network", {}).get("ip_ranges", [""])[0],
            dimensions=data.get("dimensions", {}),
            gitops_repo_url=data.get("gitops_repo_url", ""),
            sops_secret_ref=data.get("sops_secret_ref", ""),
            allow_scheduling_on_control_planes=data.get("allow_scheduling_on_control_planes", False),
            hostname=data.get("hostname", data.get("external_hosts", [])),
            internal_hosts=data.get("internal_hosts", []),
            ingress_connector=ingress_connector,
            storage=storage,
            cluster_chart=cluster_chart,
            inline_manifest_names=inline_names,
            network=network,
        )
        return ClusterResponse(name=name, spec=spec)

    async def _provision_gitops_repos(self, spec: ClusterSpec) -> ClusterSpec:
        """TR-039: Create {cluster}-infra and {cluster}-apps as private repos on the git forge.

        Returns an updated spec with gitops_repo_url populated from the created infra repo.
        Raises RuntimeError if repo creation fails (cluster provisioning must not proceed).
        """
        infra_name = f"{spec.name}-infra"
        apps_name = f"{spec.name}-apps"

        infra_url = await self._gh.create_repo(
            name=infra_name,
            description=f"Flux infrastructure manifests for {spec.name} cluster",
            private=True,
        )
        await self._gh.create_repo(
            name=apps_name,
            description=f"Application workloads for {spec.name} cluster",
            private=True,
        )

        return spec.model_copy(update={"gitops_repo_url": infra_url})

    async def create_cluster(self, spec: ClusterSpec) -> ClusterResponse:
        if spec.managed_gitops:
            # TR-039: provision repos first — cluster creation fails if this fails
            spec = await self._provision_gitops_repos(spec)

        # T-036 (CC-176): fetch static inline manifests at provision time.
        # These are public, non-sensitive manifests required on every cluster.
        # Fetch failure raises httpx.HTTPStatusError — provisioning halts; all three required.
        from fastapi import HTTPException
        try:
            static_inline = fetch_static_inline_manifests()
        except httpx.HTTPStatusError as exc:
            raise HTTPException(
                status_code=424,
                detail=(
                    f"Failed to fetch static inline manifest "
                    f"(HTTP {exc.response.status_code}): {exc.request.url}. "
                    "Provisioning halted — all static inline manifests are required."
                ),
            ) from exc
        except httpx.RequestError as exc:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"Network error fetching static inline manifest: {exc}. "
                    "Provisioning halted — all static inline manifests are required."
                ),
            ) from exc

        branch = f"cluster/provision-{spec.name}-{uuid.uuid4().hex[:8]}"
        await self._git.create_branch(branch)

        await self._git.write_file(
            _cluster_values_path(spec.name),
            _render_values(spec, inline_manifests=static_inline),
        )
        await self._git.write_file(_cluster_yaml_path(spec.name), _render_cluster_yaml(spec.name))
        await self._git.write_file(_kustomization_path(spec.name), _render_kustomization(spec.name))
        await self._git.write_file(_kustomizeconfig_path(spec.name), _KUSTOMIZECONFIG)

        await self._git.commit(f"chore: provision cluster {spec.name}")
        await self._git.push()

        # T-034 (CC-174): piraeus-operator Flux Kustomization.
        # Generated in {cluster}-infra when storage.internal_linstor is True.
        # Opens a separate PR on {cluster}-infra; piraeus installs Linstor in-cluster storage.
        piraeus_pr_url: Optional[str] = None
        if spec.storage and spec.storage.internal_linstor:
            piraeus_branch = f"cluster/piraeus-{spec.name}-{uuid.uuid4().hex[:8]}"
            git_infra = repo_router.git_for_infra(spec.name)
            gh_infra = repo_router.github_for_infra(spec.name)
            await git_infra.create_branch(piraeus_branch)
            await git_infra.write_file(
                f"{_PIRAEUS_INFRA_PATH}/piraeus-operator.yaml",
                _render_piraeus_kustomization(spec.name),
            )
            await git_infra.commit(f"feat: add piraeus-operator kustomization for {spec.name}")
            await git_infra.push()
            piraeus_pr_url = await gh_infra.create_pr(
                branch=piraeus_branch,
                title=f"feat: piraeus-operator — {spec.name}",
                body=(
                    f"Adds piraeus-operator Flux Kustomization to `{spec.name}-infra`.\n\n"
                    f"**Condition**: `storage.internal_linstor == True`\n\n"
                    f"Deploys Linstor in-cluster distributed storage (DRBD) via the piraeus-operator.\n\n"
                    f"**Prerequisites**: Talos drbd kernel extension must be present in the cluster image.\n"
                ),
                labels=["cluster", _CLUSTER_STAGE_LABEL],
                reviewers=_CLUSTER_REVIEWERS,
            )

        pr_body = (
            f"Automated cluster provisioning for `{spec.name}`.\n\n"
            f"IP range: {spec.ip_range}\n\n"
        )
        if spec.managed_gitops:
            pr_body += (
                f"**GitOps repos provisioned (TR-039)**:\n"
                f"- `{spec.name}-infra`: {spec.gitops_repo_url}\n"
                f"- `{spec.name}-apps`: (companion workload repo)\n\n"
                f"**Next steps** (CC-053 — not yet automated):\n"
                f"- Generate and register deploy keys for both repos\n"
                f"- Generate per-cluster SOPS age key, encrypt with management key\n"
                f"- Bootstrap Flux on the new cluster\n"
            )
        if piraeus_pr_url:
            pr_body += f"\n**piraeus-operator PR**: {piraeus_pr_url}\n"

        pr_url = await self._gh.create_pr(
            branch=branch,
            title=f"Provision cluster: {spec.name}",
            body=pr_body,
            labels=["cluster", _CLUSTER_STAGE_LABEL],
            reviewers=_CLUSTER_REVIEWERS,
        )

        return ClusterResponse(name=spec.name, spec=spec, pr_url=pr_url)

    async def update_cluster(self, name: str, spec: ClusterSpec) -> ClusterResponse:
        from fastapi import HTTPException

        # Read existing spec for diff classification
        existing = await self.get_cluster(name)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Cluster {name!r} not found")

        classification = classify_cluster_changes(existing.spec, spec)

        if classification.category == ChangeCategory.PROHIBITED:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Prohibited change(s) on live cluster: {', '.join(classification.changed_fields)}. "
                    f"These fields cannot be modified without decommissioning and reprovisioning the cluster."
                ),
            )

        machine_hash = classification.machine_template_hash
        branch = f"cluster/update-{name}-{uuid.uuid4().hex[:8]}"
        await self._git.create_branch(branch)
        await self._git.write_file(_cluster_values_path(name), _render_values(spec, machine_hash))
        await self._git.commit(f"chore: update cluster {name}")
        await self._git.push()

        body_lines = [f"Cluster spec update for `{name}`."]
        if classification.changed_fields:
            body_lines.append(f"\n**Changed fields:** {', '.join(classification.changed_fields)}")
        if classification.category == ChangeCategory.IMMUTABLE_TEMPLATE:
            body_lines.append(
                f"\n⚠️ **Cat 1 — Immutable template change.** "
                f"New ProxmoxMachineTemplates will be created with suffix `-{machine_hash}`. "
                f"Existing nodes are not replaced automatically — rolling replacement must be triggered manually."
            )
        elif classification.category == ChangeCategory.ROLLING:
            body_lines.append(
                f"\n⚠️ **Cat 3 — Rolling update.** "
                f"CAPI will perform a rolling node replacement for: "
                f"{', '.join(classification.changed_fields)}."
            )

        pr_url = await self._gh.create_pr(
            branch=branch,
            title=f"Update cluster: {name} (Cat {classification.category})",
            body="\n".join(body_lines),
            labels=["cluster", _CLUSTER_STAGE_LABEL],
            reviewers=_CLUSTER_REVIEWERS,
        )

        return ClusterResponse(name=name, spec=spec, pr_url=pr_url)

    async def suspend_cluster(self, name: str) -> ClusterSuspendResponse:
        """PR: add suspend: true to the cluster's Kustomization in ManagementCluster/clusters.yaml.

        When Flux reconciles the PR, it suspends the cluster's HelmRelease reconciliation
        without deleting any resources. The cluster continues to run unmanaged.
        """
        branch = f"cluster/suspend-{name}-{uuid.uuid4().hex[:8]}"
        await self._git.create_branch(branch)

        content = await self._git.read_file(_MGMT_CLUSTERS_PATH)
        updated = _set_kustomization_suspended(content, name)
        await self._git.write_file(_MGMT_CLUSTERS_PATH, updated)
        await self._git.commit(f"chore: suspend cluster {name}")
        await self._git.push()

        pr_url = await self._gh.create_pr(
            branch=branch,
            title=f"Suspend cluster: {name}",
            body=(
                f"Sets `spec.suspend: true` on the `{name}-cluster` Kustomization.\n\n"
                f"The cluster continues to run but Flux stops reconciling its HelmRelease.\n"
                f"Reverse with a follow-up PR removing the suspend flag, or proceed to "
                f"`DELETE /clusters/{name}` to decommission."
            ),
            labels=["cluster", _CLUSTER_STAGE_LABEL],
            reviewers=_CLUSTER_REVIEWERS,
        )

        return ClusterSuspendResponse(name=name, pr_url=pr_url)

    async def decommission_cluster(self, name: str) -> ClusterDecommissionResponse:
        """PR: remove cluster-chart files + Kustomization entry. Archives infra/apps repos.

        When the PR is merged, Flux prunes the {name}-cluster Kustomization which cascades to
        deleting the HelmRelease → CAPI deprovisions all cluster machines.
        Repos {name}-infra and {name}-apps are archived (read-only) before the PR is opened.
        """
        branch = f"cluster/decommission-{name}-{uuid.uuid4().hex[:8]}"
        await self._git.create_branch(branch)

        # Remove cluster-chart files
        chart_files = [
            f"{_CLUSTER_CHARTS_BASE}/{name}/{name}-values.yaml",
            f"{_CLUSTER_CHARTS_BASE}/{name}/{name}.yaml",
            f"{_CLUSTER_CHARTS_BASE}/{name}/kustomization.yaml",
            f"{_CLUSTER_CHARTS_BASE}/{name}/kustomizeconfig.yaml",
        ]
        for path in chart_files:
            try:
                await self._git.delete_file(path)
            except FileNotFoundError:
                pass  # already absent — idempotent

        # Remove the Kustomization entry from ManagementCluster/clusters.yaml
        content = await self._git.read_file(_MGMT_CLUSTERS_PATH)
        updated = _remove_kustomization(content, name)
        await self._git.write_file(_MGMT_CLUSTERS_PATH, updated)

        await self._git.commit(f"chore: decommission cluster {name}")
        await self._git.push()

        # Archive repos (read-only, history preserved) before PR so the intent is clear
        archived: List[str] = []
        for repo_suffix in ("infra", "apps"):
            repo_name = f"{name}-{repo_suffix}"
            await self._gh.archive_repo(repo_name)
            archived.append(repo_name)

        pr_url = await self._gh.create_pr(
            branch=branch,
            title=f"Decommission cluster: {name}",
            body=(
                f"Removes `{name}` cluster-chart files and its Kustomization entry from "
                f"`clusters/ManagementCluster/clusters.yaml`.\n\n"
                f"**Effect on merge**: Flux prunes the `{name}-cluster` Kustomization → "
                f"HelmRelease deleted → CAPI deprovisions all `{name}` machines.\n\n"
                f"**Repos archived** (read-only):\n"
                + "".join(f"- `{r}`\n" for r in archived)
            ),
            labels=["cluster", _CLUSTER_STAGE_LABEL],
            reviewers=_CLUSTER_REVIEWERS,
        )

        return ClusterDecommissionResponse(name=name, pr_url=pr_url, archived_repos=archived)

    async def wire_ingress_connector(self, cluster_name: str) -> IngressConnectorResponse:
        """CC-068 Phase 1: render cloudflared manifests into {cluster}-apps and {cluster}-infra.

        Writes to two repos:
        - {cluster}-apps: gitops/gitops-apps/cloudflared/ (HelmRelease + kustomization)
        - {cluster}-infra: appends cloudflared Flux Kustomization to clusters/{name}/{name}-apps.yaml

        Raises ValueError if the cluster has no ingress_connector configured or it is disabled.
        """
        cluster = await self.get_cluster(cluster_name)
        if not cluster:
            raise FileNotFoundError(f"Cluster {cluster_name!r} not found")

        connector = cluster.spec.ingress_connector
        if not connector or not connector.enabled:
            raise ValueError(
                f"Cluster {cluster_name!r} has no ingress_connector configured or it is disabled. "
                "Set ingress_connector.enabled=true in the cluster spec first."
            )

        branch = f"cluster/ingress-connector-{cluster_name}-{uuid.uuid4().hex[:8]}"

        # --- apps repo: write cloudflared manifests ---
        git_apps = repo_router.git_for_apps(cluster_name)
        gh_apps = repo_router.github_for_apps(cluster_name)

        await git_apps.create_branch(branch)
        await git_apps.write_file(
            f"{_CLOUDFLARED_APPS_PATH}/cloudflared.yaml",
            _render_cloudflared_yaml(connector),
        )
        await git_apps.write_file(
            f"{_CLOUDFLARED_APPS_PATH}/kustomization.yaml",
            _render_cloudflared_apps_kustomization(),
        )
        await git_apps.commit(f"feat: add cloudflared ingress connector for {cluster_name}")
        await git_apps.push()

        apps_pr_url = await gh_apps.create_pr(
            branch=branch,
            title=f"feat: wire cloudflared ingress connector — {cluster_name}",
            body=(
                f"Adds cloudflared HelmRelease to `{cluster_name}-apps`.\n\n"
                f"**Tunnel token secret**: `{connector.token_secret_ref.name}` "
                f"(key: `{connector.token_secret_ref.key}`) must exist in namespace "
                f"`{connector.namespace}` before this can reconcile.\n\n"
                f"**Companion PR** in `{cluster_name}-infra` adds the Flux Kustomization entry.\n\n"
                f"CC-068 Phase 1"
            ),
            labels=["cluster", _CLUSTER_STAGE_LABEL],
            reviewers=_CLUSTER_REVIEWERS,
        )

        # --- infra repo: append Flux Kustomization entry to {name}-apps.yaml ---
        git_infra = repo_router.git_for_infra(cluster_name)
        gh_infra = repo_router.github_for_infra(cluster_name)

        await git_infra.create_branch(branch)
        apps_yaml_path = f"clusters/{cluster_name}/{cluster_name}-apps.yaml"
        existing = await git_infra.read_file(apps_yaml_path)
        updated = existing.rstrip("\n") + "\n" + _render_cloudflared_flux_kustomization(cluster_name)
        await git_infra.write_file(apps_yaml_path, updated)
        await git_infra.commit(f"feat: add cloudflared Kustomization for {cluster_name}")
        await git_infra.push()

        infra_pr_url = await gh_infra.create_pr(
            branch=branch,
            title=f"feat: wire cloudflared Flux Kustomization — {cluster_name}",
            body=(
                f"Adds `cloudflared` Flux Kustomization to `clusters/{cluster_name}/{cluster_name}-apps.yaml`.\n\n"
                f"**Merge after** the companion `{cluster_name}-apps` PR so the HelmRelease exists "
                f"before Flux reconciles this Kustomization.\n\n"
                f"CC-068 Phase 1"
            ),
            labels=["cluster", _CLUSTER_STAGE_LABEL],
            reviewers=_CLUSTER_REVIEWERS,
        )

        return IngressConnectorResponse(
            name=cluster_name,
            apps_pr_url=apps_pr_url,
            infra_pr_url=infra_pr_url,
        )

    async def wire_storage_classes(self, cluster_name: str) -> StorageClassesResponse:
        """Render democratic-csi HelmRelease manifests into {cluster}-infra based on platform.capabilities.

        Writes to {cluster}-infra:
        - gitops/gitops-infra/storage-classes/democratic-csi-{backend}.yaml per enabled backend
        - gitops/gitops-infra/storage-classes/kustomization.yaml
        - Appends storage-classes Flux Kustomization to clusters/{name}/infrastructure.yaml

        Raises ValueError if the cluster has no storage-capable backends configured.
        """
        cluster = await self.get_cluster(cluster_name)
        if not cluster:
            raise FileNotFoundError(f"Cluster {cluster_name!r} not found")

        caps = cluster.spec.platform.capabilities if cluster.spec.platform else None
        backends: List[str] = []
        if caps and caps.nfs:
            if not caps.nfs_server:
                raise ValueError("platform.capabilities.nfs_server is required when nfs=True")
            backends.append("nfs")
        if caps and caps.iscsi:
            if not caps.iscsi_server:
                raise ValueError("platform.capabilities.iscsi_server is required when iscsi=True")
            backends.append("iscsi")

        if not backends:
            raise ValueError(
                f"Cluster {cluster_name!r} has no NFS or iSCSI capabilities configured. "
                "Set platform.capabilities.nfs=true or iscsi=true with the corresponding server address."
            )

        branch = f"cluster/storage-classes-{cluster_name}-{uuid.uuid4().hex[:8]}"
        git_infra = repo_router.git_for_infra(cluster_name)
        gh_infra = repo_router.github_for_infra(cluster_name)

        await git_infra.create_branch(branch)

        if caps.nfs:
            await git_infra.write_file(
                f"{_STORAGE_CLASSES_INFRA_PATH}/democratic-csi-nfs.yaml",
                _render_democratic_csi_nfs_yaml(caps.nfs_server),
            )
        if caps.iscsi:
            await git_infra.write_file(
                f"{_STORAGE_CLASSES_INFRA_PATH}/democratic-csi-iscsi.yaml",
                _render_democratic_csi_iscsi_yaml(caps.iscsi_server),
            )
        await git_infra.write_file(
            f"{_STORAGE_CLASSES_INFRA_PATH}/kustomization.yaml",
            _render_storage_classes_kustomization(backends),
        )

        infra_yaml_path = f"clusters/{cluster_name}/infrastructure.yaml"
        existing = await git_infra.read_file(infra_yaml_path)
        updated = existing.rstrip("\n") + "\n" + _render_storage_classes_flux_kustomization(cluster_name)
        await git_infra.write_file(infra_yaml_path, updated)

        await git_infra.commit(f"feat: add storage-classes manifests for {cluster_name}")
        await git_infra.push()

        infra_pr_url = await gh_infra.create_pr(
            branch=branch,
            title=f"feat: wire storage-classes — {cluster_name}",
            body=(
                f"Adds democratic-csi StorageClass deployment to `{cluster_name}-infra`.\n\n"
                f"**Backends**: {', '.join(backends)}\n\n"
                + (
                    f"**NFS server**: `{caps.nfs_server}`\n"
                    if caps.nfs else ""
                )
                + (
                    f"**iSCSI server**: `{caps.iscsi_server}`\n\n"
                    f"⚠️ iSCSI requires the `iscsi-tools` Talos machine extension (Cat 3 — "
                    f"rolling node update). Ensure the extension is in the cluster's Talos image "
                    f"before merging.\n\n"
                    if caps.iscsi else "\n"
                )
                + f"**Prerequisite**: Secret `democratic-csi-ssh` must exist in namespace "
                f"`democratic-csi` with the SSH private key for ZFS host access."
            ),
            labels=["cluster", _CLUSTER_STAGE_LABEL],
            reviewers=_CLUSTER_REVIEWERS,
        )

        return StorageClassesResponse(
            name=cluster_name,
            infra_pr_url=infra_pr_url,
            backends=backends,
        )

    async def wire_gateway(self, cluster_name: str) -> "GatewayWireResponse":
        """Render Gateway manifests into {cluster}-infra based on ClusterSpec.hostname and .internal_hosts.

        Writes to {cluster}-infra:
        - gitops/gitops-infra/gateway/gateway.yaml  (GatewayClass, Gateway, optional ClusterIssuer + Certificate)
        - gitops/gitops-infra/gateway/kustomization.yaml
        - Appends gateway Flux Kustomization to clusters/{name}/infrastructure.yaml

        Raises ValueError if neither hostname nor internal_hosts is configured.
        """
        from ..models.cluster import GatewayWireResponse

        cluster = await self.get_cluster(cluster_name)
        if not cluster:
            raise FileNotFoundError(f"Cluster {cluster_name!r} not found")

        public_hosts = cluster.spec.hostname
        int_hosts = cluster.spec.internal_hosts

        if not public_hosts and not int_hosts:
            raise ValueError(
                f"Cluster {cluster_name!r} has no hostname or internal_hosts configured. "
                "Set at least one in the cluster spec before wiring the gateway."
            )

        branch = f"cluster/gateway-{cluster_name}-{uuid.uuid4().hex[:8]}"
        git_infra = repo_router.git_for_infra(cluster_name)
        gh_infra = repo_router.github_for_infra(cluster_name)

        await git_infra.create_branch(branch)
        await git_infra.write_file(
            f"{_GATEWAY_INFRA_PATH}/gateway.yaml",
            _render_gateway_yaml(public_hosts, int_hosts),
        )
        await git_infra.write_file(
            f"{_GATEWAY_INFRA_PATH}/kustomization.yaml",
            _render_gateway_kustomization(),
        )

        infra_yaml_path = f"clusters/{cluster_name}/infrastructure.yaml"
        existing = await git_infra.read_file(infra_yaml_path)
        updated = existing.rstrip("\n") + "\n" + _render_gateway_flux_kustomization(cluster_name)
        await git_infra.write_file(infra_yaml_path, updated)

        await git_infra.commit(f"feat: add gateway manifests for {cluster_name}")
        await git_infra.push()

        pr_body = (
            f"Adds Gateway manifests to `{cluster_name}-infra`.\n\n"
        )
        if public_hosts:
            pr_body += f"**Public (HTTP-80)**: {', '.join(f'`{h}`' for h in public_hosts)}\n\n"
        if int_hosts:
            pr_body += (
                f"**Internal (HTTPS-443)**: {', '.join(f'`{h}`' for h in int_hosts)}\n\n"
                f"Includes `lets-encrypt-dns01` ClusterIssuer and wildcard Certificate "
                f"for `{_INTERNAL_WILDCARD_DOMAIN}`.\n\n"
                f"**Prerequisites**: `cloudflare-api-token` Secret in `cert-manager` namespace "
                f"(deployed via `00-prerequisites` Kustomization).\n\n"
            )

        infra_pr_url = await gh_infra.create_pr(
            branch=branch,
            title=f"feat: wire gateway — {cluster_name}",
            body=pr_body,
            labels=["cluster", _CLUSTER_STAGE_LABEL],
            reviewers=_CLUSTER_REVIEWERS,
        )

        return GatewayWireResponse(
            name=cluster_name,
            infra_pr_url=infra_pr_url,
            public_hosts=public_hosts,
            internal_hosts=int_hosts,
        )

    async def bootstrap_cluster(
        self,
        cluster_name: str,
        request: ClusterBootstrapRequest,
        _sops_svc=None,       # injectable for tests
        _deploy_key_svc=None,  # injectable for tests
    ) -> ClusterBootstrapResponse:
        """CC-053b — Install SOPS key + SSH deploy keys on a newly provisioned cluster.

        Requires the cluster to be running and reachable via the CAPI management cluster.
        Kubeconfig is extracted from the CAPI management secret — no local kubeconfig needed.

        Orchestrates:
        1. SOPS age key generation, encryption, management-infra PR, cluster Secret install
        2. SSH deploy key for {cluster}-infra → GitHub + cluster flux-system Secret + GitRepository CR
        3. SSH deploy key for {cluster}-apps  → GitHub + cluster flux-system Secret + GitRepository CR

        After this call, Flux can be bootstrapped on the cluster pointing at {cluster}-infra
        and it will have credentials to pull from both repos.
        """
        import yaml as _yaml
        from .kubeconfig_service import KubeconfigService, rewrite_kubeconfig_server
        from .sops_service import SOPSService
        from .deploy_key_service import DeployKeyService, SKIP_K8S
        from .github_service import GITHUB_ORG
        from ..models.sops import SOPSBootstrapRequest
        from ..models.deploy_key import GitAccessRequest

        cluster = await self.get_cluster(cluster_name)
        if not cluster:
            raise FileNotFoundError(f"Cluster {cluster_name!r} not found in registry")

        # Extract kubeconfig from CAPI (raises HTTPException 503/404 if not available)
        kubeconfig_dict: dict = {}
        if not SKIP_K8S:
            kubeconfig_yaml = await KubeconfigService().extract_kubeconfig(cluster_name)
            if cluster.spec.bastion:
                b = cluster.spec.bastion
                kubeconfig_yaml = rewrite_kubeconfig_server(
                    kubeconfig_yaml, b.ip, b.api_port
                )
            kubeconfig_dict = _yaml.safe_load(kubeconfig_yaml)

        # 1. SOPS bootstrap: generate key, encrypt, PR on management-infra, install Secret
        sops_svc = _sops_svc if _sops_svc is not None else SOPSService()
        sops_result = await sops_svc.sops_bootstrap(
            cluster_name,
            SOPSBootstrapRequest(
                management_sops_public_key=request.management_sops_public_key
            ),
        )

        # 2 & 3. SSH deploy keys for infra and apps repos
        dks = _deploy_key_svc if _deploy_key_svc is not None else DeployKeyService()
        org = GITHUB_ORG

        infra_name = repo_router.infra_repo_name(cluster_name)
        apps_name = repo_router.apps_repo_name(cluster_name)

        infra_result = await dks.configure_repository_access(
            infra_name,
            GitAccessRequest(
                cluster=cluster_name,
                git_url=f"git@github.com:{org}/{infra_name}.git",
            ),
            kubeconfig_dict=kubeconfig_dict or None,
        )
        apps_result = await dks.configure_repository_access(
            apps_name,
            GitAccessRequest(
                cluster=cluster_name,
                git_url=f"git@github.com:{org}/{apps_name}.git",
            ),
            kubeconfig_dict=kubeconfig_dict or None,
        )

        return ClusterBootstrapResponse(
            cluster_name=cluster_name,
            sops_public_key=sops_result.sops_public_key,
            sops_mgmt_pr_url=sops_result.mgmt_pr_url,
            infra_key_id=infra_result.github_key_id,
            apps_key_id=apps_result.github_key_id,
            secrets_created=not SKIP_K8S,
        )
