from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
import uuid
import os


def upload_to_images(instance, filename):
    ext = filename.split('.')[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    return os.path.join('uploads/images', filename)

def upload_to_videos(instance, filename):
    ext = filename.split('.')[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    return os.path.join('uploads/videos', filename)

def upload_to_docs(instance, filename):
    ext = filename.split('.')[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    return os.path.join('uploads/docs', filename)

def upload_avatar(instance, filename):
    ext = filename.split('.')[-1]
    return f'avatars/{instance.user.id}.{ext}'


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    avatar = models.ImageField(upload_to=upload_avatar, null=True, blank=True)
    bio = models.CharField(max_length=200, blank=True)
    is_online = models.BooleanField(default=False)
    last_seen = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username}'s profile"

    @property
    def avatar_url(self):
        if self.avatar:
            return self.avatar.url
        # Generate initials-based avatar color
        return None

    def get_initials(self):
        name = self.user.get_full_name() or self.user.username
        parts = name.split()
        if len(parts) >= 2:
            return (parts[0][0] + parts[1][0]).upper()
        return name[:2].upper()


class Subscription(models.Model):
    PLAN_FREE = 'free'
    PLAN_PRO = 'pro'
    PLAN_CHOICES = [
        (PLAN_FREE, 'Free'),
        (PLAN_PRO, 'Pro'),
    ]

    STATUS_ACTIVE = 'active'
    STATUS_CANCELLED = 'cancelled'
    STATUS_EXPIRED = 'expired'
    STATUS_CHOICES = [
        (STATUS_ACTIVE, 'Active'),
        (STATUS_CANCELLED, 'Cancelled'),
        (STATUS_EXPIRED, 'Expired'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    plan = models.CharField(max_length=20, choices=PLAN_CHOICES, default=PLAN_FREE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    stripe_customer_id = models.CharField(max_length=100, blank=True)
    stripe_subscription_id = models.CharField(max_length=100, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} - {self.plan}"

    @property
    def is_pro(self):
        if self.plan == self.PLAN_PRO and self.status == self.STATUS_ACTIVE:
            if self.expires_at and self.expires_at > timezone.now():
                return True
            elif not self.expires_at:
                return True
        return False


class DailyMessageCount(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='daily_counts')
    date = models.DateField(default=timezone.now)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('user', 'date')

    def __str__(self):
        return f"{self.user.username} - {self.date}: {self.count}"


class DailyAiUsage(models.Model):
    """Counts AI API uses per user per day (assistant, summaries, code coach, etc.). Pro users are not limited."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='daily_ai_usage')
    date = models.DateField(default=timezone.now)
    count = models.PositiveIntegerField(default=0)

    class Meta:
        unique_together = ('user', 'date')

    def __str__(self):
        return f"{self.user.username} AI {self.date}: {self.count}"


class Room(models.Model):
    ROOM_DIRECT = 'direct'
    ROOM_GROUP = 'group'
    ROOM_TYPE_CHOICES = [
        (ROOM_DIRECT, 'Direct Message'),
        (ROOM_GROUP, 'Group Chat'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200, blank=True)
    room_type = models.CharField(max_length=20, choices=ROOM_TYPE_CHOICES, default=ROOM_DIRECT)
    members = models.ManyToManyField(User, related_name='chat_rooms', through='RoomMembership')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_rooms')
    avatar = models.ImageField(upload_to='room_avatars/', null=True, blank=True)
    description = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.name or f"Room {self.id}"

    def get_display_name(self, user):
        """Get room name from the perspective of a user."""
        if self.room_type == self.ROOM_DIRECT:
            other = self.members.exclude(id=user.id).first()
            if other:
                return other.get_full_name() or other.username
        return self.name

    def get_last_message(self):
        return self.messages.order_by('-created_at').first()

    def get_unread_count(self, user):
        membership = self.memberships.filter(user=user).first()
        if not membership:
            return 0
        return self.messages.filter(
            created_at__gt=membership.last_read_at
        ).exclude(sender=user).count()


class RoomMembership(models.Model):
    ROLE_ADMIN = 'admin'
    ROLE_MEMBER = 'member'
    ROLE_CHOICES = [
        (ROLE_ADMIN, 'Admin'),
        (ROLE_MEMBER, 'Member'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='memberships')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_MEMBER)
    joined_at = models.DateTimeField(auto_now_add=True)
    last_read_at = models.DateTimeField(default=timezone.now)
    is_muted = models.BooleanField(default=False)

    class Meta:
        unique_together = ('user', 'room')


class Message(models.Model):
    TYPE_TEXT = 'text'
    TYPE_IMAGE = 'image'
    TYPE_VIDEO = 'video'
    TYPE_DOC = 'doc'
    TYPE_EMOJI = 'emoji'
    TYPE_GIF = 'gif'
    TYPE_SYSTEM = 'system'
    TYPE_CHOICES = [
        (TYPE_TEXT, 'Text'),
        (TYPE_IMAGE, 'Image'),
        (TYPE_VIDEO, 'Video'),
        (TYPE_DOC, 'Document'),
        (TYPE_EMOJI, 'Emoji Only'),
        (TYPE_GIF, 'GIF'),
        (TYPE_SYSTEM, 'System'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    room = models.ForeignKey(Room, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    message_type = models.CharField(max_length=10, choices=TYPE_CHOICES, default=TYPE_TEXT)

    # Content
    text = models.TextField(blank=True)
    gif_url = models.URLField(max_length=2048, blank=True, help_text='External GIF (Tenor/Giphy CDN) when message_type is gif')
    image = models.ImageField(upload_to=upload_to_images, null=True, blank=True)
    video = models.FileField(upload_to=upload_to_videos, null=True, blank=True)
    document = models.FileField(upload_to=upload_to_docs, null=True, blank=True)
    file_name = models.CharField(max_length=255, blank=True)
    file_size = models.PositiveBigIntegerField(null=True, blank=True)
    mime_type = models.CharField(max_length=100, blank=True)

    # Reply
    reply_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='replies')

    # Metadata
    is_edited = models.BooleanField(default=False)
    is_deleted = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.sender.username}: {self.text[:50] or self.message_type}"

    def get_file_size_display(self):
        if not self.file_size:
            return ''
        if self.file_size < 1024:
            return f"{self.file_size} B"
        elif self.file_size < 1024 * 1024:
            return f"{self.file_size / 1024:.1f} KB"
        else:
            return f"{self.file_size / (1024 * 1024):.1f} MB"

    def get_doc_icon(self):
        ext = self.file_name.split('.')[-1].lower() if self.file_name else ''
        icons = {
            'pdf': '📄', 'doc': '📝', 'docx': '📝',
            'xls': '📊', 'xlsx': '📊', 'txt': '📃',
            'zip': '📦', 'rar': '📦',
        }
        return icons.get(ext, '📎')


class Reaction(models.Model):
    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name='reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    emoji = models.CharField(max_length=10)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('message', 'user', 'emoji')


class Notification(models.Model):
    TYPE_MESSAGE = 'message'
    TYPE_REACTION = 'reaction'
    TYPE_MENTION = 'mention'
    TYPE_SYSTEM = 'system'
    TYPE_SUBSCRIPTION = 'subscription'
    TYPE_CHOICES = [
        (TYPE_MESSAGE, 'New Message'),
        (TYPE_REACTION, 'Reaction'),
        (TYPE_MENTION, 'Mention'),
        (TYPE_SYSTEM, 'System'),
        (TYPE_SUBSCRIPTION, 'Subscription'),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    sender = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    notification_type = models.CharField(max_length=20, choices=TYPE_CHOICES)
    title = models.CharField(max_length=200)
    body = models.TextField(blank=True)
    room = models.ForeignKey(Room, on_delete=models.SET_NULL, null=True, blank=True)
    message = models.ForeignKey(Message, on_delete=models.SET_NULL, null=True, blank=True)
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Notif for {self.recipient.username}: {self.title}"
