from pathlib import Path
import os

from dotenv import load_dotenv

from core.runtime_config import get_runtime_config

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=True)

SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-me')
DEBUG = os.getenv('DEBUG', '1') == '1'
def _split_csv_env(value: str):
    return [item.strip() for item in (value or '').split(',') if item.strip()]


ALLOWED_HOSTS = _split_csv_env(os.getenv('ALLOWED_HOSTS', '127.0.0.1,localhost,[::1]'))
CSRF_TRUSTED_ORIGINS = _split_csv_env(
    os.getenv(
        'CSRF_TRUSTED_ORIGINS',
        'http://localhost:5666,http://127.0.0.1:5666,http://[::1]:5666,http://localhost:5173,http://127.0.0.1:5173,http://[::1]:5173,http://localhost:8000,http://127.0.0.1:8000,http://[::1]:8000',
    )
)

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
    'accounts',
    'bot',
    'orders',
    'cloud',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'shop.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'shop.wsgi.application'
ASGI_APPLICATION = 'shop.asgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.mysql',
        'NAME': get_runtime_config('mysql_database', os.getenv('MYSQL_DATABASE', 'a')),
        'USER': get_runtime_config('mysql_user', os.getenv('MYSQL_USER', 'a')),
        'PASSWORD': get_runtime_config('mysql_password', os.getenv('MYSQL_PASSWORD', '123456')),
        'HOST': get_runtime_config('mysql_host', os.getenv('MYSQL_HOST', '127.0.0.1')),
        'PORT': int(get_runtime_config('mysql_port', os.getenv('MYSQL_PORT', '3306'))),
        'OPTIONS': {
            'charset': 'utf8mb4',
        },
        'TEST': {
            'NAME': os.getenv('MYSQL_TEST_DATABASE') or None,
        },
    }
}

if os.getenv('DJANGO_TEST_SQLITE', '0') == '1':
    DATABASES['default'] = {
        'ENGINE': 'django.db.backends.sqlite3',
        'NAME': ':memory:',
        'TEST': {
            'NAME': ':memory:',
        },
    }
elif os.getenv('DJANGO_TEST_REUSE_DB', '0') == '1' or os.getenv('DJANGO_TEST_USE_EXISTING_DB', '0') == '1':
    DATABASES['default']['TEST']['NAME'] = DATABASES['default']['NAME']

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'zh-hans'
TIME_ZONE = 'Asia/Shanghai'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
