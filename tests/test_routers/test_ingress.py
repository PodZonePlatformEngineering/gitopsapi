"""
Router tests for /api/v1/clusters/{name}/ingress-rules (CC-068 Phase 2).

Covers: happy paths, 404/422/502 error branches, and role enforcement.
CloudflareService and ClusterService are patched — no real API calls.
"""

import pytest
from unittest.mock import AsyncMock, patch

from tests.conftest import CLUSTER_OP_HEADERS, NO_ROLE_HEADERS

from gitopsgui.models.cluster import (
    ClusterResponse,
    ClusterSpec,
    ClusterDimensions,
    IngressConnectorSpec,
)
from gitopsgui.models.ingress import IngressRule, TunnelConfig, IngressRuleDeleteResponse


TUNNEL_ID = "71e24b2a-94c2-4064-bf4e-137150356331"

_DIMENSIONS = ClusterDimensions(
    control_plane_count=1,
    worker_count=1,
    cpu_per_node=2,
    memory_gb_per_node=4,
    boot_volume_gb=20,
)

_SPEC_WITH_TUNNEL = ClusterSpec(
    name="test-cluster",
    vip="192.168.1.100",
    ip_range="192.168.1.101-192.168.1.107",
    dimensions=_DIMENSIONS,
    sops_secret_ref="sops-key",
    ingress_connector=IngressConnectorSpec(tunnel_id=TUNNEL_ID),
)

_SPEC_NO_TUNNEL = ClusterSpec(
    name="test-cluster",
    vip="192.168.1.100",
    ip_range="192.168.1.101-192.168.1.107",
    dimensions=_DIMENSIONS,
    sops_secret_ref="sops-key",
    ingress_connector=None,
)

_CLUSTER_WITH_TUNNEL = ClusterResponse(name="test-cluster", spec=_SPEC_WITH_TUNNEL)
_CLUSTER_NO_TUNNEL = ClusterResponse(name="test-cluster", spec=_SPEC_NO_TUNNEL)

_RULE = IngressRule(hostname="qdrant.podzone.cloud", service="http://192.168.4.179:80")
_TUNNEL_CONFIG = TunnelConfig(tunnel_id=TUNNEL_ID, ingress_rules=[_RULE])
_EMPTY_CONFIG = TunnelConfig(tunnel_id=TUNNEL_ID, ingress_rules=[])


# ---------------------------------------------------------------------------
# GET /api/v1/clusters/{name}/ingress-rules
# ---------------------------------------------------------------------------

def test_get_ingress_rules_returns_config(client):
    with patch(
        "gitopsgui.api.routers.ingress.ClusterService.get_cluster",
        new=AsyncMock(return_value=_CLUSTER_WITH_TUNNEL),
    ), patch(
        "gitopsgui.api.routers.ingress.CloudflareService.get_tunnel_config",
        new=AsyncMock(return_value=_TUNNEL_CONFIG),
    ):
        r = client.get("/api/v1/clusters/test-cluster/ingress-rules", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["tunnel_id"] == TUNNEL_ID
    assert len(body["ingress_rules"]) == 1
    assert body["ingress_rules"][0]["hostname"] == "qdrant.podzone.cloud"


def test_get_ingress_rules_cluster_not_found(client):
    with patch(
        "gitopsgui.api.routers.ingress.ClusterService.get_cluster",
        new=AsyncMock(return_value=None),
    ):
        r = client.get("/api/v1/clusters/missing/ingress-rules", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 404


def test_get_ingress_rules_no_tunnel_id(client):
    with patch(
        "gitopsgui.api.routers.ingress.ClusterService.get_cluster",
        new=AsyncMock(return_value=_CLUSTER_NO_TUNNEL),
    ):
        r = client.get("/api/v1/clusters/test-cluster/ingress-rules", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 422


def test_get_ingress_rules_cloudflare_error_returns_502(client):
    with patch(
        "gitopsgui.api.routers.ingress.ClusterService.get_cluster",
        new=AsyncMock(return_value=_CLUSTER_WITH_TUNNEL),
    ), patch(
        "gitopsgui.api.routers.ingress.CloudflareService.get_tunnel_config",
        new=AsyncMock(side_effect=RuntimeError("CF unreachable")),
    ):
        r = client.get("/api/v1/clusters/test-cluster/ingress-rules", headers=CLUSTER_OP_HEADERS)
    assert r.status_code == 502


def test_get_ingress_rules_no_auth(no_auth_client):
    r = no_auth_client.get("/api/v1/clusters/test-cluster/ingress-rules", headers=NO_ROLE_HEADERS)
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# PUT /api/v1/clusters/{name}/ingress-rules
# ---------------------------------------------------------------------------

def test_upsert_ingress_rule_returns_updated_config(client):
    updated = TunnelConfig(tunnel_id=TUNNEL_ID, ingress_rules=[_RULE])
    with patch(
        "gitopsgui.api.routers.ingress.ClusterService.get_cluster",
        new=AsyncMock(return_value=_CLUSTER_WITH_TUNNEL),
    ), patch(
        "gitopsgui.api.routers.ingress.CloudflareService.upsert_rule",
        new=AsyncMock(return_value=updated),
    ):
        r = client.put(
            "/api/v1/clusters/test-cluster/ingress-rules",
            headers=CLUSTER_OP_HEADERS,
            json={"hostname": "qdrant.podzone.cloud", "service": "http://192.168.4.179:80"},
        )
    assert r.status_code == 200
    body = r.json()
    assert body["tunnel_id"] == TUNNEL_ID
    assert body["ingress_rules"][0]["hostname"] == "qdrant.podzone.cloud"


def test_upsert_ingress_rule_cluster_not_found(client):
    with patch(
        "gitopsgui.api.routers.ingress.ClusterService.get_cluster",
        new=AsyncMock(return_value=None),
    ):
        r = client.put(
            "/api/v1/clusters/missing/ingress-rules",
            headers=CLUSTER_OP_HEADERS,
            json={"hostname": "foo.example.com", "service": "http://1.2.3.4:80"},
        )
    assert r.status_code == 404


def test_upsert_ingress_rule_no_auth(no_auth_client):
    r = no_auth_client.put(
        "/api/v1/clusters/test-cluster/ingress-rules",
        headers=NO_ROLE_HEADERS,
        json={"hostname": "foo.example.com", "service": "http://1.2.3.4:80"},
    )
    assert r.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /api/v1/clusters/{name}/ingress-rules/{hostname}
# ---------------------------------------------------------------------------

def test_delete_ingress_rule_returns_updated_config(client):
    delete_response = IngressRuleDeleteResponse(
        tunnel_id=TUNNEL_ID,
        hostname="qdrant.podzone.cloud",
        ingress_rules=[],
    )
    with patch(
        "gitopsgui.api.routers.ingress.ClusterService.get_cluster",
        new=AsyncMock(return_value=_CLUSTER_WITH_TUNNEL),
    ), patch(
        "gitopsgui.api.routers.ingress.CloudflareService.delete_rule",
        new=AsyncMock(return_value=_EMPTY_CONFIG),
    ):
        r = client.delete(
            "/api/v1/clusters/test-cluster/ingress-rules/qdrant.podzone.cloud",
            headers=CLUSTER_OP_HEADERS,
        )
    assert r.status_code == 200
    body = r.json()
    assert body["tunnel_id"] == TUNNEL_ID
    assert body["hostname"] == "qdrant.podzone.cloud"
    assert body["ingress_rules"] == []


def test_delete_ingress_rule_cluster_not_found(client):
    with patch(
        "gitopsgui.api.routers.ingress.ClusterService.get_cluster",
        new=AsyncMock(return_value=None),
    ):
        r = client.delete(
            "/api/v1/clusters/missing/ingress-rules/qdrant.podzone.cloud",
            headers=CLUSTER_OP_HEADERS,
        )
    assert r.status_code == 404


def test_delete_ingress_rule_no_auth(no_auth_client):
    r = no_auth_client.delete(
        "/api/v1/clusters/test-cluster/ingress-rules/qdrant.podzone.cloud",
        headers=NO_ROLE_HEADERS,
    )
    assert r.status_code == 401
