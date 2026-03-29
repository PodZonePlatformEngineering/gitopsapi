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

    iscsi: bool = False
    # iSCSI target reachable from this cluster's network.
    # Enables democratic-csi iSCSI StorageClass + Talos iscsi-tools extension. Cat 3 change.

    s3: bool = False
    # S3-compatible store reachable (MinIO or external).
    # Enables S3 bucket provisioner StorageClass deployment. Cat 2 change.


class PlatformSpec(BaseModel):
    name: str                # human identifier for the hypervisor (e.g. "venus", "saturn")
    type: str = "proxmox"   # provisioning platform type; only "proxmox" supported
    endpoint: str            # Proxmox API URL (e.g. "https://192.168.4.50:8006")
    nodes: List[str]         # Proxmox node names allowed to schedule VMs (→ ProxmoxCluster.allowedNodes)
    talos_template: TalosTemplateSpec = Field(default_factory=TalosTemplateSpec)
    credentials_ref: str = "capmox-manager-credentials"  # K8s secret name with CAPMOX API credentials
    bridge: str = "vmbr0"               # Proxmox VM network bridge
    capabilities: PlatformCapabilities = Field(default_factory=PlatformCapabilities)


class ClusterSpec(BaseModel):
    name: str
    platform: Optional[PlatformSpec] = None  # null for externally-managed clusters (managed_gitops=False)
    vip: str
    ip_range: str
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
