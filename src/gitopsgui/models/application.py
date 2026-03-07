from pydantic import BaseModel
from typing import Optional


class ApplicationSpec(BaseModel):
    name: str
    cluster: str
    helm_repo_url: str
    chart_name: str
    chart_version: str
    values_yaml: str = ""
    app_repo_url: Optional[str] = None  # for hosted apps


class ApplicationStatus(BaseModel):
    helm_release_status: Optional[str] = None
    chart_version: Optional[str] = None
    last_reconcile: Optional[str] = None


class ApplicationResponse(BaseModel):
    name: str
    spec: ApplicationSpec
    status: Optional[ApplicationStatus] = None
    pr_url: Optional[str] = None
