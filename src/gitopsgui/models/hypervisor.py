from pydantic import BaseModel, Field
from typing import List, Optional


class HypervisorAuditData(BaseModel):
    """Discovered state populated by the audit script (Gap D). Empty until audit run."""
    bridges: List[str] = []
    storage_pools: List[str] = []
    template_vms: List[str] = []        # e.g. ["talos-v1.12.6"]
    proxmox_nodes: List[str] = []       # discovered Proxmox cluster node names
    last_audited: Optional[str] = None  # ISO 8601 timestamp


class HypervisorSpec(BaseModel):
    name: str
    # Unique identifier — used as key in ConfigMap and in URL path.
    # Lowercase, no spaces (e.g. "mercury", "venus").

    type: str = "proxmox"
    # Hypervisor platform type. Only "proxmox" supported currently.

    endpoint: str
    # Proxmox API URL, e.g. "https://freyr:8008/" (proxied) or "https://192.168.4.52:8006/" (direct).

    host_ip: str
    # Hypervisor host IP for SSH access, e.g. "192.168.4.52".
    # Used by Gap C (SSH/SCP orchestration) to run Egg scripts remotely.

    credentials_ref: str = "capmox-manager-credentials"
    # K8s Secret name in GITOPSAPI_NAMESPACE containing Proxmox API credentials.
    # CAPMOX reads this Secret directly; gitopsapi stores only the reference.

    ssh_credentials_ref: Optional[str] = None
    # K8s Secret name containing SSH root credentials for Egg script execution (Gap C).
    # e.g. "mercury-root". Optional — only required for bootstrap operations.

    idrac_ip: Optional[str] = None
    # iDRAC IP for out-of-band management, e.g. "192.168.4.57". Informational only for now.

    idrac_credentials_ref: Optional[str] = None
    # K8s Secret name for iDRAC credentials, e.g. "mercury-idrac". Informational only.

    nodes: List[str] = Field(default_factory=list)
    # Proxmox node names allowed to schedule VMs (→ ProxmoxCluster.allowedNodes).
    # e.g. ["mercury"]. Populated manually or by audit.

    bridge: str = "vmbr0"
    # Default VM network bridge. Overridable per ClusterSpec.platform.bridge.

    default_storage_pool: str = "local-lvm"
    # Default Proxmox storage pool for VM disks. Override with confirmed pool name.
    # e.g. "zfs-pool-01" for Mercury.

    audit: HypervisorAuditData = Field(default_factory=HypervisorAuditData)
    # Discovered state. Populated by audit script via PATCH /hypervisors/{name}.
    # Empty on initial registration.


class HypervisorResponse(HypervisorSpec):
    pass


class HypervisorListResponse(BaseModel):
    items: List[HypervisorResponse]


class BootstrapConfig(BaseModel):
    """Input for POST /hypervisors/{name}/bootstrap."""

    talos_version: str = "v1.12.6"
    talos_schema_id: str = "6adc7e7fba27948460e2231e5272e88b85159da3f3db980551976bf9898ff64b"
    cluster_name: str
    # e.g. "mercury-management"

    vip: str
    # Control-plane VIP, e.g. "192.168.4.150"

    template_vmid: int = 9000
    new_vmid: int = 100
    cpu: int = 4
    memory_mb: int = 8192
    disk_gb: int = 50
    install_disk: str = "/dev/vda"
    kubernetes_version: str = "v1.34.6"

    cluster_chart_repo_url: str = "oci://ghcr.io/podzoneplatformengineering/cluster-chart"
    cluster_chart_version: str = "0.1.40"

    skip_template: bool = False
    # Set True if talos template VM already exists — skips egg-template.sh


class BootstrapStatus(BaseModel):
    """Response for POST /hypervisors/{name}/bootstrap."""

    hypervisor: str
    cluster_name: str
    status: str
    # "complete" | "failed"

    steps_completed: List[str]
    # e.g. ["audit", "template", "provision", "platform_install"]

    kubeconfig_secret_name: Optional[str] = None
    # K8s Secret name where kubeconfig was stored, if applicable

    error: Optional[str] = None
    # Set on failure
