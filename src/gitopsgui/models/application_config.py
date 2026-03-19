from pydantic import BaseModel
from typing import List, Optional


class ApplicationClusterConfig(BaseModel):
    app_id: str
    cluster_id: str
    chart_version_override: Optional[str] = None
    values_override: str = ""
    enabled: bool = True
    pipeline_stage: Optional[str] = None  # dev | ete | production | None
    gitops_source_ref: Optional[str] = None  # external GitRepository CR name (FR-046a)
    external_hosts: List[str] = []  # subset of cluster.external_hosts routed to this app; drives HTTPRoute


class ApplicationClusterConfigResponse(BaseModel):
    id: str  # <app_id>-<cluster_id>
    app_id: str
    cluster_id: str
    chart_version_override: Optional[str] = None
    values_override: str = ""
    enabled: bool = True
    pipeline_stage: Optional[str] = None
    gitops_source_ref: Optional[str] = None
    external_hosts: List[str] = []
    pr_url: Optional[str] = None


class PatchApplicationClusterConfig(BaseModel):
    chart_version_override: Optional[str] = None
    values_override: Optional[str] = None
    enabled: Optional[bool] = None
    external_hosts: Optional[List[str]] = None
