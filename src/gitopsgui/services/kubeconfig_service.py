"""
GITGUI-009 — Kubeconfig extraction service.
Fetches kubeconfigs from management cluster CAPI secrets, SOPS-encrypts, stores in gitops repo.
"""

import os
from typing import Optional

MGMT_KUBECONFIG_SECRET = os.environ.get("MGMT_KUBECONFIG_SECRET", "")
SOPS_AGE_KEY_SECRET = os.environ.get("SOPS_AGE_KEY_SECRET", "")

_ROLE_CLUSTER_ACCESS = {
    "cluster_operator": {"dev", "ete", "production"},
    "build_manager":    {"dev", "ete"},
    "senior_developer": {"dev", "ete"},
}


class KubeconfigService:
    async def extract_kubeconfig(self, cluster_name: str) -> str:
        """Read <cluster_name>-kubeconfig secret from management cluster via CAPI."""
        raise NotImplementedError

    async def sops_encrypt(self, kubeconfig_yaml: str) -> str:
        """Encrypt kubeconfig with repo age key."""
        raise NotImplementedError

    async def store_kubeconfig(self, cluster_name: str, encrypted_kubeconfig: str) -> str:
        """Write clusters/<name>/kubeconfig.sops.yaml via PR; returns PR URL."""
        raise NotImplementedError

    async def get_kubeconfig(self, cluster_name: str, caller_role: str) -> str:
        """Decrypt and return kubeconfig YAML, enforcing role-based cluster access."""
        cluster_type = await self._resolve_cluster_type(cluster_name)
        permitted = _ROLE_CLUSTER_ACCESS.get(caller_role, set())
        if cluster_type not in permitted:
            from fastapi import HTTPException
            raise HTTPException(
                status_code=403,
                detail=f"Role {caller_role!r} cannot access kubeconfig for {cluster_type!r} clusters",
            )
        raise NotImplementedError

    async def _resolve_cluster_type(self, cluster_name: str) -> str:
        """Return 'dev', 'ete', or 'production' based on pipeline membership."""
        raise NotImplementedError
