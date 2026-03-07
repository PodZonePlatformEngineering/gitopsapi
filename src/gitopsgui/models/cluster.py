from pydantic import BaseModel
from typing import Optional


class ClusterDimensions(BaseModel):
    control_plane_count: int = 3
    worker_count: int = 3
    cpu_per_node: int = 4
    memory_gb_per_node: int = 16
    boot_volume_gb: int = 50


class ClusterSpec(BaseModel):
    name: str
    platform: str
    ip_range: str
    dimensions: ClusterDimensions
    gitops_repo_url: str
    sops_secret_ref: str


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
