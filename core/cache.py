import asyncio
import logging
import os
from collections import defaultdict
from datetime import date
from urllib.parse import quote

from django.apps import apps
import redis.asyncio as redis

from core.runtime_config import get_runtime_config

logger = logging.getLogger(__name__)


_cached_config: dict[str, str] = {}
_config_lock = asyncio.Lock()
_daily_stats: dict[str, dict[str, int]] = defaultdict(dict)
_daily_stats_lock = asyncio.Lock()


def _site_config_model():
    return apps.get_model('core', 'SiteConfig')


def build_redis_url() -> str:
    host = get_runtime_config('redis_host', os.getenv('REDIS_HOST', '127.0.0.1')).strip() or '127.0.0.1'
    port = get_runtime_config('redis_port', os.getenv('REDIS_PORT', '6379')).strip() or '6379'
    db = get_runtime_config('redis_db', os.getenv('REDIS_DB', '0')).strip() or '0'
    password = get_runtime_config('redis_password', os.getenv('REDIS_PASSWORD', ''))
    auth = f':{quote(password, safe="")}@' if password else ''
    return f'redis://{auth}{host}:{port}/{db}'


REDIS_URL = build_redis_url()
CONFIG_KEY_PREFIX = 'site_config:'

_redis: redis.Redis | None = None


async def refresh_config(keys: list[str] | None = None):
    global _cached_config
    selected = keys or list(_cached_config.keys())
    if not selected:
        return
    SiteConfig = _site_config_model()
    async with _config_lock:
        values = await asyncio.to_thread(
            lambda: {item.key: SiteConfig.get(item.key, '') for item in SiteConfig.objects.filter(key__in=selected)}
        )
        for key in selected:
            if key in values:
                _cached_config[key] = values[key]
            else:
                _cached_config.pop(key, None)


async def get_config(key: str, default: str = '') -> str:
    if key in _cached_config and _cached_config[key] != '':
        return _cached_config[key]
    SiteConfig = _site_config_model()
    value = await asyncio.to_thread(lambda: SiteConfig.get(key, get_runtime_config(key, default)))
    if value != '':
        _cached_config[key] = value
    return value


async def get_redis() -> redis.Redis | None:
    global _redis, REDIS_URL
    if _redis is not None:
        return _redis
    try:
        REDIS_URL = build_redis_url()
        _redis = redis.from_url(REDIS_URL, decode_responses=True)
        await _redis.ping()
        logger.info('Redis connected')
        return _redis
    except Exception as exc:
        logger.warning('Redis unavailable, fallback to DB/local cache: %s', exc)
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
        except Exception:
            pass
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
        except Exception:
            pass
    async with _daily_stats_lock:
        return int(_daily_stats[today].get(key, default) or default)


async def close():
    global _redis
    if _redis is not None:
        try:
            await _redis.close()
        except Exception:
            pass
        _redis = None


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
