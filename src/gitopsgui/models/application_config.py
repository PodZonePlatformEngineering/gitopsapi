from pydantic import BaseModel
from typing import List, Optional


class HTTPRouteSpec(BaseModel):
    gateway_name: str
    gateway_namespace: str
    port: int = 80
    path_prefix: str = "/"


class SecretRef(BaseModel):
    name: str
    namespace: Optional[str] = None  # defaults to app namespace at deploy time


class ConfigMapRef(BaseModel):
    name: str
    namespace: Optional[str] = None  # defaults to app namespace at deploy time


class ApplicationDeployment(BaseModel):
    app_id: str
    cluster_id: str
    chart_version_override: Optional[str] = None
    values_override: str = ""
    enabled: bool = True
    pipeline_stage: Optional[str] = None  # dev | ete | production | None
    gitops_source_ref: Optional[str] = None  # external GitRepository CR name (FR-046a)
    external_hosts: List[str] = []  # hostnames routed to this app; drives HTTPRoute generation
    http_route: Optional[HTTPRouteSpec] = None  # when set + external_hosts non-empty: generates HTTPRoute manifest
    secret_refs: List[SecretRef] = []  # secrets required before HelmRelease; written as Kustomization annotations
    config_map_refs: List[ConfigMapRef] = []  # configmaps required before HelmRelease; same


class ApplicationDeploymentResponse(BaseModel):
    id: str  # <app_id>-<cluster_id>
    app_id: str
    cluster_id: str
    chart_version_override: Optional[str] = None
    values_override: str = ""
    enabled: bool = True
    pipeline_stage: Optional[str] = None
    gitops_source_ref: Optional[str] = None
    external_hosts: List[str] = []
    http_route: Optional[HTTPRouteSpec] = None
    secret_refs: List[SecretRef] = []
    config_map_refs: List[ConfigMapRef] = []
    pr_url: Optional[str] = None


class PatchApplicationDeployment(BaseModel):
    chart_version_override: Optional[str] = None
    values_override: Optional[str] = None
    enabled: Optional[bool] = None
    external_hosts: Optional[List[str]] = None
    http_route: Optional[HTTPRouteSpec] = None
    secret_refs: Optional[List[SecretRef]] = None
    config_map_refs: Optional[List[ConfigMapRef]] = None


# Backwards-compatible aliases — /api/v1/application-configs still accepted
ApplicationClusterConfig = ApplicationDeployment
ApplicationClusterConfigResponse = ApplicationDeploymentResponse
PatchApplicationClusterConfig = PatchApplicationDeployment
