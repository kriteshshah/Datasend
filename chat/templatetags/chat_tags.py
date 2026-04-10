from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe
import re

register = template.Library()

# Usernames may include letters, digits, _, ., @, +, - (Django default)
_MENTION_USER_RE = re.compile(r"(?<![A-Za-z0-9_.@+-])@([A-Za-z0-9_.@+-]+)(?![A-Za-z0-9_.@+-])")


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


@register.inclusion_tag("chat/partials/message_bubble.html", takes_context=True)
def message_bubble(context, message):
    return {
        "message": message,
        "request": context["request"],
        "user": context["request"].user,
    }


@register.filter(needs_autoescape=True)
def format_chat_mentions(text, members, autoescape=True):
    """
    Escape message text, then wrap @username spans for mentionable users (case-insensitive username match).
    `members` is a queryset/list of Users, or list of dicts with username and id (e.g. mention_members).
    """
    if text is None:
        return ""
    s = escape(str(text))
    if not members:
        return mark_safe(s)
    user_map = {}
    for u in members:
        if isinstance(u, dict):
            un = u.get("username")
            uid = u.get("id")
        else:
            un = getattr(u, "username", None)
            uid = getattr(u, "pk", None)
        if un and uid is not None:
            user_map[str(un).lower()] = (escape(str(un)), uid)
    if not user_map:
        return mark_safe(s)

    def repl(m):
        raw = m.group(1)
        key = raw.lower()
        if key not in user_map:
            return m.group(0)
        esc_un, uid = user_map[key]
        return f'<span class="mention" data-user-id="{uid}" title="Mention">@{esc_un}</span>'

    out = []
    last = 0
    for m in _MENTION_USER_RE.finditer(s):
        out.append(s[last : m.start()])
        out.append(repl(m))
        last = m.end()
    out.append(s[last:])
    return mark_safe("".join(out))
