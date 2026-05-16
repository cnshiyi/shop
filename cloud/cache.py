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
            'last_energy', 'last_bandwidth', 'usdt_threshold', 'trx_threshold',
            'energy_threshold', 'bandwidth_threshold'
        )
    )
    pipe = r.pipeline()
    grouped: dict[str, list[dict]] = {}
    for mon in monitors:
        grouped.setdefault(mon['address'], []).append(_monitor_entry(mon))
    for address, entries in grouped.items():
        pipe.hset(MONITORS_KEY, address, json.dumps(entries, ensure_ascii=False))
    await pipe.execute()
    now = time.time()
    if force_log or now - _last_init_log_at >= 600:
        logger.info('监控缓存已同步: %d 个地址，%d 个监控', len(grouped), len(monitors))
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
        'energy_threshold': int(mon.get('energy_threshold', 1) or 0),
        'bandwidth_threshold': int(mon.get('bandwidth_threshold', 1) or 0),
    }


def _decode_monitor_entries(raw) -> list[dict]:
    if not raw:
        return []
    if isinstance(raw, bytes):
        raw = raw.decode()
    try:
        data = json.loads(raw)
    except Exception:
        logger.warning('监控缓存解析失败，已忽略异常缓存: raw=%r', raw)
        return []
    if isinstance(data, list):
        return [item for item in data if isinstance(item, dict)]
    if isinstance(data, dict):
        return [data]
    return []


async def _write_monitor_entries(redis, address: str, entries: list[dict]):
    if entries:
        await redis.hset(MONITORS_KEY, address, json.dumps(entries, ensure_ascii=False))
    else:
        await redis.hdel(MONITORS_KEY, address)


async def add_monitor_to_cache(monitor_id: int, user_id: int, address: str,
                               remark: str | None, usdt_threshold: Decimal,
                               trx_threshold: Decimal, monitor_transfers: bool = True,
                               monitor_resources: bool = False, energy_threshold: int = 1,
                               bandwidth_threshold: int = 1):
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
        'energy_threshold': int(energy_threshold or 0),
        'bandwidth_threshold': int(bandwidth_threshold or 0),
    }
    entries = _decode_monitor_entries(await r.hget(MONITORS_KEY, address))
    entries = [item for item in entries if int(item.get('id') or 0) != int(monitor_id)]
    entries.append(entry)
    await _write_monitor_entries(r, address, entries)


async def remove_monitor_from_cache(address: str, monitor_id: int | None = None):
    r = await get_redis()
    if r is None:
        return
    if monitor_id is None:
        await r.hdel(MONITORS_KEY, address)
        return
    entries = [
        item for item in _decode_monitor_entries(await r.hget(MONITORS_KEY, address))
        if int(item.get('id') or 0) != int(monitor_id)
    ]
    await _write_monitor_entries(r, address, entries)


async def update_monitor_threshold_in_cache(address: str, currency: str, amount, monitor_id: int | None = None):
    r = await get_redis()
    if r is None:
        return
    raw = await r.hget(MONITORS_KEY, address)
    entries = _decode_monitor_entries(raw)
    if entries:
        key_map = {
            'USDT': 'usdt_threshold',
            'TRX': 'trx_threshold',
            'ENERGY': 'energy_threshold',
            'BANDWIDTH': 'bandwidth_threshold',
        }
        key = key_map.get(str(currency or '').upper(), 'trx_threshold')
        for entry in entries:
            if monitor_id is not None and int(entry.get('id') or 0) != int(monitor_id):
                continue
            entry[key] = int(amount) if key in {'energy_threshold', 'bandwidth_threshold'} else str(amount)
        await _write_monitor_entries(r, address, entries)


async def update_monitor_flag_in_cache(address: str, field: str, value: bool, monitor_id: int | None = None):
    r = await get_redis()
    if r is None:
        return
    raw = await r.hget(MONITORS_KEY, address)
    entries = _decode_monitor_entries(raw)
    if entries and field in {'monitor_transfers', 'monitor_resources'}:
        for entry in entries:
            if monitor_id is not None and int(entry.get('id') or 0) != int(monitor_id):
                continue
            entry[field] = value
        await _write_monitor_entries(r, address, entries)


async def get_monitor_addresses() -> dict[str, list[dict]]:
    global _last_redis_warn_at
    r = await get_redis()
    if r is not None:
        try:
            all_data = await r.hgetall(MONITORS_KEY)
            if all_data:
                result: dict[str, list[dict]] = {}
                for addr, raw in all_data.items():
                    if isinstance(addr, bytes):
                        addr = addr.decode()
                    entries = _decode_monitor_entries(raw)
                    if entries:
                        result.setdefault(addr, []).extend(entries)
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
            'energy_threshold': int(getattr(mon, 'energy_threshold', 1) or 0),
            'bandwidth_threshold': int(getattr(mon, 'bandwidth_threshold', 1) or 0),
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
