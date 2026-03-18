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
SECRET_KEY = os.getenv('SECRET_KEY')
DEBUG = os.getenv('DEBUG')
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
    'channels',
    'chat',
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
SUBSCRIPTION_PRICE_ID = os.getenv('STRIPE_PRICE_ID', '')

# ── Chat limits ───────────────────────────────────────────────────────────────
FREE_MESSAGES_PER_DAY = 30
MAX_IMAGE_SIZE_MB     = 10
MAX_VIDEO_SIZE_MB     = 100
MAX_DOC_SIZE_MB       = 25

ALLOWED_IMAGE_TYPES = ['image/jpeg', 'image/png', 'image/gif', 'image/webp']
ALLOWED_VIDEO_TYPES = ['video/mp4', 'video/webm', 'video/ogg', 'video/quicktime']
ALLOWED_DOC_TYPES   = [
    'application/pdf',
    'application/msword',
    'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'application/vnd.ms-excel',
    'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'text/plain',
]
