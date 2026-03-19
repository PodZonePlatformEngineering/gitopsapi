from fastapi import APIRouter, Depends, HTTPException, Query
from typing import List, Optional

from ...models.pr import PRDetail
from ...services.git_service import SKIP_APPROVAL_CHECK
from ...services.github_service import GitHubService
from ..auth import require_role, CallerInfo

router = APIRouter(tags=["prs"])

_STAGE_APPROVERS = {
    "dev": {"build_manager"},
    "ete": {"build_manager"},
    "production": {"build_manager", "cluster_operator"},
}


@router.get("/prs", response_model=List[PRDetail])
async def list_prs(
    state: Optional[str] = Query("open", description="open | closed | all"),
    label: Optional[str] = Query(None),
    _=require_role("cluster_operator", "build_manager", "senior_developer"),
):
    svc = GitHubService()
    return await svc.list_prs(state=state, label=label)


@router.get("/prs/{pr_number}", response_model=PRDetail)
async def get_pr(
    pr_number: int,
    _=require_role("cluster_operator", "build_manager", "senior_developer"),
):
    svc = GitHubService()
    result = await svc.get_pr(pr_number)
    if not result:
        raise HTTPException(status_code=404, detail=f"PR #{pr_number} not found")
    return result


@router.post("/prs/{pr_number}/approve", status_code=204)
async def approve_pr(pr_number: int, caller: CallerInfo = require_role("cluster_operator", "build_manager")):
    svc = GitHubService()
    pr = await svc.get_pr(pr_number)
    if not pr:
        raise HTTPException(status_code=404, detail=f"PR #{pr_number} not found")

    stage = pr.stage or ""
    permitted = _STAGE_APPROVERS.get(stage, {"cluster_operator"})
    if caller.role not in permitted:
        raise HTTPException(
            status_code=403,
            detail=f"Role {caller.role!r} cannot approve {stage!r} stage PRs",
        )

    await svc.approve_pr(pr_number, caller.username)


@router.post("/prs/{pr_number}/merge", status_code=204)
async def merge_pr(
    pr_number: int,
    caller: CallerInfo = require_role("cluster_operator", "build_manager"),
):
    svc = GitHubService()
    pr = await svc.get_pr(pr_number)
    if not pr:
        raise HTTPException(status_code=404, detail=f"PR #{pr_number} not found")
    if not pr.approvals_satisfied and not SKIP_APPROVAL_CHECK:
        raise HTTPException(status_code=409, detail="Required approvals not yet satisfied")

    await svc.merge_pr(pr_number)
