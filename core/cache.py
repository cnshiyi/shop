import asyncio
import logging
import os
import time
from collections import defaultdict
from datetime import date
from urllib.parse import quote

from django.apps import apps
import redis.asyncio as redis
from asgiref.sync import sync_to_async

from core.runtime_config import get_runtime_config

logger = logging.getLogger(__name__)


_cached_config: dict[str, str] = {}
_config_lock = asyncio.Lock()
_daily_stats: dict[str, dict[str, int]] = defaultdict(dict)
_daily_stats_lock = asyncio.Lock()


def _site_config_model():
    return apps.get_model('core', 'SiteConfig')


def _site_config_get_with_runtime_default(key: str, default: str = '') -> str:
    SiteConfig = _site_config_model()
    return SiteConfig.get(key, get_runtime_config(key, default))


def build_redis_url() -> str:
    host = get_runtime_config('redis_host', os.getenv('REDIS_HOST', '127.0.0.1')).strip() or '127.0.0.1'
    port = get_runtime_config('redis_port', os.getenv('REDIS_PORT', '6379')).strip() or '6379'
    db = get_runtime_config('redis_db', os.getenv('REDIS_DB', '0')).strip() or '0'
    password = get_runtime_config('redis_password', os.getenv('REDIS_PASSWORD', ''))
    auth = f':{quote(password, safe="")}@' if password else ''
    return f'redis://{auth}{host}:{port}/{db}'


REDIS_URL = os.getenv('REDIS_URL') or f"redis://{os.getenv('REDIS_HOST', '127.0.0.1')}:{os.getenv('REDIS_PORT', '6379')}/{os.getenv('REDIS_DB', '0')}"
CONFIG_KEY_PREFIX = 'site_config:'

_redis: redis.Redis | None = None
_redis_last_failure_at: float = 0
_redis_failure_logged = False


def _redis_retry_interval_seconds() -> int:
    try:
        return max(1, int(str(os.getenv('REDIS_RETRY_INTERVAL_SECONDS', '30')).strip()))
    except (TypeError, ValueError):
        return 30


def _redis_retry_now() -> float:
    return time.monotonic()


def get_cached_config_value(key: str, default: str = '') -> str:
    return _cached_config.get(key, default)


def cache_config_value(key: str, value: str):
    if value != '':
        _cached_config[key] = value
    else:
        _cached_config.pop(key, None)


def invalidate_config_cache(keys: list[str] | tuple[str, ...] | set[str] | str | None = None):
    if keys is None:
        _cached_config.clear()
        return
    if isinstance(keys, str):
        selected = [keys]
    else:
        selected = list(keys)
    for key in selected:
        _cached_config.pop(key, None)


async def refresh_config(keys: list[str] | None = None):
    global _cached_config
    selected = keys or list(_cached_config.keys())
    if not selected:
        return
    SiteConfig = _site_config_model()
    async with _config_lock:
        values = await sync_to_async(
            lambda: {item.key: SiteConfig.get(item.key, '') for item in SiteConfig.objects.filter(key__in=selected)},
            thread_sensitive=os.environ.get('DJANGO_TEST_SQLITE') == '1',
        )()
        for key in selected:
            if key in values:
                cache_config_value(key, values[key])
            else:
                invalidate_config_cache(key)


async def get_config(key: str, default: str = '') -> str:
    cached = get_cached_config_value(key, '')
    if cached != '':
        return cached
    value = await sync_to_async(
        _site_config_get_with_runtime_default,
        thread_sensitive=os.environ.get('DJANGO_TEST_SQLITE') == '1',
    )(key, default)
    cache_config_value(key, value)
    return value


async def get_redis() -> redis.Redis | None:
    global _redis, REDIS_URL, _redis_failure_logged, _redis_last_failure_at
    if _redis is not None:
        return _redis
    now = _redis_retry_now()
    if _redis_last_failure_at and now - _redis_last_failure_at < _redis_retry_interval_seconds():
        return None
    try:
        REDIS_URL = build_redis_url()
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
        await _redis.ping()
        _redis_last_failure_at = 0
        _redis_failure_logged = False
        logger.info('Redis connected')
        return _redis
    except Exception as exc:
        _redis_last_failure_at = now
        if not _redis_failure_logged:
            logger.warning('Redis unavailable, fallback to DB/local cache: %s', exc)
            _redis_failure_logged = True
        else:
            logger.debug('REDIS_UNAVAILABLE_RETRY_FAILED error=%s', exc)
        _redis = None
        return None


def _today_key() -> str:
    return date.today().isoformat()


async def bump_daily_stats(*parts, amount: int | None = None) -> int:
    if not parts:
        return 0
    if amount is None and len(parts) >= 2 and isinstance(parts[-1], (int, float)):
        amount = int(parts[-1])
        key_parts = parts[:-1]
    else:
        key_parts = parts
        amount = int(amount or 1)
    key = ':'.join(str(part).strip() for part in key_parts if str(part).strip())
    if not key:
        return 0
    redis_client = await get_redis()
    today = _today_key()
    redis_key = f'daily_stats:{today}'
    if redis_client is not None:
        try:
            return int(await redis_client.hincrby(redis_key, key, amount))
        except Exception as exc:
            logger.debug('REDIS_DAILY_STATS_INCREMENT_FAILED key=%s amount=%s error=%s', key, amount, exc)
    async with _daily_stats_lock:
        current = int(_daily_stats[today].get(key, 0) or 0) + amount
        _daily_stats[today][key] = current
        return current


async def get_daily_stats(key: str, default: int = 0) -> int:
    redis_client = await get_redis()
    today = _today_key()
    redis_key = f'daily_stats:{today}'
    if redis_client is not None:
        try:
            value = await redis_client.hget(redis_key, key)
            return int(value or default)
        except Exception as exc:
            logger.debug('REDIS_DAILY_STATS_READ_FAILED key=%s default=%s error=%s', key, default, exc)
    async with _daily_stats_lock:
        return int(_daily_stats[today].get(key, default) or default)


async def close():
    global _redis, _redis_failure_logged, _redis_last_failure_at
    if _redis is not None:
        try:
            await _redis.close()
        except Exception as exc:
            logger.debug('REDIS_CLOSE_FAILED error=%s', exc)
        _redis = None
    _redis_last_failure_at = 0
    _redis_failure_logged = False


__all__ = [
    'CONFIG_KEY_PREFIX',
    'REDIS_URL',
    'build_redis_url',
    'bump_daily_stats',
    'close',
    'get_config',
    'get_daily_stats',
    'get_redis',
    'refresh_config',
]
