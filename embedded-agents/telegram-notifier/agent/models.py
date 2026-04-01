"""Pydantic models for telegram-notifier.

Derived from the notification class standard input/output interface:
  agenticflows/roles/notification/README.md

Input: NotificationRequest
Output: NotificationResponse (delivery receipt per channel)
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------


class NotificationContent(BaseModel):
    summary: str = ""
    detail_url: str = ""
    structured: dict[str, Any] = {}


class NotificationRequest(BaseModel):
    actor: str
    context: Literal["dev-build", "ete", "prod"]
    message_type: Literal["alert", "report", "digest", "approval-request"]
    priority: Literal["high", "normal", "low"] = "normal"
    subject: str
    content: NotificationContent
    reply_to: str = ""


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


class ChannelDelivery(BaseModel):
    channel_type: Literal["telegram", "mail", "incoming-file"]
    channel_id: str
    status: Literal["delivered", "failed", "disabled"]
    timestamp: str = ""
    error: str = ""


class NotificationResponse(BaseModel):
    agent_id: str
    actor: str
    context: str
    deliveries: list[ChannelDelivery] = []
    errors: list[str] = []
