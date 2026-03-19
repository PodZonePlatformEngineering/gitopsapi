from pydantic import BaseModel
from typing import List, Optional


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


class PlatformSpec(BaseModel):
    name: str           # human identifier for the hypervisor/provisioning node (e.g. "erectus")
    type: str = "proxmox"  # provisioning platform type; default proxmox
    endpoint: str       # platform management API URL (e.g. "https://192.168.1.201:8006")
    nodes: List[str]    # provisioning node names (single: ["erectus"], multi: ["pve1", "pve2"])


class ClusterSpec(BaseModel):
    name: str
    platform: Optional[PlatformSpec] = None  # null for externally-managed clusters (managed_gitops=False)
    vip: str
    ip_range: str
    dimensions: ClusterDimensions
    managed_gitops: bool = True  # TR-039: platform creates/manages {cluster}-infra and {cluster}-apps repos
    gitops_repo_url: Optional[str] = None  # required when managed_gitops=False; derived when managed_gitops=True
    sops_secret_ref: str
    extra_manifests: List[str] = []  # URLs applied as Talos extra_manifests (cilium, flux, gateway-api, etc.)
    bastion: Optional[BastionSpec] = None  # if set, kubeconfig server URL is rewritten to bastion
    allow_scheduling_on_control_planes: bool = False  # enables Talos allowSchedulingOnControlPlanes; required when worker_count=0
    external_hosts: List[str] = []  # FQDNs served externally via this cluster's Gateway; drives cert-manager certs + Gateway listeners at provisioning time


class ClusterSuspendResponse(BaseModel):
    name: str
    pr_url: str


class ClusterDecommissionResponse(BaseModel):
    name: str
    pr_url: str
    archived_repos: List[str]


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
