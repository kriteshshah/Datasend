from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth.models import User
from django.conf import settings


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Custom adapter that:
    - Links Google login to existing account with same email
    - Ensures UserProfile and Subscription exist after social login
    - Auto-fixes the django.contrib.sites domain on first request
    """

    def pre_social_login(self, request, sociallogin):
        # Auto-fix site domain so redirect URI matches Google Console
        self._fix_site_domain()

        if sociallogin.is_existing:
            return
        try:
            email = sociallogin.account.extra_data.get('email', '').lower()
            if email:
                existing = User.objects.get(email__iexact=email)
                sociallogin.connect(request, existing)
        except User.DoesNotExist:
            pass

    def populate_user(self, request, sociallogin, data):
        user = super().populate_user(request, sociallogin, data)
        if not user.username:
            email = data.get('email', '')
            base = email.split('@')[0] if email else 'user'
            username = base
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f"{base}{counter}"
                counter += 1
            user.username = username
        return user

    def save_user(self, request, sociallogin, form=None):
        user = super().save_user(request, sociallogin, form)
        self._ensure_profile(user)
        return user

    @staticmethod
    def _fix_site_domain():
        """Keep the sites table in sync with the actual domain."""
        try:
            from django.contrib.sites.models import Site
            is_prod = getattr(settings, '_IS_PRODUCTION', False)
            domain  = 'datasend-xpoz.onrender.com' if is_prod else '127.0.0.1:8000'
            Site.objects.filter(id=1).update(domain=domain, name=domain)
        except Exception:
            pass

    @staticmethod
    def _ensure_profile(user):
        from .models import UserProfile, Subscription
        UserProfile.objects.get_or_create(user=user)
        Subscription.objects.get_or_create(user=user)
