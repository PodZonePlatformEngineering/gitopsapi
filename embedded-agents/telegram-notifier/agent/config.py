"""Settings for telegram-notifier.

All secrets injected via environment variables (Kubernetes Secrets).
Never hard-coded here.

Environment variables:
  TELEGRAM_BOT_TOKEN      — from secretctl: telegram-bot-token (required in prod)
  OLLAMA_BASE_URL         — default: http://ollama:11434
  OLLAMA_MODEL            — default: llama3.2
  CHANNEL_REGISTRY_PATH   — path to channel-registry.yaml (default: /config/channel-registry.yaml)
  AGENT_ID                — agent identifier
  REPORTING_SINK_PATH     — path to podZoneAgentReporting mount (default: /reporting)
"""

from __future__ import annotations

import os


class Settings:
    def __init__(self) -> None:
        self.telegram_bot_token: str = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self.ollama_base_url: str = os.environ.get("OLLAMA_BASE_URL", "http://ollama:11434")
        self.ollama_model: str = os.environ.get("OLLAMA_MODEL", "llama3.2")
        self.channel_registry_path: str = os.environ.get(
            "CHANNEL_REGISTRY_PATH", "/config/channel-registry.yaml"
        )
        self.agent_id: str = os.environ.get("AGENT_ID", "telegram-notifier")
        self.reporting_sink_path: str = os.environ.get("REPORTING_SINK_PATH", "/reporting")
        self.max_summary_chars: int = int(os.environ.get("MAX_SUMMARY_CHARS", "280"))


settings = Settings()
