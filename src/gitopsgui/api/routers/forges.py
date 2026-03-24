"""CC-083 — GitForge CRUD endpoints."""

from typing import List

from fastapi import APIRouter, HTTPException

from ...models.credentials import GitForgeCreate, GitForgeResponse
from ...services.credential_store import CredentialStore
from ..auth import require_role

router = APIRouter(tags=["credentials"])


@router.post("/forges", response_model=GitForgeResponse, status_code=201)
async def create_forge(
    spec: GitForgeCreate,
    _=require_role("cluster_operator"),
):
    svc = CredentialStore()
    return await svc.create_forge(spec)


@router.get("/forges", response_model=List[GitForgeResponse])
async def list_forges(_=require_role("cluster_operator", "build_manager", "senior_developer")):
    svc = CredentialStore()
    return await svc.list_forges()


@router.get("/forges/{forge_id}", response_model=GitForgeResponse)
async def get_forge(
    forge_id: str,
    _=require_role("cluster_operator", "build_manager", "senior_developer"),
):
    svc = CredentialStore()
    result = await svc.get_forge(forge_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Forge {forge_id!r} not found")
    return result


@router.delete("/forges/{forge_id}", status_code=204)
async def delete_forge(
    forge_id: str,
    _=require_role("cluster_operator"),
):
    svc = CredentialStore()
    deleted = await svc.delete_forge(forge_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Forge {forge_id!r} not found")
