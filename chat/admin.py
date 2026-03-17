from django.contrib import admin
from .models import Room, Message, UserProfile, Subscription, DailyMessageCount, Notification, RoomMembership, Reaction

@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ['user', 'is_online', 'last_seen']
    search_fields = ['user__username']

@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ['user', 'plan', 'status', 'expires_at']
    list_filter = ['plan', 'status']

@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ['id', 'name', 'room_type', 'created_by', 'created_at']
    list_filter = ['room_type']

@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ['sender', 'room', 'message_type', 'created_at', 'is_deleted']
    list_filter = ['message_type', 'is_deleted']

@admin.register(DailyMessageCount)
class DailyMessageCountAdmin(admin.ModelAdmin):
    list_display = ['user', 'date', 'count']

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ['recipient', 'notification_type', 'title', 'is_read', 'created_at']
    list_filter = ['notification_type', 'is_read']

admin.site.register(RoomMembership)
admin.site.register(Reaction)
