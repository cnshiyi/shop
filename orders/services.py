"""过渡层：统一暴露 orders 域服务。"""

import logging
import random
import time
from decimal import Decimal, ROUND_DOWN

import httpx
from asgiref.sync import sync_to_async
from django.utils import timezone

from biz.services.commerce import (
    buy_with_balance,
    create_address_order,
    create_cart_balance_orders,
    get_balance_detail,
    list_balance_details,
)
from cloud.models import AddressMonitor, CloudServerOrder, CloudServerPlan
from orders.models import CartItem, Order, Product, Recharge

logger = logging.getLogger(__name__)
_cached_rate: Decimal | None = None
_cache_time = 0.0
_CACHE_TTL = 60


def _generate_order_no() -> str:
    return f'ORD{int(time.time() * 1000)}{random.randint(1000, 9999)}'


def _generate_unique_pay_amount(base_amount: Decimal, currency: str) -> Decimal:
    base = base_amount.quantize(Decimal('0.001'), rounding=ROUND_DOWN)
    for _ in range(100):
        pay_amount = (base + Decimal(random.randint(1, 999)) / Decimal('1000')).quantize(Decimal('0.001'), rounding=ROUND_DOWN)
        order_exists = Order.objects.filter(pay_amount=pay_amount, status='pending', currency=currency).exists()
        recharge_exists = Recharge.objects.filter(pay_amount=pay_amount, status='pending', currency=currency).exists()
        if not order_exists and not recharge_exists:
            return pay_amount
    return (base + Decimal(random.randint(1, 999)) / Decimal('1000')).quantize(Decimal('0.001'), rounding=ROUND_DOWN)


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
def add_to_cart(user_id: int, product_id: int, quantity: int = 1, item_type: str = 'product'):
    quantity = max(1, int(quantity or 1))
    if item_type == 'cloud_plan':
        plan = CloudServerPlan.objects.filter(id=product_id, is_active=True).first()
        if not plan:
            return None
        item = CartItem.objects.filter(user_id=user_id, item_type='cloud_plan', cloud_plan_id=product_id).first()
        if item:
            item.quantity += quantity
            item.save(update_fields=['quantity', 'updated_at'])
            return item
        return CartItem.objects.create(user_id=user_id, item_type='cloud_plan', cloud_plan_id=product_id, quantity=quantity)
    product = Product.objects.filter(id=product_id, is_active=True).first()
    if not product:
        return None
    item = CartItem.objects.filter(user_id=user_id, item_type='product', product_id=product_id).first()
    if item:
        item.quantity += quantity
        item.save(update_fields=['quantity', 'updated_at'])
        return item
    return CartItem.objects.create(user_id=user_id, item_type='product', product_id=product_id, quantity=quantity)


@sync_to_async
def list_cart_items(user_id: int):
    items = list(CartItem.objects.select_related('product', 'cloud_plan').filter(user_id=user_id).order_by('-updated_at', '-id'))
    total = Decimal('0')
    for item in items:
        if item.item_type == 'cloud_plan' and item.cloud_plan:
            total += Decimal(str(item.cloud_plan.price or 0)) * item.quantity
        elif item.product:
            total += Decimal(str(item.product.price or 0)) * item.quantity
    return items, total


@sync_to_async
def remove_cart_item(user_id: int, product_id: int, item_type: str = 'product'):
    filters = {'user_id': user_id, 'item_type': item_type}
    if item_type == 'cloud_plan':
        filters['cloud_plan_id'] = product_id
    else:
        filters['product_id'] = product_id
    deleted, _ = CartItem.objects.filter(**filters).delete()
    return deleted > 0


@sync_to_async
def clear_cart(user_id: int, item_type: str | None = None):
    qs = CartItem.objects.filter(user_id=user_id)
    if item_type:
        qs = qs.filter(item_type=item_type)
    qs.delete()
    return True


@sync_to_async
def create_cart_address_orders(user_id: int, currency: str = 'USDT'):
    items = list(CartItem.objects.select_related('product', 'cloud_plan').filter(user_id=user_id, item_type='product', product__is_active=True))
    orders = []
    for item in items:
        total = Decimal(str(item.product.price or 0)) * item.quantity
        pay_amount = _generate_unique_pay_amount(total, currency)
        expired_at = timezone.now() + timezone.timedelta(minutes=15)
        orders.append(Order.objects.create(
            order_no=_generate_order_no(), user_id=user_id, product=item.product, product_name=item.product.name,
            quantity=item.quantity, currency=currency, total_amount=total, pay_amount=pay_amount,
            pay_method='address', status='pending', expired_at=expired_at,
        ))
    CartItem.objects.filter(user_id=user_id).delete()
    return orders


@sync_to_async
def list_products(page: int = 1, per_page: int = 5):
    qs = Product.objects.filter(is_active=True).order_by('-sort_order', '-id')
    total = qs.count()
    items = list(qs[(page - 1) * per_page: page * per_page])
    return items, total


@sync_to_async
def get_product(product_id: int):
    return Product.objects.filter(id=product_id, is_active=True).first()


@sync_to_async
def list_orders(user_id: int, page: int = 1, per_page: int = 5):
    qs = Order.objects.filter(user_id=user_id).order_by('-created_at')
    total = qs.count()
    return list(qs[(page - 1) * per_page: page * per_page]), total


@sync_to_async
def get_order(order_id: int):
    return Order.objects.filter(id=order_id).first()


@sync_to_async
def list_cloud_orders(user_id: int, page: int = 1, per_page: int = 5):
    qs = CloudServerOrder.objects.filter(user_id=user_id).order_by('-created_at')
    total = qs.count()
    return list(qs[(page - 1) * per_page: page * per_page]), total


@sync_to_async
def get_cloud_order(order_id: int, user_id: int | None = None):
    qs = CloudServerOrder.objects.filter(id=order_id)
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    return qs.first()


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
