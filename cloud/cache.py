import json
import logging
import time
from decimal import Decimal

from asgiref.sync import sync_to_async

from core.cache import get_redis

logger = logging.getLogger(__name__)

MONITORS_KEY = 'monitoring:monitors'
_last_monitor_sync: float = 0
_last_init_log_at: float = 0
_last_redis_warn_at: float = 0
_MONITOR_SYNC_INTERVAL = 60
_REDIS_WARN_INTERVAL = 600


async def init_monitor_cache(force_log: bool = False):
    from cloud.models import AddressMonitor
    global _last_init_log_at
    r = await get_redis()
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
        pipe.hset(MONITORS_KEY, mon['address'], json.dumps(_monitor_entry(mon), ensure_ascii=False))
    await pipe.execute()
    now = time.time()
    if force_log or now - _last_init_log_at >= 600:
        logger.info('监控缓存已同步: %d 个地址', len(monitors))
        _last_init_log_at = now


def _monitor_entry(mon: dict) -> dict:
    return {
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


async def add_monitor_to_cache(monitor_id: int, user_id: int, address: str,
                               remark: str | None, usdt_threshold: Decimal,
                               trx_threshold: Decimal, monitor_transfers: bool = True,
                               monitor_resources: bool = False):
    r = await get_redis()
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
    r = await get_redis()
    if r is None:
        return
    await r.hdel(MONITORS_KEY, address)


async def update_monitor_threshold_in_cache(address: str, currency: str, amount: Decimal):
    r = await get_redis()
    if r is None:
        return
    raw = await r.hget(MONITORS_KEY, address)
    if raw:
        entry = json.loads(raw)
        key = 'usdt_threshold' if currency == 'USDT' else 'trx_threshold'
        entry[key] = str(amount)
        await r.hset(MONITORS_KEY, address, json.dumps(entry, ensure_ascii=False))


async def update_monitor_flag_in_cache(address: str, field: str, value: bool):
    r = await get_redis()
    if r is None:
        return
    raw = await r.hget(MONITORS_KEY, address)
    if raw:
        entry = json.loads(raw)
        if field in {'monitor_transfers', 'monitor_resources'}:
            entry[field] = value
            await r.hset(MONITORS_KEY, address, json.dumps(entry, ensure_ascii=False))


async def get_monitor_addresses() -> dict[str, list[dict]]:
    global _last_redis_warn_at
    r = await get_redis()
    if r is not None:
        try:
            all_data = await r.hgetall(MONITORS_KEY)
            if all_data:
                result: dict[str, list[dict]] = {}
                for addr, raw in all_data.items():
                    result.setdefault(addr, []).append(json.loads(raw))
                return result
        except Exception as exc:
            now = time.time()
            if now - _last_redis_warn_at >= _REDIS_WARN_INTERVAL:
                logger.warning('监控缓存读取异常，已降级数据库: %s', exc)
                _last_redis_warn_at = now
    return await _db_fallback_get_monitors()


@sync_to_async
def _db_fallback_get_monitors():
    from cloud.models import AddressMonitor
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
    global _last_monitor_sync
    now = time.time()
    if now - _last_monitor_sync < _MONITOR_SYNC_INTERVAL:
        return
    _last_monitor_sync = now
    try:
        await init_monitor_cache(force_log=False)
    except Exception as exc:
        logger.error('Redis 监控同步失败: %s', exc)


__all__ = [
    'add_monitor_to_cache',
    'get_monitor_addresses',
    'init_monitor_cache',
    'maybe_sync_monitors',
    'remove_monitor_from_cache',
    'update_monitor_flag_in_cache',
    'update_monitor_threshold_in_cache',
]
