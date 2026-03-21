"""
GITGUI-009 — Kubeconfig extraction service.
Fetches kubeconfigs from management cluster CAPI secrets and returns them to callers.

GITGUI-028 — Bastion URL rewrite.
When a cluster has a bastion spec, the kubeconfig server URL is rewritten from the internal
k8s API address to https://<bastion_hostname>:<api_port> before the kubeconfig is returned.
This allows users outside the cluster network to reach the API via the bastion host.

Implementation:
  - Management cluster kubeconfig is written to MGMT_KUBECONFIG_PATH on startup
    from the MGMT_KUBECONFIG_SECRET env var (see api/main.py lifespan).
  - CAPI stores a {cluster}-kubeconfig secret in namespace {cluster} on the
    management cluster. The secret key is 'value' (cluster.x-k8s.io/secret type).
  - Bastion spec (hostname + api_port) is read from the cluster values YAML via
    ClusterService, then applied to rewrite the server URL.

Role-based access:
  cluster_operator — all clusters (dev, ete, production)
  build_manager    — dev + ete only
  senior_developer — dev + ete only
"""

import os
from typing import Optional

import yaml

MGMT_KUBECONFIG_PATH = os.environ.get("MGMT_KUBECONFIG_PATH", "/tmp/mgmt-kubeconfig")

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


def _cluster_type_from_name(cluster_name: str) -> str:
    """Derive pipeline type from cluster name suffix.

    Convention:
      *dev  → 'dev'
      *ete  → 'ete'
      *prod → 'production'
      anything else (platform-services, management, agentsonly, …) → 'production'
    """
    name = cluster_name.lower()
    if name.endswith("dev"):
        return "dev"
    if name.endswith("ete"):
        return "ete"
    return "production"


class KubeconfigService:
    async def extract_kubeconfig(self, cluster_name: str) -> str:
        """Read {cluster_name}-kubeconfig secret from management cluster via CAPI.

        The secret lives in namespace {cluster_name} and has type cluster.x-k8s.io/secret.
        The kubeconfig YAML is stored under the 'value' key (already base64-decoded by
        the kubernetes client).

        The kubernetes client is synchronous; it runs in a thread pool via asyncio.to_thread
        to avoid blocking the event loop.
        """
        import asyncio
        import base64
        from fastapi import HTTPException

        if not os.path.exists(MGMT_KUBECONFIG_PATH):
            raise HTTPException(
                status_code=503,
                detail="Management cluster kubeconfig not available — MGMT_KUBECONFIG_SECRET not set",
            )

        def _fetch() -> bytes:
            from kubernetes import client, config as k8s_config  # type: ignore
            k8s_config.load_kube_config(config_file=MGMT_KUBECONFIG_PATH)
            v1 = client.CoreV1Api()
            secret_name = f"{cluster_name}-kubeconfig"
            secret = v1.read_namespaced_secret(name=secret_name, namespace=cluster_name)
            raw = secret.data.get("value")
            if not raw:
                raise ValueError(f"Secret '{secret_name}' has no 'value' key")
            return base64.b64decode(raw)

        try:
            data = await asyncio.to_thread(_fetch)
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=str(exc))
        except Exception as exc:
            secret_name = f"{cluster_name}-kubeconfig"
            raise HTTPException(
                status_code=404,
                detail=f"CAPI kubeconfig secret '{secret_name}' not found in namespace '{cluster_name}': {exc}",
            )

        return data.decode()

    async def get_kubeconfig(self, cluster_name: str, caller_role: str) -> str:
        """Return kubeconfig YAML for cluster_name, enforcing role-based access.

        Server URL is rewritten to the bastion address if the cluster spec
        includes a bastion (GITGUI-028).
        """
        from fastapi import HTTPException
        from .cluster_service import ClusterService

        cluster_type = _cluster_type_from_name(cluster_name)
        permitted = _ROLE_CLUSTER_ACCESS.get(caller_role, set())
        if cluster_type not in permitted:
            raise HTTPException(
                status_code=403,
                detail=f"Role {caller_role!r} cannot access kubeconfig for {cluster_type!r} clusters",
            )

        kubeconfig_yaml = await self.extract_kubeconfig(cluster_name)

        # Rewrite server URL to bastion if the cluster spec has one
        cluster_svc = ClusterService()
        cluster = await cluster_svc.get_cluster(cluster_name)
        if cluster and cluster.spec.bastion:
            bastion = cluster.spec.bastion
            kubeconfig_yaml = rewrite_kubeconfig_server(
                kubeconfig_yaml,
                bastion_hostname=bastion.ip,
                api_port=bastion.api_port,
            )

        return kubeconfig_yaml
