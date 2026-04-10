"""
AI service router — tries Gemini first, falls back to Groq if quota is exceeded.

Usage:
    from .ai_service import assistant_reply, generate_transcript_json, summarize_recent_messages, is_configured
    from .ai_service import generate_code_project
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)


# ── Provider availability ─────────────────────────────────────────────────────

def _gemini_on() -> bool:
    return bool((getattr(settings, "GEMINI_API_KEY", "") or "").strip())


def _groq_on() -> bool:
    return bool((getattr(settings, "GROQ_API_KEY", "") or "").strip())


def is_configured() -> bool:
    """True if at least one AI provider is available."""
    return _gemini_on() or _groq_on()


def active_provider() -> str:
    """Returns 'gemini', 'groq', 'both', or 'none'."""
    g = _gemini_on()
    k = _groq_on()
    if g and k:
        return "both"
    if g:
        return "gemini"
    if k:
        return "groq"
    return "none"


# ── Quota / error detection ───────────────────────────────────────────────────

def _is_quota_error(err_msg: str) -> bool:
    """Detect Gemini quota-exceeded errors by their friendly message."""
    m = (err_msg or "").lower()
    return (
        "quota exceeded" in m
        or "resource exhausted" in m
        or "429" in m
        or "try again later" in m
        or "rate limit" in m
    )


# ── Public API — mirrors gemini_client.py exactly ────────────────────────────

def assistant_reply(
    history: list[dict[str, Any]],
    user_message: str,
) -> tuple[str | None, str | None]:
    """Try Gemini; if quota exceeded fall back to Groq. Returns (text, error)."""
    if _gemini_on():
        from .gemini_client import assistant_reply as _gemini_reply
        text, err = _gemini_reply(history, user_message)
        if text:
            return text, None
        if err and _is_quota_error(err) and _groq_on():
            logger.warning("Gemini quota exceeded for assistant_reply — switching to Groq")
            from .groq_client import assistant_reply as _groq_reply
            return _groq_reply(history, user_message)
        return text, err

    if _groq_on():
        from .groq_client import assistant_reply as _groq_reply
        return _groq_reply(history, user_message)

    return None, "AI is not configured (no GEMINI_API_KEY or GROQ_API_KEY set)."


def generate_transcript_json(
    scenario: str,
    room_summary: str,
    num_turns: int,
) -> tuple[list[dict[str, str]] | None, str | None]:
    """Try Gemini; if quota exceeded fall back to Groq. Returns (lines, error)."""
    if _gemini_on():
        from .gemini_client import generate_transcript_json as _gemini_transcript
        lines, err = _gemini_transcript(scenario, room_summary, num_turns)
        if lines:
            return lines, None
        if err and _is_quota_error(err) and _groq_on():
            logger.warning("Gemini quota exceeded for generate_transcript_json — switching to Groq")
            from .groq_client import generate_transcript_json as _groq_transcript
            return _groq_transcript(scenario, room_summary, num_turns)
        return lines, err

    if _groq_on():
        from .groq_client import generate_transcript_json as _groq_transcript
        return _groq_transcript(scenario, room_summary, num_turns)

    return None, "AI is not configured (no GEMINI_API_KEY or GROQ_API_KEY set)."


def stream_codegen_coach(
    messages: list[dict[str, Any]],
    phase: str = "discover",
    include_code: bool = True,
) -> Iterator[str]:
    """Token stream: project coach with optional code-in-reply, or general chat when include_code is False."""
    if _groq_on():
        from .groq_client import stream_codegen_coach as _groq_stream
        yield from _groq_stream(messages, phase, include_code)
        return
    if _gemini_on():
        from .gemini_client import stream_codegen_coach as _gemini_stream
        yield from _gemini_stream(messages, phase, include_code)
        return
    raise RuntimeError("AI is not configured for streaming (set GROQ_API_KEY or GEMINI_API_KEY).")


def generate_code_project(
    user_prompt: str,
    stack_hint: str = "",
    continuation_note: str | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    if _gemini_on():
        from .gemini_client import generate_code_project as _gemini_gen
        data, err = _gemini_gen(user_prompt, stack_hint, continuation_note)
        if data:
            return data, None
        if err == "OUTPUT_TRUNCATED" and _groq_on():
            logger.warning("Gemini code JSON truncated — retrying with Groq")
            from .groq_client import generate_code_project as _groq_gen
            return _groq_gen(user_prompt, stack_hint, continuation_note)
        if err and _is_quota_error(err) and _groq_on():
            logger.warning("Gemini quota exceeded for generate_code_project — switching to Groq")
            from .groq_client import generate_code_project as _groq_gen
            return _groq_gen(user_prompt, stack_hint, continuation_note)
        return data, err

    if _groq_on():
        from .groq_client import generate_code_project as _groq_gen
        return _groq_gen(user_prompt, stack_hint, continuation_note)

    return None, "AI is not configured (no GEMINI_API_KEY or GROQ_API_KEY set)."


def summarize_recent_messages(
    lines: list[str],
) -> tuple[str | None, str | None]:
    """Try Gemini; if quota exceeded fall back to Groq. Returns (summary, error)."""
    if _gemini_on():
        from .gemini_client import summarize_recent_messages as _gemini_summarize
        summary, err = _gemini_summarize(lines)
        if summary:
            return summary, None
        if err and _is_quota_error(err) and _groq_on():
            logger.warning("Gemini quota exceeded for summarize_recent_messages — switching to Groq")
            from .groq_client import summarize_recent_messages as _groq_summarize
            return _groq_summarize(lines)
        return summary, err

    if _groq_on():
        from .groq_client import summarize_recent_messages as _groq_summarize
        return _groq_summarize(lines)

    return None, "AI is not configured (no GEMINI_API_KEY or GROQ_API_KEY set)."
