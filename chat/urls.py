from django.urls import path
from . import views
from .webhooks import stripe_webhook
from allauth.socialaccount.providers.oauth2.views import OAuth2LoginView
from allauth.socialaccount.providers.google.views import GoogleOAuth2Adapter

google_login = OAuth2LoginView.adapter_view(GoogleOAuth2Adapter)

urlpatterns = [
    # Auth
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    # Google OAuth shortcut
    path('login/google/', google_login, name='google_login_redirect'),

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