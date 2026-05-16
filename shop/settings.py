import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

from dotenv import load_dotenv

from core.runtime_config import get_runtime_config

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / '.env', override=False)

DEBUG = os.getenv('DEBUG', '1') == '1'
SECRET_KEY = os.getenv('SECRET_KEY', 'dev-secret-key-change-me')
if not DEBUG and SECRET_KEY == 'dev-secret-key-change-me':
    raise ImproperlyConfigured('生产环境必须通过 SECRET_KEY 配置强随机密钥')


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def _env_int(name: str, default: int = 0) -> int:
    try:
        return int(str(os.getenv(name, str(default))).strip())
    except (TypeError, ValueError):
        return default


def _split_csv_env(value: str):
    return [item.strip() for item in (value or '').split(',') if item.strip()]


ALLOWED_HOSTS = _split_csv_env(os.getenv('ALLOWED_HOSTS', '127.0.0.1,localhost,[::1]'))
ADMIN_FRONTEND_URL = os.getenv('ADMIN_FRONTEND_URL', '/')
CSRF_TRUSTED_ORIGINS = _split_csv_env(
    os.getenv(
        'CSRF_TRUSTED_ORIGINS',
        'http://localhost:5666,http://127.0.0.1:5666,http://[::1]:5666,http://localhost:5173,http://127.0.0.1:5173,http://[::1]:5173,http://localhost:8000,http://127.0.0.1:8000,http://[::1]:8000',
    )
)

INSTALLED_APPS = [
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'core',
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
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'shop.urls'

WSGI_APPLICATION = 'shop.wsgi.application'
ASGI_APPLICATION = 'shop.asgi.application'

database_engine = os.getenv('DB_ENGINE', 'mysql').strip().lower()

if database_engine == 'sqlite':
    sqlite_name = os.getenv('SQLITE_NAME', 'db.sqlite3').strip() or 'db.sqlite3'
    sqlite_path = sqlite_name if sqlite_name == ':memory:' else str(BASE_DIR / sqlite_name)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': sqlite_path,
            'TEST': {
                'NAME': ':memory:' if sqlite_name == ':memory:' else sqlite_path,
            },
        }
    }
else:
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

MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

SESSION_COOKIE_AGE = 60 * 60
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SESSION_SAVE_EVERY_REQUEST = False
SESSION_COOKIE_SECURE = _env_bool('SESSION_COOKIE_SECURE', not DEBUG)
CSRF_COOKIE_SECURE = _env_bool('CSRF_COOKIE_SECURE', not DEBUG)
SECURE_SSL_REDIRECT = _env_bool('SECURE_SSL_REDIRECT', not DEBUG)
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_HSTS_SECONDS = _env_int('SECURE_HSTS_SECONDS', 31536000 if not DEBUG else 0)
SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', not DEBUG)
SECURE_HSTS_PRELOAD = _env_bool('SECURE_HSTS_PRELOAD', not DEBUG)

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO')
CLOUD_LOG_LEVEL = os.getenv('CLOUD_LOG_LEVEL', LOG_LEVEL)

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'console': {
            'format': '%(asctime)s [%(levelname)s] %(name)s: %(message)s',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'console',
            'level': 'DEBUG',
        },
    },
    'loggers': {
        'cloud': {
            'handlers': ['console'],
            'level': CLOUD_LOG_LEVEL,
            'propagate': False,
        },
        'core': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'bot': {
            'handlers': ['console'],
            'level': LOG_LEVEL,
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}
