"""
Google Gemini API helpers (server-side only). Requires GEMINI_API_KEY in settings.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

ASSISTANT_SYSTEM = (
    "You are a helpful assistant in a chat app. Answer clearly and directly, like a concise "
    "search-style summary when the user asks for facts or explanations. If you are unsure, say so. "
    "Refuse only requests that are clearly illegal or intended to cause serious harm; otherwise "
    "be helpful. Keep answers focused; use short paragraphs or bullet points when appropriate."
)

TRANSCRIPT_SYSTEM = (
    "You output only valid JSON when asked. No markdown fences, no commentary before or after."
)


def is_configured() -> bool:
    key = getattr(settings, "GEMINI_API_KEY", "") or ""
    return bool(key.strip())


# Ordered fallbacks when the configured model is missing (404) — e.g. deprecated 2.0 Flash.
_MODEL_FALLBACKS = (
    "gemini-2.5-flash",
    "gemini-2.5-flash-lite",
    "gemini-1.5-flash",
)


def _model_candidates() -> list[str]:
    configured = (getattr(settings, "GEMINI_MODEL", "") or "").strip()
    primary = configured or "gemini-2.5-flash"
    out: list[str] = []
    for m in (primary,) + _MODEL_FALLBACKS:
        if m and m not in out:
            out.append(m)
    return out


def _is_model_not_found(exc: Exception) -> bool:
    msg = str(exc).lower()
    return (
        "404" in msg
        or "not found" in msg
        or "invalid model" in msg
        or "does not exist" in msg
        or "is not found" in msg
    )


def _ensure_genai():
    try:
        import google.generativeai as genai
    except ImportError:
        return None, "The google-generativeai package is not installed."

    key = (getattr(settings, "GEMINI_API_KEY", "") or "").strip()
    if not key:
        return None, "AI is not configured (missing GEMINI_API_KEY)."
    genai.configure(api_key=key)
    return genai, None


def assistant_reply(
    history: list[dict[str, Any]],
    user_message: str,
) -> tuple[str | None, str | None]:
    """
    Multi-turn assistant. history items: {"role": "user"|"model", "text": "..."}.
    Returns (assistant_text, error_message).
    """
    text = (user_message or "").strip()
    if not text:
        return None, "Message is empty."
    if len(text) > 8000:
        return None, "Message is too long."

    genai_mod, err = _ensure_genai()
    if err:
        return None, err

    hist: list[dict[str, Any]] = []
    for m in (history or [])[-24:]:
        role = m.get("role")
        part = (m.get("text") or "").strip()
        if not part or role not in ("user", "model"):
            continue
        r = "user" if role == "user" else "model"
        hist.append({"role": r, "parts": [part[:8000]]})

    last_missing: Exception | None = None
    for model_name in _model_candidates():
        model = genai_mod.GenerativeModel(model_name, system_instruction=ASSISTANT_SYSTEM)
        try:
            chat = model.start_chat(history=hist)
            resp = chat.send_message(text)
            out = (resp.text or "").strip()
            if not out:
                return None, "The model returned an empty response. Try rephrasing."
            return out, None
        except Exception as e:
            if _is_model_not_found(e):
                last_missing = e
                logger.warning("Gemini model %r unavailable (%s); trying fallback", model_name, e)
                continue
            logger.exception("Gemini assistant_reply failed")
            return None, _friendly_api_error(e)
    return None, _friendly_api_error(last_missing) if last_missing else "No Gemini model available."


def generate_transcript_json(
    scenario: str,
    room_summary: str,
    num_turns: int,
) -> tuple[list[dict[str, str]] | None, str | None]:
    """Returns list of {"side": "me"|"other", "text": "..."} or error."""
    scenario = (scenario or "").strip()
    if not scenario:
        return None, "Describe what conversation you want."
    if len(scenario) > 4000:
        return None, "Scenario is too long."

    n = max(4, min(int(num_turns or 8), 24))

    genai_mod, err = _ensure_genai()
    if err:
        return None, err

    ctx = (room_summary or "").strip()[:2000]
    prompt = f"""Create a fictional chat transcript for a messaging app.

Context about the chat (may be empty): {ctx}

User request: {scenario}

Return a JSON array of {n} messages alternating speakers. Each item must be an object with:
- "side": either "me" (the user who requested this) or "other" (the other participant)
- "text": the message text (natural, casual chat; each under 400 characters)

Start with "me" or "other" as appropriate. Output ONLY the JSON array, no markdown."""

    raw = ""
    last_missing: Exception | None = None
    for model_name in _model_candidates():
        model = genai_mod.GenerativeModel(model_name, system_instruction=TRANSCRIPT_SYSTEM)
        try:
            resp = model.generate_content(
                prompt,
                generation_config=genai_mod.GenerationConfig(
                    response_mime_type="application/json",
                ),
            )
            raw = (resp.text or "").strip()
            data = json.loads(raw)
            if not isinstance(data, list):
                return None, "Invalid AI response format."
            cleaned: list[dict[str, str]] = []
            for item in data:
                if not isinstance(item, dict):
                    continue
                side = str(item.get("side", "")).lower()
                t = str(item.get("text", "")).strip()
                if side not in ("me", "other") or not t:
                    continue
                cleaned.append({"side": side, "text": t[:2000]})
            if len(cleaned) < 2:
                return None, "Could not parse a usable transcript. Try again with a clearer prompt."
            return cleaned, None
        except json.JSONDecodeError:
            logger.warning("Gemini transcript JSON parse failed: %s", raw[:500])
            return None, "Could not parse AI response. Try again."
        except Exception as e:
            if _is_model_not_found(e):
                last_missing = e
                logger.warning("Gemini model %r unavailable (%s); trying fallback", model_name, e)
                continue
            logger.exception("Gemini generate_transcript_json failed")
            return None, _friendly_api_error(e)
    return None, _friendly_api_error(last_missing) if last_missing else "No Gemini model available."


def _friendly_api_error(e: Exception) -> str:
    msg = str(e).lower()
    if "api key" in msg or "401" in msg or "403" in msg:
        return "AI key is invalid or not allowed for this model. Check GEMINI_API_KEY and GEMINI_MODEL."
    if "429" in msg or "resource exhausted" in msg:
        return "AI quota exceeded. Try again later."
    if "404" in msg or "not found" in msg:
        return (
            "That Gemini model is not available for your API key (often deprecated). "
            "Set GEMINI_MODEL to gemini-2.5-flash or gemini-1.5-flash in your environment."
        )
    return "AI request failed. Please try again in a moment."


def summarize_recent_messages(lines: list[str]) -> tuple[str | None, str | None]:
    """Optional: compact summary of last messages for context."""
    if not lines:
        return None, "No messages to summarize."
    genai_mod, err = _ensure_genai()
    if err:
        return None, err

    blob = "\n".join(lines[-40:])[:12000]
    summary_prompt = (
        "Summarize this chat history in 3–6 short bullet points for the user. "
        "Focus on decisions, open questions, and tone.\n\n---\n" + blob
    )
    last_missing: Exception | None = None
    for model_name in _model_candidates():
        model = genai_mod.GenerativeModel(model_name)
        try:
            resp = model.generate_content(summary_prompt)
            out = (resp.text or "").strip()
            return (out, None) if out else (None, "Empty summary.")
        except Exception as e:
            if _is_model_not_found(e):
                last_missing = e
                logger.warning("Gemini model %r unavailable (%s); trying fallback", model_name, e)
                continue
            logger.exception("Gemini summarize failed")
            return None, _friendly_api_error(e)
    return None, _friendly_api_error(last_missing) if last_missing else "No Gemini model available."
