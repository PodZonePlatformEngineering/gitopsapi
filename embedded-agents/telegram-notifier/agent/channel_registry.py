"""Channel Registry loader.

Reads the YAML channel registry config injected at deployment.
Channel config is deployment config — never in request payload.

Format (agenticflows/roles/notification/README.md):

  channels:
    - actor: prompt-engineer
      type: telegram
      channel_id: "-100xxxxxxxxxx"
      contexts: [prod]
      enabled: true
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Channel:
    actor: str
    type: str
    contexts: list[str]
    enabled: bool
    channel_id: str = ""
    path: str = ""
    address: str = ""

    def matches(self, actor: str, context: str) -> bool:
        return (
            self.actor == actor
            and context in self.contexts
            and self.enabled
        )


def load_registry(path: str) -> list[Channel]:
    """Load channel registry from YAML file.

    Returns empty list (gracefully) if file does not exist — allows the
    agent to start without a registry in dev/test and report zero channels.
    """
    registry_path = Path(path)
    if not registry_path.exists():
        return []

    with registry_path.open() as f:
        data: dict[str, Any] = yaml.safe_load(f) or {}

    channels: list[Channel] = []
    for entry in data.get("channels", []):
        channels.append(
            Channel(
                actor=entry["actor"],
                type=entry["type"],
                contexts=entry.get("contexts", []),
                enabled=entry.get("enabled", False),
                channel_id=entry.get("channel_id", ""),
                path=entry.get("path", ""),
                address=entry.get("address", ""),
            )
        )
    return channels


def resolve_channels(
    registry: list[Channel], actor: str, context: str, channel_type: str
) -> list[Channel]:
    return [ch for ch in registry if ch.matches(actor, context) and ch.type == channel_type]
