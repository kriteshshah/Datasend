from django import template
from django.utils.safestring import mark_safe
import re

register = template.Library()


@register.filter
def get_display_name(room, user):
    """Get room display name from user's perspective."""
    return room.get_display_name(user)


@register.filter
def get_unread_count(room, user):
    """Get unread message count for user in room."""
    return room.get_unread_count(user)


@register.filter
def format_file_size(size_bytes):
    """Format bytes to human-readable size."""
    if not size_bytes:
        return ''
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


@register.filter
def avatar_color(username):
    """Return a deterministic avatar color class based on username."""
    colors = ['av-purple', 'av-green', 'av-blue', 'av-pink', 'av-orange']
    index = sum(ord(c) for c in (username or 'u')) % len(colors)
    return colors[index]


@register.simple_tag
def get_room_name(room, user):
    return room.get_display_name(user)


@register.inclusion_tag('chat/partials/message_bubble.html', takes_context=True)
def message_bubble(context, message):
    return {
        'message': message,
        'request': context['request'],
        'user': context['request'].user,
    }
