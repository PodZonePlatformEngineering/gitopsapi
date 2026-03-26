from fastapi import APIRouter, HTTPException
from typing import List, Optional

from ...models.application_config import (
    ApplicationDeployment,
    ApplicationDeploymentResponse,
    PatchApplicationDeployment,
)
from ...services.app_config_service import AppConfigService
from ..auth import require_role

router = APIRouter(tags=["application-deployments"])


# ── /application-deployments (canonical) ────────────────────────────────────

@router.post("/application-deployments", response_model=ApplicationDeploymentResponse, status_code=202)
async def assign_application_to_cluster(
    spec: ApplicationDeployment,
    _=require_role("cluster_operator", "security_admin"),
):
    svc = AppConfigService()
    return await svc.create(spec)


@router.get("/application-deployments", response_model=List[ApplicationDeploymentResponse])
async def list_application_deployments(
    application: Optional[str] = None,
    cluster: Optional[str] = None,
    _=require_role("cluster_operator", "build_manager", "senior_developer", "security_admin"),
):
    if not application and not cluster:
        raise HTTPException(
            status_code=400,
            detail="Provide ?application=<name> or ?cluster=<name>",
        )
    svc = AppConfigService()
    if cluster:
        return await svc.list_by_cluster(cluster)
    return await svc.list_by_application(application)


@router.patch("/application-deployments/{config_id}", response_model=ApplicationDeploymentResponse, status_code=202)
async def patch_application_deployment(
    config_id: str,
    body: PatchApplicationDeployment,
    _=require_role("cluster_operator", "build_manager", "security_admin"),
):
    svc = AppConfigService()
    return await svc.patch(config_id, body)


@router.delete("/application-deployments/{config_id}", response_model=ApplicationDeploymentResponse, status_code=202)
async def remove_application_from_cluster(
    config_id: str,
    _=require_role("cluster_operator", "security_admin"),
):
    svc = AppConfigService()
    return await svc.delete(config_id)


# ── /application-configs (backwards-compatible alias) ────────────────────────

@router.post("/application-configs", response_model=ApplicationDeploymentResponse, status_code=202,
             include_in_schema=False)
async def assign_application_to_cluster_legacy(
    spec: ApplicationDeployment,
    _=require_role("cluster_operator", "security_admin"),
):
    svc = AppConfigService()
    return await svc.create(spec)


@router.get("/application-configs", response_model=List[ApplicationDeploymentResponse],
            include_in_schema=False)
async def list_application_configs_legacy(
    application: Optional[str] = None,
    cluster: Optional[str] = None,
    _=require_role("cluster_operator", "build_manager", "senior_developer", "security_admin"),
):
    if not application and not cluster:
        raise HTTPException(
            status_code=400,
            detail="Provide ?application=<name> or ?cluster=<name>",
        )
    svc = AppConfigService()
    if cluster:
        return await svc.list_by_cluster(cluster)
    return await svc.list_by_application(application)


@router.patch("/application-configs/{config_id}", response_model=ApplicationDeploymentResponse, status_code=202,
              include_in_schema=False)
async def patch_application_config_legacy(
    config_id: str,
    body: PatchApplicationDeployment,
    _=require_role("cluster_operator", "build_manager", "security_admin"),
):
    svc = AppConfigService()
    return await svc.patch(config_id, body)


@router.delete("/application-configs/{config_id}", response_model=ApplicationDeploymentResponse, status_code=202,
               include_in_schema=False)
async def remove_application_from_cluster_legacy(
    config_id: str,
    _=require_role("cluster_operator", "security_admin"),
):
    svc = AppConfigService()
    return await svc.delete(config_id)
