from pydantic import BaseModel
from typing import Optional, List


class ReviewerStatus(BaseModel):
    login: str
    role: str
    approved: bool


class PRDetail(BaseModel):
    pr_number: int
    title: str
    state: str
    labels: List[str] = []
    stage: Optional[str] = None          # dev | ete | production
    resource_type: Optional[str] = None  # cluster | application | pipeline | promotion
    diff_url: str
    reviews: List[ReviewerStatus] = []
    required_approvers: List[str] = []
    approvals_satisfied: bool = False
    pr_url: str


class ApproveRequest(BaseModel):
    pass  # caller identity comes from JWT; no body needed
