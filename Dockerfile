FROM python:3.11-slim-bullseye

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# OS deps for pdf2htmlEX + fonts for closer visual fidelity
RUN apt-get update && apt-get install -y --no-install-recommends \
    pdf2htmlex \
    poppler-utils \
    fontconfig \
    fonts-dejavu-core \
    fonts-liberation \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

CMD ["bash", "-lc", "python manage.py migrate --noinput && python manage.py collectstatic --noinput && gunicorn config.wsgi:application --config gunicorn.conf.py"]
