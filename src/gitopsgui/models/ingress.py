"""Pydantic models for Cloudflare Tunnel ingress rule management (CC-068 Phase 2)."""

from typing import List
from pydantic import BaseModel


class IngressRule(BaseModel):
    hostname: str    # e.g. "qdrant.podzone.cloud"
    service: str     # e.g. "http://192.168.4.179:80" or "https://192.168.4.179:443"


class IngressRuleUpsert(BaseModel):
    hostname: str
    service: str


class TunnelConfig(BaseModel):
    tunnel_id: str
    ingress_rules: List[IngressRule]


class IngressRuleDeleteResponse(BaseModel):
    tunnel_id: str
    hostname: str
    ingress_rules: List[IngressRule]
