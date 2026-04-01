"""Tests for telegram-notifier.

All tests use mocks — no live Telegram API or Ollama required.
"""

import os
import pytest
from httpx import AsyncClient, ASGITransport

# Set required env vars before importing app
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "test-token")
os.environ.setdefault("OLLAMA_MODEL", "llama3.2")
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")
os.environ.setdefault("CHANNEL_REGISTRY_PATH", "/nonexistent/registry.yaml")
os.environ.setdefault("AGENT_ID", "telegram-notifier")
os.environ.setdefault("REPORTING_SINK_PATH", "/tmp/test-reporting")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_request_payload(
    actor="prompt-engineer",
    context="prod",
    message_type="alert",
    subject="Test alert",
    summary="Something happened.",
):
    return {
        "actor": actor,
        "context": context,
        "message_type": message_type,
        "priority": "normal",
        "subject": subject,
        "content": {"summary": summary, "detail_url": "", "structured": {}},
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_health():
    from agent.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


@pytest.mark.asyncio
async def test_agent_card():
    from agent.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/.well-known/agent.json")
    assert r.status_code == 200
    card = r.json()
    assert card["agent_id"] == "telegram-notifier"
    assert "alert" in card["capabilities"]
    assert card["endpoints"]["card"] == "/.well-known/agent.json"


@pytest.mark.asyncio
async def test_task_no_channels(monkeypatch):
    """With an empty registry, /task should return 200 with zero deliveries."""
    from agent import main as m
    from agent.channel_registry import Channel
    from agent.ollama_client import OllamaClient

    class MockOllama(OllamaClient):
        async def generate(self, prompt, system=""):
            return "short summary"

    monkeypatch.setattr(m, "_registry", [])
    monkeypatch.setattr(m, "_ollama", MockOllama(base_url="", model="llama3.2"))

    from agent.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/task", json=_make_request_payload())
    assert r.status_code == 200
    body = r.json()
    assert body["deliveries"] == []
    assert body["actor"] == "prompt-engineer"


@pytest.mark.asyncio
async def test_task_with_mock_channel(monkeypatch, tmp_path):
    """With a mocked channel and Telegram client, /task should record a delivery."""
    from agent import main as m
    from agent import telegram_client
    from agent.channel_registry import Channel
    from agent.ollama_client import OllamaClient

    class MockOllama(OllamaClient):
        async def generate(self, prompt, system=""):
            return "short"

    test_channel = Channel(
        actor="prompt-engineer",
        type="telegram",
        contexts=["prod"],
        enabled=True,
        channel_id="-100999",
    )
    monkeypatch.setattr(m, "_registry", [test_channel])
    monkeypatch.setattr(m, "_ollama", MockOllama(base_url="", model="llama3.2"))
    monkeypatch.setattr(m, "write_delivery_log", lambda *a, **kw: None)

    async def mock_send(bot_token, chat_id, text, parse_mode="MarkdownV2"):
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(telegram_client, "send_message", mock_send)
    monkeypatch.setattr(m, "send_message", mock_send)

    from agent.main import app
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.post("/task", json=_make_request_payload())
    assert r.status_code == 200
    body = r.json()
    assert len(body["deliveries"]) == 1
    assert body["deliveries"][0]["status"] == "delivered"
    assert body["deliveries"][0]["channel_id"] == "-100999"


def test_formatter_alert():
    from agent.formatter import format_message
    from agent.models import NotificationContent, NotificationRequest

    req = NotificationRequest(
        actor="prompt-engineer",
        context="prod",
        message_type="alert",
        subject="Disk full",
        content=NotificationContent(summary="freyr /var is at 98%"),
    )
    msg = format_message(req, "freyr /var is at 98%")
    assert "⚠️" in msg
    assert "Disk full" in msg or "Disk" in msg


def test_formatter_digest():
    from agent.formatter import format_message
    from agent.models import NotificationContent, NotificationRequest

    req = NotificationRequest(
        actor="executive",
        context="prod",
        message_type="digest",
        subject="Daily",
        content=NotificationContent(
            summary="",
            structured={"items": ["Build passed", "Deploy complete"]},
        ),
    )
    msg = format_message(req, "")
    assert "📋" in msg
    assert "Build passed" in msg or "Build" in msg
