"""Embedded agent FastAPI skeleton — PROJ-020/T-003.

This is the canonical base for all podzone embedded agents. Copy this template
and extend it for each concrete agent (e.g. telegram-notifier, observability-logs).

Endpoints:
  POST /task                    — receive a task payload; return structured JSON
  GET  /health                  — liveness check; always returns 200 if operational
  GET  /.well-known/agent.json  — Agent Card (A2A protocol capability descriptor)

Extension points (search for "EXTEND HERE"):
  - TaskRequest model: add agent-specific input fields
  - TaskResponse model: add agent-specific output fields
  - process_task(): replace the stub with real inference logic

All secrets must be injected via environment variables (Kubernetes Secrets).
Never hard-code credentials, tokens, or DSNs.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .card import build_agent_card
from .logging_config import get_logger, log_task
from .ollama_client import OllamaClient, OllamaClientProtocol

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# State populated at startup
# ---------------------------------------------------------------------------
_agent_card: dict = {}
_ollama: OllamaClientProtocol | None = None
_agent_id: str = ""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    global _agent_card, _ollama, _agent_id
    _agent_card = build_agent_card()
    _agent_id = _agent_card["agent_id"]
    _ollama = OllamaClient.from_env()
    logger.info("agent started", extra={"extra": {"agent_id": _agent_id}})
    yield
    logger.info("agent stopped", extra={"extra": {"agent_id": _agent_id}})


app = FastAPI(
    title="Embedded Agent",
    description="Podzone embedded agent skeleton — extend per agent type.",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Request / Response models
# EXTEND HERE: add agent-specific fields to TaskRequest and TaskResponse
# ---------------------------------------------------------------------------


class TaskRequest(BaseModel):
    task_type: str
    trigger_mode: str = "http"
    payload: dict[str, Any] = {}


class TaskResponse(BaseModel):
    agent_id: str
    task_type: str
    status: str
    result: dict[str, Any] = {}


# ---------------------------------------------------------------------------
# Task processing
# EXTEND HERE: replace the stub with real inference / action logic
# ---------------------------------------------------------------------------


async def process_task(request: TaskRequest) -> dict[str, Any]:
    """Stub task processor — replace with agent-specific logic.

    Receives the task payload, calls Ollama if inference is needed, and
    returns a structured result dict. All secrets needed here must come
    from os.environ (injected as Kubernetes Secrets at deployment).
    """
    assert _ollama is not None, "Ollama client not initialised"

    # Example: pass payload as a prompt to Ollama and return the response.
    # Real agents will have typed input/output and domain-specific logic here.
    prompt = request.payload.get("prompt", "(no prompt provided)")
    response_text = await _ollama.generate(prompt=prompt)
    return {"response": response_text}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.post("/task", response_model=TaskResponse)
async def task(request: TaskRequest) -> TaskResponse:
    """Receive a task and return a structured JSON response.

    Logs agent_id, task_type, trigger_mode, duration_ms, and status on every call.
    """
    with log_task(
        logger,
        agent_id=_agent_id,
        task_type=request.task_type,
        trigger_mode=request.trigger_mode,
    ):
        try:
            result = await process_task(request)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    return TaskResponse(
        agent_id=_agent_id,
        task_type=request.task_type,
        status="ok",
        result=result,
    )


@app.get("/health")
async def health() -> dict[str, str]:
    """Liveness check. Always returns 200 if the process is operational."""
    return {"status": "ok", "agent_id": _agent_id}


@app.get("/.well-known/agent.json")
async def agent_card() -> dict:
    """Serve the Agent Card (A2A capability descriptor).

    Runtime equivalent of READMEFIRST.md — describes what this agent does
    and how to invoke it. Version must be bumped on any capability change.
    """
    return _agent_card
