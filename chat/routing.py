from django.urls import re_path
from . import consumers

# ^/?  makes the leading slash optional.
# Daphne passes scope['path'] WITH a leading slash: /ws/chat/uuid/
# The plain  ws/chat/...  pattern never matches /ws/chat/... → 404.
websocket_urlpatterns = [
    re_path(r'^/?ws/chat/(?P<room_id>[0-9a-f-]+)/$', consumers.ChatConsumer.as_asgi()),
    re_path(r'^/?ws/notifications/$',consumers.NotificationConsumer.as_asgi()),
]