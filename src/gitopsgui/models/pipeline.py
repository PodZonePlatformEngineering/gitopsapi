from pydantic import BaseModel
from typing import Optional, List


class PipelineSpec(BaseModel):
    name: str
    dev_cluster_id: str
    ete_cluster_id: str
    prod_cluster_id: str
    app_id: str
    chart_version: str
    release_id: str


class ChangeSpec(BaseModel):
    change_request_id: str
    change_name: str
    description: str
    app_branch: str


class DeploymentRecord(BaseModel):
    release_id: str
    stage: str
    status: str
    timestamp: str
    pr_url: Optional[str] = None
    chart_version: Optional[str] = None


class TestResult(BaseModel):
    release_id: str
    passed: int
    failed: int
    test_cases: List[dict] = []
    raw_output: Optional[str] = None


class PipelineResponse(BaseModel):
    name: str
    spec: PipelineSpec
    pr_url: Optional[str] = None


class PromoteRequest(BaseModel):
    target_stage: str  # dev | ete | production
