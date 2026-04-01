"""telegram-notifier — Iris — PROJ-020/T-007

Embedded notification agent. Delivers structured notifications to Telegram
channels per actor and context. Extends the T-003 canonical skeleton.

Endpoints:
  POST /task                    — receive NotificationRequest; return delivery receipt
  GET  /health                  — liveness check
  GET  /.well-known/agent.json  — Agent Card (A2A protocol)

Secrets: TELEGRAM_BOT_TOKEN via env var (Kubernetes Secret, sourced via secretctl).
External egress: api.telegram.org:443 only (Atlas NetworkPolicy PROJ-020/T-011).
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException

from .channel_registry import Channel, load_registry, resolve_channels
from .config import settings
from .formatter import build_summary, format_message
from .models import ChannelDelivery, NotificationRequest, NotificationResponse
from .ollama_client import OllamaClient
from .reporting import write_delivery_log
from .telegram_client import send_message

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(message)s")

# ---------------------------------------------------------------------------
# Agent Card
# ---------------------------------------------------------------------------
AGENT_CARD = {
    "agent_id": "telegram-notifier",
    "domain": "notification",
    "version": "0.1.0",
    "description": "Delivers structured notifications to Telegram channels per actor and context.",
    "capabilities": ["alert", "report", "digest", "approval-request"],
    "trigger_modes": ["http", "scheduled"],
    "inference_model": settings.ollama_model,
    "endpoints": {
        "task": "/task",
        "health": "/health",
        "card": "/.well-known/agent.json",
    },
}

# ---------------------------------------------------------------------------
# State populated at startup
# ---------------------------------------------------------------------------
_registry: list[Channel] = []
_ollama: OllamaClient | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _registry, _ollama
    _registry = load_registry(settings.channel_registry_path)
    _ollama = OllamaClient(
        base_url=settings.ollama_base_url, model=settings.ollama_model
    )
    channel_count = len(_registry)
    logger.info(
        '{"agent_id": "telegram-notifier", "event": "startup", "channels_loaded": %d}',
        channel_count,
    )
    yield
    logger.info('{"agent_id": "telegram-notifier", "event": "shutdown"}')


app = FastAPI(
    title="telegram-notifier",
    description="Podzone notification agent — Iris",
    version="0.1.0",
    lifespan=lifespan,
)

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health() -> dict:
    return {"status": "ok", "agent_id": settings.agent_id}


@app.get("/.well-known/agent.json")
async def agent_card() -> dict:
    return AGENT_CARD


@app.post("/task", response_model=NotificationResponse)
async def task(request: NotificationRequest) -> NotificationResponse:
    """Receive a notification request and deliver to all resolved Telegram channels."""
    import time

    start = time.monotonic()

    if not settings.telegram_bot_token:
        raise HTTPException(
            status_code=503,
            detail="TELEGRAM_BOT_TOKEN not configured — secret not yet injected",
        )

    assert _ollama is not None

    # Build summary (shorten via Ollama if needed)
    summary = await build_summary(request, _ollama.generate, settings.max_summary_chars)

    # Format Telegram message
    text = format_message(request, summary)

    # Resolve channels from registry
    channels = resolve_channels(_registry, request.actor, request.context, "telegram")

    deliveries: list[ChannelDelivery] = []
    errors: list[str] = []

    if not channels:
        logger.warning(
            '{"agent_id": "telegram-notifier", "event": "no_channels", '
            '"actor": "%s", "context": "%s"}',
            request.actor,
            request.context,
        )

    for channel in channels:
        ts = datetime.now(timezone.utc).isoformat()
        try:
            await send_message(
                bot_token=settings.telegram_bot_token,
                chat_id=channel.channel_id,
                text=text,
            )
            deliveries.append(
                ChannelDelivery(
                    channel_type="telegram",
                    channel_id=channel.channel_id,
                    status="delivered",
                    timestamp=ts,
                )
            )
        except Exception as exc:
            err = str(exc)
            errors.append(err)
            deliveries.append(
                ChannelDelivery(
                    channel_type="telegram",
                    channel_id=channel.channel_id,
                    status="failed",
                    timestamp=ts,
                    error=err,
                )
            )

    response = NotificationResponse(
        agent_id=settings.agent_id,
        actor=request.actor,
        context=request.context,
        deliveries=deliveries,
        errors=errors,
    )

    # Write delivery log to reporting sink (non-fatal)
    try:
        write_delivery_log(response, settings.reporting_sink_path)
    except Exception as exc:
        logger.warning('{"agent_id": "telegram-notifier", "event": "log_write_failed", "error": "%s"}', exc)

    duration_ms = int((time.monotonic() - start) * 1000)
    logger.info(
        '{"agent_id": "telegram-notifier", "actor": "%s", "context": "%s", '
        '"message_type": "%s", "deliveries": %d, "errors": %d, "duration_ms": %d}',
        request.actor,
        request.context,
        request.message_type,
        len(deliveries),
        len(errors),
        duration_ms,
    )

    return response
