#!/bin/bash

set -e

#  python manage.py loaddata permissions asset_categories fields roles &&

python manage.py migrate &&
python manage.py collectstatic --noinput &&

daphne -b 0.0.0.0 -p $PORT chatproject.asgi:application