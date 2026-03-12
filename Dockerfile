FROM python:3.11-slim-bullseye

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# OS deps + pdf2htmlEX AppImage (Debian packages no longer ship pdf2htmlEX)
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ca-certificates \
    poppler-utils \
    fontconfig \
    fonts-dejavu-core \
    fonts-liberation \
 && curl -L -o /tmp/pdf2htmlEX.AppImage \
    https://github.com/pdf2htmlEX/pdf2htmlEX/releases/download/v0.18.8.rc1/pdf2htmlEX-0.18.8.rc1-master-20200630-Ubuntu-bionic-x86_64.AppImage \
 && chmod +x /tmp/pdf2htmlEX.AppImage \
 && /tmp/pdf2htmlEX.AppImage --appimage-extract \
 && mv squashfs-root/usr/bin/pdf2htmlEX /usr/local/bin/pdf2htmlEX \
 && rm -rf /tmp/pdf2htmlEX.AppImage squashfs-root \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["bash", "-lc", "python scripts/wait_for_db.py && python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.wsgi:application --config gunicorn.conf.py"]
