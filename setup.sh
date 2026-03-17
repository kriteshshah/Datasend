#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Spark Chat — Quick Setup Script
# Run: chmod +x setup.sh && ./setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -e

PYTHON=${PYTHON:-python3}
VENV_DIR=".venv"

echo ""
echo "⚡  SPARK CHAT — Setup"
echo "────────────────────────────────────────────"

# 1. Create virtual environment
echo "› Creating virtual environment..."
$PYTHON -m venv $VENV_DIR
source $VENV_DIR/bin/activate

# 2. Upgrade pip and install dependencies
echo "› Installing dependencies..."
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "  ✓ Dependencies installed"

# 3. Create .env if it doesn't exist
if [ ! -f ".env" ]; then
    echo "› Creating .env file..."
    cat > .env << EOF
# ── Django ─────────────────────────────────────────
SECRET_KEY=$(python -c "import secrets; print(secrets.token_urlsafe(50))")
DEBUG=True

# ── Redis (for WebSocket channels) ─────────────────
# Install Redis: brew install redis  |  sudo apt install redis-server
REDIS_HOST=127.0.0.1
REDIS_PORT=6379

# ── Stripe (optional – for subscription payments) ──
STRIPE_PUBLIC_KEY=pk_test_your_key_here
STRIPE_SECRET_KEY=sk_test_your_key_here
STRIPE_WEBHOOK_SECRET=whsec_your_webhook_secret_here
STRIPE_PRICE_ID=price_your_monthly_price_id_here
EOF
    echo "  ✓ .env created (edit it to add your Stripe keys)"
else
    echo "  ✓ .env already exists"
fi

# 4. Migrations
echo "› Running database migrations..."
python manage.py migrate --run-syncdb -q
echo "  ✓ Database ready (SQLite)"

# 5. Create superuser if desired
echo ""
read -p "  Create admin superuser? (y/N): " CREATE_SUPER
if [[ "$CREATE_SUPER" =~ ^[Yy]$ ]]; then
    python manage.py createsuperuser
fi

# 6. Collect static files
echo "› Collecting static files..."
python manage.py collectstatic --noinput -q
echo "  ✓ Static files collected"

echo ""
echo "════════════════════════════════════════════"
echo "  ✅  Setup complete!"
echo ""
echo "  START THE SERVER:"
echo ""
echo "  Option A — With Redis (full real-time WebSocket support):"
echo "    1. Start Redis:  redis-server"
echo "    2. Run Daphne:   daphne -p 8000 chatproject.asgi:application"
echo ""
echo "  Option B — Dev mode without Redis:"
echo "    Edit chatproject/settings.py, switch CHANNEL_LAYERS to"
echo "    InMemoryChannelLayer (instructions in the file), then:"
echo "    python manage.py runserver"
echo ""
echo "  Open: http://127.0.0.1:8000"
echo "  Admin: http://127.0.0.1:8000/admin"
echo "════════════════════════════════════════════"
echo ""
