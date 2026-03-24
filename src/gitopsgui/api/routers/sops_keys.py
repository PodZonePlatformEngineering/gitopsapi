"""CC-083 — SopsKey CRUD endpoints.

POST /sops-keys/{id}        — generate a new age key pair and store it
POST /sops-keys/{id}/import — import an existing age key pair
GET  /sops-keys             — list all sops keys (public keys only)
GET  /sops-keys/{id}        — get sops key by id (public key only)
DELETE /sops-keys/{id}      — delete sops key

Private keys are never returned via API; they are available internally via
CredentialStore.get_sops_private_key() for cluster bootstrap operations.
"""

from typing import List

from fastapi import APIRouter, HTTPException

from ...models.credentials import SopsKeyImport, SopsKeyResponse
from ...services.credential_store import CredentialStore
from ..auth import require_role

router = APIRouter(tags=["credentials"])


@router.post("/sops-keys/{key_id}", response_model=SopsKeyResponse, status_code=201)
async def generate_sops_key(
    key_id: str,
    _=require_role("cluster_operator"),
):
    """Generate a new age key pair for the given id and store it encrypted."""
    svc = CredentialStore()
    existing = await svc.get_sops_key(key_id)
    if existing:
        raise HTTPException(status_code=409, detail=f"Sops key {key_id!r} already exists")
    return await svc.generate_sops_key(key_id)


@router.post("/sops-keys/{key_id}/import", response_model=SopsKeyResponse, status_code=201)
async def import_sops_key(
    key_id: str,
    spec: SopsKeyImport,
    _=require_role("cluster_operator"),
):
    """Import an existing age key pair."""
    if spec.id != key_id:
        raise HTTPException(status_code=422, detail="key_id in path must match id in body")
    svc = CredentialStore()
    return await svc.import_sops_key(spec)


@router.get("/sops-keys", response_model=List[SopsKeyResponse])
async def list_sops_keys(_=require_role("cluster_operator", "build_manager", "senior_developer")):
    svc = CredentialStore()
    return await svc.list_sops_keys()


@router.get("/sops-keys/{key_id}", response_model=SopsKeyResponse)
async def get_sops_key(
    key_id: str,
    _=require_role("cluster_operator", "build_manager", "senior_developer"),
):
    svc = CredentialStore()
    result = await svc.get_sops_key(key_id)
    if not result:
        raise HTTPException(status_code=404, detail=f"Sops key {key_id!r} not found")
    return result


@router.delete("/sops-keys/{key_id}", status_code=204)
async def delete_sops_key(
    key_id: str,
    _=require_role("cluster_operator"),
):
    svc = CredentialStore()
    deleted = await svc.delete_sops_key(key_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Sops key {key_id!r} not found")
