from fastapi import APIRouter, HTTPException

from ...models.hypervisor import HypervisorSpec, HypervisorResponse, HypervisorListResponse
from ...services.hypervisor_service import HypervisorService
from ..auth import require_role

router = APIRouter(tags=["hypervisors"])


@router.post("/hypervisors", response_model=HypervisorResponse, status_code=201)
async def create_hypervisor(
    spec: HypervisorSpec,
    _=require_role("cluster_operator"),
):
    svc = HypervisorService()
    try:
        return await svc.create(spec)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/hypervisors", response_model=HypervisorListResponse)
async def list_hypervisors(_=require_role("cluster_operator", "build_manager", "senior_developer")):
    svc = HypervisorService()
    return await svc.list()


@router.get("/hypervisors/{name}", response_model=HypervisorResponse)
async def get_hypervisor(
    name: str,
    _=require_role("cluster_operator", "build_manager", "senior_developer"),
):
    svc = HypervisorService()
    result = await svc.get(name)
    if not result:
        raise HTTPException(status_code=404, detail=f"Hypervisor {name!r} not found")
    return result


@router.patch("/hypervisors/{name}", response_model=HypervisorResponse, status_code=200)
async def update_hypervisor(
    name: str,
    spec: HypervisorSpec,
    _=require_role("cluster_operator"),
):
    svc = HypervisorService()
    try:
        return await svc.update(name, spec)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/hypervisors/{name}", status_code=204)
async def delete_hypervisor(
    name: str,
    _=require_role("cluster_operator"),
):
    svc = HypervisorService()
    try:
        await svc.delete(name)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
