"""TR-GIT-001 — Repository Git access configuration endpoints."""

from fastapi import APIRouter, HTTPException

from ...models.deploy_key import GitAccessRequest, GitAccessResponse
from ...services.deploy_key_service import DeployKeyService
from ..auth import require_role

router = APIRouter(tags=["repositories"])


@router.post(
    "/repositories/{repo_name}/configure-git-access",
    response_model=GitAccessResponse,
    status_code=201,
    summary="Configure Git deploy-key access for a repository (TR-GIT-001)",
)
async def configure_repository_git_access(
    repo_name: str,
    request: GitAccessRequest,
    _=require_role("cluster_operator"),
):
    """Generate an SSH deploy key, upload it to GitHub, create a K8s Secret in
    flux-system, and create a Flux GitRepository CR on the target cluster.

    Idempotent: if the Secret or GitRepository CR already exists it is replaced.
    """
    svc = DeployKeyService()
    try:
        return await svc.configure_repository_access(repo_name, request)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
