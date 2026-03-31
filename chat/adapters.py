from allauth.socialaccount.adapter import DefaultSocialAccountAdapter
from django.contrib.auth.models import User


class SocialAccountAdapter(DefaultSocialAccountAdapter):
    """
    Custom adapter that:
    - Links Google login to existing account with same email
    - Ensures UserProfile and Subscription exist after social login
    """

    def pre_social_login(self, request, sociallogin):
        """
        If the Google email already belongs to an existing user,
        connect the social account to that user automatically.
        """
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
    def _ensure_profile(user):
        # Late import to avoid app-registry issues at startup
        from .models import UserProfile, Subscription
        UserProfile.objects.get_or_create(user=user)
        Subscription.objects.get_or_create(user=user)
