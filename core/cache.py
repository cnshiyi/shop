import logging
import os
from datetime import datetime, timedelta
from decimal import Decimal

import redis.asyncio as redis

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
CFG_PREFIX = 'core:cfg:'
DAILY_PREFIX = 'daily:'
CFG_TTL = 120

_redis: redis.Redis | None = None
_redis_ok: bool = True


async def get_redis() -> redis.Redis | None:
    global _redis, _redis_ok
    if not _redis_ok:
        return None
    if _redis is None:
        try:
            _redis = redis.from_url(REDIS_URL, decode_responses=True)
            await _redis.ping()
        except Exception as exc:
            logger.warning('Redis 不可用，降级到数据库: %s', exc)
            _redis_ok = False
            _redis = None
    return _redis


async def get_config(key: str, default: str = '') -> str:
    r = await get_redis()
    redis_key = CFG_PREFIX + key
    if r is not None:
        try:
            value = await r.get(redis_key)
            if value is not None:
                return value
        except Exception:
            pass

    from core.models import SiteConfig
    value = SiteConfig.get(key, default)

    if r is not None:
        try:
            await r.set(redis_key, value, ex=CFG_TTL)
        except Exception:
            pass
    return value


async def invalidate_config(key: str):
    r = await get_redis()
    if r is None:
        return
    try:
        await r.delete(CFG_PREFIX + key)
    except Exception:
        pass


async def refresh_config(keys: list[str]):
    r = await get_redis()
    if r is None:
        return
    from core.models import SiteConfig
    for key in keys:
        value = SiteConfig.get(key, '')
        try:
            await r.set(CFG_PREFIX + key, value, ex=CFG_TTL)
        except Exception:
            pass


async def get_daily_stats(address: str, currency: str, date_key: str | None = None) -> dict[str, str]:
    date_key = date_key or datetime.now().strftime('%Y-%m-%d')
    redis_key = f'{DAILY_PREFIX}{date_key}:{address}:{currency}'
    r = await get_redis()
    if r is None:
        return {'income': '0', 'expense': '0'}
    raw = await r.hgetall(redis_key)
    if not raw:
        return {'income': '0', 'expense': '0'}
    return {'income': raw.get('income', '0'), 'expense': raw.get('expense', '0')}


async def bump_daily_stats(address: str, currency: str, direction: str, amount: Decimal) -> dict[str, str]:
    date_key = datetime.now().strftime('%Y-%m-%d')
    redis_key = f'{DAILY_PREFIX}{date_key}:{address}:{currency}'
    r = await get_redis()
    if r is None:
        return {'income': '0', 'expense': '0'}
    expire_at = datetime.combine(datetime.now().date() + timedelta(days=1), datetime.min.time())
    pipe = r.pipeline()
    pipe.hincrbyfloat(redis_key, direction, float(amount))
    pipe.expireat(redis_key, expire_at)
    pipe.hgetall(redis_key)
    _, _, raw = await pipe.execute()
    return {'income': raw.get('income', '0'), 'expense': raw.get('expense', '0')}


async def close():
    global _redis
    if _redis:
        try:
            await _redis.aclose()
        except Exception:
            pass
        _redis = None
