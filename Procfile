release: python scripts/wait_for_db.py && python manage.py migrate --noinput && python manage.py collectstatic --noinput
web: python scripts/wait_for_db.py && PYTHONUNBUFFERED=1 gunicorn config.wsgi:application --config gunicorn.conf.py
