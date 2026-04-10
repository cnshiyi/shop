import logging
import time
from decimal import Decimal, ROUND_DOWN

import httpx

logger = logging.getLogger(__name__)
_cached_rate: Decimal | None = None
_cache_time = 0.0
_CACHE_TTL = 60


async def get_trx_price() -> Decimal:
    global _cached_rate, _cache_time
    now = time.time()
    if _cached_rate is not None and now - _cache_time < _CACHE_TTL:
        return _cached_rate
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get('https://api.binance.com/api/v3/ticker/price?symbol=TRXUSDT')
            response.raise_for_status()
            _cached_rate = Decimal(response.json()['price'])
            _cache_time = now
            return _cached_rate
    except Exception as exc:
        logger.warning('获取 TRX 汇率失败: %s', exc)
        if _cached_rate is not None:
            return _cached_rate
        raise RuntimeError('无法获取 TRX/USDT 汇率，请稍后重试') from exc


async def usdt_to_trx(usdt_amount: Decimal) -> Decimal:
    trx_price = await get_trx_price()
    return (usdt_amount / trx_price).quantize(Decimal('0.001'), rounding=ROUND_DOWN)


async def get_exchange_rate_display() -> str:
    trx_price = await get_trx_price()
    trx_per_usdt = (Decimal('1') / trx_price).quantize(Decimal('0.001'), rounding=ROUND_DOWN)
    return f'1 USDT ≈ {trx_per_usdt} TRX'
