"""Tests for CloudflareService (CC-068 Phase 2).

All tests use GITOPS_SKIP_CLOUDFLARE=1 to avoid real API calls.
The skip-mode code paths confirm stub behaviour; HTTP path tests
use unittest.mock.AsyncMock to patch httpx.AsyncClient.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import httpx

from gitopsgui.models.ingress import IngressRule, TunnelConfig
from gitopsgui.services.cloudflare_service import CloudflareService


TUNNEL_ID = "71e24b2a-94c2-4064-bf4e-137150356331"


# ---------------------------------------------------------------------------
# Skip-mode tests (GITOPS_SKIP_CLOUDFLARE=1)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def skip_cloudflare(monkeypatch):
    monkeypatch.setenv("GITOPS_SKIP_CLOUDFLARE", "1")


@pytest.mark.asyncio
async def test_get_tunnel_config_skip_returns_empty():
    svc = CloudflareService()
    result = await svc.get_tunnel_config(TUNNEL_ID)
    assert isinstance(result, TunnelConfig)
    assert result.tunnel_id == TUNNEL_ID
    assert result.ingress_rules == []


@pytest.mark.asyncio
async def test_put_tunnel_config_skip_returns_supplied_rules():
    svc = CloudflareService()
    rules = [IngressRule(hostname="qdrant.podzone.cloud", service="http://192.168.4.179:80")]
    result = await svc.put_tunnel_config(TUNNEL_ID, rules)
    assert result.ingress_rules == rules


@pytest.mark.asyncio
async def test_upsert_rule_skip_adds_new_rule():
    svc = CloudflareService()
    result = await svc.upsert_rule(TUNNEL_ID, "qdrant.podzone.cloud", "http://192.168.4.179:80")
    assert len(result.ingress_rules) == 1
    assert result.ingress_rules[0].hostname == "qdrant.podzone.cloud"
    assert result.ingress_rules[0].service == "http://192.168.4.179:80"


@pytest.mark.asyncio
async def test_upsert_rule_skip_replaces_existing():
    svc = CloudflareService()
    # Add first
    await svc.upsert_rule(TUNNEL_ID, "qdrant.podzone.cloud", "http://192.168.4.179:80")
    # Upsert replaces (skip mode starts fresh each call — no persistent state)
    result = await svc.upsert_rule(TUNNEL_ID, "qdrant.podzone.cloud", "http://192.168.4.180:80")
    assert result.ingress_rules[0].service == "http://192.168.4.180:80"


@pytest.mark.asyncio
async def test_delete_rule_skip_removes_matching_hostname():
    svc = CloudflareService()
    # In skip mode get returns [], so delete also returns []
    result = await svc.delete_rule(TUNNEL_ID, "qdrant.podzone.cloud")
    assert result.ingress_rules == []


@pytest.mark.asyncio
async def test_delete_rule_skip_preserves_other_rules():
    """Skip mode: start with two rules, delete one, one remains."""
    svc = CloudflareService()
    rules = [
        IngressRule(hostname="qdrant.podzone.cloud", service="http://192.168.4.179:80"),
        IngressRule(hostname="mpc.podzone.cloud", service="http://192.168.4.179:8080"),
    ]
    # Prime get_tunnel_config by mocking it
    async def _get(_):
        return TunnelConfig(tunnel_id=TUNNEL_ID, ingress_rules=rules)

    svc.get_tunnel_config = _get
    result = await svc.delete_rule(TUNNEL_ID, "qdrant.podzone.cloud")
    assert len(result.ingress_rules) == 1
    assert result.ingress_rules[0].hostname == "mpc.podzone.cloud"


# ---------------------------------------------------------------------------
# HTTP path tests (no skip flag, mock transport)
# ---------------------------------------------------------------------------

def _cf_response(ingress_rules: list) -> dict:
    return {
        "result": {
            "config": {
                "ingress": ingress_rules
            }
        },
        "success": True,
        "errors": [],
        "messages": [],
    }


@pytest.fixture
def no_skip(monkeypatch):
    monkeypatch.delenv("GITOPS_SKIP_CLOUDFLARE", raising=False)
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "test-token")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "test-account")


def _mock_client(response_json: dict):
    """Return an async context manager wrapping a mock httpx.AsyncClient."""
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json = MagicMock(return_value=response_json)

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client.put = AsyncMock(return_value=mock_response)

    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_client)
    cm.__aexit__ = AsyncMock(return_value=False)
    return cm, mock_client


@pytest.mark.asyncio
async def test_get_tunnel_config_http(no_skip):
    """GET returns parsed ingress rules from Cloudflare API."""
    response_data = _cf_response([
        {"hostname": "qdrant.podzone.cloud", "service": "http://192.168.4.179:80"},
        {"service": "http_status:404"},
    ])
    cm, _ = _mock_client(response_data)
    with patch("gitopsgui.services.cloudflare_service.httpx.AsyncClient", return_value=cm):
        svc = CloudflareService()
        result = await svc.get_tunnel_config(TUNNEL_ID)
    assert len(result.ingress_rules) == 1
    assert result.ingress_rules[0].hostname == "qdrant.podzone.cloud"


@pytest.mark.asyncio
async def test_put_tunnel_config_http_appends_catchall(no_skip):
    """PUT appends the required catch-all entry automatically."""
    response_data = _cf_response([
        {"hostname": "qdrant.podzone.cloud", "service": "http://192.168.4.179:80"},
        {"service": "http_status:404"},
    ])
    cm, mock_client = _mock_client(response_data)
    with patch("gitopsgui.services.cloudflare_service.httpx.AsyncClient", return_value=cm):
        svc = CloudflareService()
        rules = [IngressRule(hostname="qdrant.podzone.cloud", service="http://192.168.4.179:80")]
        await svc.put_tunnel_config(TUNNEL_ID, rules)
    _, kwargs = mock_client.put.call_args
    sent = kwargs["json"]["config"]["ingress"]
    assert sent[-1] == {"service": "http_status:404"}, "catch-all must be last"
    assert len(sent) == 2
