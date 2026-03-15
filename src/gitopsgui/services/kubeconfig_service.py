"""
GITGUI-009 — Kubeconfig extraction service.
Fetches kubeconfigs from management cluster CAPI secrets, SOPS-encrypts, stores in gitops repo.

GITGUI-028 — Bastion URL rewrite.
When a cluster has a bastion spec, the kubeconfig server URL is rewritten from the internal
k8s API address to https://<bastion_hostname>:<api_port> before the kubeconfig is returned.
This allows users outside the cluster network to reach the API via the bastion host.
"""

import os
import re
from typing import Optional

import yaml

MGMT_KUBECONFIG_SECRET = os.environ.get("MGMT_KUBECONFIG_SECRET", "")
SOPS_AGE_KEY_SECRET = os.environ.get("SOPS_AGE_KEY_SECRET", "")

_ROLE_CLUSTER_ACCESS = {
    "cluster_operator": {"dev", "ete", "production"},
    "build_manager":    {"dev", "ete"},
    "senior_developer": {"dev", "ete"},
}


def rewrite_kubeconfig_server(kubeconfig_yaml: str, bastion_hostname: str, api_port: int = 6443) -> str:
    """GITGUI-028 — Rewrite all cluster server URLs in a kubeconfig to point at the bastion.

    Replaces the `server:` field in every cluster entry with
    https://<bastion_hostname>:<api_port>.  Certificate data is
    preserved; callers should ensure the bastion presents a cert
    trusted by the existing certificate-authority-data, or strip it
    and set insecure-skip-tls-verify if the bastion terminates TLS
    with a self-signed cert.
    """
    kc = yaml.safe_load(kubeconfig_yaml)
    for cluster_entry in kc.get("clusters", []):
        cluster_entry["cluster"]["server"] = f"https://{bastion_hostname}:{api_port}"
    return yaml.dump(kc, default_flow_style=False)


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
        """Decrypt and return kubeconfig YAML, enforcing role-based cluster access.

        If the cluster spec includes a bastion, the server URL is rewritten via
        rewrite_kubeconfig_server() before the kubeconfig is returned to the caller.
        """
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
