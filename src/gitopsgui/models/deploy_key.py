"""TR-GIT-001 — Deploy key and Git access models."""

from typing import Optional
from pydantic import BaseModel


class GitAccessRequest(BaseModel):
    """Request to configure Git deploy-key access for a repository."""
    cluster: str       # Target cluster context name (e.g. "gitopsdev")
    git_url: str       # SSH clone URL (e.g. git@github.com:your-org/gitopsdev-infra.git)


class GitAccessResponse(BaseModel):
    """Response from deploy-key configuration."""
    repo_name: str
    github_key_id: int          # Deploy key ID from GitHub (0 when GITOPS_SKIP_GITHUB=1)
    secret_name: str            # K8s Secret name created in flux-system
    gitrepository_created: bool
    error: Optional[str] = None
