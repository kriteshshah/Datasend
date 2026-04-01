from django.urls import path, reverse
from django.shortcuts import redirect
from . import views
from . import ai_views
from .webhooks import stripe_webhook


def google_login_redirect(request):
    return redirect(reverse('google_login'))


urlpatterns = [
    # Auth
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    # Google OAuth shortcut — redirects to allauth's built-in google login URL
    path('login/google/', google_login_redirect, name='google_login_redirect'),

    # Chat
    path('room/<uuid:room_id>/', views.room_view, name='room'),
    path('create-room/', views.create_room, name='create_room'),

    # File Upload
    path('room/<uuid:room_id>/upload/', views.upload_file, name='upload_file'),

    # API
    path('api/users/search/', views.search_users, name='search_users'),
    path('api/notifications/', views.notifications_list, name='notifications_list'),
    path('api/notifications/<uuid:notification_id>/read/', views.mark_notification_read, name='mark_notification_read'),
    path('api/room/<uuid:room_id>/messages/', views.get_messages, name='get_messages'),
    path('api/quota/', views.get_quota, name='get_quota'),

    # Gemini AI (requires GEMINI_API_KEY)
    path('api/ai/assistant/', ai_views.ai_assistant, name='ai_assistant'),
    path('api/room/<uuid:room_id>/ai/transcript/', ai_views.ai_transcript, name='ai_transcript'),
    path('api/room/<uuid:room_id>/ai/apply-transcript/', ai_views.ai_apply_transcript, name='ai_apply_transcript'),
    path('api/room/<uuid:room_id>/ai/summarize/', ai_views.ai_summarize_room, name='ai_summarize_room'),

    # Subscription
    path('subscribe/', views.subscription_page, name='subscription'),
    path('subscribe/create-checkout/', views.create_checkout_session, name='create_checkout'),
    path('subscribe/success/', views.subscription_success, name='subscription_success'),
    path('subscribe/cancel/', views.cancel_subscription, name='cancel_subscription'),
    path('subscribe/activate/', views.manual_activate, name='manual_activate'),

    # Stripe Webhook (csrf_exempt inside webhooks.py)
    path('webhooks/stripe/', stripe_webhook, name='stripe_webhook'),

    # Profile & utils
    path('profile/', views.profile_view, name='profile'),
    path('ws-check/', views.ws_check, name='ws_check'),
]
