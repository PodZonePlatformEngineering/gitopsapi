"""Injectable Ollama client for embedded agents.

Model and base URL are injected from environment variables at startup.
Never hard-code model names — they are infrastructure concerns declared
in the agent's identity file and injected via Kubernetes manifests.

Environment variables:
  OLLAMA_MODEL     — model name (required, e.g. "llama3.2")
  OLLAMA_BASE_URL  — Ollama base URL (default: http://ollama:11434)
"""

from __future__ import annotations

import os
from typing import Protocol


class OllamaClientProtocol(Protocol):
    """Interface that concrete Ollama clients (and test mocks) must satisfy."""

    async def generate(self, prompt: str, system: str = "") -> str:
        """Send a generate request and return the response text."""
        ...


class OllamaClient:
    """Minimal async Ollama client using httpx."""

    def __init__(self, base_url: str, model: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model

    @classmethod
    def from_env(cls) -> "OllamaClient":
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
        model = os.environ["OLLAMA_MODEL"]
        return cls(base_url=base_url, model=model)

    async def generate(self, prompt: str, system: str = "") -> str:
        import httpx

        payload: dict = {"model": self.model, "prompt": prompt, "stream": False}
        if system:
            payload["system"] = system

        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                f"{self.base_url}/api/generate", json=payload
            )
            response.raise_for_status()
            return response.json()["response"]
