from django.utils import timezone
from django.conf import settings


def subscription_context(request):
    """Inject subscription and quota info into every template."""
    if not request.user.is_authenticated:
        return {}

    try:
        sub = request.user.subscription
        is_pro = sub.is_pro
    except Exception:
        is_pro = False
        sub = None

    FREE_LIMIT = getattr(settings, 'FREE_MESSAGES_PER_DAY', 30)

    if not is_pro:
        from chat.models import DailyMessageCount
        today = timezone.now().date()
        daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
        remaining = max(0, FREE_LIMIT - daily.count)
    else:
        remaining = 999

    return {
        'subscription': sub,
        'is_pro': is_pro,
        'remaining_messages': remaining,
        'free_limit': FREE_LIMIT,
    }
