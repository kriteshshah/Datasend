# ⚡ Spark Chat

A full-featured, real-time Django chat application with emoji support, media sharing, document uploads, WebSocket notifications, and a built-in subscription/paywall system.

---

## ✨ Features

| Feature | Free | Pro |
|---|---|---|
| Real-time messaging (WebSocket) | ✅ | ✅ |
| Emoji picker & reactions | ✅ | ✅ |
| Image sharing (view inline) | ✅ | ✅ |
| Video sharing (play inline) | ❌ | ✅ |
| Document sharing (PDF, DOCX, XLSX…) | ❌ | ✅ |
| Daily message limit | 30/day | Unlimited |
| Real-time notifications | ✅ | ✅ |
| Typing indicators | ✅ | ✅ |
| Online/offline presence | ✅ | ✅ |
| Reply to messages | ✅ | ✅ |
| Delete messages | ✅ | ✅ |
| Group chats | ✅ | ✅ |
| Message history / pagination | ✅ | ✅ |
| Lightbox for photos & videos | ✅ | ✅ |
| User profiles & avatars | ✅ | ✅ |
| Stripe subscription payments | — | ✅ |

---

## 🏗️ Tech Stack

| Layer | Technology |
|---|---|
| Web framework | **Django 4.2** |
| Real-time | **Django Channels 4 + Daphne** |
| Channel layer | **Redis** (or in-memory for dev) |
| Database | **SQLite** (swap to PostgreSQL for prod) |
| Payments | **Stripe** |
| File storage | Local filesystem (swap to S3 via `django-storages`) |
| Frontend | Vanilla JS + WebSockets (no framework) |

---

## 🚀 Quick Start

### Prerequisites
- Python 3.10+
- Redis (for real-time WebSocket support)

```bash
# Install Redis
# macOS:
brew install redis && brew services start redis

# Ubuntu/Debian:
sudo apt install redis-server && sudo systemctl start redis
```

### 1. Clone / unzip the project

```bash
cd chatproject
```

### 2. Run setup script
```bash
chmod +x setup.sh
./setup.sh
```
This will:
- Create a Python virtual environment
- Install all dependencies
- Generate a `.env` file with a random secret key
- Run database migrations
- Optionally create a superuser

### 3. Start the server

**With Redis (recommended — full WebSocket support):**
```bash
source .venv/bin/activate
daphne -p 8000 chatproject.asgi:application
```

**Dev mode without Redis:**

Edit `chatproject/settings.py` and replace the `CHANNEL_LAYERS` block with:
```python
CHANNEL_LAYERS = {
    'default': {
        'BACKEND': 'channels.layers.InMemoryChannelLayer',
    },
}
```
Then:
```bash
source .venv/bin/activate
python manage.py runserver
```

### 4. Open the app
- **App:** http://127.0.0.1:8000
- **Admin:** http://127.0.0.1:8000/admin

---

## 💳 Stripe Subscription Setup

1. Create a [Stripe account](https://stripe.com)
2. In the Stripe dashboard, create a **Recurring Price** (e.g. ₹299/month)
3. Copy your keys into `.env`:

```env
STRIPE_PUBLIC_KEY=pk_live_...
STRIPE_SECRET_KEY=sk_live_...
STRIPE_WEBHOOK_SECRET=whsec_...
STRIPE_PRICE_ID=price_...
```

4. Register the webhook endpoint in Stripe Dashboard:
   - URL: `https://yourdomain.com/webhooks/stripe/`
   - Events to listen for:
     - `checkout.session.completed`
     - `customer.subscription.updated`
     - `customer.subscription.deleted`
     - `invoice.payment_failed`
     - `invoice.payment_succeeded`

5. For local testing, use [Stripe CLI](https://stripe.com/docs/stripe-cli):
```bash
stripe listen --forward-to localhost:8000/webhooks/stripe/
```

---

## 📁 Project Structure

```
chatproject/
├── chatproject/
│   ├── settings.py          # Django settings
│   ├── urls.py              # Root URL config
│   ├── asgi.py              # ASGI app (Channels + HTTP)
│   └── wsgi.py              # WSGI fallback
│
├── chat/
│   ├── models.py            # All data models
│   ├── views.py             # HTTP views + file upload
│   ├── consumers.py         # WebSocket consumers (chat + notifications)
│   ├── routing.py           # WebSocket URL routing
│   ├── urls.py              # HTTP URL patterns
│   ├── signals.py           # Auto-create profile/subscription on user creation
│   ├── webhooks.py          # Stripe webhook handler
│   ├── context_processors.py# Injects subscription data into every template
│   ├── templatetags/
│   │   └── chat_tags.py     # Custom template filters
│   └── templates/chat/
│       ├── base.html        # Base layout + toast system
│       ├── login.html
│       ├── register.html
│       ├── home.html        # Sidebar + room list
│       ├── room.html        # Full chat UI (WebSocket + file upload)
│       ├── subscription.html
│       ├── subscription_success.html
│       ├── profile.html
│       ├── create_room.html
│       └── partials/
│           └── message_bubble.html
│
├── media/                   # Uploaded files (images, videos, docs)
├── requirements.txt
├── setup.sh                 # One-command setup
└── manage.py
```

---

## 🔌 WebSocket Architecture

Two WebSocket consumers run in parallel for each connected user:

| Consumer | URL | Purpose |
|---|---|---|
| `ChatConsumer` | `ws://host/ws/chat/<room_id>/` | Messages, typing, reactions, file events, read receipts |
| `NotificationConsumer` | `ws://host/ws/notifications/` | Cross-room notifications, unread badge count |

**Message flow for a text message:**
```
Client → WebSocket → ChatConsumer.receive()
  → check quota (DB)
  → save Message (DB)
  → increment DailyMessageCount (DB)
  → channel_layer.group_send() → broadcast to all room members
  → save Notification for offline members (DB)
  → push notification via NotificationConsumer to offline members
```

**File upload flow:**
```
Client → POST /room/<id>/upload/ → upload_file() view
  → validate type & size
  → save Message + file (DB + filesystem)
  → channel_layer.group_send('file_message') → broadcast
  → save Notifications for offline members
```

---

## 🛡️ Subscription / Quota System

| Rule | Detail |
|---|---|
| Free daily limit | 30 messages per calendar day (resets at midnight) |
| Enforcement | Both WebSocket consumer (text) and upload view (files) check quota |
| Pro activation | Via Stripe `checkout.session.completed` webhook |
| Pro check | `Subscription.is_pro` property checks plan + status + expiry |
| Quota API | `GET /api/quota/` — returns remaining count, useful for JS polling |

---

## ⚙️ Configuration Reference (`.env`)

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | auto-generated | Django secret key |
| `DEBUG` | `True` | Set to `False` in production |
| `REDIS_HOST` | `127.0.0.1` | Redis hostname |
| `REDIS_PORT` | `6379` | Redis port |
| `STRIPE_PUBLIC_KEY` | — | Stripe publishable key |
| `STRIPE_SECRET_KEY` | — | Stripe secret key |
| `STRIPE_WEBHOOK_SECRET` | — | Stripe webhook signing secret |
| `STRIPE_PRICE_ID` | — | Stripe recurring price ID |

---

## 🚀 Production Deployment Checklist

- [ ] Set `DEBUG=False` in `.env`
- [ ] Generate a strong `SECRET_KEY`
- [ ] Switch database to PostgreSQL
- [ ] Use Redis Cloud or self-hosted Redis
- [ ] Configure `ALLOWED_HOSTS` with your domain
- [ ] Move media storage to AWS S3 (`django-storages`)
- [ ] Serve with Daphne behind Nginx
- [ ] Add HTTPS / SSL termination at Nginx
- [ ] Register Stripe webhook with production URL
- [ ] Set all Stripe keys to live keys

**Nginx + Daphne example:**
```nginx
upstream daphne {
    server 127.0.0.1:8000;
}

server {
    listen 443 ssl;
    server_name yourdomain.com;

    location / { proxy_pass http://daphne; proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade"; proxy_set_header Host $host; }
    location /media/ { alias /path/to/media/; }
    location /static/ { alias /path/to/staticfiles/; }
}
```

---

## 📱 File Support

| Type | Formats | Max Size |
|---|---|---|
| Images | JPEG, PNG, GIF, WebP | 10 MB |
| Videos | MP4, WebM, OGG, MOV | 100 MB |
| Documents | PDF, DOC/DOCX, XLS/XLSX, TXT | 25 MB |

Images and videos render inline in the chat. Videos use the native HTML5 `<video>` player. Documents show a download card with icon + filename + size.

---

## 🔑 Default Admin Access

After running `python manage.py createsuperuser`, go to:
`http://127.0.0.1:8000/admin/`

From the admin you can:
- Manually activate Pro for any user (set `Subscription.plan = pro`)
- View all messages, rooms, reactions
- Monitor daily message counts
- Manage notifications

---

*Built with Django Channels, WebSockets, and ❤️*
