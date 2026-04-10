import json
import logging
import os
import time
from decimal import Decimal

import redis.asyncio as redis
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/0')
REDIS_KEY = 'tron:monitor_addresses'
_SYNC_INTERVAL = 60

_redis: redis.Redis | None = None
_last_sync: float = 0
_redis_ok: bool = True


async def get_redis() -> redis.Redis | None:
    global _redis, _redis_ok
    if not _redis_ok:
        return None
    if _redis is None:
        try:
            _redis = redis.from_url(REDIS_URL, decode_responses=True)
            await _redis.ping()
        except Exception as e:
            logger.warning('Redis 不可用，回退到数据库直查: %s', e)
            _redis_ok = False
            _redis = None
    return _redis


# ── 启动初始化 ────────────────────────────────────────────────────────────

async def init_monitor_cache():
    """程序启动时从数据库全量加载监控地址到 Redis。"""
    from monitors.models import AddressMonitor
    r = await get_redis()
    if r is None:
        logger.info('跳过 Redis 监控缓存初始化（Redis 不可用）')
        return

    await r.delete(REDIS_KEY)

    monitors = await sync_to_async(list)(
        AddressMonitor.objects.filter(is_active=True)
        .values('id', 'user_id', 'address', 'remark', 'usdt_threshold', 'trx_threshold')
    )
    pipe = r.pipeline()
    for mon in monitors:
        entry = {
            'id': mon['id'],
            'user_id': mon['user_id'],
            'address': mon['address'],
            'remark': mon['remark'] or '',
            'usdt_threshold': str(mon['usdt_threshold']),
            'trx_threshold': str(mon['trx_threshold']),
        }
        pipe.hset(REDIS_KEY, mon['address'], json.dumps(entry, ensure_ascii=False))
    await pipe.execute()
    logger.info('Redis 监控缓存已初始化: %d 个地址', len(monitors))


# ── 增删改操作 ────────────────────────────────────────────────────────────

async def add_monitor_to_cache(monitor_id: int, user_id: int, address: str, remark: str | None,
                               usdt_threshold: Decimal = Decimal('1'), trx_threshold: Decimal = Decimal('1')):
    r = await get_redis()
    if r is None:
        return
    entry = {
        'id': monitor_id,
        'user_id': user_id,
        'address': address,
        'remark': remark or '',
        'usdt_threshold': str(usdt_threshold),
        'trx_threshold': str(trx_threshold),
    }
    await r.hset(REDIS_KEY, address, json.dumps(entry, ensure_ascii=False))


async def remove_monitor_from_cache(address: str):
    r = await get_redis()
    if r is None:
        return
    await r.hdel(REDIS_KEY, address)


async def update_monitor_threshold_in_cache(address: str, currency: str, amount: Decimal):
    r = await get_redis()
    if r is None:
        return
    raw = await r.hget(REDIS_KEY, address)
    if raw:
        entry = json.loads(raw)
        key = 'usdt_threshold' if currency == 'USDT' else 'trx_threshold'
        entry[key] = str(amount)
        await r.hset(REDIS_KEY, address, json.dumps(entry, ensure_ascii=False))


# ── 读取 ──────────────────────────────────────────────────────────────────

async def get_monitor_addresses() -> dict[str, list[dict]]:
    """从 Redis 读取全部监控地址。Redis 不可用时回退到数据库。"""
    r = await get_redis()
    if r is not None:
        try:
            all_data = await r.hgetall(REDIS_KEY)
            if all_data:
                result: dict[str, list[dict]] = {}
                for addr, raw in all_data.items():
                    entry = json.loads(raw)
                    result.setdefault(addr, []).append(entry)
                return result
        except Exception as e:
            logger.warning('Redis 读取失败，回退数据库: %s', e)

    # 回退：直接查数据库
    return await _db_fallback_get_monitors()


@sync_to_async
def _db_fallback_get_monitors():
    from monitors.models import AddressMonitor
    qs = AddressMonitor.objects.filter(is_active=True)
    result: dict[str, list[dict]] = {}
    for mon in qs:
        entry = {
            'id': mon.id,
            'user_id': mon.user_id,
            'address': mon.address,
            'remark': mon.remark or '',
            'usdt_threshold': str(mon.usdt_threshold),
            'trx_threshold': str(mon.trx_threshold),
        }
        result.setdefault(mon.address, []).append(entry)
    return result


# ── 定时同步 ──────────────────────────────────────────────────────────────

async def maybe_sync():
    """定时（60秒）从数据库同步一次 Redis 缓存。"""
    global _last_sync
    now = time.time()
    if now - _last_sync < _SYNC_INTERVAL:
        return
    _last_sync = now
    try:
        await init_monitor_cache()
    except Exception as e:
        logger.error('Redis 监控缓存同步失败: %s', e)
