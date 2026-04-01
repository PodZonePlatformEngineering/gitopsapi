"""Delivery log writer for podZoneAgentReporting sink.

Appends a delivery log entry to the reporting sink (file mount).
Hermes (or a scheduled task) commits from the sink to git.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .models import NotificationResponse


def write_delivery_log(response: NotificationResponse, sink_path: str) -> None:
    """Append one JSON delivery log line to {sink_path}/telegram-notifier-deliveries.jsonl.

    Creates the file and directory if absent. Non-fatal — failures are logged
    but do not affect delivery status.
    """
    sink = Path(sink_path)
    sink.mkdir(parents=True, exist_ok=True)
    log_file = sink / "telegram-notifier-deliveries.jsonl"

    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "agent_id": response.agent_id,
        "actor": response.actor,
        "context": response.context,
        "deliveries": [d.model_dump() for d in response.deliveries],
        "errors": response.errors,
    }
    with log_file.open("a") as f:
        f.write(json.dumps(entry) + "\n")
