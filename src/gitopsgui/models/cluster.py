from uuid import uuid4

from pydantic import BaseModel, Field, model_validator
from typing import List, Optional


class TokenSecretRef(BaseModel):
    name: str
    key: str = "token"


class IngressConnectorSpec(BaseModel):
    enabled: bool = True
    type: str = "cloudflare-tunnel"       # only "cloudflare-tunnel" supported
    tunnel_id: Optional[str] = None       # Cloudflare Tunnel UUID (informational; used in Phase 2 API calls)
    replicas: int = 2
    namespace: str = "cloudflared"
    token_secret_ref: TokenSecretRef = Field(
        default_factory=lambda: TokenSecretRef(name="cloudflare-tunnel-token")
    )


class StorageSpec(BaseModel):
    internal_linstor: bool = False
    # Deploy Piraeus/Linstor on this cluster. Cat 1 change (machine template suffix).

    linstor_disk_gb: Optional[int] = None
    # Additional VM data disk for Linstor pool (GB). None = no dedicated data disk. Cat 1 change.

    emptydir_gb: int = 0
    # emptyDir headroom added to provisioned boot disk size. Cat 1 change.
    # Use when apps require large ephemeral local storage (e.g. ollama model cache).
    # Provisioned boot disk = dimensions.boot_volume_gb + emptydir_gb.

    @model_validator(mode='before')
    @classmethod
    def _migrate_legacy_fields(cls, values):
        """Migrate pre-redesign fields: enabled → internal_linstor, size → linstor_disk_gb."""
        if isinstance(values, dict):
            if 'enabled' in values and 'internal_linstor' not in values:
                values['internal_linstor'] = values.pop('enabled')
            if 'size' in values and 'linstor_disk_gb' not in values:
                values['linstor_disk_gb'] = values.pop('size')
        return values


class ClusterDimensions(BaseModel):
    control_plane_count: int = 3
    worker_count: int = 3
    cpu_per_node: int = 4
    memory_gb_per_node: int = 16
    boot_volume_gb: int = 50


class BastionSpec(BaseModel):
    hostname: str
    ip: str
    api_port: int = 6443  # port on bastion that forwards to k8s API server


class TalosTemplateSpec(BaseModel):
    name: str = "0-talos-template"  # VM template name on the hypervisor
    version: str = "v1.9.5"        # Talos release version (informational, not enforced)
    vmid: int = 100                 # VMID of the template on this hypervisor
    node: Optional[str] = None     # Proxmox node where template resides; defaults to platform.nodes[0]


class PlatformCapabilities(BaseModel):
    nfs: bool = False
    # NFS target reachable from this cluster's network.
    # Enables democratic-csi NFS StorageClass deployment. Cat 2 change.
    nfs_server: Optional[str] = None
    # IP or hostname of the NFS/ZFS server (required when nfs=True).

    iscsi: bool = False
    # iSCSI target reachable from this cluster's network.
    # Enables democratic-csi iSCSI StorageClass + Talos iscsi-tools extension. Cat 3 change.
    iscsi_server: Optional[str] = None
    # IP or hostname of the iSCSI/ZFS server (required when iscsi=True).

    s3: bool = False
    # S3-compatible store reachable (MinIO or external).
    # Enables S3 bucket provisioner StorageClass deployment. Cat 2 change.
    s3_endpoint: Optional[str] = None
    # S3 endpoint URL (required when s3=True).


class PlatformSpec(BaseModel):
    name: str                # human identifier for the hypervisor (e.g. "venus", "saturn")
    type: str = "proxmox"   # provisioning platform type; only "proxmox" supported
    endpoint: str            # Proxmox API URL (e.g. "https://192.168.4.50:8006")
    nodes: List[str]         # Proxmox node names allowed to schedule VMs (→ ProxmoxCluster.allowedNodes)
    talos_template: TalosTemplateSpec = Field(default_factory=TalosTemplateSpec)
    credentials_ref: str = "capmox-manager-credentials"  # K8s secret name with CAPMOX API credentials
    bridge: str = "vmbr0"               # Proxmox VM network bridge
    capabilities: PlatformCapabilities = Field(default_factory=PlatformCapabilities)


class ClusterChartSpec(BaseModel):
    """
    Records the cluster-chart version binding for a cluster's generated values file.
    Versioned to match cluster-chart release. 'type' identifies the CAPI provider stack.
    When CAPI providers beyond proxmox-talos are added, new type values will be introduced.
    """
    id: str                         # UUID — unique identifier for this binding instance
    version: str                    # cluster-chart semver, e.g. "0.1.39"
    type: str = "proxmox-talos"    # provider type; only "proxmox-talos" currently supported


class NetworkSpec(BaseModel):
    # ---- Identity ----
    id: str
    # UUID — unique reference to this network config instance.
    # Same pattern as ClusterChartSpec.id.

    type: str = "flannel"
    # CNI type. "flannel" = default kube-proxy (no extra config).
    # "cilium" = Cilium CNI (generates cilium InlineManifest; sets cni:none +
    # proxy.disabled in cluster-chart).
    # Cat 4 — immutable. Changing CNI on a live cluster requires reprovisioning.

    # ---- Core (all CNI types) ----
    vip: str
    # Cluster VIP — kube-apiserver endpoint IP.
    # Maps to cluster-chart values: network.endpoint_ip + vip (top-level).
    # Cat 4 — changing VIP requires cluster reprovisioning.

    ip_range: str
    # Cluster IP pool / CAPI IP range, e.g. "192.168.4.0/24".
    # Maps to values: network.ip_ranges (list wrapper applied in _render_values).
    # Cat 4 — CAPI/Proxmox IP pool cannot be changed on a live cluster.

    lb_pool_start: Optional[str] = None
    lb_pool_stop: Optional[str] = None
    # L2 load balancer IP pool (CiliumLoadBalancerIPPool CR when type="cilium").
    # When set, T-039 appends CiliumLoadBalancerIPPool + CiliumL2AnnouncementPolicy
    # YAML to the cilium InlineManifest.
    # Cat 0 — live update (K8s object; can be patched without reprovisioning).

    cert_sans: Optional[List[str]] = None
    # Additional SANs for the API server TLS cert.
    # Maps to values: network.certSANs.
    # Cat 4 — baked into TLS cert at bootstrap; changing requires reprovisioning.

    dns_domain: str = "cluster.local"
    # Cluster DNS domain.
    # Cat 4 — built into kubeadm config at bootstrap.

    pod_cidr: Optional[str] = None
    # Explicit pod CIDR if distinct from ip_range.
    # Default: cluster-chart derives from ip_range. Provided for advanced overrides.

    service_cidr: Optional[str] = None
    # Service network CIDR, e.g. "10.96.0.0/12".
    # Default: cluster-chart uses Kubernetes default.

    # ---- Cilium: version ----
    cilium_version: str = "1.19.2"
    # Cilium helm chart version used by T-039 to generate the InlineManifest.
    # Type-approval baseline: 1.19.2 (current stable, 2026-04-08; requires k8s >= 1.21, helm >= 3.0).
    # Previously deployed on freyr: 1.17.4.
    # Cat 1 — changing version triggers new InlineManifest content → new MachineTemplate
    # → rolling node replacement.

    # ---- Cilium: capability flags ----
    # These are cluster capability requirements, not Cilium-internal settings.
    # For type="cilium", they map directly to helm template flags (see T-039).
    # For type="flannel" (future), gitopsapi would generate equivalent gitops app
    # deployments (MetalLB, Envoy Gateway, etc.) — not yet implemented.
    #
    # Defaults represent the opinionated base install for a podzone cluster.
    # All Cilium flag changes = Cat 1 (new InlineManifest → new MachineTemplate).

    kube_proxy_replacement: bool = True
    # kubeProxyReplacement=true. Must be True for Talos+Cilium. Kept configurable
    # for edge cases (e.g. migration testing). type="cilium" only.

    ingress_controller: bool = True
    # Cluster ingress controller.
    # type="cilium": ingressController.enabled=true + loadbalancerMode + default flags.
    # type="flannel": future — gitops app (Nginx/Envoy).

    ingress_controller_lb_mode: str = "shared"
    # ingressController.loadbalancerMode. "shared" or "dedicated".
    # Only effective when ingress_controller=True and type="cilium".

    ingress_controller_default: bool = True
    # ingressController.default — make Cilium the default ingress class.
    # Only effective when ingress_controller=True and type="cilium".

    l2_load_balancer: bool = True
    # L2 load balancer (LB IP advertising via ARP/NDP).
    # type="cilium": l2announcements.enabled=true + lease timers + loadBalancerIPs.
    # type="flannel": future — gitops app (MetalLB).

    l2_lease_duration: str = "3s"
    l2_lease_renew_deadline: str = "1s"
    l2_lease_retry_period: str = "200ms"
    # L2 announcement lease timers. Only effective when l2_load_balancer=True
    # and type="cilium".

    l7_proxy: bool = True
    # L7 proxy / service mesh.
    # type="cilium": l7Proxy=true + envoyConfig.enabled=true + loadBalancer.l7.backend=envoy.
    # Grouped as a single flag — always enabled together.
    # type="flannel": future — gitops app (standalone Envoy).

    gateway_api: bool = True
    # Gateway API controller.
    # type="cilium": gatewayAPI.enabled=true (CRDs installed separately via static
    # InlineManifest; this enables cilium's GatewayClass "cilium" controller).
    # type="flannel": future — gitops app (Envoy Gateway / Contour).

    gateway_api_alpn: bool = False
    # gatewayAPI.enableAlpn — TLS ALPN negotiation for Gateway API listeners.
    # Only effective when gateway_api=True and type="cilium". Opt-in.

    gateway_api_app_protocol: bool = False
    # gatewayAPI.enableAppProtocol — appProtocol routing decisions.
    # Only effective when gateway_api=True and type="cilium". Opt-in.

    # ---- Cilium: observability (opt-in) ----
    hubble_relay: bool = False
    # hubble.relay.enabled — Hubble metrics relay. Cilium-specific. Opt-in.

    hubble_ui: bool = False
    # hubble.ui.enabled — Hubble web UI. Requires hubble_relay=True.
    # Cilium-specific. Opt-in.


class ClusterSpec(BaseModel):
    name: str
    platform: Optional[PlatformSpec] = None  # null for externally-managed clusters (managed_gitops=False)

    # DEPRECATED — use network.vip instead. Retained for backward compat with existing values files.
    vip: Optional[str] = None
    # DEPRECATED — use network.ip_range instead. Retained for backward compat with existing values files.
    ip_range: Optional[str] = None

    dimensions: ClusterDimensions  # worker node dimensions (and control plane if controlplane_dimensions not set)
    controlplane_dimensions: Optional[ClusterDimensions] = None  # if set, control planes use these dims; workers use dimensions
    kubernetes_version: Optional[str] = None  # e.g. "v1.34.2" — Cat 3 change (rolling node replacement)
    talos_image: Optional[str] = None  # factory image URL — Cat 3 change (rolling OS update)
    managed_gitops: bool = True  # TR-039: platform creates/manages {cluster}-infra and {cluster}-apps repos
    gitops_repo_url: Optional[str] = None  # required when managed_gitops=False; derived when managed_gitops=True
    sops_secret_ref: str
    extra_manifests: List[str] = []  # URLs applied as Talos extra_manifests (cilium, flux, gateway-api, etc.)
    bastion: Optional[BastionSpec] = None  # if set, kubeconfig server URL is rewritten to bastion
    allow_scheduling_on_control_planes: bool = False  # enables Talos allowSchedulingOnControlPlanes; required when worker_count=0
    hostname: List[str] = []
    # Public-facing FQDNs — Cloudflare-proxied, HTTP-80 Gateway listeners.
    # Previously named external_hosts (migrated transparently on read).
    internal_hosts: List[str] = []
    # Internal-only FQDNs — HTTPS-443 listeners, DNS-01 wildcard TLS (*.internal.podzone.net).
    # DNS resolution via pfSense Unbound Host Overrides (internal clients only).
    ingress_connector: Optional[IngressConnectorSpec] = None  # CC-068: cloudflared tunnel connector config
    storage: Optional[StorageSpec] = None  # storage class config; None = default (no linstor, no emptydir headroom)
    cluster_chart: Optional[ClusterChartSpec] = None  # CC-166: cluster-chart version binding (roundtrip metadata)

    # CC-178: NetworkSpec — cluster network capability declaration.
    network: Optional[NetworkSpec] = None
    # When present, network.vip/ip_range/cert_sans/type supersede the legacy top-level fields.

    # CC-177: cluster-chart values keys required for ETE provisioning.
    # All five map directly to cluster-chart values.yaml keys (v0.1.39+).
    # DEPRECATED — use network.type instead. Retained for backward compat.
    cni: Optional[str] = None
    # CNI selection. "" = default kube-proxy; "cilium" = Cilium CNI (sets network.cni.name:none + proxy.disabled:true in chart).
    # Cat 4 immutable — CNI type cannot be changed on a live cluster. Reprovision required.
    # Note: proxy.disabled is derived from cni=="cilium" in the chart template; not a separate configurable field.

    machine_install_disk: Optional[str] = None
    # Install disk path, e.g. "/dev/vda" (Proxmox virtio) or "/dev/sda" (physical/SATA).
    # Maps to values key: machine.installDisk. Chart default: /dev/vda.
    # Cat 3 — requires node replacement to change.

    talos_version: Optional[str] = None
    # Short Talos version string, e.g. "v1.12". Used in TalosControlPlane talosVersion field.
    # Must be consistent with talos_image (e.g. image v1.12.6 → talos_version v1.12).
    # Maps to values key: cluster.talos_version. Also written as roundtrip metadata.
    # Cat 1 — requires new MachineTemplate (triggers rolling update).

    # DEPRECATED — use network.cert_sans instead. Retained for backward compat.
    cert_sans: Optional[List[str]] = None
    # Additional SANs for the API server TLS certificate. IPs or hostnames.
    # Maps to values key: network.certSANs. Applied only when set and non-empty.
    # Cat 4 immutable — cert SANs baked in at bootstrap; changing requires cluster reprovision.

    # T-033 (CC-173) — InlineManifest redaction.
    # On write (provision): full {name, contents} dicts go into the values file via _render_values.
    # On read (GET /clusters): only manifest names are surfaced here; contents are never returned.
    # The values file in git contains full contents for cluster-chart CAPI provisioning.
    # API responses must never expose contents (SOPS key, fluxinstance) — see T-033 brief.
    inline_manifest_names: List[str] = []
    # Names of embedded InlineManifests. Populated on read from the values file.
    # Contents are intentionally absent — use this field to confirm which manifests are embedded.

    @model_validator(mode='before')
    @classmethod
    def _migrate_network_fields(cls, values):
        """Migrate legacy top-level fields (vip, ip_range, cni, cert_sans) into NetworkSpec.

        When 'network' is absent but legacy fields are present, construct a NetworkSpec
        from them. This allows existing values files to load without modification.
        Same pattern as StorageSpec._migrate_legacy_fields.
        """
        if not isinstance(values, dict):
            return values
        if values.get('network') is not None:
            return values
        vip = values.get('vip', None)
        ip_range = values.get('ip_range', None)
        cni = values.get('cni', None)
        cert_sans = values.get('cert_sans', None)
        if vip or ip_range:
            values['network'] = {
                'id': str(uuid4()),
                'type': cni if cni else 'flannel',
                'vip': vip or '',
                'ip_range': ip_range or '',
                'cert_sans': cert_sans,
            }
        return values


class ClusterSuspendResponse(BaseModel):
    name: str
    pr_url: str


class ClusterDecommissionResponse(BaseModel):
    name: str
    pr_url: str
    archived_repos: List[str]


class IngressConnectorResponse(BaseModel):
    name: str
    apps_pr_url: str
    infra_pr_url: str


class StorageClassesResponse(BaseModel):
    name: str
    infra_pr_url: str
    backends: List[str]  # e.g. ["nfs", "iscsi"]


class GatewayWireResponse(BaseModel):
    name: str
    infra_pr_url: str
    public_hosts: List[str]   # HTTP-80 listeners generated
    internal_hosts: List[str] # HTTPS-443 listeners generated; wildcard cert if non-empty


class ClusterStatus(BaseModel):
    flux_status: Optional[str] = None
    k8s_version: Optional[str] = None
    node_count: Optional[int] = None
    last_reconcile: Optional[str] = None


class ClusterResponse(BaseModel):
    name: str
    spec: ClusterSpec
    status: Optional[ClusterStatus] = None
    pr_url: Optional[str] = None
