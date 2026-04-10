"""
JSON API views for Gemini-powered assistant and AI-generated chat transcripts.
"""

from __future__ import annotations

import io
import json
import logging
import re
import zipfile

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .ai_service import (
    assistant_reply,
    generate_code_project,
    generate_transcript_json,
    is_configured,
    stream_codegen_coach,
    summarize_recent_messages,
)
from .models import DailyAiUsage, DailyMessageCount, Message, Notification, Room, Subscription

logger = logging.getLogger(__name__)


def _ai_quota_exceeded_response(limit: int, used: int) -> JsonResponse:
    return JsonResponse(
        {
            "error": "ai_quota_exceeded",
            "message": (
                f"You've used all {limit} free AI requests for today. "
                "Upgrade to Pro for unlimited AI chat and messaging."
            ),
            "upgrade_url": "/subscribe/",
            "limit": limit,
            "used": used,
        },
        status=403,
    )


def enforce_ai_quota(user):
    """
    Pro: unlimited. Free: FREE_AI_USES_PER_DAY per calendar day.
    Returns None if the request may proceed, or a JsonResponse to return to the client.
    """
    sub, _ = Subscription.objects.get_or_create(user=user)
    if sub.is_pro:
        return None
    lim = getattr(settings, "FREE_AI_USES_PER_DAY", 10)
    today = timezone.now().date()
    daily, _ = DailyAiUsage.objects.get_or_create(user=user, date=today)
    if daily.count >= lim:
        return _ai_quota_exceeded_response(lim, daily.count)
    return None


def record_ai_use(user):
    """Increment daily AI counter (no-op for Pro). Call only after a successful AI call."""
    sub, _ = Subscription.objects.get_or_create(user=user)
    if sub.is_pro:
        return
    today = timezone.now().date()
    daily, _ = DailyAiUsage.objects.get_or_create(user=user, date=today)
    daily.count += 1
    daily.save()


def _parse_json(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


_CODE_PATH_OK = re.compile(r"^[a-zA-Z0-9._][a-zA-Z0-9._\-/]*$")


def sanitize_code_files_for_download(files_raw: list) -> tuple[list[dict[str, str]], str | None]:
    if not isinstance(files_raw, list):
        return [], "Invalid files list."
    out: list[dict[str, str]] = []
    total = 0
    for item in files_raw[:16]:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path", "")).strip().replace("\\", "/").lstrip("/")
        if not path or ".." in path or not _CODE_PATH_OK.match(path) or len(path) > 120:
            continue
        content = str(item.get("content", ""))
        if len(content) > 100_000:
            content = content[:100_000]
        total += len(content)
        if total > 450_000:
            break
        out.append({"path": path, "content": content})
    if len(out) < 1:
        return [], "No valid files to package."
    return out, None


def zip_attachment_filename(project_name: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9._-]+", "-", (project_name or "").strip().lower()).strip("-")
    return f"{(base or 'project')[:48]}.zip"


def _compose_code_project_prompt(data: dict) -> tuple[str | None, str | None]:
    """
    Merge questionnaire + feature description into one prompt for the model.
    Returns (prompt, error_message).
    """
    desc = (data.get("prompt") or data.get("message") or "").strip()
    if not desc:
        return None, "Describe what the project should do (features and details)."

    ptype = (data.get("project_type") or "").strip()
    psize = (data.get("project_size") or "").strip()
    users = (data.get("expected_users") or "").strip()
    prod = (data.get("production") or "").strip()
    nd_val = data.get("needs_database")
    nd_raw = str(nd_val).strip().lower() if nd_val is not None else ""
    if nd_raw in ("true", "1", "y"):
        nd_raw = "yes"
    elif nd_raw in ("false", "0", "n"):
        nd_raw = "no"

    if not ptype or not psize or not users or not prod or nd_raw not in ("yes", "no"):
        return None, "Please answer all project questions (type, size, users, production, database)."

    db_line = ""
    if nd_raw == "yes":
        db = (data.get("database") or data.get("database_choice") or "").strip()
        if not db:
            return None, "Choose which database to use (or “Not sure”)."
        other = (data.get("database_other") or "").strip()[:200]
        if db == "Other" and not other:
            return None, "Specify the database name or service under “Database details”."
        db_line = f"- Database: required. Preferred stack: {db}."
        if other:
            db_line += f" Extra detail: {other}."
    else:
        db_line = (
            "- Database: not required. Prefer in-memory stores, flat files, or mocks unless "
            "the feature description clearly needs persistence; if a tiny local DB helps, SQLite is OK."
        )

    composed = f"""PROJECT CONSTRAINTS (follow these when choosing structure, dependencies, and files):

- Type: {ptype}
- Scope / how big: {psize}
- Expected users or traffic: {users}
- Production / “live” intent: {prod}
{db_line}

FEATURE DESCRIPTION (implement this):
---
{desc}
---
"""
    return composed, None


def _compose_prompt_from_messages(messages: list) -> str:
    lines: list[str] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = m.get("role")
        c = (m.get("content") or "").strip()
        if not c:
            continue
        c = (
            c.replace("[[READY_TO_GENERATE]]", "")
            .replace("[[READY_TO_UPDATE]]", "")
            .strip()
        )
        if not c:
            continue
        who = "User" if role == "user" else "Assistant"
        lines.append(f"{who}: {c}")
    return "\n\n".join(lines)


def _compose_update_prompt(messages: list, files: list) -> str:
    conv = _compose_prompt_from_messages(messages)
    parts: list[str] = []
    for f in files[:14]:
        if not isinstance(f, dict):
            continue
        p = str(f.get("path", "")).strip()
        body = str(f.get("content", ""))[:3200]
        if p:
            parts.append(f"--- FILE: {p} ---\n{body}")
    blob = "\n\n".join(parts)
    return f"""{conv}

CURRENT PROJECT FILES (return a full updated JSON project; edit, add, or remove files as needed):
{blob}
"""


def _media_url(request, field_file):
    if not field_file:
        return None
    url = field_file.url
    if url.startswith("http"):
        return url
    return request.build_absolute_uri(url)


def _sender_payload(request, user):
    try:
        profile = user.profile
        avatar = _media_url(request, profile.avatar) if profile.avatar else None
        initials = profile.get_initials()
    except Exception:
        avatar = None
        initials = user.username[:2].upper()
    return avatar, initials


def _broadcast_chat_message(request, room_id, msg, remaining_after=None):
    """Emit the same shape as ChatConsumer.handle_send_message."""
    user = msg.sender
    avatar, initials = _sender_payload(request, user)
    text = msg.text or ""
    msg_type = msg.message_type
    payload = {
        "type": "chat_message",
        "message_id": str(msg.id),
        "text": text,
        "message_type": msg_type,
        "sender_id": user.id,
        "sender_username": user.username,
        "sender_avatar": avatar,
        "sender_initials": initials,
        "reply_to": None,
        "timestamp": msg.created_at.strftime("%H:%M"),
    }
    if remaining_after is not None:
        payload["remaining_messages"] = remaining_after
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(f"chat_{room_id}", payload)


def _notify_offline(request, room, msg, sender, text_preview):
    members = room.members.exclude(id=sender.id).filter(profile__is_online=False)
    for member in members:
        Notification.objects.create(
            recipient=member,
            sender=sender,
            notification_type=Notification.TYPE_MESSAGE,
            title=f"New message from {sender.username}",
            body=text_preview[:200],
            room=room,
            message=msg,
        )


def _quota_check(user, extra_sends: int):
    """Returns (ok, error_response_or_none, remaining_after_batch)."""
    sub, _ = Subscription.objects.get_or_create(user=user)
    if sub.is_pro:
        return True, None, None
    FREE_LIMIT = getattr(settings, "FREE_MESSAGES_PER_DAY", 30)
    today = timezone.now().date()
    daily, _ = DailyMessageCount.objects.get_or_create(user=user, date=today)
    remaining = FREE_LIMIT - daily.count
    if remaining < extra_sends:
        return False, JsonResponse(
            {
                "error": "quota_exceeded",
                "message": f"Posting this transcript needs {extra_sends} messages but you only have {max(0, remaining)} left today.",
                "upgrade_url": "/subscribe/",
            },
            status=403,
        ), None
    return True, None, remaining - extra_sends


def _increment_quota(user, n: int):
    if n <= 0:
        return
    sub, _ = Subscription.objects.get_or_create(user=user)
    if sub.is_pro:
        return
    today = timezone.now().date()
    daily, _ = DailyMessageCount.objects.get_or_create(user=user, date=today)
    daily.count += n
    daily.save()


@login_required
@require_POST
def ai_assistant(request):
    if not is_configured():
        return JsonResponse({"error": "AI is not configured."}, status=503)
    data = _parse_json(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    history = data.get("history") or []
    message = data.get("message") or ""
    if not isinstance(history, list):
        return JsonResponse({"error": "history must be a list."}, status=400)
    denied = enforce_ai_quota(request.user)
    if denied:
        return denied
    reply, err = assistant_reply(history, message)
    if err:
        return JsonResponse({"error": err}, status=400)
    record_ai_use(request.user)
    return JsonResponse({"reply": reply})


@login_required
@require_POST
def ai_transcript(request, room_id):
    if not is_configured():
        return JsonResponse({"error": "AI is not configured."}, status=503)
    room = get_object_or_404(Room, id=room_id)
    if not room.members.filter(id=request.user.id).exists():
        return JsonResponse({"error": "Forbidden."}, status=403)
    data = _parse_json(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    scenario = data.get("scenario") or data.get("prompt") or ""
    num_turns = data.get("num_turns") or data.get("turns") or 8
    include_context = data.get("include_context", True)

    room_summary = ""
    if include_context:
        recent = (
            Message.objects.filter(room=room, is_deleted=False)
            .select_related("sender")
            .order_by("-created_at")[:15]
        )
        bits = []
        for m in reversed(list(recent)):
            who = m.sender.username
            t = (m.text or "").strip() or f"[{m.message_type}]"
            bits.append(f"{who}: {t[:200]}")
        room_summary = "\n".join(bits)

    denied = enforce_ai_quota(request.user)
    if denied:
        return denied
    lines, err = generate_transcript_json(scenario, room_summary, num_turns)
    if err:
        return JsonResponse({"error": err}, status=400)
    record_ai_use(request.user)
    return JsonResponse({"lines": lines})


@login_required
@require_POST
def ai_apply_transcript(request, room_id):
    room = get_object_or_404(Room, id=room_id)
    if not room.members.filter(id=request.user.id).exists():
        return JsonResponse({"error": "Forbidden."}, status=403)
    data = _parse_json(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    lines = data.get("lines")
    if not isinstance(lines, list) or len(lines) < 1:
        return JsonResponse({"error": "lines must be a non-empty array."}, status=400)
    if len(lines) > 24:
        return JsonResponse({"error": "At most 24 lines per request."}, status=400)

    other = room.members.exclude(id=request.user.id).first()
    cleaned = []
    for item in lines:
        if not isinstance(item, dict):
            continue
        side = str(item.get("side", "")).lower()
        text = str(item.get("text", "")).strip()
        if side not in ("me", "other") or not text:
            continue
        cleaned.append((side, text[:2000]))
    if len(cleaned) < 1:
        return JsonResponse({"error": "No valid lines to post."}, status=400)

    ok, err_resp, _ = _quota_check(request.user, len(cleaned))
    if not ok:
        return err_resp

    sub, _ = Subscription.objects.get_or_create(user=request.user)
    FREE_LIMIT = getattr(settings, "FREE_MESSAGES_PER_DAY", 30)
    today = timezone.now().date()
    daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
    remaining = 9999 if sub.is_pro else max(0, FREE_LIMIT - daily.count)

    created_ids = []
    for side, text in cleaned:
        sender = request.user if side == "me" else (other or request.user)
        if side == "other" and other is None:
            text = f"(Demo) {text}"
        msg = Message.objects.create(
            room=room,
            sender=sender,
            message_type=Message.TYPE_TEXT,
            text=text,
        )
        created_ids.append(str(msg.id))
        if not sub.is_pro:
            remaining = max(0, remaining - 1)
        _broadcast_chat_message(request, room_id, msg, remaining_after=remaining if not sub.is_pro else None)
        _notify_offline(request, room, msg, sender, text)

    _increment_quota(request.user, len(cleaned))
    Room.objects.filter(id=room.id).update(updated_at=timezone.now())

    return JsonResponse({"success": True, "message_ids": created_ids, "posted": len(created_ids)})


@login_required
@require_POST
def ai_summarize_room(request, room_id):
    if not is_configured():
        return JsonResponse({"error": "AI is not configured."}, status=503)
    room = get_object_or_404(Room, id=room_id)
    if not room.members.filter(id=request.user.id).exists():
        return JsonResponse({"error": "Forbidden."}, status=403)
    recent = (
        Message.objects.filter(room=room, is_deleted=False, message_type=Message.TYPE_TEXT)
        .select_related("sender")
        .order_by("-created_at")[:50]
    )
    lines = []
    for m in reversed(list(recent)):
        lines.append(f"{m.sender.username}: {(m.text or '').strip()}")
    denied = enforce_ai_quota(request.user)
    if denied:
        return denied
    summary, err = summarize_recent_messages(lines)
    if err:
        return JsonResponse({"error": err}, status=400)
    record_ai_use(request.user)
    return JsonResponse({"summary": summary})


@login_required
@require_POST
def ai_code_project_chat_stream(request):
    if not is_configured():
        return JsonResponse(
            {"error": "AI is not configured. Set GROQ_API_KEY or GEMINI_API_KEY."},
            status=503,
        )
    data = _parse_json(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    messages = data.get("messages")
    phase = (data.get("phase") or "discover").strip() or "discover"
    include_code = data.get("include_code")
    if include_code is None:
        include_code = True
    else:
        include_code = bool(include_code)
    if not isinstance(messages, list) or len(messages) < 1:
        return JsonResponse({"error": "messages (non-empty list) required."}, status=400)
    last = messages[-1]
    if not isinstance(last, dict) or last.get("role") != "user":
        return JsonResponse({"error": "Last message must be from the user."}, status=400)

    denied = enforce_ai_quota(request.user)
    if denied:
        return denied
    record_ai_use(request.user)

    def event_stream():
        try:
            for piece in stream_codegen_coach(messages, phase, include_code):
                if not piece:
                    continue
                yield f"data: {json.dumps({'c': piece}, ensure_ascii=False)}\n\n".encode("utf-8")
            yield b'data: {"t":"done"}\n\n'
        except Exception as e:
            logger.exception("ai_code_project_chat_stream")
            yield f"data: {json.dumps({'e': str(e)[:400]})}\n\n".encode("utf-8")

    resp = StreamingHttpResponse(event_stream(), content_type="text/event-stream; charset=utf-8")
    resp["Cache-Control"] = "no-cache"
    resp["X-Accel-Buffering"] = "no"
    return resp


@login_required
@require_POST
def ai_code_project_generate(request):
    if not is_configured():
        return JsonResponse(
            {"error": "AI is not configured. Set GROQ_API_KEY or GEMINI_API_KEY."},
            status=503,
        )
    data = _parse_json(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)

    stack = (data.get("stack_hint") or data.get("stack") or data.get("tech") or "").strip()
    continuation_custom = (data.get("continuation_note") or "").strip()[:4000]
    truncation_retry = bool(data.get("truncation_retry"))

    messages = data.get("messages")
    files = data.get("files")

    denied = enforce_ai_quota(request.user)
    if denied:
        return denied

    if isinstance(messages, list) and len(messages) > 0:
        if isinstance(files, list) and len(files) > 0:
            prompt = _compose_update_prompt(messages, files)
        else:
            prompt = _compose_prompt_from_messages(messages)
        if not prompt.strip():
            return JsonResponse({"error": "Conversation is empty."}, status=400)
        cont_parts: list[str] = []
        if continuation_custom:
            cont_parts.append(continuation_custom)
        if truncation_retry:
            cont_parts.append(
                "The previous JSON output was truncated or invalid. Output ONE complete valid JSON object only "
                "with keys project_name, summary, files. Use at most 8 files with concise complete content."
            )
        cont_note = "\n".join(cont_parts) if cont_parts else None
        raw, err = generate_code_project(prompt, stack, cont_note)
    else:
        prompt, perr = _compose_code_project_prompt(data)
        if perr:
            return JsonResponse({"error": perr}, status=400)
        cont_note = continuation_custom if continuation_custom else None
        if truncation_retry:
            extra = (
                "The previous JSON output was truncated. Output ONE complete valid JSON object only; "
                "use fewer smaller files."
            )
            cont_note = f"{cont_note}\n{extra}" if cont_note else extra
        raw, err = generate_code_project(prompt, stack, cont_note)

    if err == "OUTPUT_TRUNCATED":
        return JsonResponse(
            {
                "error": "OUTPUT_TRUNCATED",
                "message": "The model hit a size limit. Tap “Continue generation” to retry with tighter output.",
            },
            status=409,
        )
    if err:
        return JsonResponse({"error": err}, status=400)

    files_in = raw.get("files") if isinstance(raw, dict) else None
    if not isinstance(files_in, list):
        return JsonResponse({"error": "Invalid AI output."}, status=400)
    files_out, serr = sanitize_code_files_for_download(files_in)
    if serr:
        return JsonResponse({"error": serr}, status=400)

    record_ai_use(request.user)

    project_name = str(raw.get("project_name") or "generated-project").strip()[:200]
    summary = str(raw.get("summary") or "").strip()[:500]
    return JsonResponse(
        {
            "success": True,
            "project_name": project_name,
            "summary": summary,
            "files": files_out,
        }
    )


@login_required
@require_POST
def ai_code_project_zip(request):
    data = _parse_json(request)
    if data is None:
        return JsonResponse({"error": "Invalid JSON."}, status=400)
    files_raw = data.get("files")
    project_name = (data.get("project_name") or "project").strip()
    if not isinstance(files_raw, list):
        return JsonResponse({"error": "files must be a list."}, status=400)
    cleaned, serr = sanitize_code_files_for_download(files_raw)
    if serr:
        return JsonResponse({"error": serr}, status=400)

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in cleaned:
            zf.writestr(f["path"], f["content"].encode("utf-8"))
    buf.seek(0)
    out = buf.getvalue()
    resp = HttpResponse(out, content_type="application/zip")
    resp["Content-Disposition"] = f'attachment; filename="{zip_attachment_filename(project_name)}"'
    return resp
