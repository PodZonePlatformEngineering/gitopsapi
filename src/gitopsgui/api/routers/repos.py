"""CC-083 — GitRepo CRUD endpoints."""

from typing import List

from fastapi import APIRouter, HTTPException

from ...models.credentials import GitRepoCreate, GitRepoResponse
from ...services.credential_store import CredentialStore
from ..auth import require_role

router = APIRouter(tags=["credentials"])


@router.post("/repos", response_model=GitRepoResponse, status_code=201)
async def create_repo(
    spec: GitRepoCreate,
    _=require_role("cluster_operator"),
):
    svc = CredentialStore()
    # Verify forge exists before accepting the repo
    forge = await svc.get_forge(spec.forge_id)
    if not forge:
        raise HTTPException(status_code=422, detail=f"Forge {spec.forge_id!r} not found")
    result = await svc.create_repo(spec)
    # Populate derived repo_url from forge
    result.repo_url = f"{forge.forge_url}/{spec.repo_name}"
    return result


@router.get("/repos", response_model=List[GitRepoResponse])
async def list_repos(_=require_role("cluster_operator", "build_manager", "senior_developer")):
    svc = CredentialStore()
    return await svc.list_repos()


@router.get("/repos/{repo_id}", response_model=GitRepoResponse)
async def get_repo(
    repo_id: str,
    _=require_role("cluster_operator", "build_manager", "senior_developer"),
):
    svc = CredentialStore()
    result = await svc.get_repo(repo_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Repo {repo_id!r} not found")
    # Populate derived repo_url
    forge = await svc.get_forge(result.forge_id)
    if forge:
        result.repo_url = f"{forge.forge_url}/{result.repo_name}"
    return result


@router.delete("/repos/{repo_id}", status_code=204)
async def delete_repo(
    repo_id: str,
    _=require_role("cluster_operator"),
):
    svc = CredentialStore()
    deleted = await svc.delete_repo(repo_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Repo {repo_id!r} not found")
