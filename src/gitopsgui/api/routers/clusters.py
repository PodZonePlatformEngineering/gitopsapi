from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from typing import List

from ...models.cluster import ClusterSpec, ClusterResponse, ClusterSuspendResponse, ClusterDecommissionResponse, IngressConnectorResponse
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
    "/clusters/{name}/suspend",
    response_model=ClusterSuspendResponse,
    status_code=202,
    summary="Suspend Flux reconciliation for a cluster without deleting resources",
)
async def suspend_cluster(
    name: str,
    _=require_role("cluster_operator"),
):
    """PR: sets spec.suspend: true on the cluster's Kustomization in ManagementCluster/clusters.yaml.
    The cluster continues running; Flux stops reconciling. Reversible before decommission.
    """
    svc = ClusterService()
    return await svc.suspend_cluster(name)


@router.delete(
    "/clusters/{name}",
    response_model=ClusterDecommissionResponse,
    status_code=202,
    summary="Decommission a cluster: remove from git and archive repos",
)
async def decommission_cluster(
    name: str,
    _=require_role("cluster_operator"),
):
    """PR: removes cluster-chart files and the Kustomization entry from ManagementCluster/clusters.yaml.
    On merge, Flux prunes the Kustomization → HelmRelease deleted → CAPI deprovisions machines.
    The {name}-infra and {name}-apps repos are archived (read-only) before the PR is opened.
    """
    svc = ClusterService()
    try:
        return await svc.decommission_cluster(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post(
    "/clusters/{name}/gateway",
    response_model=IngressConnectorResponse,
    status_code=202,
    summary="Wire cloudflared ingress connector for a cluster (CC-068)",
)
async def wire_ingress_connector(
    name: str,
    _=require_role("cluster_operator"),
):
    """Renders cloudflared HelmRelease into {name}-apps and Flux Kustomization into {name}-infra.

    The cluster spec must have ingress_connector.enabled=true set before calling this endpoint.
    Opens two PRs: one per repo. Merge apps PR first, then infra PR.
    """
    svc = ClusterService()
    try:
        return await svc.wire_ingress_connector(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


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
