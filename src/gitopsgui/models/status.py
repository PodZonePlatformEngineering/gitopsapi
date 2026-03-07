from pydantic import BaseModel
from typing import Optional, List, Any, Dict


class FluxResourceStatus(BaseModel):
    name: str
    namespace: str
    kind: str
    ready: bool
    message: Optional[str] = None
    last_reconcile: Optional[str] = None


class ClusterFluxStatus(BaseModel):
    cluster: str
    kustomizations: List[FluxResourceStatus] = []
    helm_releases: List[FluxResourceStatus] = []
    helm_repositories: List[FluxResourceStatus] = []


class AggregateStatus(BaseModel):
    clusters: List[ClusterFluxStatus] = []


class ResourceSummary(BaseModel):
    name: str
    namespace: str
    kind: str
    status: Optional[str] = None
    conditions: List[Dict[str, Any]] = []


class ResourceDetail(BaseModel):
    name: str
    namespace: str
    kind: str
    labels: Dict[str, str] = {}
    annotations: Dict[str, str] = {}
    conditions: List[Dict[str, Any]] = []
    spec: Dict[str, Any] = {}
    events: List[Dict[str, Any]] = []


class LogResponse(BaseModel):
    pod: str
    container: Optional[str] = None
    lines: List[str] = []
