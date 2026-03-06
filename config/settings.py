import os
from pathlib import Path
import dj_database_url
from dotenv import load_dotenv
from django.core.exceptions import ImproperlyConfigured

load_dotenv(override=True)

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-dev-key')
DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')

if not DEBUG and SECRET_KEY == 'django-insecure-dev-key':
    raise ImproperlyConfigured(
        "SECRET_KEY must be set in production. Set the SECRET_KEY environment variable."
    )

_allowed_hosts_env = os.getenv('ALLOWED_HOSTS', '')
ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts_env.split(',') if h.strip()]
if not ALLOWED_HOSTS:
    if not DEBUG:
        raise ImproperlyConfigured(
            "ALLOWED_HOSTS must be set in production. "
            "Example: ALLOWED_HOSTS=docmonk.up.railway.app"
        )
    ALLOWED_HOSTS = ['*']

# Railway serves behind a proxy over HTTPS
CSRF_TRUSTED_ORIGINS = [f"https://{h}" for h in ALLOWED_HOSTS if h != '*']

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.staticfiles',
    'rest_framework',
    'analyzer',
    'qa',
]

MIDDLEWARE = [
    'config.middleware.HealthCheckMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.middleware.common.CommonMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

if not DEBUG and not os.getenv('DATABASE_URL'):
    raise ImproperlyConfigured(
        "DATABASE_URL must be set in production. "
        "DocMonk requires PostgreSQL — SQLite is not supported for deployed instances."
    )

DATABASES = {
    'default': dj_database_url.config(
        default=f'sqlite:///{BASE_DIR / "db.sqlite3"}',
        # conn_max_age=0: gevent workers share thread-local storage across greenlets.
        # Persistent connections (conn_max_age > 0) can be used by two greenlets
        # concurrently, corrupting the connection state. 0 means each request
        # opens and closes its own connection safely.
        conn_max_age=0,
        conn_health_checks=True,
    )
}

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{levelname}] {asctime} {name} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': 'WARNING',
    },
    'loggers': {
        'django': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'analyzer': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
        'qa': {
            'handlers': ['console'],
            'level': 'INFO',
            'propagate': False,
        },
    },
}

REST_FRAMEWORK = {
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.MultiPartParser',
    ],
}

# Storage backend: "r2" (default) or "local"
STORAGE_BACKEND = os.getenv('STORAGE_BACKEND', 'r2').lower()

# Cloudflare R2
R2_ENDPOINT_URL = os.getenv('R2_ENDPOINT_URL')
R2_ACCESS_KEY_ID = os.getenv('R2_ACCESS_KEY_ID')
R2_SECRET_ACCESS_KEY = os.getenv('R2_SECRET_ACCESS_KEY')
R2_BUCKET_NAME = os.getenv('R2_BUCKET_NAME')

# AI Provider Pool — Groq (keys 1 & 2) + Cerebras (keys 1 & 2)
# GROQ_API_KEY_1 falls back to the legacy GROQ_API_KEY env var
GROQ_API_KEY_1 = os.getenv('GROQ_API_KEY_1') or os.getenv('GROQ_API_KEY')
GROQ_API_KEY_2 = os.getenv('GROQ_API_KEY_2')
CEREBRAS_KEY_1 = os.getenv('CEREBRAS_KEY_1')
CEREBRAS_KEY_2 = os.getenv('CEREBRAS_KEY_2')
GROQ_MODEL      = os.getenv('GROQ_MODEL',      'llama-3.3-70b-versatile')
CEREBRAS_MODEL  = os.getenv('CEREBRAS_MODEL',  'gpt-oss-120b')

# Annotated PDFs output directory (fallback when R2 is not configured)
ANNOTATED_PDF_DIR = Path(os.getenv('ANNOTATED_PDF_DIR', str(BASE_DIR / 'annotated_pdfs')))
try:
    ANNOTATED_PDF_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    # Read-only filesystem (e.g. Leapcell); use /tmp as fallback
    ANNOTATED_PDF_DIR = Path('/tmp/annotated_pdfs')
    ANNOTATED_PDF_DIR.mkdir(parents=True, exist_ok=True)
