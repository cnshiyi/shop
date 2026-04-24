"""过渡层：统一暴露 orders 域服务。"""

import logging
import time
from decimal import Decimal, ROUND_DOWN

import httpx
from asgiref.sync import sync_to_async

from biz.services.commerce import (
    add_to_cart,
    buy_with_balance,
    clear_cart,
    create_address_order,
    create_cart_address_orders,
    create_cart_balance_orders,
    get_balance_detail,
    get_cloud_order,
    get_order,
    get_product,
    list_balance_details,
    list_cart_items,
    list_cloud_orders,
    list_orders,
    list_products,
    remove_cart_item,
)
from cloud.models import AddressMonitor
from orders.models import Recharge

logger = logging.getLogger(__name__)
_cached_rate: Decimal | None = None
_cache_time = 0.0
_CACHE_TTL = 60


@sync_to_async
def list_recharges(user_id: int, page: int = 1, per_page: int = 5):
    qs = Recharge.objects.filter(user_id=user_id).order_by('-created_at')
    total = qs.count()
    return list(qs[(page - 1) * per_page: page * per_page]), total


@sync_to_async
def create_recharge(user_id: int, amount: Decimal, currency: str, receive_address: str):
    pay_amount = _generate_unique_pay_amount(amount, currency)
    return Recharge.objects.create(user_id=user_id, amount=amount, pay_amount=pay_amount, currency=currency, status='pending')


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


@sync_to_async
def list_monitors(user_id: int):
    return list(AddressMonitor.objects.filter(user_id=user_id).order_by('-created_at'))


@sync_to_async
def get_monitor(monitor_id: int, user_id: int):
    return AddressMonitor.objects.filter(id=monitor_id, user_id=user_id).first()


@sync_to_async
def add_monitor(user_id: int, address: str, remark: str | None):
    return AddressMonitor.objects.create(user_id=user_id, address=address, remark=remark or '')


@sync_to_async
def delete_monitor(monitor_id: int, user_id: int) -> bool:
    deleted, _ = AddressMonitor.objects.filter(id=monitor_id, user_id=user_id).delete()
    return deleted > 0


@sync_to_async
def set_monitor_threshold(monitor_id: int, user_id: int, currency: str, amount: Decimal) -> bool:
    field = 'usdt_threshold' if currency == 'USDT' else 'trx_threshold'
    return AddressMonitor.objects.filter(id=monitor_id, user_id=user_id).update(**{field: amount}) > 0


@sync_to_async
def toggle_monitor_flag(monitor_id: int, user_id: int, field: str):
    monitor = AddressMonitor.objects.filter(id=monitor_id, user_id=user_id).first()
    if not monitor or field not in {'monitor_transfers', 'monitor_resources'}:
        return None
    current = getattr(monitor, field)
    setattr(monitor, field, not current)
    monitor.save(update_fields=[field])
    return monitor

__all__ = [
    'add_monitor',
    'add_to_cart',
    'buy_with_balance',
    'clear_cart',
    'create_address_order',
    'create_cart_address_orders',
    'create_cart_balance_orders',
    'create_recharge',
    'delete_monitor',
    'get_balance_detail',
    'get_cloud_order',
    'get_exchange_rate_display',
    'get_monitor',
    'get_order',
    'get_product',
    'get_trx_price',
    'list_balance_details',
    'list_cart_items',
    'list_cloud_orders',
    'list_monitors',
    'list_orders',
    'list_products',
    'list_recharges',
    'remove_cart_item',
    'set_monitor_threshold',
    'toggle_monitor_flag',
    'usdt_to_trx',
]
