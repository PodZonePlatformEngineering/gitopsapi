"""Telegram Bot API delivery client.

Sends messages to Telegram channels via the Bot API.
Bot token is injected from config (never hard-coded).

External egress: api.telegram.org:443 — the one permitted external egress
for this agent (Atlas NetworkPolicy PROJ-020/T-011).
"""

from __future__ import annotations

import httpx

TELEGRAM_API_BASE = "https://api.telegram.org"


async def send_message(
    bot_token: str,
    chat_id: str,
    text: str,
    parse_mode: str = "MarkdownV2",
) -> dict:
    """Send a message to a Telegram chat. Returns the API response dict.

    Raises httpx.HTTPStatusError on delivery failure.
    """
    url = f"{TELEGRAM_API_BASE}/bot{bot_token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": parse_mode,
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()
