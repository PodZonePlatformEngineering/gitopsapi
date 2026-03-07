from fastapi import APIRouter, Depends, HTTPException
from typing import List

from ...models.application import ApplicationSpec, ApplicationResponse
from ...services.app_service import AppService
from ..auth import require_role

router = APIRouter(tags=["applications"])


@router.post("/applications", response_model=ApplicationResponse, status_code=202)
async def add_application(
    spec: ApplicationSpec,
    _=Depends(require_role("cluster_operator")),
):
    svc = AppService()
    return await svc.create_application(spec)


@router.get("/applications", response_model=List[ApplicationResponse])
async def list_applications(_=Depends(require_role("cluster_operator", "build_manager", "senior_developer"))):
    svc = AppService()
    return await svc.list_applications()


@router.get("/applications/{name}", response_model=ApplicationResponse)
async def get_application(
    name: str,
    _=Depends(require_role("cluster_operator", "build_manager", "senior_developer")),
):
    svc = AppService()
    result = await svc.get_application(name)
    if not result:
        raise HTTPException(status_code=404, detail=f"Application {name!r} not found")
    return result
