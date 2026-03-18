"""
Stripe Webhook Handler
Handles subscription lifecycle events from Stripe
"""

import json
import stripe
from django.conf import settings
from django.http import HttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.contrib.auth.models import User
from django.utils import timezone
import datetime

from .models import Subscription, Notification


@csrf_exempt
@require_POST
def stripe_webhook(request):
    """
    Handle Stripe webhook events for subscription management.
    
    Events handled:
    - checkout.session.completed  → activate Pro plan
    - customer.subscription.updated → update plan status
    - customer.subscription.deleted → downgrade to Free
    - invoice.payment_failed        → notify user
    """
    payload = request.body
    sig_header = request.META.get('HTTP_STRIPE_SIGNATURE', '')
    webhook_secret = settings.STRIPE_WEBHOOK_SECRET

    if not webhook_secret:
        # Dev mode - skip signature verification
        event = json.loads(payload)
    else:
        stripe.api_key = settings.STRIPE_SECRET_KEY
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except ValueError:
            return HttpResponse(status=400)
        except stripe.error.SignatureVerificationError:
            return HttpResponse(status=400)

    event_type = event['type']
    data = event['data']['object']

    if event_type == 'checkout.session.completed':
        _handle_checkout_completed(data)

    elif event_type == 'customer.subscription.updated':
        _handle_subscription_updated(data)

    elif event_type == 'customer.subscription.deleted':
        _handle_subscription_deleted(data)

    elif event_type == 'invoice.payment_failed':
        _handle_payment_failed(data)

    elif event_type == 'invoice.payment_succeeded':
        _handle_payment_succeeded(data)

    return HttpResponse(status=200)


def _handle_checkout_completed(session):
    """Activate Pro plan after successful checkout. Called by webhook."""
    # session can be a Stripe SDK object OR a plain dict — handle both
    def _get(obj, key, default=None):
        if hasattr(obj, key):
            return getattr(obj, key, default)
        if isinstance(obj, dict):
            return obj.get(key, default)
        return default

    client_ref      = _get(session, 'client_reference_id')
    customer_id     = _get(session, 'customer', '')
    subscription_id = _get(session, 'subscription', '')

    if not client_ref:
        return

    try:
        user = User.objects.get(id=client_ref)
        sub, _ = Subscription.objects.get_or_create(user=user)
        sub.plan                   = Subscription.PLAN_PRO
        sub.status                 = Subscription.STATUS_ACTIVE
        sub.stripe_customer_id     = customer_id or ''
        sub.stripe_subscription_id = subscription_id or ''
        sub.started_at             = timezone.now()
        sub.expires_at             = timezone.now() + datetime.timedelta(days=30)
        sub.save()

        Notification.objects.get_or_create(
            recipient=user,
            notification_type=Notification.TYPE_SUBSCRIPTION,
            title='🎉 Welcome to Pro!',
            defaults={'body': 'You now have unlimited messaging and all premium features unlocked.'}
        )
    except User.DoesNotExist:
        pass


def _handle_subscription_updated(stripe_sub):
    """Update subscription status."""
    customer_id = stripe_sub.get('customer')
    status = stripe_sub.get('status')
    current_period_end = stripe_sub.get('current_period_end')

    try:
        sub = Subscription.objects.get(stripe_customer_id=customer_id)
        if status == 'active':
            sub.status = Subscription.STATUS_ACTIVE
            sub.plan = Subscription.PLAN_PRO
        elif status in ('canceled', 'unpaid', 'past_due'):
            sub.status = Subscription.STATUS_CANCELLED

        if current_period_end:
            sub.expires_at = datetime.datetime.fromtimestamp(
                current_period_end, tz=timezone.utc
            )
        sub.save()
    except Subscription.DoesNotExist:
        pass


def _handle_subscription_deleted(stripe_sub):
    """Downgrade user to free plan."""
    customer_id = stripe_sub.get('customer')
    try:
        sub = Subscription.objects.get(stripe_customer_id=customer_id)
        sub.plan = Subscription.PLAN_FREE
        sub.status = Subscription.STATUS_EXPIRED
        sub.expires_at = timezone.now()
        sub.save()

        Notification.objects.create(
            recipient=sub.user,
            notification_type=Notification.TYPE_SUBSCRIPTION,
            title='Subscription ended',
            body='Your Pro subscription has ended. You are now on the Free plan with 30 messages/day.',
        )
    except Subscription.DoesNotExist:
        pass


def _handle_payment_failed(invoice):
    """Notify user of failed payment."""
    customer_id = invoice.get('customer')
    try:
        sub = Subscription.objects.get(stripe_customer_id=customer_id)
        Notification.objects.create(
            recipient=sub.user,
            notification_type=Notification.TYPE_SUBSCRIPTION,
            title='⚠️ Payment failed',
            body='Your Pro subscription payment failed. Please update your payment method.',
        )
    except Subscription.DoesNotExist:
        pass


def _handle_payment_succeeded(invoice):
    """Extend subscription on successful renewal."""
    customer_id = invoice.get('customer')
    try:
        sub = Subscription.objects.get(stripe_customer_id=customer_id)
        sub.plan = Subscription.PLAN_PRO
        sub.status = Subscription.STATUS_ACTIVE
        sub.expires_at = timezone.now() + datetime.timedelta(days=30)
        sub.save()
    except Subscription.DoesNotExist:
        pass