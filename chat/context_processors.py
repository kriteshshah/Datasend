from django.utils import timezone
from django.conf import settings


def subscription_context(request):
    """Inject subscription, quota, and AI provider info into every template."""
    FREE_LIMIT = getattr(settings, "FREE_MESSAGES_PER_DAY", 30)
    gemini_on = bool((getattr(settings, "GEMINI_API_KEY", "") or "").strip())
    groq_on = bool((getattr(settings, "GROQ_API_KEY", "") or "").strip())

    if not request.user.is_authenticated:
        return {
            "subscription": None,
            "is_pro": False,
            "remaining_messages": 0,
            "free_limit": FREE_LIMIT,
            "gemini_enabled": gemini_on,
            "groq_enabled": groq_on,
            "active_provider": _active_provider(gemini_on, groq_on),
            "fallback_enabled": gemini_on and groq_on,
        }

    try:
        sub = request.user.subscription
        is_pro = sub.is_pro
    except Exception:
        is_pro = False
        sub = None

    if not is_pro:
        from chat.models import DailyMessageCount
        today = timezone.now().date()
        daily, _ = DailyMessageCount.objects.get_or_create(user=request.user, date=today)
        remaining = max(0, FREE_LIMIT - daily.count)
    else:
        remaining = 999

    return {
        "subscription": sub,
        "is_pro": is_pro,
        "remaining_messages": remaining,
        "free_limit": FREE_LIMIT,
        "gemini_enabled": gemini_on,
        "groq_enabled": groq_on,
        "active_provider": _active_provider(gemini_on, groq_on),
        "fallback_enabled": gemini_on and groq_on,
    }


def _active_provider(gemini_on: bool, groq_on: bool) -> str:
    if gemini_on and groq_on:
        return "both"
    if gemini_on:
        return "gemini"
    if groq_on:
        return "groq"
    return "none"
