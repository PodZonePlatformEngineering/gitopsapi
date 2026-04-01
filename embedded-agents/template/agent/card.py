"""Agent Card builder for embedded agents (A2A protocol).

The Agent Card is the runtime equivalent of READMEFIRST.md — it describes
what this agent can do and how to invoke it. Served at /.well-known/agent.json.

All values are populated from environment variables at startup so the same
container image can be deployed as different agent instances without rebuilding.

Environment variables:
  AGENT_ID          — unique agent identifier (required)
  AGENT_DOMAIN      — domain this agent belongs to (required)
  AGENT_VERSION     — semver string (default: "0.1.0")
  AGENT_DESCRIPTION — human-readable description (required)
  AGENT_CAPABILITIES — comma-separated capability list (required)
  AGENT_TRIGGER_MODES — comma-separated trigger modes (default: "http,scheduled")
  OLLAMA_MODEL      — inference model name (required)
"""

from __future__ import annotations

import os


def build_agent_card() -> dict:
    """Build the Agent Card dict from environment variables.

    Called once at startup and cached. Raises KeyError if required vars are absent.
    """
    capabilities_raw = os.environ["AGENT_CAPABILITIES"]
    capabilities = [c.strip() for c in capabilities_raw.split(",") if c.strip()]

    trigger_modes_raw = os.environ.get("AGENT_TRIGGER_MODES", "http,scheduled")
    trigger_modes = [t.strip() for t in trigger_modes_raw.split(",") if t.strip()]

    return {
        "agent_id": os.environ["AGENT_ID"],
        "domain": os.environ["AGENT_DOMAIN"],
        "version": os.environ.get("AGENT_VERSION", "0.1.0"),
        "description": os.environ["AGENT_DESCRIPTION"],
        "capabilities": capabilities,
        "trigger_modes": trigger_modes,
        "inference_model": os.environ["OLLAMA_MODEL"],
        "endpoints": {
            "task": "/task",
            "health": "/health",
            "card": "/.well-known/agent.json",
        },
    }
