"""Cloudflare Tunnel ingress rule management endpoints (CC-068 Phase 2).

All routes operate against the tunnel registered on the cluster's ingress_connector.
The tunnel_id is read from the cluster spec; the Cloudflare API token is injected
via CLOUDFLARE_API_TOKEN env var (secretctl / K8s Secret at runtime).
"""

from fastapi import APIRouter, HTTPException

from ...models.ingress import IngressRuleUpsert, TunnelConfig, IngressRuleDeleteResponse
from ...services.cloudflare_service import CloudflareService
from ...services.cluster_service import ClusterService
from ..auth import require_role


router = APIRouter(tags=["ingress"])


async def _resolve_tunnel_id(name: str) -> str:
    """Return the tunnel_id from the cluster's ingress_connector spec, or raise 404/422."""
    svc = ClusterService()
    cluster = await svc.get_cluster(name)
    if cluster is None:
        raise HTTPException(status_code=404, detail=f"Cluster '{name}' not found")
    connector = cluster.spec.ingress_connector
    if not connector or not connector.tunnel_id:
        raise HTTPException(
            status_code=422,
            detail=f"Cluster '{name}' has no ingress_connector.tunnel_id configured",
        )
    return connector.tunnel_id


@router.get(
    "/clusters/{name}/ingress-rules",
    response_model=TunnelConfig,
    summary="List current Cloudflare Tunnel ingress rules for a cluster (CC-068)",
)
async def get_ingress_rules(
    name: str,
    _=require_role("cluster_operator"),
):
    """Return all ingress rules currently registered on the cluster's Cloudflare Tunnel."""
    tunnel_id = await _resolve_tunnel_id(name)
    cf = CloudflareService()
    try:
        return await cf.get_tunnel_config(tunnel_id)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cloudflare API error: {exc}") from exc


@router.put(
    "/clusters/{name}/ingress-rules",
    response_model=TunnelConfig,
    summary="Add or update a Cloudflare Tunnel ingress rule for a cluster (CC-068)",
)
async def upsert_ingress_rule(
    name: str,
    rule: IngressRuleUpsert,
    _=require_role("cluster_operator"),
):
    """Upsert a single ingress rule by hostname.

    Existing rules for other hostnames are preserved.
    The Cloudflare API requires a catch-all entry — this is appended automatically.
    """
    tunnel_id = await _resolve_tunnel_id(name)
    cf = CloudflareService()
    try:
        return await cf.upsert_rule(tunnel_id, rule.hostname, rule.service)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cloudflare API error: {exc}") from exc


@router.delete(
    "/clusters/{name}/ingress-rules/{hostname:path}",
    response_model=IngressRuleDeleteResponse,
    summary="Remove a Cloudflare Tunnel ingress rule by hostname (CC-068)",
)
async def delete_ingress_rule(
    name: str,
    hostname: str,
    _=require_role("cluster_operator"),
):
    """Remove the ingress rule for the given hostname from the cluster's tunnel."""
    tunnel_id = await _resolve_tunnel_id(name)
    cf = CloudflareService()
    try:
        result = await cf.delete_rule(tunnel_id, hostname)
        return IngressRuleDeleteResponse(
            tunnel_id=result.tunnel_id,
            hostname=hostname,
            ingress_rules=result.ingress_rules,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Cloudflare API error: {exc}") from exc
