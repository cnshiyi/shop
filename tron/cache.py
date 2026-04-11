"""
tron/cache.py — Redis 统一缓存层

缓存项：
  - 监控地址 (hash: tron:monitors)
  - 站点配置 (string: tron:cfg:<key>)

Redis 不可用时自动降级到数据库。
"""
import json
import logging
import os
import time
from decimal import Decimal

import redis.asyncio as redis
from asgiref.sync import sync_to_async

logger = logging.getLogger(__name__)

REDIS_URL = os.getenv('REDIS_URL', 'redis://127.0.0.1:6379/0')

# ── key 前缀 ──────────────────────────────────────────────────────────────
MONITORS_KEY = 'tron:monitors'
CFG_PREFIX = 'tron:cfg:'
DAILY_PREFIX = 'tron:daily:'
CFG_TTL = 120  # 配置缓存 2 分钟

# ── 内部状态 ──────────────────────────────────────────────────────────────
_redis: redis.Redis | None = None
_redis_ok: bool = True
_last_monitor_sync: float = 0
_MONITOR_SYNC_INTERVAL = 60


async def _get_redis() -> redis.Redis | None:
    global _redis, _redis_ok
    if not _redis_ok:
        return None
    if _redis is None:
        try:
            _redis = redis.from_url(REDIS_URL, decode_responses=True)
            await _redis.ping()
        except Exception as e:
            logger.warning('Redis 不可用，降级到数据库: %s', e)
            _redis_ok = False
            _redis = None
    return _redis


# ══════════════════════════════════════════════════════════════════════════
# 站点配置缓存
# ══════════════════════════════════════════════════════════════════════════

async def get_config(key: str, default: str = '') -> str:
    """从 Redis 读取配置，miss 则查数据库并回写 Redis。"""
    r = await _get_redis()
    redis_key = CFG_PREFIX + key

    if r is not None:
        try:
            val = await r.get(redis_key)
            if val is not None:
                return val
        except Exception:
            pass

    # 回退数据库
    from core.models import SiteConfig
    val = SiteConfig.get(key, default)

    # 回写 Redis
    if r is not None:
        try:
            await r.set(redis_key, val, ex=CFG_TTL)
        except Exception:
            pass
    return val


async def invalidate_config(key: str):
    """配置变更时清除缓存。"""
    r = await _get_redis()
    if r is None:
        return
    try:
        await r.delete(CFG_PREFIX + key)
    except Exception:
        pass


async def refresh_config(keys: list[str]):
    """批量刷新配置到 Redis（启动时调用）。"""
    r = await _get_redis()
    if r is None:
        return
    from core.models import SiteConfig
    for key in keys:
        val = SiteConfig.get(key, '')
        try:
            await r.set(CFG_PREFIX + key, val, ex=CFG_TTL)
        except Exception:
            pass


# ══════════════════════════════════════════════════════════════════════════
# 监控地址缓存
# ══════════════════════════════════════════════════════════════════════════

async def init_monitor_cache():
    """启动时从数据库全量加载监控地址到 Redis。"""
    from monitors.models import AddressMonitor
    r = await _get_redis()
    if r is None:
        logger.info('跳过 Redis 监控缓存初始化（Redis 不可用）')
        return

    await r.delete(MONITORS_KEY)
    monitors = await sync_to_async(list)(
        AddressMonitor.objects.filter(is_active=True)
        .values(
            'id', 'user_id', 'address', 'remark', 'monitor_transfers', 'monitor_resources',
            'last_energy', 'last_bandwidth', 'usdt_threshold', 'trx_threshold'
        )
    )
    pipe = r.pipeline()
    for mon in monitors:
        entry = {
            'id': mon['id'],
            'user_id': mon['user_id'],
            'address': mon['address'],
            'remark': mon['remark'] or '',
            'monitor_transfers': bool(mon.get('monitor_transfers', True)),
            'monitor_resources': bool(mon.get('monitor_resources', False)),
            'last_energy': int(mon.get('last_energy', 0) or 0),
            'last_bandwidth': int(mon.get('last_bandwidth', 0) or 0),
            'usdt_threshold': str(mon['usdt_threshold']),
            'trx_threshold': str(mon['trx_threshold']),
        }
        pipe.hset(MONITORS_KEY, mon['address'], json.dumps(entry, ensure_ascii=False))
    await pipe.execute()
    logger.info('Redis 监控缓存已初始化: %d 个地址', len(monitors))


async def add_monitor_to_cache(monitor_id: int, user_id: int, address: str,
                               remark: str | None, usdt_threshold: Decimal,
                               trx_threshold: Decimal, monitor_transfers: bool = True,
                               monitor_resources: bool = False):
    r = await _get_redis()
    if r is None:
        return
    entry = {
        'id': monitor_id,
        'user_id': user_id,
        'address': address,
        'remark': remark or '',
        'monitor_transfers': monitor_transfers,
        'monitor_resources': monitor_resources,
        'last_energy': 0,
        'last_bandwidth': 0,
        'usdt_threshold': str(usdt_threshold),
        'trx_threshold': str(trx_threshold),
    }
    await r.hset(MONITORS_KEY, address, json.dumps(entry, ensure_ascii=False))


async def remove_monitor_from_cache(address: str):
    r = await _get_redis()
    if r is None:
        return
    await r.hdel(MONITORS_KEY, address)


async def update_monitor_threshold_in_cache(address: str, currency: str, amount: Decimal):
    r = await _get_redis()
    if r is None:
        return
    raw = await r.hget(MONITORS_KEY, address)
    if raw:
        entry = json.loads(raw)
        key = 'usdt_threshold' if currency == 'USDT' else 'trx_threshold'
        entry[key] = str(amount)
        await r.hset(MONITORS_KEY, address, json.dumps(entry, ensure_ascii=False))


async def update_monitor_flag_in_cache(address: str, field: str, value: bool):
    r = await _get_redis()
    if r is None:
        return
    raw = await r.hget(MONITORS_KEY, address)
    if raw:
        entry = json.loads(raw)
        if field in {'monitor_transfers', 'monitor_resources'}:
            entry[field] = value
            await r.hset(MONITORS_KEY, address, json.dumps(entry, ensure_ascii=False))


async def get_monitor_addresses() -> dict[str, list[dict]]:
    """从 Redis 读取全部监控地址。降级到数据库。"""
    r = await _get_redis()
    if r is not None:
        try:
            all_data = await r.hgetall(MONITORS_KEY)
            if all_data:
                result: dict[str, list[dict]] = {}
                for addr, raw in all_data.items():
                    result.setdefault(addr, []).append(json.loads(raw))
                return result
        except Exception as e:
            logger.warning('Redis 读取监控失败，降级数据库: %s', e)
    return await _db_fallback_get_monitors()


@sync_to_async
def _db_fallback_get_monitors():
    from monitors.models import AddressMonitor
    qs = AddressMonitor.objects.filter(is_active=True)
    result: dict[str, list[dict]] = {}
    for mon in qs:
        entry = {
            'id': mon.id, 'user_id': mon.user_id, 'address': mon.address,
            'remark': mon.remark or '',
            'monitor_transfers': mon.monitor_transfers,
            'monitor_resources': mon.monitor_resources,
            'last_energy': mon.last_energy,
            'last_bandwidth': mon.last_bandwidth,
            'usdt_threshold': str(mon.usdt_threshold),
            'trx_threshold': str(mon.trx_threshold),
        }
        result.setdefault(mon.address, []).append(entry)
    return result


async def maybe_sync_monitors():
    """定时从数据库同步监控地址到 Redis。"""
    global _last_monitor_sync
    now = time.time()
    if now - _last_monitor_sync < _MONITOR_SYNC_INTERVAL:
        return
    _last_monitor_sync = now
    try:
        await init_monitor_cache()
    except Exception as e:
        logger.error('Redis 监控同步失败: %s', e)


async def get_daily_stats(address: str, currency: str, date_key: str | None = None) -> dict[str, str]:
    from datetime import datetime

    date_key = date_key or datetime.now().strftime('%Y-%m-%d')
    redis_key = f'{DAILY_PREFIX}{date_key}:{address}:{currency}'
    r = await _get_redis()
    if r is None:
        return {'income': '0', 'expense': '0'}
    raw = await r.hgetall(redis_key)
    if not raw:
        return {'income': '0', 'expense': '0'}
    return {'income': raw.get('income', '0'), 'expense': raw.get('expense', '0')}


async def bump_daily_stats(address: str, currency: str, direction: str, amount: Decimal) -> dict[str, str]:
    from datetime import datetime, timedelta

    date_key = datetime.now().strftime('%Y-%m-%d')
    redis_key = f'{DAILY_PREFIX}{date_key}:{address}:{currency}'
    r = await _get_redis()
    if r is None:
        return {'income': '0', 'expense': '0'}
    expire_at = datetime.combine(datetime.now().date() + timedelta(days=1), datetime.min.time())
    pipe = r.pipeline()
    pipe.hincrbyfloat(redis_key, direction, float(amount))
    pipe.expireat(redis_key, expire_at)
    pipe.hgetall(redis_key)
    _, _, raw = await pipe.execute()
    return {'income': raw.get('income', '0'), 'expense': raw.get('expense', '0')}


# ══════════════════════════════════════════════════════════════════════════
# 启动 / 关闭
# ══════════════════════════════════════════════════════════════════════════

async def init_all():
    """启动时初始化所有 Redis 缓存。"""
    await refresh_config(['receive_address', 'trongrid_api_key'])
    await init_monitor_cache()


async def close():
    """关闭 Redis 连接。"""
    global _redis
    if _redis:
        try:
            await _redis.aclose()
        except Exception:
            pass
        _redis = None
