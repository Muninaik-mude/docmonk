import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv('SECRET_KEY', 'django-insecure-dev-key')
DEBUG = os.getenv('DEBUG', 'False').lower() in ('true', '1', 'yes')

# In production set ALLOWED_HOSTS to your Leapcell domain, e.g.:
# ALLOWED_HOSTS=your-app.leapcell.dev
_allowed_hosts_env = os.getenv('ALLOWED_HOSTS', '')
ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts_env.split(',') if h.strip()] or ['*']

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.staticfiles',
    'rest_framework',
    'analyzer',
]

MIDDLEWARE = [
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

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': BASE_DIR / 'db.sqlite3',
    }
}

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

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

# Groq AI (primary)
GROQ_API_KEY = os.getenv('GROQ_API_KEY')
GROQ_MODEL = os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b')

# Annotated PDFs output directory (fallback when R2 is not configured)
ANNOTATED_PDF_DIR = BASE_DIR / 'annotated_pdfs'
ANNOTATED_PDF_DIR.mkdir(parents=True, exist_ok=True)
