"""
Views for the chat application
"""

import json
import os
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.http import JsonResponse, HttpResponseForbidden
from django.views.decorators.http import require_POST, require_GET
from django.views.decorators.csrf import csrf_exempt
from django.utils import timezone
from django.db.models import Q, Count
from django.conf import settings
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
import uuid

from .models import (
    Room, RoomMembership, Message, Notification, UserProfile,
    Subscription, DailyMessageCount, Reaction
)


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
        email = request.POST.get('email', '').strip()
        password = request.POST.get('password', '')
        password2 = request.POST.get('password2', '')
        full_name = request.POST.get('full_name', '').strip()

        if password != password2:
            error = 'Passwords do not match'
        elif User.objects.filter(username=username).exists():
            error = 'Username already taken'
        elif User.objects.filter(email=email).exists():
            error = 'Email already registered'
        else:
            user = User.objects.create_user(
                username=username, email=email, password=password
            )
            if full_name:
                parts = full_name.split(' ', 1)
                user.first_name = parts[0]
                user.last_name = parts[1] if len(parts) > 1 else ''
                user.save()
            _ensure_profile(user)
            login(request, user)
            return redirect('home')
    
    return render(request, 'chat/register.html', {'error': error})


def logout_view(request):
    logout(request)
    return redirect('login')


def _ensure_profile(user):
    """Create profile and subscription if missing."""
    UserProfile.objects.get_or_create(user=user)
    Subscription.objects.get_or_create(user=user)


# ─── Main Views ───────────────────────────────────────────────────────────────

@login_required
def home(request):
    _ensure_profile(request.user)
    rooms = Room.objects.filter(
        members=request.user, is_active=True
    ).order_by('-updated_at')

    # Get subscription info
    sub, _ = Subscription.objects.get_or_create(user=request.user)
    today = timezone.now().date()
    daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
    
    FREE_LIMIT = getattr(settings, 'FREE_MESSAGES_PER_DAY', 30)
    remaining = max(0, FREE_LIMIT - daily.count)

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
    
    # Get last 50 messages
    messages = Message.objects.filter(
        room=room, is_deleted=False
    ).select_related('sender', 'sender__profile', 'reply_to', 'reply_to__sender').order_by('-created_at')[:50]
    messages = list(reversed(messages))

    # Mark room as read
    RoomMembership.objects.filter(user=request.user, room=room).update(
        last_read_at=timezone.now()
    )

    # Subscription info
    sub, _ = Subscription.objects.get_or_create(user=request.user)
    today = timezone.now().date()
    daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
    FREE_LIMIT = getattr(settings, 'FREE_MESSAGES_PER_DAY', 30)
    remaining = max(0, FREE_LIMIT - daily.count)

    # Room members with online status
    members = room.members.select_related('profile').all()

    context = {
        'room': room,
        'messages': messages,
        'members': members,
        'subscription': sub,
        'remaining_messages': remaining,
        'free_limit': FREE_LIMIT,
        'is_pro': sub.is_pro,
        'room_name': room.get_display_name(request.user),
        'stripe_public_key': settings.STRIPE_PUBLIC_KEY,
    }
    return render(request, 'chat/room.html', context)


@login_required
def create_room(request):
    if request.method == 'POST':
        room_type = request.POST.get('room_type', 'group')
        name = request.POST.get('name', '').strip()
        member_ids = request.POST.getlist('members')
        
        if room_type == 'direct' and len(member_ids) == 1:
            # Check for existing DM
            other_user = get_object_or_404(User, id=member_ids[0])
            existing = Room.objects.filter(
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
            room = Room.objects.create(
                room_type='group', name=name, created_by=request.user
            )
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
    """Handle image, video, doc uploads"""
    room = get_object_or_404(Room, id=room_id)
    if not room.members.filter(id=request.user.id).exists():
        return JsonResponse({'error': 'Not a member'}, status=403)

    # Check quota
    sub, _ = Subscription.objects.get_or_create(user=request.user)
    if not sub.is_pro:
        today = timezone.now().date()
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

    content_type = file.content_type
    file_size = file.size

    # Determine type and validate
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
        return JsonResponse({'error': f'File too large. Max size is {max_size // (1024*1024)}MB'}, status=400)

    # Create message
    msg = Message(
        room=room,
        sender=request.user,
        message_type=msg_type,
        file_name=file.name,
        file_size=file_size,
        mime_type=content_type,
        text=request.POST.get('caption', ''),
    )

    if msg_type == 'image':
        msg.image = file
    elif msg_type == 'video':
        msg.video = file
    else:
        msg.document = file

    msg.save()

    # Increment count
    today = timezone.now().date()
    daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
    daily.count += 1
    daily.save()

    # Get avatar info
    try:
        profile = request.user.profile
        avatar = profile.avatar.url if profile.avatar else None
        initials = profile.get_initials()
    except Exception:
        avatar = None
        initials = request.user.username[:2].upper()

    # Get file URL
    file_url = None
    if msg_type == 'image' and msg.image:
        file_url = request.build_absolute_uri(msg.image.url)
    elif msg_type == 'video' and msg.video:
        file_url = request.build_absolute_uri(msg.video.url)
    elif msg_type == 'doc' and msg.document:
        file_url = request.build_absolute_uri(msg.document.url)

    # Broadcast via WebSocket
    channel_layer = get_channel_layer()
    async_to_sync(channel_layer.group_send)(
        f'chat_{room_id}',
        {
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
        }
    )

    # Send notifications to offline members
    for member in room.members.exclude(id=request.user.id).filter(profile__is_online=False):
        Notification.objects.create(
            recipient=member,
            sender=request.user,
            notification_type=Notification.TYPE_MESSAGE,
            title=f"{request.user.username} sent a {msg_type}",
            body=file.name,
            room=room,
            message=msg,
        )

    return JsonResponse({
        'success': True,
        'message_id': str(msg.id),
        'file_url': file_url,
    })


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
            avatar = u.profile.avatar.url if u.profile.avatar else None
            initials = u.profile.get_initials()
            is_online = u.profile.is_online
        except Exception:
            avatar = None
            initials = u.username[:2].upper()
            is_online = False
        
        data.append({
            'id': u.id,
            'username': u.username,
            'full_name': u.get_full_name(),
            'avatar': avatar,
            'initials': initials,
            'is_online': is_online,
        })
    
    return JsonResponse({'users': data})


@login_required
@require_GET
def notifications_list(request):
    notifs = Notification.objects.filter(
        recipient=request.user
    ).select_related('sender', 'room')[:20]
    
    data = []
    for n in notifs:
        data.append({
            'id': str(n.id),
            'type': n.notification_type,
            'title': n.title,
            'body': n.body,
            'is_read': n.is_read,
            'room_id': str(n.room.id) if n.room else None,
            'sender': n.sender.username if n.sender else None,
            'timestamp': n.created_at.strftime('%b %d, %H:%M'),
        })
    
    return JsonResponse({'notifications': data})


@login_required
@require_POST
def mark_notification_read(request, notification_id):
    Notification.objects.filter(
        id=notification_id, recipient=request.user
    ).update(is_read=True)
    return JsonResponse({'success': True})


@login_required
def subscription_page(request):
    sub, _ = Subscription.objects.get_or_create(user=request.user)
    today = timezone.now().date()
    daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
    FREE_LIMIT = getattr(settings, 'FREE_MESSAGES_PER_DAY', 30)
    
    context = {
        'subscription': sub,
        'remaining': max(0, FREE_LIMIT - daily.count),
        'free_limit': FREE_LIMIT,
        'is_pro': sub.is_pro,
        'stripe_public_key': settings.STRIPE_PUBLIC_KEY,
    }
    return render(request, 'chat/subscription.html', context)


@login_required
def get_messages(request, room_id):
    """Load older messages (pagination)"""
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
    
    messages = qs.select_related('sender', 'sender__profile').order_by('-created_at')[:20]
    
    data = []
    for msg in reversed(messages):
        try:
            avatar = msg.sender.profile.avatar.url if msg.sender.profile.avatar else None
            initials = msg.sender.profile.get_initials()
        except Exception:
            avatar = None
            initials = msg.sender.username[:2].upper()
        
        d = {
            'id': str(msg.id),
            'type': msg.message_type,
            'text': msg.text,
            'sender_id': msg.sender.id,
            'sender_username': msg.sender.username,
            'sender_avatar': avatar,
            'sender_initials': initials,
            'timestamp': msg.created_at.strftime('%H:%M'),
            'is_own': msg.sender == request.user,
        }
        
        if msg.image:
            d['file_url'] = request.build_absolute_uri(msg.image.url)
        elif msg.video:
            d['file_url'] = request.build_absolute_uri(msg.video.url)
        elif msg.document:
            d['file_url'] = request.build_absolute_uri(msg.document.url)
            d['file_name'] = msg.file_name
            d['file_size'] = msg.get_file_size_display()
            d['doc_icon'] = msg.get_doc_icon()
        
        data.append(d)
    
    return JsonResponse({'messages': data, 'has_more': len(data) == 20})


# ─── Stripe Checkout ──────────────────────────────────────────────────────────

@login_required
@require_POST
def create_checkout_session(request):
    """Create a Stripe Checkout session for Pro subscription."""
    try:
        import stripe
        stripe.api_key = settings.STRIPE_SECRET_KEY

        session = stripe.checkout.Session.create(
            payment_method_types=['card'],
            line_items=[{
                'price': settings.SUBSCRIPTION_PRICE_ID,
                'quantity': 1,
            }],
            mode='subscription',
            client_reference_id=str(request.user.id),
            customer_email=request.user.email,
            success_url=request.build_absolute_uri('/subscribe/success/'),
            cancel_url=request.build_absolute_uri('/subscribe/'),
        )
        return JsonResponse({'checkout_url': session.url})
    except Exception as e:
        return JsonResponse({'error': str(e)}, status=400)


@login_required
def subscription_success(request):
    """Landing page after successful Stripe payment."""
    return render(request, 'chat/subscription_success.html')


@login_required
def stripe_webhook_view(request):
    """Proxy to webhooks module (imported directly in urls.py)."""
    pass


@login_required
def profile_view(request):
    """User profile page — update avatar, bio, display name."""
    _ensure_profile(request.user)
    error = None
    success = None

    if request.method == 'POST':
        action = request.POST.get('action')

        if action == 'update_profile':
            full_name = request.POST.get('full_name', '').strip()
            bio = request.POST.get('bio', '').strip()

            if full_name:
                parts = full_name.split(' ', 1)
                request.user.first_name = parts[0]
                request.user.last_name = parts[1] if len(parts) > 1 else ''
                request.user.save()

            profile = request.user.profile
            profile.bio = bio[:200]

            if 'avatar' in request.FILES:
                av = request.FILES['avatar']
                if av.content_type not in settings.ALLOWED_IMAGE_TYPES:
                    error = 'Only images are allowed as avatars.'
                elif av.size > 2 * 1024 * 1024:
                    error = 'Avatar must be under 2 MB.'
                else:
                    profile.avatar = av

            if not error:
                profile.save()
                success = 'Profile updated successfully!'

        elif action == 'change_password':
            old_pw = request.POST.get('old_password', '')
            new_pw = request.POST.get('new_password', '')
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

    sub, _ = Subscription.objects.get_or_create(user=request.user)
    today = timezone.now().date()
    daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
    FREE_LIMIT = getattr(settings, 'FREE_MESSAGES_PER_DAY', 30)

    context = {
        'subscription': sub,
        'remaining': max(0, FREE_LIMIT - daily.count),
        'free_limit': FREE_LIMIT,
        'error': error,
        'success': success,
    }
    return render(request, 'chat/profile.html', context)


@login_required
@require_GET
def get_quota(request):
    """Return current message quota for the logged-in user."""
    _ensure_profile(request.user)
    sub, _ = Subscription.objects.get_or_create(user=request.user)
    FREE_LIMIT = getattr(settings, 'FREE_MESSAGES_PER_DAY', 30)

    if sub.is_pro:
        return JsonResponse({'is_pro': True, 'remaining': 9999, 'limit': FREE_LIMIT})

    today = timezone.now().date()
    daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
    remaining = max(0, FREE_LIMIT - daily.count)
    return JsonResponse({
        'is_pro': False,
        'remaining': remaining,
        'limit': FREE_LIMIT,
        'used': daily.count,
    })
