"""Tests for the embedded agent template skeleton.

Uses a mock Ollama client so no live Ollama instance is required.
"""

import os
import pytest
from httpx import AsyncClient, ASGITransport


# Provide required env vars before the app module is imported
os.environ.setdefault("AGENT_ID", "test-agent")
os.environ.setdefault("AGENT_DOMAIN", "test")
os.environ.setdefault("AGENT_DESCRIPTION", "Test agent")
os.environ.setdefault("AGENT_CAPABILITIES", "test-capability")
os.environ.setdefault("OLLAMA_MODEL", "llama3.2")
os.environ.setdefault("OLLAMA_BASE_URL", "http://localhost:11434")


class MockOllamaClient:
    async def generate(self, prompt: str, system: str = "") -> str:
        return f"mock response to: {prompt}"


@pytest.fixture()
def app_with_mock_ollama(monkeypatch):
    from agent import main as agent_main

    monkeypatch.setattr(agent_main, "_ollama", MockOllamaClient())
    monkeypatch.setattr(agent_main, "_agent_id", "test-agent")
    monkeypatch.setattr(
        agent_main,
        "_agent_card",
        {
            "agent_id": "test-agent",
            "domain": "test",
            "version": "0.1.0",
            "description": "Test agent",
            "capabilities": ["test-capability"],
            "trigger_modes": ["http"],
            "inference_model": "llama3.2",
            "endpoints": {
                "task": "/task",
                "health": "/health",
                "card": "/.well-known/agent.json",
            },
        },
    )
    return agent_main.app


@pytest.mark.asyncio
async def test_health(app_with_mock_ollama):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_mock_ollama), base_url="http://test"
    ) as client:
        response = await client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["agent_id"] == "test-agent"


@pytest.mark.asyncio
async def test_agent_card(app_with_mock_ollama):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_mock_ollama), base_url="http://test"
    ) as client:
        response = await client.get("/.well-known/agent.json")
    assert response.status_code == 200
    card = response.json()
    assert card["agent_id"] == "test-agent"
    assert "/task" == card["endpoints"]["task"]
    assert "/.well-known/agent.json" == card["endpoints"]["card"]


@pytest.mark.asyncio
async def test_task_endpoint(app_with_mock_ollama):
    async with AsyncClient(
        transport=ASGITransport(app=app_with_mock_ollama), base_url="http://test"
    ) as client:
        response = await client.post(
            "/task",
            json={
                "task_type": "generate",
                "trigger_mode": "http",
                "payload": {"prompt": "hello"},
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["agent_id"] == "test-agent"
    assert "mock response to: hello" in body["result"]["response"]
