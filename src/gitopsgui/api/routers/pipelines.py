from fastapi import APIRouter, Depends, HTTPException
from typing import List

from ...models.pipeline import (
    PipelineSpec, PipelineResponse, ChangeSpec,
    DeploymentRecord, TestResult, PromoteRequest,
)
from ...services.pipeline_service import PipelineService
from ..auth import require_role

router = APIRouter(tags=["pipelines"])


@router.post("/pipelines", response_model=PipelineResponse, status_code=202)
async def create_pipeline(
    spec: PipelineSpec,
    _=Depends(require_role("build_manager")),
):
    svc = PipelineService()
    return await svc.create_pipeline(spec)


@router.get("/pipelines", response_model=List[PipelineResponse])
async def list_pipelines(_=Depends(require_role("cluster_operator", "build_manager", "senior_developer"))):
    svc = PipelineService()
    return await svc.list_pipelines()


@router.get("/pipelines/{name}", response_model=PipelineResponse)
async def get_pipeline(
    name: str,
    _=Depends(require_role("cluster_operator", "build_manager", "senior_developer")),
):
    svc = PipelineService()
    result = await svc.get_pipeline(name)
    if not result:
        raise HTTPException(status_code=404, detail=f"Pipeline {name!r} not found")
    return result


@router.post("/pipelines/{name}/changes", response_model=PipelineResponse, status_code=202)
async def add_change(
    name: str,
    change: ChangeSpec,
    _=Depends(require_role("build_manager")),
):
    svc = PipelineService()
    return await svc.create_change(name, change)


@router.get("/pipelines/{name}/history", response_model=List[DeploymentRecord])
async def get_history(
    name: str,
    _=Depends(require_role("cluster_operator", "build_manager", "senior_developer")),
):
    svc = PipelineService()
    return await svc.get_history(name)


@router.get("/pipelines/{name}/history/{release_id}/tests", response_model=TestResult)
async def get_test_results(
    name: str,
    release_id: str,
    _=Depends(require_role("cluster_operator", "build_manager", "senior_developer")),
):
    svc = PipelineService()
    return await svc.get_test_results(name, release_id)


@router.post("/pipelines/{name}/promote", response_model=PipelineResponse, status_code=202)
async def promote(
    name: str,
    req: PromoteRequest,
    _=Depends(require_role("build_manager")),
):
    svc = PipelineService()
    return await svc.promote(name, req.target_stage)
