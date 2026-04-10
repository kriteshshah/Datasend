"""
Django settings for chatproject — Spark Chat / Datasend
"""

import os
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env for local dev — on Render env vars are injected directly,
# so this is a no-op in production (file simply won't exist).
load_dotenv(BASE_DIR / '.env.staging')

# ── Security ──────────────────────────────────────────────────────────────────
def _env_bool(name: str, default: bool = False) -> bool:
    v = (os.getenv(name, "") or "").strip().lower()
    if v in ("1", "true", "yes", "y", "on"):
        return True
    if v in ("0", "false", "no", "n", "off"):
        return False
    return default


SECRET_KEY = (os.getenv("SECRET_KEY", "") or "").strip()
if not SECRET_KEY:
    raise RuntimeError(
        "Missing SECRET_KEY environment variable. "
        "Set it in your Render service Environment settings."
    )

DEBUG = _env_bool("DEBUG", default=False)
ALLOWED_HOSTS = ['*']

# Required for Django 4.x CSRF checks over HTTPS.
# Set in Render env: CSRF_TRUSTED_ORIGINS=https://datasend-xpoz.onrender.com
_csrf = os.getenv('CSRF_TRUSTED_ORIGINS', '')
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf.split(',') if o.strip()]

# ── Apps ──────────────────────────────────────────────────────────────────────
INSTALLED_APPS = [
    'daphne',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'channels',
    'chat',
    # django-allauth
    'allauth',
    'allauth.account',
    'allauth.socialaccount',
    'allauth.socialaccount.providers.google',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
    'allauth.account.middleware.AccountMiddleware',
]

ROOT_URLCONF = 'chatproject.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'chat.context_processors.subscription_context',
            ],
        },
    },
]

# ── ASGI ──────────────────────────────────────────────────────────────────────
ASGI_APPLICATION = 'chatproject.asgi.application'

# ── Channel Layers ────────────────────────────────────────────────────────────
#
#  RENDER:  attach a Redis instance → Render auto-injects REDIS_URL.
#           Do NOT copy the REDIS_URL into your local .env — it only
#           resolves inside Render's private network.
#
#  LOCAL with Redis:  set USE_REDIS=true in .env
#
#  LOCAL no Redis (default):  InMemoryChannelLayer, zero setup needed.
#
_REDIS_URL = os.getenv('REDIS_URL', '').strip()
_USE_REDIS  = os.getenv('USE_REDIS', 'false').lower() == 'true'

if _REDIS_URL:
    # Production on Render (or any host that provides a full Redis URL)
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [_REDIS_URL],
            },
        },
    }
elif _USE_REDIS:
    # Local dev with Redis installed
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels_redis.core.RedisChannelLayer',
            'CONFIG': {
                'hosts': [(
                    os.getenv('REDIS_HOST', '127.0.0.1'),
                    int(os.getenv('REDIS_PORT', 6379)),
                )],
            },
        },
    }
else:
    # Local dev, no Redis — everything still works in a single process
    CHANNEL_LAYERS = {
        'default': {
            'BACKEND': 'channels.layers.InMemoryChannelLayer',
        },
    }

# ── Database ──────────────────────────────────────────────────────────────────
_DB_URL = os.getenv('DATABASE_URL', '')
if _DB_URL:
    DATABASES = {'default': dj_database_url.parse(_DB_URL)}
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# ── Auth ──────────────────────────────────────────────────────────────────────
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/login/'

# ── django-allauth ─────────────────────────────────────────────────────────────
SITE_ID = 1

AUTHENTICATION_BACKENDS = [
    'django.contrib.auth.backends.ModelBackend',
    'allauth.account.auth_backends.AuthenticationBackend',
]

# Auto-detect environment: Render injects RENDER=true
_IS_PRODUCTION = os.getenv('RENDER', '') == 'true'
_SITE_DOMAIN   = 'datasend-xpoz.onrender.com' if _IS_PRODUCTION else '127.0.0.1:8000'
_SITE_PROTOCOL = 'https' if _IS_PRODUCTION else 'http'
_CALLBACK_URL  = f'{_SITE_PROTOCOL}://{_SITE_DOMAIN}/accounts/google/login/callback/'

SOCIALACCOUNT_PROVIDERS = {
    'google': {
        'SCOPE': ['profile', 'email'],
        'AUTH_PARAMS': {'access_type': 'online'},
        'OAUTH_PKCE_ENABLED': True,
        'REDIRECT_URI': _CALLBACK_URL,
        'APP': {
            'client_id': os.getenv('GOOGLE_CLIENT_ID', ''),
            'secret': os.getenv('GOOGLE_CLIENT_SECRET', ''),
            'key': '',
        },
    }
}

SOCIALACCOUNT_AUTO_SIGNUP = True
SOCIALACCOUNT_LOGIN_ON_GET = True
ACCOUNT_EMAIL_REQUIRED = False
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_AUTHENTICATION_METHOD = 'username_email'
SOCIALACCOUNT_ADAPTER = 'chat.adapters.SocialAccountAdapter'

# ── Internationalisation ──────────────────────────────────────────────────────
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'Asia/Kolkata'
USE_I18N = True
USE_TZ = True

# ── Static & Media ────────────────────────────────────────────────────────────
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = []
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ── Stripe ────────────────────────────────────────────────────────────────────
STRIPE_PUBLIC_KEY     = os.getenv('STRIPE_PUBLIC_KEY', '')
STRIPE_SECRET_KEY     = os.getenv('STRIPE_SECRET_KEY', '')
STRIPE_WEBHOOK_SECRET = os.getenv('STRIPE_WEBHOOK_SECRET', '')
SUBSCRIPTION_PRICE_ID = os.getenv("STRIPE_PRICE_ID", "")
# Shown in UI (Stripe recurring amount must match your Price in the dashboard).
SUBSCRIPTION_DISPLAY_PRICE = os.getenv("SUBSCRIPTION_DISPLAY_PRICE", "₹309")
# >0 adds a Stripe trial (checkout shows ₹0 “due today” until the trial ends). Set 0 to charge immediately.
SUBSCRIPTION_TRIAL_DAYS = int(os.getenv("SUBSCRIPTION_TRIAL_DAYS", "0"))

# ── Chat limits ───────────────────────────────────────────────────────────────
FREE_MESSAGES_PER_DAY = 30
# Free users: AI assistant, room AI, code coach, etc. Pro = unlimited.
FREE_AI_USES_PER_DAY = int(os.getenv("FREE_AI_USES_PER_DAY", "10"))
MAX_IMAGE_SIZE_MB     = 10
MAX_VIDEO_SIZE_MB     = 100
MAX_DOC_SIZE_MB       = 25

ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
ALLOWED_VIDEO_TYPES = ['video/mp4', 'video/webm', 'video/ogg', 'video/quicktime']
# ── Google Gemini (AI assistant & generated transcripts) ───────────────────────
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
# Default: 2.0 Flash is deprecated on the Gemini API; use 2.5 Flash (see ai.google.dev models doc).
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ── Groq (fallback when Gemini quota is exceeded) ─────────────────────────────
# Get your API key from: https://console.groq.com  →  API Keys
# Install SDK: pip install groq
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")

# ── GIF picker (Tenor and/or Giphy — like WhatsApp / Teams) ───────────────────
# GIF picker: Giphy is the default provider (Tenor optional fallback for existing keys).
# https://developers.giphy.com/docs/api/
GIPHY_API_KEY = os.getenv("GIPHY_API_KEY", "").strip()
# Tenor (optional; Google often restricts new API access)
TENOR_API_KEY = os.getenv("TENOR_API_KEY", "").strip()
TENOR_CLIENT_KEY = os.getenv("TENOR_CLIENT_KEY", "spark_chat").strip() or "spark_chat"

ALLOWED_DOC_TYPES   = [
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'text/plain',
]
