"""
Views for the chat application
"""

import json
import os
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponseForbidden, HttpResponse
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Q
from django.db import transaction, IntegrityError
from django.conf import settings
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
import uuid

from django.urls import reverse

from .gif_providers import gif_picker_configured, search_gifs
from .subscription_service import activate_pro_from_stripe
from .models import (
    Room, RoomMembership, Message, Notification, UserProfile,
    Subscription, DailyMessageCount, DailyAiUsage, Reaction,
)

logger = logging.getLogger(__name__)


# ─── Auth Views ───────────────────────────────────────────────────────────────

def login_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        password = request.POST.get('password', '')
        user = authenticate(request, username=username, password=password)
        if user:
            login(request, user)
            _ensure_profile(user)
            return redirect('home')
        error = 'Invalid username or password'
    return render(request, 'chat/login.html', {'error': error})


def register_view(request):
    if request.user.is_authenticated:
        return redirect('home')
    error = None
    if request.method == 'POST':
        username = request.POST.get('username', '').strip()
        email    = request.POST.get('email', '').strip().lower()
        password = request.POST.get('password', '')
        password2 = request.POST.get('password2', '')
        full_name = request.POST.get('full_name', '').strip()
        if not username:
            error = 'Username is required'
        elif not password:
            error = 'Password is required'
        elif password != password2:
            error = 'Passwords do not match'
        elif User.objects.filter(username=username).exists():
            error = 'Username already taken'
        elif email and User.objects.filter(email__iexact=email).exists():
            error = 'Email already registered'
        else:
            try:
                with transaction.atomic():
                    user = User.objects.create_user(
                        username=username,
                        email=email,
                        password=password,
                    )
                    if full_name:
                        parts = full_name.split(' ', 1)
                        user.first_name = parts[0]
                        user.last_name = parts[1] if len(parts) > 1 else ''
                        user.save()
                    _ensure_profile(user)
                    login(request, user, backend=settings.AUTHENTICATION_BACKENDS[0])
                return redirect('home')
            except IntegrityError:
                error = 'Username or email is already in use. Please try a different one.'
            except Exception:
                logger.exception("Registration failed for username=%s", username)
                error = 'Could not create account right now. Please try again.'
    return render(request, 'chat/register.html', {'error': error})


def logout_view(request):
    logout(request)
    return redirect('login')


def _ensure_profile(user):
    UserProfile.objects.get_or_create(user=user)
    Subscription.objects.get_or_create(user=user)


# ─── Main Views ───────────────────────────────────────────────────────────────

@login_required
def home(request):
    _ensure_profile(request.user)
    rooms = Room.objects.filter(members=request.user, is_active=True).order_by('-updated_at')
    sub, _ = Subscription.objects.get_or_create(user=request.user)
    today  = timezone.now().date()
    daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
    FREE_LIMIT = getattr(settings, 'FREE_MESSAGES_PER_DAY', 30)
    remaining  = max(0, FREE_LIMIT - daily.count)
    context = {
        'rooms': rooms,
        'subscription': sub,
        'remaining_messages': remaining,
        'free_limit': FREE_LIMIT,
        'is_pro': sub.is_pro,
        'stripe_public_key': settings.STRIPE_PUBLIC_KEY,
    }
    return render(request, 'chat/home.html', context)


@login_required
def room_view(request, room_id):
    _ensure_profile(request.user)
    room = get_object_or_404(Room, id=room_id)
    if not room.members.filter(id=request.user.id).exists():
        return HttpResponseForbidden('You are not a member of this room')
    messages = Message.objects.filter(
        room=room, is_deleted=False
    ).select_related('sender', 'sender__profile', 'reply_to', 'reply_to__sender').order_by('-created_at')[:50]
    messages = list(reversed(messages))
    RoomMembership.objects.filter(user=request.user, room=room).update(last_read_at=timezone.now())
    sub, _   = Subscription.objects.get_or_create(user=request.user)
    today    = timezone.now().date()
    daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
    FREE_LIMIT = getattr(settings, 'FREE_MESSAGES_PER_DAY', 30)
    remaining  = max(0, FREE_LIMIT - daily.count)
    members = room.members.select_related('profile').all()
    mention_members = [
        {
            "id": m.id,
            "username": m.username,
            "label": (m.get_full_name() or "").strip() or m.username,
        }
        for m in members
        if m.id != request.user.id
    ]
    context = {
        "room": room,
        "messages": messages,
        "members": members,
        "mention_members": mention_members,
        "is_group_room": room.room_type == Room.ROOM_GROUP,
        "subscription": sub,
        "remaining_messages": remaining,
        "free_limit": FREE_LIMIT,
        "is_pro": sub.is_pro,
        "room_name": room.get_display_name(request.user),
        "stripe_public_key": settings.STRIPE_PUBLIC_KEY,
        "gif_picker_enabled": gif_picker_configured(),
        "gif_search_url": reverse("gif_search"),
        "subscription_price": getattr(settings, "SUBSCRIPTION_DISPLAY_PRICE", "₹309"),
        "free_ai_limit": getattr(settings, "FREE_AI_USES_PER_DAY", 10),
    }
    return render(request, "chat/room.html", context)


@login_required
def create_room(request):
    if request.method == 'POST':
        room_type  = request.POST.get('room_type', 'group')
        name       = request.POST.get('name', '').strip()
        member_ids = request.POST.getlist('members')
        if room_type == 'direct' and len(member_ids) == 1:
            other_user = get_object_or_404(User, id=member_ids[0])
            existing   = Room.objects.filter(
                room_type='direct', members=request.user
            ).filter(members=other_user).first()
            if existing:
                return redirect('room', room_id=existing.id)
            room = Room.objects.create(room_type='direct', created_by=request.user)
            RoomMembership.objects.create(user=request.user, room=room, role='admin')
            RoomMembership.objects.create(user=other_user, room=room)
        else:
            if not name:
                return JsonResponse({'error': 'Group name required'}, status=400)
            room = Room.objects.create(room_type='group', name=name, created_by=request.user)
            RoomMembership.objects.create(user=request.user, room=room, role='admin')
            for uid in member_ids:
                try:
                    u = User.objects.get(id=uid)
                    RoomMembership.objects.get_or_create(user=u, room=room)
                except User.DoesNotExist:
                    pass
        return redirect('room', room_id=room.id)
    users = User.objects.exclude(id=request.user.id).select_related('profile')
    return render(request, 'chat/create_room.html', {'users': users})


# ─── File Upload API ──────────────────────────────────────────────────────────

@login_required
@require_POST
def upload_file(request, room_id):
    room = get_object_or_404(Room, id=room_id)
    if not room.members.filter(id=request.user.id).exists():
        return JsonResponse({'error': 'Not a member'}, status=403)
    sub, _ = Subscription.objects.get_or_create(user=request.user)
    if not sub.is_pro:
        today    = timezone.now().date()
        daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
        FREE_LIMIT = getattr(settings, 'FREE_MESSAGES_PER_DAY', 30)
        if daily.count >= FREE_LIMIT:
            return JsonResponse({
                'error': 'quota_exceeded',
                'message': 'Daily limit reached. Upgrade to Pro!',
                'upgrade_url': '/subscribe/'
            }, status=403)
    file = request.FILES.get('file')
    if not file:
        return JsonResponse({'error': 'No file provided'}, status=400)
    content_type = (file.content_type or "").strip().lower()
    name_lower = (getattr(file, "name", "") or "").lower()
    if not content_type or content_type == "application/octet-stream":
        if name_lower.endswith(".gif"):
            content_type = "image/gif"
        elif name_lower.endswith((".jpg", ".jpeg")):
            content_type = "image/jpeg"
        elif name_lower.endswith(".png"):
            content_type = "image/png"
        elif name_lower.endswith(".webp"):
            content_type = "image/webp"
    file_size = file.size
    if content_type in settings.ALLOWED_IMAGE_TYPES:
        msg_type = 'image'
        max_size = settings.MAX_IMAGE_SIZE_MB * 1024 * 1024
    elif content_type in settings.ALLOWED_VIDEO_TYPES:
        msg_type = 'video'
        max_size = settings.MAX_VIDEO_SIZE_MB * 1024 * 1024
    elif content_type in settings.ALLOWED_DOC_TYPES:
        msg_type = 'doc'
        max_size = settings.MAX_DOC_SIZE_MB * 1024 * 1024
    else:
        return JsonResponse({'error': f'File type not supported: {content_type}'}, status=400)
    if file_size > max_size:
        return JsonResponse({'error': f'File too large. Max {max_size//(1024*1024)}MB'}, status=400)
    msg = Message(
        room=room, sender=request.user, message_type=msg_type,
        file_name=file.name, file_size=file_size, mime_type=content_type,
        text=request.POST.get('caption', ''),
    )
    if msg_type == 'image':   msg.image    = file
    elif msg_type == 'video': msg.video    = file
    else:                     msg.document = file
    msg.save()
    today    = timezone.now().date()
    daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
    daily.count += 1
    daily.save()
    try:
        profile  = request.user.profile
        avatar   = profile.avatar.url if profile.avatar else None
        initials = profile.get_initials()
    except Exception:
        avatar   = None
        initials = request.user.username[:2].upper()
    file_url = None
    if msg_type == 'image' and msg.image:       file_url = request.build_absolute_uri(msg.image.url)
    elif msg_type == 'video' and msg.video:     file_url = request.build_absolute_uri(msg.video.url)
    elif msg_type == 'doc' and msg.document:    file_url = request.build_absolute_uri(msg.document.url)
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(f'chat_{room_id}', {
        'type': 'file_message',
        'message_id': str(msg.id),
        'message_type': msg_type,
        'file_url': file_url,
        'file_name': file.name,
        'file_size': msg.get_file_size_display(),
        'mime_type': content_type,
        'caption': msg.text,
        'doc_icon': msg.get_doc_icon(),
        'sender_id': request.user.id,
        'sender_username': request.user.username,
        'sender_avatar': avatar,
        'sender_initials': initials,
        'timestamp': msg.created_at.strftime('%H:%M'),
    })
    for member in room.members.exclude(id=request.user.id).filter(profile__is_online=False):
        Notification.objects.create(
            recipient=member, sender=request.user,
            notification_type=Notification.TYPE_MESSAGE,
            title=f"{request.user.username} sent a {msg_type}",
            body=file.name, room=room, message=msg,
        )
    return JsonResponse({'success': True, 'message_id': str(msg.id), 'file_url': file_url})


# ─── API Endpoints ────────────────────────────────────────────────────────────

@login_required
@require_GET
def search_users(request):
    q = request.GET.get('q', '').strip()
    if len(q) < 2:
        return JsonResponse({'users': []})
    users = User.objects.filter(
        Q(username__icontains=q) | Q(first_name__icontains=q) | Q(last_name__icontains=q)
    ).exclude(id=request.user.id)[:10]
    data = []
    for u in users:
        try:
            avatar   = u.profile.avatar.url if u.profile.avatar else None
            initials = u.profile.get_initials()
            is_online = u.profile.is_online
        except Exception:
            avatar = None; initials = u.username[:2].upper(); is_online = False
        data.append({'id': u.id, 'username': u.username, 'full_name': u.get_full_name(),
                     'avatar': avatar, 'initials': initials, 'is_online': is_online})
    return JsonResponse({'users': data})


@login_required
@require_GET
def notifications_list(request):
    notifs = Notification.objects.filter(recipient=request.user).select_related('sender', 'room')[:20]
    data   = []
    for n in notifs:
        data.append({
            'id': str(n.id), 'type': n.notification_type,
            'title': n.title, 'body': n.body, 'is_read': n.is_read,
            'room_id': str(n.room.id) if n.room else None,
            'sender': n.sender.username if n.sender else None,
            'timestamp': n.created_at.strftime('%b %d, %H:%M'),
        })
    return JsonResponse({'notifications': data})


@login_required
@require_POST
def mark_notification_read(request, notification_id):
    Notification.objects.filter(id=notification_id, recipient=request.user).update(is_read=True)
    return JsonResponse({'success': True})


@login_required
@require_GET
def gif_search(request):
    """Search Tenor/Giphy for GIFs (API keys on server only)."""
    if not gif_picker_configured():
        return JsonResponse(
            {
                "enabled": False,
                "results": [],
                "message": "Set GIPHY_API_KEY to enable GIF search (get a key at developers.giphy.com).",
            }
        )
    q = request.GET.get("q", "").strip()
    results, provider = search_gifs(q, 24)
    return JsonResponse({"enabled": True, "provider": provider, "results": results})


def _message_reply_json(msg):
    if not getattr(msg, "reply_to_id", None):
        return None
    rt = msg.reply_to
    if rt is None:
        return None
    preview = (rt.text or "")[:100] if rt.text else ""
    if rt.message_type == Message.TYPE_GIF:
        preview = "🎞️ GIF"
    elif rt.message_type == Message.TYPE_IMAGE:
        preview = "📷 Photo"
    elif rt.message_type == Message.TYPE_VIDEO:
        preview = "🎥 Video"
    elif rt.message_type == Message.TYPE_DOC:
        preview = rt.file_name or "📎 File"
    return {
        "id": str(rt.id),
        "text": preview or "Message",
        "sender": rt.sender.username,
    }


@login_required
def get_messages(request, room_id):
    room = get_object_or_404(Room, id=room_id)
    if not room.members.filter(id=request.user.id).exists():
        return JsonResponse({'error': 'Forbidden'}, status=403)
    before_id = request.GET.get('before')
    qs = Message.objects.filter(room=room, is_deleted=False)
    if before_id:
        try:
            before_msg = Message.objects.get(id=before_id)
            qs = qs.filter(created_at__lt=before_msg.created_at)
        except Message.DoesNotExist:
            pass
    messages = qs.select_related(
        "sender", "sender__profile", "reply_to", "reply_to__sender"
    ).order_by("-created_at")[:20]
    data = []
    for msg in reversed(list(messages)):
        try:
            avatar   = msg.sender.profile.avatar.url if msg.sender.profile.avatar else None
            initials = msg.sender.profile.get_initials()
        except Exception:
            avatar = None; initials = msg.sender.username[:2].upper()
        d = {
            'id': str(msg.id), 'type': msg.message_type, 'text': msg.text,
            'sender_id': msg.sender.id, 'sender_username': msg.sender.username,
            'sender_avatar': avatar, 'sender_initials': initials,
            'timestamp': msg.created_at.strftime('%H:%M'),
            'is_own': msg.sender == request.user,
        }
        if msg.message_type == Message.TYPE_GIF and msg.gif_url:
            d["type"] = "gif"
            d["gif_url"] = msg.gif_url
            d["file_name"] = "GIF"
            d["mime_type"] = "image/gif"
        elif msg.image:
            d["file_url"] = request.build_absolute_uri(msg.image.url)
            d["file_name"] = msg.file_name or ""
            d["mime_type"] = msg.mime_type or ""
        elif msg.video:
            d["file_url"] = request.build_absolute_uri(msg.video.url)
            d["file_name"] = msg.file_name or ""
            d["mime_type"] = msg.mime_type or ""
        elif msg.document:
            d["file_url"] = request.build_absolute_uri(msg.document.url)
            d["file_name"] = msg.file_name
            d["file_size"] = msg.get_file_size_display()
            d["doc_icon"] = msg.get_doc_icon()
        rj = _message_reply_json(msg)
        if rj:
            d["reply_to"] = rj
        data.append(d)
    return JsonResponse({'messages': data, 'has_more': len(data) == 20})


# ─── Subscription Views ───────────────────────────────────────────────────────

@login_required
def subscription_page(request):
    _ensure_profile(request.user)
    sub, _   = Subscription.objects.get_or_create(user=request.user)
    today    = timezone.now().date()
    daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
    ai_daily, _ = DailyAiUsage.objects.get_or_create(user=request.user, date=today)
    FREE_LIMIT = getattr(settings, 'FREE_MESSAGES_PER_DAY', 30)
    AI_LIMIT = getattr(settings, 'FREE_AI_USES_PER_DAY', 10)
    context = {
        'subscription': sub,
        'remaining': max(0, FREE_LIMIT - daily.count),
        'free_limit': FREE_LIMIT,
        'free_ai_limit': AI_LIMIT,
        'ai_used_today': 0 if sub.is_pro else ai_daily.count,
        'ai_remaining': 9999 if sub.is_pro else max(0, AI_LIMIT - ai_daily.count),
        'is_pro': sub.is_pro,
        'stripe_public_key': settings.STRIPE_PUBLIC_KEY,
        'subscription_price': getattr(settings, 'SUBSCRIPTION_DISPLAY_PRICE', '₹309'),
        'subscription_trial_days': int(getattr(settings, 'SUBSCRIPTION_TRIAL_DAYS', 0)),
    }
    return render(request, 'chat/subscription.html', context)


@login_required
@require_POST
def create_checkout_session(request):
    """
    Create a Stripe Checkout session.

    Flow:
      1. User clicks Upgrade on /subscribe/
      2. This view creates a Stripe Checkout session for the recurring Price (SUBSCRIPTION_PRICE_ID).
      3. User is redirected to Stripe-hosted checkout (amount must match SUBSCRIPTION_DISPLAY_PRICE in UI).
      4. On success → /subscribe/success/ → plan activated via webhook
    """
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY

    price_id = getattr(settings, 'SUBSCRIPTION_PRICE_ID', '')
    if not price_id:
        return JsonResponse({
            'error': 'Stripe Price ID not configured. Set STRIPE_PRICE_ID in environment variables.'
        }, status=400)

    try:
        # Build success / cancel URLs
        success_url = request.build_absolute_uri('/subscribe/success/?session_id={CHECKOUT_SESSION_ID}')
        cancel_url  = request.build_absolute_uri('/subscribe/?cancelled=1')

        session_params = {
            'payment_method_types': ['card'],
            'mode': 'subscription',
            'client_reference_id': str(request.user.id),
            'customer_email': request.user.email or None,
            'success_url': success_url,
            'cancel_url': cancel_url,
            'line_items': [{
                'price': price_id,
                'quantity': 1,
            }],
            # Allow promotion codes in the checkout
            'allow_promotion_codes': True,
            # Collect billing address
            'billing_address_collection': 'auto',
            # Locale — auto-detects Indian users
            'locale': 'auto',
        }

        # Add trial period if configured (e.g. SUBSCRIPTION_TRIAL_DAYS=7)
        trial_days = int(getattr(settings, 'SUBSCRIPTION_TRIAL_DAYS', 0))
        if trial_days > 0:
            session_params['subscription_data'] = {
                'trial_period_days': trial_days,
            }

        session = stripe.checkout.Session.create(**session_params)
        return JsonResponse({'checkout_url': session.url})

    except stripe.error.AuthenticationError:
        return JsonResponse({'error': 'Invalid Stripe API key. Check STRIPE_SECRET_KEY.'}, status=400)
    except stripe.error.InvalidRequestError as e:
        return JsonResponse({'error': f'Stripe error: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
def subscription_success(request):
    """
    Called after successful Stripe Checkout.
    Works even WITHOUT webhooks (local dev, webhook not configured).
    Retrieves the session from Stripe directly and activates Pro immediately.
    """
    session_id = request.GET.get('session_id', '')
    activation_error = None

    if session_id and settings.STRIPE_SECRET_KEY:
        try:
            import stripe
            stripe.api_key = settings.STRIPE_SECRET_KEY

            session = stripe.checkout.Session.retrieve(
                session_id,
                expand=['subscription'],
            )

            ref = getattr(session, 'client_reference_id', None) or ''
            if ref and str(request.user.id) != str(ref):
                activation_error = (
                    'This payment was started from a different account. '
                    'Log in with the same user you used at checkout, or use “Already paid” on the subscribe page.'
                )
            elif session.status != 'complete':
                activation_error = f'Checkout is not complete yet (status: {session.status}).'
            elif getattr(session, 'mode', None) != 'subscription':
                activation_error = 'This session is not a subscription checkout.'
            elif not getattr(session, 'subscription', None):
                activation_error = 'No subscription was created on this checkout. Contact support if you were charged.'
            else:
                # Trial checkouts: payment_status is often "unpaid" until the first charge — still valid Pro start
                activate_pro_from_stripe(
                    request.user,
                    _stripe_id(session.customer),
                    _stripe_id(session.subscription),
                )

        except Exception as e:
            activation_error = str(e)

    # Always show success page — worst case webhook fires later
    return render(request, 'chat/subscription_success.html', {
        'activation_error': activation_error
    })


def _stripe_id(obj):
    """Stripe expand returns str id or object/dict with id."""
    if obj is None:
        return ''
    if isinstance(obj, str):
        return obj
    if isinstance(obj, dict):
        return str(obj.get('id', '') or '')
    return str(getattr(obj, 'id', '') or '')


def _activate_pro_from_session(user, session):
    """Activate Pro from a Checkout Session (manual recovery / legacy callers)."""
    customer_id = _stripe_id(getattr(session, 'customer', None))
    subscription_id = _stripe_id(getattr(session, 'subscription', None))
    activate_pro_from_stripe(user, customer_id, subscription_id)


@login_required
@require_POST
def cancel_subscription(request):
    """Cancel the user's Pro subscription via Stripe."""
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY
    sub, _ = Subscription.objects.get_or_create(user=request.user)
    if not sub.stripe_subscription_id:
        return JsonResponse({'error': 'No active subscription found.'}, status=400)
    try:
        # Cancel at period end — user keeps Pro until billing cycle ends
        stripe.Subscription.modify(
            sub.stripe_subscription_id,
            cancel_at_period_end=True,
        )
        Notification.objects.create(
            recipient=request.user,
            notification_type=Notification.TYPE_SUBSCRIPTION,
            title='Subscription cancellation scheduled',
            body='Your Pro subscription will end at the current billing period. You will move to the Free plan after that.',
        )
        return JsonResponse({'success': True, 'message': 'Subscription will cancel at period end.'})
    except stripe.error.StripeError as e:
        return JsonResponse({'error': str(e)}, status=400)


# ─── Profile ──────────────────────────────────────────────────────────────────

@login_required
def profile_view(request):
    _ensure_profile(request.user)
    error = None; success = None
    if request.method == 'POST':
        action = request.POST.get('action')
        if action == 'update_profile':
            full_name = request.POST.get('full_name', '').strip()
            bio       = request.POST.get('bio', '').strip()
            if full_name:
                parts = full_name.split(' ', 1)
                request.user.first_name = parts[0]
                request.user.last_name  = parts[1] if len(parts) > 1 else ''
                request.user.save()
            profile = request.user.profile
            profile.bio = bio[:200]
            if 'avatar' in request.FILES:
                av = request.FILES['avatar']
                if av.content_type not in settings.ALLOWED_IMAGE_TYPES:
                    error = 'Only images allowed as avatars.'
                elif av.size > 2 * 1024 * 1024:
                    error = 'Avatar must be under 2 MB.'
                else:
                    profile.avatar = av
            if not error:
                profile.save()
                success = 'Profile updated!'
        elif action == 'change_password':
            old_pw  = request.POST.get('old_password', '')
            new_pw  = request.POST.get('new_password', '')
            new_pw2 = request.POST.get('new_password2', '')
            if not request.user.check_password(old_pw):
                error = 'Current password is incorrect.'
            elif new_pw != new_pw2:
                error = 'New passwords do not match.'
            elif len(new_pw) < 8:
                error = 'Password must be at least 8 characters.'
            else:
                request.user.set_password(new_pw)
                request.user.save()
                success = 'Password changed. Please log in again.'
    sub, _   = Subscription.objects.get_or_create(user=request.user)
    today    = timezone.now().date()
    daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
    FREE_LIMIT = getattr(settings, 'FREE_MESSAGES_PER_DAY', 30)
    context = {
        'subscription': sub, 'remaining': max(0, FREE_LIMIT - daily.count),
        'free_limit': FREE_LIMIT, 'error': error, 'success': success,
    }
    return render(request, 'chat/profile.html', context)


# ─── API: Quota ───────────────────────────────────────────────────────────────

@login_required
@require_GET
def get_quota(request):
    _ensure_profile(request.user)
    sub, _ = Subscription.objects.get_or_create(user=request.user)
    FREE_LIMIT = getattr(settings, 'FREE_MESSAGES_PER_DAY', 30)
    AI_LIMIT = getattr(settings, 'FREE_AI_USES_PER_DAY', 10)
    today = timezone.now().date()
    ai_daily, _ = DailyAiUsage.objects.get_or_create(user=request.user, date=today)
    if sub.is_pro:
        return JsonResponse({
            'is_pro': True,
            'remaining': 9999,
            'limit': FREE_LIMIT,
            'ai_unlimited': True,
            'ai_limit': AI_LIMIT,
            'ai_used': ai_daily.count,
            'ai_remaining': 9999,
        })
    daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
    remaining = max(0, FREE_LIMIT - daily.count)
    ai_rem = max(0, AI_LIMIT - ai_daily.count)
    return JsonResponse({
        'is_pro': False,
        'remaining': remaining,
        'limit': FREE_LIMIT,
        'used': daily.count,
        'ai_unlimited': False,
        'ai_limit': AI_LIMIT,
        'ai_used': ai_daily.count,
        'ai_remaining': ai_rem,
    })


# ─── Manual Pro Activation (for missed webhooks) ─────────────────────────────

@login_required
@require_POST
def manual_activate(request):
    """
    User already paid but plan didn't activate (webhook missed).
    Looks up their latest Stripe session and activates manually.
    Safe to call multiple times — idempotent.
    """
    import stripe
    stripe.api_key = settings.STRIPE_SECRET_KEY

    sub, _ = Subscription.objects.get_or_create(user=request.user)

    # Already pro
    if sub.is_pro:
        return JsonResponse({'success': True, 'message': 'Your Pro plan is already active!'})

    if not settings.STRIPE_SECRET_KEY:
        return JsonResponse({'error': 'Stripe not configured.'}, status=400)

    try:
        sessions = stripe.checkout.Session.list(limit=20)
        paid_session = None
        for s in sessions.data:
            if str(s.client_reference_id) != str(request.user.id):
                continue
            if s.status != 'complete' or s.mode != 'subscription':
                continue
            if not s.subscription:
                continue
            # Include trial: payment_status may be "unpaid" until first charge
            paid_session = s
            break

        if paid_session:
            _activate_pro_from_session(request.user, paid_session)
            return JsonResponse({'success': True, 'message': '✅ Pro plan activated! Refresh the page.'})
        else:
            if request.user.email:
                customers = stripe.Customer.list(email=request.user.email, limit=1)
                if customers.data:
                    cust = customers.data[0]
                    subs = stripe.Subscription.list(customer=cust.id, limit=5)
                    stripe_sub = None
                    for cand in subs.data:
                        if getattr(cand, 'status', None) in ('active', 'trialing'):
                            stripe_sub = cand
                            break
                    if stripe_sub:
                        activate_pro_from_stripe(
                            request.user,
                            cust.id,
                            stripe_sub.id,
                        )
                        return JsonResponse({'success': True, 'message': '✅ Pro plan activated! Refresh the page.'})

            return JsonResponse({
                'error': 'No completed payment found for your account. '
                         'Please contact support with your payment receipt.'
            }, status=404)

    except stripe.error.StripeError as e:
        return JsonResponse({'error': f'Stripe error: {str(e)}'}, status=400)
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


# ─── ASGI Health Check ────────────────────────────────────────────────────────

def ws_check(request):
    """Visit /ws-check/ to confirm Daphne (ASGI) is serving."""
    import sys
    sv = request.META.get('SERVER_SOFTWARE', '')
    server = 'daphne' if 'daphne' in sv.lower() else ('gunicorn ← WRONG' if 'gunicorn' in sv.lower() else 'asgi/daphne')
    cl = settings.CHANNEL_LAYERS['default']['BACKEND'].split('.')[-1]
    return HttpResponse(json.dumps({
        'server': server, 'channel_layer': cl,
        'debug': settings.DEBUG, 'allowed_hosts': settings.ALLOWED_HOSTS,
    }, indent=2), content_type='application/json')