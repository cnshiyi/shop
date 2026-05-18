import logging
import time
import asyncio
import threading
from decimal import Decimal, ROUND_DOWN

import httpx

logger = logging.getLogger(__name__)
_cached_rate: Decimal | None = None
_cache_time = 0.0
_CACHE_TTL = 60
_rate_lock = asyncio.Lock()
_sync_rate_lock = threading.Lock()


def _cache_is_fresh(now: float | None = None) -> bool:
    now = time.time() if now is None else now
    return _cached_rate is not None and now - _cache_time < _CACHE_TTL


def _get_cached_rate(*, allow_stale: bool = False) -> Decimal | None:
    if _cached_rate is None:
        return None
    if allow_stale or _cache_is_fresh():
        return _cached_rate
    return None


def _update_cached_rate(rate: Decimal, now: float | None = None) -> Decimal:
    global _cached_rate, _cache_time
    _cached_rate = rate
    _cache_time = time.time() if now is None else now
    return rate


async def _fetch_trx_price() -> Decimal:
    async with httpx.AsyncClient(timeout=5) as client:
        response = await client.get('https://api.binance.com/api/v3/ticker/price?symbol=TRXUSDT')
        response.raise_for_status()
        return Decimal(response.json()['price'])


def _fetch_trx_price_sync() -> Decimal:
    with httpx.Client(timeout=5) as client:
        response = client.get('https://api.binance.com/api/v3/ticker/price?symbol=TRXUSDT')
        response.raise_for_status()
        return Decimal(response.json()['price'])


async def get_trx_price() -> Decimal:
    now = time.time()
    cached = _get_cached_rate()
    if cached is not None:
        return cached
    async with _rate_lock:
        now = time.time()
        cached = _get_cached_rate()
        if cached is not None:
            return cached
        try:
            return _update_cached_rate(await _fetch_trx_price(), now)
        except Exception as exc:
            logger.warning('获取 TRX 汇率失败: %s', exc)
            stale_cached = _get_cached_rate(allow_stale=True)
            if stale_cached is not None:
                return stale_cached
            raise RuntimeError('无法获取 TRX/USDT 汇率，请稍后重试') from exc


def get_trx_price_sync() -> Decimal:
    now = time.time()
    cached = _get_cached_rate()
    if cached is not None:
        return cached
    with _sync_rate_lock:
        now = time.time()
        cached = _get_cached_rate()
        if cached is not None:
            return cached
        try:
            return _update_cached_rate(_fetch_trx_price_sync(), now)
        except Exception as exc:
            logger.warning('同步获取 TRX 汇率失败: %s', exc)
            stale_cached = _get_cached_rate(allow_stale=True)
            if stale_cached is not None:
                return stale_cached
            raise RuntimeError('无法获取 TRX/USDT 汇率，请稍后重试') from exc


async def usdt_to_trx(usdt_amount: Decimal) -> Decimal:
    trx_price = await get_trx_price()
    return (usdt_amount / trx_price).quantize(Decimal('0.001'), rounding=ROUND_DOWN)


def convert_usdt_to_trx_sync(usdt_amount: Decimal) -> Decimal:
    trx_price = get_trx_price_sync()
    return (usdt_amount / trx_price).quantize(Decimal('0.001'), rounding=ROUND_DOWN)


async def get_exchange_rate_display() -> str:
    trx_price = await get_trx_price()
    trx_per_usdt = (Decimal('1') / trx_price).quantize(Decimal('0.001'), rounding=ROUND_DOWN)
    return f'1 USDT ≈ {trx_per_usdt} TRX'


async def warm_trx_price_cache() -> Decimal | None:
    try:
        return await get_trx_price()
    except Exception as exc:
        logger.warning('预热 TRX 汇率缓存失败: %s', exc)
        return None
