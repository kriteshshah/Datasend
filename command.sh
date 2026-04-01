#!/bin/bash

set -euo pipefail

# Render sets PORT; keep a sane default for local runs.
: "${PORT:=8000}"

#  python manage.py loaddata permissions asset_categories fields roles &&

python3 manage.py migrate --noinput &&
python3 manage.py collectstatic --noinput &&

daphne -b 0.0.0.0 -p $PORT chatproject.asgi:application