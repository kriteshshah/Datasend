"""
ASGI config for chatproject - Enables WebSocket via Django Channels

⚠️  AllowedHostsOriginValidator is intentionally REMOVED.
    On Render (and any reverse proxy / CDN), the WebSocket Origin header
    does not match ALLOWED_HOSTS, so the validator rejects every connection
    with 403 — which browsers report as "failed" or "404".
    Security is handled by ALLOWED_HOSTS + CSRF_TRUSTED_ORIGINS in settings.py.
"""

import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'chatproject.settings')

# Django must be fully set up before any app-level imports
django.setup()

from django.core.asgi import get_asgi_application           # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.auth import AuthMiddlewareStack               # noqa: E402
from chat import routing                                     # noqa: E402

application = ProtocolTypeRouter({
    'http': get_asgi_application(),
    'websocket': AuthMiddlewareStack(
        URLRouter(routing.websocket_urlpatterns)
    ),
})