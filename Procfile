release: python manage.py migrate --noinput && python manage.py collectstatic --noinput
web: PYTHONUNBUFFERED=1 gunicorn config.wsgi:application --config gunicorn.conf.py
