from fastapi import APIRouter, Depends
from typing import List, Optional

from ...models.status import AggregateStatus, ClusterFluxStatus, ResourceSummary, ResourceDetail, LogResponse
from ...services.k8s_service import K8sService
from ..auth import require_role

router = APIRouter(tags=["status"])


@router.get("/status", response_model=AggregateStatus)
async def aggregate_status(_=Depends(require_role("cluster_operator", "build_manager", "senior_developer"))):
    svc = K8sService()
    return await svc.list_all_flux_status()


@router.get("/status/{cluster}", response_model=ClusterFluxStatus)
async def cluster_status(
    cluster: str,
    _=Depends(require_role("cluster_operator", "build_manager", "senior_developer")),
):
    svc = K8sService()
    return await svc.get_cluster_flux_status(cluster)


@router.get("/status/{cluster}/resources", response_model=List[ResourceSummary])
async def list_resources(
    cluster: str,
    kind: Optional[str] = None,
    namespace: Optional[str] = None,
    _=Depends(require_role("cluster_operator", "build_manager", "senior_developer")),
):
    svc = K8sService()
    return await svc.list_resources(cluster, kind=kind, namespace=namespace)


@router.get("/status/{cluster}/resources/{kind}/{namespace}/{name}", response_model=ResourceDetail)
async def describe_resource(
    cluster: str,
    kind: str,
    namespace: str,
    name: str,
    _=Depends(require_role("cluster_operator", "build_manager", "senior_developer")),
):
    svc = K8sService()
    return await svc.describe_resource(cluster, kind, namespace, name)


@router.get("/status/{cluster}/resources/{kind}/{namespace}/{name}/logs", response_model=LogResponse)
async def get_logs(
    cluster: str,
    kind: str,
    namespace: str,
    name: str,
    container: Optional[str] = None,
    tail_lines: int = 100,
    _=Depends(require_role("cluster_operator", "build_manager", "senior_developer")),
):
    svc = K8sService()
    return await svc.get_logs(cluster, namespace, name, container=container, tail_lines=tail_lines)
