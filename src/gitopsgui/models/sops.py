"""TR-SOPS-002 — SOPS key lifecycle models."""

from typing import Optional
from pydantic import BaseModel


class SOPSBootstrapRequest(BaseModel):
    """Request to automate SOPS key generation and Flux bootstrap for a cluster."""
    management_sops_public_key: Optional[str] = None  # overrides MANAGEMENT_SOPS_PUBLIC_KEY env var


class SOPSBootstrapResponse(BaseModel):
    """Response from SOPS bootstrap operation."""
    cluster_name: str
    sops_public_key: str
    encrypted_key_path: str     # path in management-infra repo (e.g. sops-keys/gitopsdev.agekey.enc)
    secret_created: bool        # False when GITOPS_SKIP_K8S=1
    sops_yaml_committed: bool
    error: Optional[str] = None
