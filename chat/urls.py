from django.urls import path, reverse
from django.shortcuts import redirect

from chat.ai_views import ai_assistant, ai_transcript, ai_apply_transcript, ai_summarize_room
from chat.views import home, login_view, register_view, logout_view, room_view, create_room, upload_file, search_users, \
    notifications_list, mark_notification_read, get_messages, get_quota, subscription_page, create_checkout_session, \
    subscription_success, cancel_subscription, manual_activate, profile_view, ws_check
from chat.webhooks import stripe_webhook


def google_login_redirect(request):
    return redirect(reverse('google_login'))


urlpatterns = [
    # Auth
    path('', home, name='home'),
    path('login/', login_view, name='login'),
    path('register/', register_view, name='register'),
    path('logout/', logout_view, name='logout'),
    # Google OAuth shortcut — redirects to allauth's built-in google login URL
    path('login/google/', google_login_redirect, name='google_login_redirect'),

    # Chat
    path('room/<uuid:room_id>/', room_view, name='room'),
    path('create-room/', create_room, name='create_room'),

    # File Upload
    path('room/<uuid:room_id>/upload/', upload_file, name='upload_file'),

    # API
    path('api/users/search/', search_users, name='search_users'),
    path('api/notifications/', notifications_list, name='notifications_list'),
    path('api/notifications/<uuid:notification_id>/read/', mark_notification_read, name='mark_notification_read'),
    path('api/room/<uuid:room_id>/messages/', get_messages, name='get_messages'),
    path('api/quota/', get_quota, name='get_quota'),

    # Gemini AI (requires GEMINI_API_KEY)
    path('api/ai/assistant/', ai_assistant, name='ai_assistant'),
    path('api/room/<uuid:room_id>/ai/transcript/', ai_transcript, name='ai_transcript'),
    path('api/room/<uuid:room_id>/ai/apply-transcript/', ai_apply_transcript, name='ai_apply_transcript'),
    path('api/room/<uuid:room_id>/ai/summarize/', ai_summarize_room, name='ai_summarize_room'),

    # Subscription
    path('subscribe/', subscription_page, name='subscription'),
    path('subscribe/create-checkout/', create_checkout_session, name='create_checkout'),
    path('subscribe/success/', subscription_success, name='subscription_success'),
    path('subscribe/cancel/', cancel_subscription, name='cancel_subscription'),
    path('subscribe/activate/', manual_activate, name='manual_activate'),

    # Stripe Webhook (csrf_exempt inside webhooks.py)
    path('webhooks/stripe/', stripe_webhook, name='stripe_webhook'),

    # Profile & utils
    path('profile/', profile_view, name='profile'),
    path('ws-check/', ws_check, name='ws_check'),
]
