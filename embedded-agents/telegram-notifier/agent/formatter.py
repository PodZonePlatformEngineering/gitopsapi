"""Telegram message formatter.

Formats notification requests into Telegram-compatible Markdown (v2).
Ollama is used to shorten summaries that exceed MAX_SUMMARY_CHARS.

Message templates from agenticflows/roles/telegram-notifier/README.md.
"""

from __future__ import annotations

from .models import NotificationRequest


def _escape(text: str) -> str:
    """Escape Telegram MarkdownV2 special characters."""
    special = r"_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in special else c for c in text)


def format_message(request: NotificationRequest, summary: str) -> str:
    """Produce the Telegram message string for the given message_type."""
    s = _escape(summary)
    subj = _escape(request.subject)
    ctx = _escape(request.context)
    detail = request.content.detail_url

    if request.message_type == "alert":
        msg = f"⚠️ *{subj}*\n{s}\nContext: {ctx}"
        if detail:
            msg += f"\n[View report]({detail})"

    elif request.message_type == "report":
        msg = f"📊 *{subj}*\n{s}\nContext: {ctx}"
        if detail:
            msg += f"\n[Full report]({detail})"

    elif request.message_type == "digest":
        items = request.content.structured.get("items", [])
        lines = "\n".join(f"• {_escape(str(i))}" for i in items[:10])
        msg = f"📋 *Daily Digest*\n{lines}"
        if detail:
            msg += f"\n[View all]({detail})"

    elif request.message_type == "approval-request":
        msg = f"✅ *Approval Required: {subj}*\n{s}\nContext: {ctx}"
        if detail:
            msg += f"\nReview: {detail}"

    else:
        msg = f"*{subj}*\n{s}"

    return msg


async def build_summary(
    request: NotificationRequest,
    ollama_generate,
    max_chars: int,
) -> str:
    """Return summary text, shortening via Ollama if it exceeds max_chars."""
    raw = request.content.summary
    if not raw:
        return ""
    if len(raw) <= max_chars:
        return raw

    prompt = (
        f"Summarise the following in under {max_chars} characters for a Telegram notification. "
        f"Keep it factual and plain text only.\n\n{raw}"
    )
    return await ollama_generate(prompt=prompt)
