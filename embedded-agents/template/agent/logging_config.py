"""Structured JSON logging for embedded agents.

All task invocations are logged with: agent_id, task_type, trigger_mode,
duration_ms, status. Output goes to stdout for capture by the cluster logging stack.
"""

import json
import logging
import sys
import time
from contextlib import contextmanager
from typing import Generator


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra"):
            payload.update(record.extra)
        return json.dumps(payload)


def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
    return logger


@contextmanager
def log_task(
    logger: logging.Logger,
    agent_id: str,
    task_type: str,
    trigger_mode: str,
) -> Generator[dict, None, None]:
    """Context manager that logs task invocation with duration and status."""
    start = time.monotonic()
    record: dict = {}
    try:
        yield record
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.info(
            "task completed",
            extra={
                "extra": {
                    "agent_id": agent_id,
                    "task_type": task_type,
                    "trigger_mode": trigger_mode,
                    "duration_ms": duration_ms,
                    "status": "ok",
                }
            },
        )
    except Exception:
        duration_ms = int((time.monotonic() - start) * 1000)
        logger.exception(
            "task failed",
            extra={
                "extra": {
                    "agent_id": agent_id,
                    "task_type": task_type,
                    "trigger_mode": trigger_mode,
                    "duration_ms": duration_ms,
                    "status": "error",
                }
            },
        )
        raise
