"""
Shared Stripe → local Pro activation (used by views and webhooks).
"""

from __future__ import annotations

import datetime
import logging

from django.conf import settings
from django.utils import timezone

from .models import Notification, Subscription

logger = logging.getLogger(__name__)


def activate_pro_from_stripe(user, customer_id: str, subscription_id: str) -> None:
    """
    Set user to Pro and sync billing end from Stripe Subscription.current_period_end
    (covers trials: period end is usually trial end or next invoice date).
    """
    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.plan = Subscription.PLAN_PRO
    sub.status = Subscription.STATUS_ACTIVE
    sub.stripe_customer_id = (customer_id or "")[:100]
    sub.stripe_subscription_id = (subscription_id or "")[:100]
    sub.started_at = timezone.now()

    expires_at = None
    sid = (subscription_id or "").strip()
    if sid and getattr(settings, "STRIPE_SECRET_KEY", ""):
        try:
            import stripe

            stripe.api_key = settings.STRIPE_SECRET_KEY
            ss = stripe.Subscription.retrieve(sid)
            cpe = getattr(ss, "current_period_end", None)
            if cpe:
                expires_at = datetime.datetime.fromtimestamp(
                    int(cpe), tz=datetime.timezone.utc
                )
        except Exception as e:
            logger.warning("Could not fetch Stripe subscription %s for expires_at: %s", sid, e)

    if expires_at is None:
        expires_at = timezone.now() + datetime.timedelta(days=32)

    sub.expires_at = expires_at
    sub.save()

    Notification.objects.get_or_create(
        recipient=user,
        notification_type=Notification.TYPE_SUBSCRIPTION,
        title="🎉 Welcome to Pro!",
        defaults={
            "body": "You now have unlimited messaging and all premium features unlocked.",
        },
    )
