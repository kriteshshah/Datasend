from django.urls import path
from . import views
from .webhooks import stripe_webhook

urlpatterns = [
    # Auth
    path('', views.home, name='home'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),

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

    # Stripe Webhook
    path('webhooks/stripe/', stripe_webhook, name='stripe_webhook'),

    # Profile
    path('profile/', views.profile_view, name='profile'),
]
