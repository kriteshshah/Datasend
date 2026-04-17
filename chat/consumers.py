"""
Django Channels WebSocket Consumer
Handles real-time chat, notifications, typing indicators, online status
"""

import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from datetime import timedelta
from django.contrib.auth.models import User
import uuid


class ChatConsumer(AsyncWebsocketConsumer):
    """
    Main chat consumer - handles messages, typing, read receipts
    """

    async def connect(self):
        self.user = self.scope['user']
        if not self.user.is_authenticated:
            await self.close()
            return

        self.room_id = self.scope['url_route']['kwargs']['room_id']
        self.room_group_name = f'chat_{self.room_id}'

        # Verify user is member of room
        is_member = await self.check_room_membership()
        if not is_member:
            await self.close()
            return

        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        # Mark user online
        await self.set_user_online(True)

        await self.accept()

        # Notify room that user came online
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'user_status',
                'user_id': self.user.id,
                'username': self.user.username,
                'status': 'online'
            }
        )

    async def disconnect(self, close_code):
        if hasattr(self, 'room_group_name'):
            # Mark user offline
            await self.set_user_online(False)

            # Notify room
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'user_status',
                    'user_id': self.user.id,
                    'username': self.user.username,
                    'status': 'offline',
                    'last_seen': timezone.now().strftime('%Y-%m-%d %H:%M:%S')
                }
            )

            await self.channel_layer.group_discard(
                self.room_group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('action')

        handlers = {
            'send_message': self.handle_send_message,
            'send_gif': self.handle_send_gif,
            'typing': self.handle_typing,
            'mark_read': self.handle_mark_read,
            'delete_message': self.handle_delete_message,
            'add_reaction': self.handle_reaction,
        }

        handler = handlers.get(action)
        if handler:
            await handler(data)

    # ─── Action Handlers ───────────────────────────────────────────────────────

    async def handle_send_message(self, data):
        """Handle plain text/emoji messages sent via WebSocket"""
        text = data.get('text', '').strip()
        reply_to_id = data.get('reply_to')

        if not text:
            return

        # Check subscription / daily limit
        can_send, remaining = await self.check_message_quota()
        if not can_send:
            await self.send(text_data=json.dumps({
                'type': 'quota_exceeded',
                'message': 'You have reached your daily free message limit (30 messages). Upgrade to Pro for unlimited messaging!',
                'upgrade_url': '/subscribe/'
            }))
            return

        # Detect if emoji-only
        msg_type = 'emoji' if self.is_emoji_only(text) else 'text'

        # Save message
        message = await self.save_text_message(text, msg_type, reply_to_id)

        # Increment daily count
        await self.increment_message_count()

        # Broadcast to room
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message',
                'message_id': str(message['id']),
                'text': text,
                'message_type': msg_type,
                'sender_id': self.user.id,
                'sender_username': self.user.username,
                'sender_avatar': message['sender_avatar'],
                'sender_initials': message['sender_initials'],
                'reply_to': message.get('reply_to'),
                'timestamp': message['timestamp'],
                'can_delete': message.get('can_delete', True),
                'remaining_messages': remaining - 1,
            }
        )

        # Send notification to offline members
        await self.send_message_notifications(str(message['id']), text)

    async def handle_send_gif(self, data):
        from .gif_providers import is_allowed_gif_cdn_url

        gif_url = (data.get('url') or '').strip()[:2048]
        reply_to_id = data.get('reply_to')
        if not gif_url or not is_allowed_gif_cdn_url(gif_url):
            return

        can_send, remaining = await self.check_message_quota()
        if not can_send:
            await self.send(text_data=json.dumps({
                'type': 'quota_exceeded',
                'message': 'You have reached your daily free message limit (30 messages). Upgrade to Pro for unlimited messaging!',
                'upgrade_url': '/subscribe/'
            }))
            return

        message = await self.save_gif_message(gif_url, reply_to_id)
        await self.increment_message_count()

        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'file_message',
                'message_id': str(message['id']),
                'message_type': 'gif',
                'gif_url': gif_url,
                'file_name': 'GIF',
                'file_size': '',
                'mime_type': 'image/gif',
                'caption': '',
                'doc_icon': '🎞️',
                'sender_id': self.user.id,
                'sender_username': self.user.username,
                'sender_avatar': message['sender_avatar'],
                'sender_initials': message['sender_initials'],
                'reply_to': message.get('reply_to'),
                'timestamp': message['timestamp'],
                'remaining_messages': remaining - 1,
            }
        )
        await self.send_message_notifications(str(message['id']), 'GIF')

    async def handle_typing(self, data):
        """Broadcast typing indicator"""
        is_typing = data.get('is_typing', False)
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'typing_indicator',
                'user_id': self.user.id,
                'username': self.user.username,
                'is_typing': is_typing,
            }
        )

    async def handle_mark_read(self, data):
        """Mark messages as read"""
        await self.update_last_read()
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'read_receipt',
                'user_id': self.user.id,
                'username': self.user.username,
                'room_id': self.room_id,
            }
        )

    async def handle_delete_message(self, data):
        msg_id = data.get('message_id')
        if not msg_id:
            return
        result = await self.delete_message(msg_id)
        if result.get('success'):
            await self.channel_layer.group_send(
                self.room_group_name,
                {
                    'type': 'message_deleted',
                    'message_id': msg_id,
                    'deleted_by': self.user.username,
                }
            )
        else:
            await self.send(text_data=json.dumps({
                'type': 'delete_failed',
                'message_id': msg_id,
                'error': result.get('error', 'Unable to delete this message.'),
            }))

    async def handle_reaction(self, data):
        msg_id = data.get('message_id')
        emoji = data.get('emoji')
        if not msg_id or not emoji:
            return
        reaction_data = await self.toggle_reaction(msg_id, emoji)
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'reaction_update',
                'message_id': msg_id,
                'emoji': emoji,
                'user_id': self.user.id,
                'username': self.user.username,
                'action': reaction_data['action'],
                'reactions': reaction_data['reactions'],
            }
        )

    # ─── Group Message Handlers ────────────────────────────────────────────────

    async def chat_message(self, event):
        await self.send(text_data=json.dumps({
            'type': 'chat_message',
            **event
        }))

    async def file_message(self, event):
        await self.send(text_data=json.dumps({
            'type': 'file_message',
            **event
        }))

    async def typing_indicator(self, event):
        if event['user_id'] != self.user.id:
            await self.send(text_data=json.dumps({
                'type': 'typing',
                **event
            }))

    async def user_status(self, event):
        await self.send(text_data=json.dumps({
            'type': 'user_status',
            **event
        }))

    async def read_receipt(self, event):
        await self.send(text_data=json.dumps({
            'type': 'read_receipt',
            **event
        }))

    async def message_deleted(self, event):
        await self.send(text_data=json.dumps({
            'type': 'message_deleted',
            **event
        }))

    async def reaction_update(self, event):
        await self.send(text_data=json.dumps({
            'type': 'reaction_update',
            **event
        }))

    # ─── Database Operations ───────────────────────────────────────────────────

    @database_sync_to_async
    def check_room_membership(self):
        from .models import Room
        try:
            room = Room.objects.get(id=self.room_id)
            return room.members.filter(id=self.user.id).exists()
        except Room.DoesNotExist:
            return False

    @database_sync_to_async
    def set_user_online(self, is_online):
        from .models import UserProfile
        UserProfile.objects.update_or_create(
            user=self.user,
            defaults={'is_online': is_online, 'last_seen': timezone.now()}
        )

    @database_sync_to_async
    def check_message_quota(self):
        """Returns (can_send, remaining) tuple"""
        from .models import DailyMessageCount, Subscription
        from django.conf import settings
        from django.db.models import Q
        import datetime

        FREE_LIMIT = getattr(settings, 'FREE_MESSAGES_PER_DAY', 30)

        # Check if user has pro subscription
        try:
            sub = self.user.subscription
            if sub.is_pro:
                return True, 999  # unlimited
        except Exception:
            pass

        today = timezone.now().date()
        daily, _ = DailyMessageCount.objects.get_or_create(
            user=self.user, date=today
        )
        remaining = FREE_LIMIT - daily.count
        return remaining > 0, remaining

    @database_sync_to_async
    def increment_message_count(self):
        from .models import DailyMessageCount
        today = timezone.now().date()
        daily, _ = DailyMessageCount.objects.get_or_create(
            user=self.user, date=today
        )
        daily.count += 1
        daily.save()

    @database_sync_to_async
    def save_text_message(self, text, msg_type, reply_to_id=None):
        from .models import Message, Room, UserProfile
        room = Room.objects.get(id=self.room_id)
        reply_to = None
        if reply_to_id:
            try:
                reply_to = Message.objects.get(id=reply_to_id)
            except Message.DoesNotExist:
                pass

        msg = Message.objects.create(
            room=room,
            sender=self.user,
            message_type=msg_type,
            text=text,
            reply_to=reply_to,
        )

        # Get avatar info
        try:
            profile = self.user.profile
            avatar = profile.avatar.url if profile.avatar else None
            initials = profile.get_initials()
        except Exception:
            avatar = None
            initials = self.user.username[:2].upper()

        result = {
            'id': str(msg.id),
            'timestamp': msg.created_at.strftime('%H:%M'),
            'sender_avatar': avatar,
            'sender_initials': initials,
            'can_delete': True,
        }
        if reply_to:
            preview = reply_to.text[:100] if reply_to.text else ''
            if reply_to.message_type == Message.TYPE_GIF:
                preview = '🎞️ GIF'
            elif reply_to.message_type == Message.TYPE_IMAGE:
                preview = '📷 Photo'
            elif reply_to.message_type == Message.TYPE_VIDEO:
                preview = '🎥 Video'
            elif reply_to.message_type == Message.TYPE_DOC:
                preview = reply_to.file_name or '📎 File'
            result['reply_to'] = {
                'id': str(reply_to.id),
                'text': preview or 'Message',
                'sender': reply_to.sender.username,
            }
        return result

    @database_sync_to_async
    def save_gif_message(self, gif_url, reply_to_id=None):
        from .models import Message, Room

        room = Room.objects.get(id=self.room_id)
        reply_to = None
        if reply_to_id:
            try:
                reply_to = Message.objects.get(id=reply_to_id)
            except Message.DoesNotExist:
                pass

        msg = Message.objects.create(
            room=room,
            sender=self.user,
            message_type=Message.TYPE_GIF,
            gif_url=gif_url[:2048],
            file_name='GIF',
            mime_type='image/gif',
            reply_to=reply_to,
        )

        try:
            profile = self.user.profile
            avatar = profile.avatar.url if profile.avatar else None
            initials = profile.get_initials()
        except Exception:
            avatar = None
            initials = self.user.username[:2].upper()

        result = {
            'id': str(msg.id),
            'timestamp': msg.created_at.strftime('%H:%M'),
            'sender_avatar': avatar,
            'sender_initials': initials,
            'can_delete': True,
        }
        if reply_to:
            preview = reply_to.text[:100] if reply_to.text else ''
            if reply_to.message_type == Message.TYPE_GIF:
                preview = '🎞️ GIF'
            elif reply_to.message_type == Message.TYPE_IMAGE:
                preview = '📷 Photo'
            elif reply_to.message_type == Message.TYPE_VIDEO:
                preview = '🎥 Video'
            elif reply_to.message_type == Message.TYPE_DOC:
                preview = reply_to.file_name or '📎 File'
            result['reply_to'] = {
                'id': str(reply_to.id),
                'text': preview or 'Message',
                'sender': reply_to.sender.username,
            }
        return result

    @database_sync_to_async
    def update_last_read(self):
        from .models import RoomMembership
        RoomMembership.objects.filter(
            user=self.user, room_id=self.room_id
        ).update(last_read_at=timezone.now())

    @database_sync_to_async
    def delete_message(self, message_id):
        from .models import Message
        try:
            msg = Message.objects.get(id=message_id, sender=self.user)
            if msg.is_deleted:
                return {'success': False, 'error': 'Message is already deleted.'}

            delete_deadline = msg.created_at + timedelta(minutes=10)
            if timezone.now() > delete_deadline:
                return {'success': False, 'error': 'You can only delete a message within 10 minutes.'}

            msg.is_deleted = True
            msg.text = 'This message was deleted'
            msg.save()
            return {'success': True}
        except Message.DoesNotExist:
            return {'success': False, 'error': 'Message not found or not owned by you.'}

    @database_sync_to_async
    def toggle_reaction(self, message_id, emoji):
        from .models import Message, Reaction
        from django.db.models import Count
        try:
            msg = Message.objects.get(id=message_id)
            existing = Reaction.objects.filter(message=msg, user=self.user, emoji=emoji).first()
            if existing:
                existing.delete()
                action = 'removed'
            else:
                Reaction.objects.create(message=msg, user=self.user, emoji=emoji)
                action = 'added'

            # Get updated reaction counts
            reactions = list(
                Reaction.objects.filter(message=msg)
                .values('emoji')
                .annotate(count=Count('id'))
                .order_by('-count')
            )
            return {'action': action, 'reactions': reactions}
        except Message.DoesNotExist:
            return {'action': 'error', 'reactions': []}

    @database_sync_to_async
    def send_message_notifications(self, message_id, text):
        from .models import Room, Notification, Message
        room = Room.objects.get(id=self.room_id)
        msg = Message.objects.get(id=message_id)
        members = room.members.exclude(id=self.user.id).filter(profile__is_online=False)
        for member in members:
            Notification.objects.create(
                recipient=member,
                sender=self.user,
                notification_type=Notification.TYPE_MESSAGE,
                title=f"New message from {self.user.username}",
                body=text[:200],
                room=room,
                message=msg,
            )

    @staticmethod
    def is_emoji_only(text):
        """Check if text contains only emoji characters"""
        import re
        emoji_pattern = re.compile(
            "^[\U0001F600-\U0001F64F"
            "\U0001F300-\U0001F5FF"
            "\U0001F680-\U0001F6FF"
            "\U0001F1E0-\U0001F1FF"
            "\U00002702-\U000027B0"
            "\U000024C2-\U0001F251"
            "\u2764\u2665\u2666\u2663"
            "\\s]+$"
        )
        return bool(emoji_pattern.match(text.strip()))


class NotificationConsumer(AsyncWebsocketConsumer):
    """
    Notification consumer - delivers real-time notifications to user
    """

    async def connect(self):
        self.user = self.scope['user']
        if not self.user.is_authenticated:
            await self.close()
            return

        self.notification_group = f'notifications_{self.user.id}'
        await self.channel_layer.group_add(
            self.notification_group,
            self.channel_name
        )
        await self.accept()

        # Send unread notification count on connect
        count = await self.get_unread_count()
        await self.send(text_data=json.dumps({
            'type': 'unread_count',
            'count': count
        }))

    async def disconnect(self, close_code):
        if hasattr(self, 'notification_group'):
            await self.channel_layer.group_discard(
                self.notification_group,
                self.channel_name
            )

    async def receive(self, text_data):
        data = json.loads(text_data)
        action = data.get('action')
        if action == 'mark_read':
            await self.mark_all_read()
            await self.send(text_data=json.dumps({
                'type': 'all_read'
            }))

    async def notify(self, event):
        """Receive notification from group and forward to WebSocket"""
        await self.send(text_data=json.dumps({
            'type': 'notification',
            **event
        }))

    async def unread_count(self, event):
        await self.send(text_data=json.dumps({
            'type': 'unread_count',
            **event
        }))

    @database_sync_to_async
    def get_unread_count(self):
        from .models import Notification
        return Notification.objects.filter(recipient=self.user, is_read=False).count()

    @database_sync_to_async
    def mark_all_read(self):
        from .models import Notification
        Notification.objects.filter(recipient=self.user, is_read=False).update(is_read=True)
