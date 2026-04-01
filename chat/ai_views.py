"""
JSON API views for Gemini-powered assistant and AI-generated chat transcripts.
"""

from __future__ import annotations

import json
import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.views.decorators.http import require_POST

from .gemini_client import (
    assistant_reply,
    generate_transcript_json,
    is_configured,
    summarize_recent_messages,
)
from .models import DailyMessageCount, Message, Notification, Room, Subscription

logger = logging.getLogger(__name__)


def _parse_json(request):
    if not request.body:
        return {}
    try:
        return json.loads(request.body.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return None


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
    reply, err = assistant_reply(history, message)
    if err:
        return JsonResponse({"error": err}, status=400)
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

    lines, err = generate_transcript_json(scenario, room_summary, num_turns)
    if err:
        return JsonResponse({"error": err}, status=400)
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
    summary, err = summarize_recent_messages(lines)
    if err:
        return JsonResponse({"error": err}, status=400)
    return JsonResponse({"summary": summary})
