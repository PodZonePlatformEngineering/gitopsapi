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


class ClusterBootstrapRequest(BaseModel):
    """CC-053b — Request to bootstrap a newly provisioned cluster.

    Orchestrates SOPS key lifecycle + SSH deploy keys for both GitOps repos
    in a single call. The cluster must already be running and reachable via
    the CAPI management cluster before calling this endpoint.
    """
    management_sops_public_key: Optional[str] = None  # overrides MANAGEMENT_SOPS_PUBLIC_KEY env var


class ClusterBootstrapResponse(BaseModel):
    """CC-053b — Response from cluster bootstrap operation."""
    cluster_name: str
    sops_public_key: str            # Cluster SOPS age public key (safe to log)
    sops_mgmt_pr_url: Optional[str] = None  # PR on management-infra for encrypted SOPS key
    infra_key_id: int               # GitHub deploy key ID for {cluster}-infra (0 when SKIP_GITHUB)
    apps_key_id: int                # GitHub deploy key ID for {cluster}-apps (0 when SKIP_GITHUB)
    secrets_created: bool           # False when GITOPS_SKIP_K8S=1
