"""
CC-083 — Credential-bearing object models.

Four objects:
  GitForge   — a Git hosting platform (GitHub, Forgejo, etc.) plus org-level token
  GitRepo    — an individual GitOps repo, FK → GitForge, optional per-repo token override
  SopsKey    — an age key pair for SOPS; private key stored in K8s Secret, never returned
  GitOpsAPIConfig — singleton installation descriptor (name + ordered forge list + admin password)

Storage: K8s ConfigMap (metadata) + K8s Secret (credentials) in GITOPSAPI_NAMESPACE.
Secret fields are excluded from all response models.
"""

from typing import List, Optional
from pydantic import BaseModel


# ---------------------------------------------------------------------------
# GitForge
# ---------------------------------------------------------------------------

class GitForgeCreate(BaseModel):
    id: str                     # unique identifier e.g. "github-motttt"
    forge_url: str              # base URL incl. org: https://github.com/MoTTTT
    git_token: str              # org-level token — stored in K8s Secret; never returned
    is_default: bool = False    # if true, used when no forge_id specified on a new repo


class GitForgeResponse(BaseModel):
    id: str
    forge_url: str
    is_default: bool


# ---------------------------------------------------------------------------
# GitRepo
# ---------------------------------------------------------------------------

class GitRepoCreate(BaseModel):
    id: str                             # unique identifier e.g. "gitopsdev-infra"
    forge_id: str                       # FK → GitForge.id
    repo_name: str                      # repo name within forge org e.g. "gitopsdev-infra"
    git_token: Optional[str] = None     # per-repo token override; falls back to forge token


class GitRepoResponse(BaseModel):
    id: str
    forge_id: str
    repo_name: str
    repo_url: Optional[str] = None      # derived: {forge.forge_url}/{repo_name}


# ---------------------------------------------------------------------------
# SopsKey
# ---------------------------------------------------------------------------

class SopsKeyCreate(BaseModel):
    id: str             # unique identifier e.g. "gitopsdev-sops"


class SopsKeyImport(BaseModel):
    """Import an existing age key pair rather than generating a new one."""
    id: str
    public_key: str
    private_key: str


class SopsKeyResponse(BaseModel):
    id: str
    public_key: str     # age public key — safe to return; private key never returned


# ---------------------------------------------------------------------------
# GitOpsAPIConfig (singleton)
# ---------------------------------------------------------------------------

class GitOpsAPIConfigUpdate(BaseModel):
    name: Optional[str] = None
    forge_ids: Optional[List[str]] = None
    admin_password: Optional[str] = None    # stored bcrypt-hashed in K8s Secret


class GitOpsAPIConfigResponse(BaseModel):
    name: str
    forge_ids: List[str]
