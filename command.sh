#!/bin/bash

set -e

#  python manage.py loaddata permissions asset_categories fields roles &&

python manage.py migrate &&
python manage.py collectstatic --noinput &&

gunicorn chatproject.wsgi:application --bind 0.0.0.0:8000 --timeout 600
