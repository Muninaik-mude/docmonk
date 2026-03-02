---
name: django-architecture
description: >
  Apply this skill for any Django/DRF project scaffolding, app creation,
  settings configuration, or when reviewing project structure. Enforces
  production-grade clean architecture patterns.
---

# Django Project Architecture

## Core Philosophy
- **Separation of concerns** — each app owns one domain, nothing bleeds across
- **Thin views, fat services** — business logic lives in `services.py`, not views
- **Explicit over implicit** — no magic, every import is intentional
- **12-factor app** — config from environment, never hardcoded

---

## Canonical Project Structure

```
project_root/
├── config/                      # Project configuration (not an app)
│   ├── settings/
│   │   ├── base.py              # Shared settings
│   │   ├── development.py       # Local dev overrides
│   │   ├── production.py        # Production overrides
│   │   └── testing.py           # Test-specific settings
│   ├── urls.py                  # Root URL config
│   ├── wsgi.py
│   └── asgi.py
│
├── apps/                        # All Django apps live here
│   ├── core/                    # Shared utilities, base classes
│   │   ├── models.py            # Abstract base models (TimeStampedModel etc.)
│   │   ├── exceptions.py        # Custom exceptions
│   │   ├── pagination.py        # Shared pagination classes
│   │   ├── permissions.py       # Shared DRF permissions
│   │   └── utils.py
│   │
│   ├── users/                   # Auth & user management
│   │   ├── models.py
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── services.py          # Business logic here, NOT in views
│   │   ├── urls.py
│   │   ├── admin.py
│   │   ├── signals.py
│   │   └── tests/
│   │       ├── test_models.py
│   │       ├── test_views.py
│   │       └── test_services.py
│   │
│   └── [domain_app]/            # Same structure per domain
│
├── requirements/
│   ├── base.txt
│   ├── development.txt
│   └── production.txt
│
├── .env.example
├── manage.py
└── pytest.ini
```

---

## Settings Architecture

### base.py
```python
from pathlib import Path
import environ

env = environ.Env()
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Read .env file
environ.Env.read_env(BASE_DIR / '.env')

SECRET_KEY = env('SECRET_KEY')
DEBUG = env.bool('DEBUG', default=False)
ALLOWED_HOSTS = env.list('ALLOWED_HOSTS', default=[])

DJANGO_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

THIRD_PARTY_APPS = [
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'django_filters',
    'drf_spectacular',  # OpenAPI schema generation
]

LOCAL_APPS = [
    'apps.core',
    'apps.users',
    # add domain apps here
]

INSTALLED_APPS = DJANGO_APPS + THIRD_PARTY_APPS + LOCAL_APPS

# Database
DATABASES = {
    'default': env.db('DATABASE_URL')
}

# DRF Global Config
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_PAGINATION_CLASS': 'apps.core.pagination.StandardResultsSetPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_FILTER_BACKENDS': [
        'django_filters.rest_framework.DjangoFilterBackend',
        'rest_framework.filters.SearchFilter',
        'rest_framework.filters.OrderingFilter',
    ],
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': '100/hour',
        'user': '1000/hour',
    },
    'EXCEPTION_HANDLER': 'apps.core.exceptions.custom_exception_handler',
}
```

---

## Base Models (apps/core/models.py)

```python
import uuid
from django.db import models


class TimeStampedModel(models.Model):
    """Abstract base — every model should inherit this."""
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UUIDModel(models.Model):
    """Use UUID primary keys for public-facing resources."""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class BaseModel(UUIDModel, TimeStampedModel):
    """Combine both — use this as your default base model."""
    class Meta:
        abstract = True
```

---

## App Creation Checklist

When creating a new Django app:
1. `python manage.py startapp appname apps/appname`
2. Update `AppConfig` name to `'apps.appname'`
3. Add to `LOCAL_APPS` in settings
4. Create `apps/appname/urls.py`
5. Create `apps/appname/services.py` (business logic)
6. Include URL in `config/urls.py` with versioned prefix
7. Create `apps/appname/tests/` directory with `__init__.py`

---

## URL Versioning (config/urls.py)

```python
from django.urls import path, include

urlpatterns = [
    path('api/v1/', include([
        path('auth/', include('apps.users.urls')),
        path('orders/', include('apps.orders.urls')),
    ])),
]
```

---

## Rules Claude Must Follow
- NEVER put business logic in views or serializers — always `services.py`
- NEVER use `settings.py` at root — always split into base/dev/prod
- ALWAYS inherit from `BaseModel` unless there's a specific reason not to
- ALWAYS use `django-environ` for environment variables, never `os.environ` directly
- NEVER hardcode secrets, URLs, or credentials
- ALWAYS create a `tests/` folder inside every app, not a single `tests.py`
- ALWAYS namespace your app URLs