from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from typing import List

from ...models.cluster import ClusterSpec, ClusterResponse
from ...models.sops import SOPSBootstrapRequest, SOPSBootstrapResponse
from ...services.cluster_service import ClusterService
from ...services.kubeconfig_service import KubeconfigService
from ...services.sops_service import SOPSService
from ..auth import require_role

router = APIRouter(tags=["clusters"])


@router.post("/clusters", response_model=ClusterResponse, status_code=202)
async def provision_cluster(
    spec: ClusterSpec,
    _=require_role("cluster_operator"),
):
    svc = ClusterService()
    return await svc.create_cluster(spec)


@router.get("/clusters", response_model=List[ClusterResponse])
async def list_clusters(_=require_role("cluster_operator", "build_manager", "senior_developer")):
    svc = ClusterService()
    return await svc.list_clusters()


@router.get("/clusters/{name}", response_model=ClusterResponse)
async def get_cluster(
    name: str,
    _=require_role("cluster_operator", "build_manager", "senior_developer"),
):
    svc = ClusterService()
    result = await svc.get_cluster(name)
    if not result:
        raise HTTPException(status_code=404, detail=f"Cluster {name!r} not found")
    return result


@router.patch("/clusters/{name}", response_model=ClusterResponse, status_code=202)
async def update_cluster(
    name: str,
    spec: ClusterSpec,
    _=require_role("cluster_operator"),
):
    svc = ClusterService()
    return await svc.update_cluster(name, spec)


@router.get("/clusters/{name}/kubeconfig")
async def get_kubeconfig(name: str, caller=require_role("cluster_operator", "build_manager", "senior_developer")):
    svc = KubeconfigService()
    kubeconfig = await svc.get_kubeconfig(name, caller.role)
    return Response(
        content=kubeconfig,
        media_type="application/x-yaml",
        headers={"Content-Disposition": f'attachment; filename="{name}-kubeconfig.yaml"'},
    )


@router.post(
    "/clusters/{name}/sops-bootstrap",
    response_model=SOPSBootstrapResponse,
    status_code=201,
    summary="Bootstrap SOPS key lifecycle for a cluster (TR-SOPS-002)",
)
async def sops_bootstrap(
    name: str,
    request: SOPSBootstrapRequest,
    _=require_role("cluster_operator"),
):
    """Generate a SOPS age key for the cluster, encrypt it with the management cluster key,
    commit the encrypted key to management-infra, install the private key as a K8s Secret
    in flux-system, and write .sops.yaml to the cluster-infra repo.
    """
    svc = SOPSService()
    try:
        return await svc.sops_bootstrap(name, request)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
