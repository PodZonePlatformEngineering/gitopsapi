"""CloudflareService — Cloudflare Tunnel Configurations API integration (CC-068 Phase 2).

Manages tunnel ingress rules via:
  PUT  /accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations
  GET  /accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations

Environment variables:
  CLOUDFLARE_API_TOKEN   — Cloudflare API token with Tunnel:Edit permission
  CLOUDFLARE_ACCOUNT_ID  — Cloudflare account ID

Skip flag:
  GITOPS_SKIP_CLOUDFLARE=1  — return stubs (dev / CI environments)
"""

import os
from typing import List

import httpx

from gitopsgui.models.ingress import IngressRule, TunnelConfig


_CF_API_BASE = "https://api.cloudflare.com/client/v4"


def _skip_cloudflare() -> bool:
    return os.environ.get("GITOPS_SKIP_CLOUDFLARE", "") == "1"


def _cf_headers() -> dict:
    token = os.environ.get("CLOUDFLARE_API_TOKEN", "")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


def _account_id() -> str:
    return os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")


class CloudflareService:
    """Manage Cloudflare Tunnel ingress rules via the Configurations API."""

    async def get_tunnel_config(self, tunnel_id: str) -> TunnelConfig:
        """Return the current ingress rules for a tunnel."""
        if _skip_cloudflare():
            return TunnelConfig(tunnel_id=tunnel_id, ingress_rules=[])

        account_id = _account_id()
        url = f"{_CF_API_BASE}/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url, headers=_cf_headers())
            resp.raise_for_status()
        data = resp.json()
        ingress = data.get("result", {}).get("config", {}).get("ingress", [])
        rules = [
            IngressRule(hostname=r["hostname"], service=r["service"])
            for r in ingress
            if r.get("hostname")  # skip the catch-all entry (no hostname)
        ]
        return TunnelConfig(tunnel_id=tunnel_id, ingress_rules=rules)

    async def put_tunnel_config(self, tunnel_id: str, rules: List[IngressRule]) -> TunnelConfig:
        """Replace all ingress rules for a tunnel.

        The Cloudflare API requires a catch-all entry as the last rule.
        """
        if _skip_cloudflare():
            return TunnelConfig(tunnel_id=tunnel_id, ingress_rules=rules)

        account_id = _account_id()
        url = f"{_CF_API_BASE}/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations"
        payload = {
            "config": {
                "ingress": [
                    {"hostname": r.hostname, "service": r.service}
                    for r in rules
                ]
                + [{"service": "http_status:404"}]  # required catch-all
            }
        }
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.put(url, headers=_cf_headers(), json=payload)
            resp.raise_for_status()
        data = resp.json()
        ingress = data.get("result", {}).get("config", {}).get("ingress", [])
        result_rules = [
            IngressRule(hostname=r["hostname"], service=r["service"])
            for r in ingress
            if r.get("hostname")
        ]
        return TunnelConfig(tunnel_id=tunnel_id, ingress_rules=result_rules)

    async def upsert_rule(
        self,
        tunnel_id: str,
        hostname: str,
        service: str,
    ) -> TunnelConfig:
        """Add or update a single ingress rule, preserving existing rules."""
        current = await self.get_tunnel_config(tunnel_id)
        rules = [r for r in current.ingress_rules if r.hostname != hostname]
        rules.append(IngressRule(hostname=hostname, service=service))
        return await self.put_tunnel_config(tunnel_id, rules)

    async def delete_rule(self, tunnel_id: str, hostname: str) -> TunnelConfig:
        """Remove a single ingress rule by hostname."""
        current = await self.get_tunnel_config(tunnel_id)
        rules = [r for r in current.ingress_rules if r.hostname != hostname]
        return await self.put_tunnel_config(tunnel_id, rules)
