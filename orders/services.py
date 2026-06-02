"""过渡层：统一暴露 orders 域服务。"""

import logging
import random
import time
from decimal import Decimal, ROUND_DOWN

import httpx
from asgiref.sync import sync_to_async
from django.core.paginator import Paginator
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from core.cache import get_redis
from core.order_numbers import unique_timestamp_order_no
from orders.ledger import record_balance_ledger
from bot.models import TelegramUser
from cloud.models import AddressMonitor, CloudAsset, CloudServerOrder, CloudServerPlan
from orders.models import BalanceLedger, CartItem, Order, Product, Recharge

logger = logging.getLogger(__name__)
_cached_rate: Decimal | None = None
_cache_time = 0.0
_CACHE_TTL = 86400
_TRX_PRICE_CACHE_KEY = 'orders:trx_usdt_price:v1'
_TRX_PRICE_LAST_CACHE_KEY = 'orders:trx_usdt_price:last'
CART_MAX_QUANTITY = 99


def _normalize_cart_quantity(quantity: int) -> int:
    try:
        normalized = int(quantity or 1)
    except (TypeError, ValueError):
        normalized = 1
    if normalized < 1:
        normalized = 1
    return min(normalized, CART_MAX_QUANTITY)


def _generate_order_no() -> str:
    return unique_timestamp_order_no('ORD', lambda value: Order.objects.filter(order_no=value).exists())


def _fmt_decimal(value) -> str:
    amount = Decimal(str(value or 0)).quantize(Decimal('0.001'), rounding=ROUND_DOWN)
    text = format(amount, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def _generate_unique_pay_amount(base_amount: Decimal, currency: str) -> Decimal:
    base = base_amount.quantize(Decimal('0.001'), rounding=ROUND_DOWN)
    now = timezone.now()
    active_payment = Q(expired_at__isnull=True) | Q(expired_at__gt=now)
    for _ in range(1000):
        pay_amount = (base + Decimal(random.randint(1, 999)) / Decimal('1000')).quantize(Decimal('0.001'), rounding=ROUND_DOWN)
        order_exists = Order.objects.filter(active_payment, pay_amount=pay_amount, status='pending', currency=currency).exists()
        recharge_exists = Recharge.objects.filter(active_payment, pay_amount=pay_amount, status='pending', currency=currency).exists()
        cloud_order_exists = CloudServerOrder.objects.filter(
            active_payment,
            pay_amount=pay_amount,
            status__in=['pending', 'renew_pending'],
            currency=currency,
        ).exists()
        if not order_exists and not recharge_exists and not cloud_order_exists:
            return pay_amount
    raise RuntimeError(f'无法生成唯一链上支付金额，请稍后重试：{base} {currency}')


@sync_to_async
def list_recharges(user_id: int, page: int = 1, per_page: int = 5):
    qs = Recharge.objects.filter(user_id=user_id).order_by('-created_at')
    total = qs.count()
    return list(qs[(page - 1) * per_page: page * per_page]), total


@sync_to_async
def create_recharge(user_id: int, amount: Decimal, currency: str, receive_address: str):
    amount = Decimal(str(amount)).quantize(Decimal('0.001'), rounding=ROUND_DOWN)
    pay_amount = _generate_unique_pay_amount(amount, currency)
    return Recharge.objects.create(user_id=user_id, amount=amount, pay_amount=pay_amount, currency=currency, status='pending', receive_address=receive_address, expired_at=timezone.now() + timezone.timedelta(minutes=30))


@sync_to_async
def get_recharge(user_id: int, recharge_id: int):
    return Recharge.objects.filter(user_id=user_id, id=recharge_id).first()


async def get_trx_price(*, force_refresh: bool = False) -> Decimal:
    global _cached_rate, _cache_time
    now = time.time()
    if not force_refresh and _cached_rate is not None and now - _cache_time < _CACHE_TTL:
        return _cached_rate

    redis_client = await get_redis()
    if redis_client is not None and not force_refresh:
        try:
            cached = await redis_client.get(_TRX_PRICE_CACHE_KEY)
            if cached:
                _cached_rate = Decimal(str(cached))
                _cache_time = now
                return _cached_rate
        except Exception as exc:
            logger.warning('读取 TRX 汇率缓存失败: %s', exc)

        try:
            cached = await redis_client.get(_TRX_PRICE_LAST_CACHE_KEY)
            if cached:
                _cached_rate = Decimal(str(cached))
                _cache_time = now
                return _cached_rate
        except Exception as exc:
            logger.warning('读取 TRX 最近汇率缓存失败: %s', exc)

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.get('https://api.binance.com/api/v3/ticker/price?symbol=TRXUSDT')
            response.raise_for_status()
            _cached_rate = Decimal(response.json()['price'])
            _cache_time = now
            if redis_client is not None:
                try:
                    await redis_client.setex(_TRX_PRICE_CACHE_KEY, _CACHE_TTL, str(_cached_rate))
                    await redis_client.set(_TRX_PRICE_LAST_CACHE_KEY, str(_cached_rate))
                except Exception as exc:
                    logger.warning('写入 TRX 汇率缓存失败: %s', exc)
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
    quantity = _normalize_cart_quantity(quantity)
    if item_type == 'cloud_plan':
        plan = CloudServerPlan.objects.filter(id=product_id, is_active=True).first()
        if not plan:
            return None
        item = CartItem.objects.filter(user_id=user_id, item_type='cloud_plan', cloud_plan_id=product_id).first()
        if item:
            item.quantity = min(CART_MAX_QUANTITY, int(item.quantity or 0) + quantity)
            item.save(update_fields=['quantity', 'updated_at'])
            return item
        return CartItem.objects.create(user_id=user_id, item_type='cloud_plan', cloud_plan_id=product_id, quantity=quantity)
    product = Product.objects.filter(id=product_id, is_active=True).first()
    if not product:
        return None
    item = CartItem.objects.filter(user_id=user_id, item_type='product', product_id=product_id).first()
    if item:
        item.quantity = min(CART_MAX_QUANTITY, int(item.quantity or 0) + quantity)
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
    items = list(CartItem.objects.select_related('product').filter(user_id=user_id, item_type='product', product__is_active=True))
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
    if items:
        CartItem.objects.filter(user_id=user_id, item_type='product', id__in=[item.id for item in items]).delete()
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


_INACTIVE_CLOUD_RESOURCE_STATUSES = {'deleted', 'deleting', 'terminated', 'terminating', 'expired'}


def _first_nonblank(*values) -> str:
    for value in values:
        text = str(value or '').strip()
        if text:
            return text
    return ''


def _cloud_order_filter_queryset(queryset, order_filter: str):
    order_filter = str(order_filter or 'all').strip().lower()
    if order_filter == 'paid':
        return queryset.filter(Q(paid_at__isnull=False) | Q(status__in={'paid', 'provisioning', 'completed', 'expiring', 'suspended'}))
    if order_filter == 'unpaid':
        return queryset.filter(status__in={'pending', 'renew_pending'}, paid_at__isnull=True)
    if order_filter == 'renew':
        return queryset.filter(Q(status='renew_pending') | Q(last_renewed_at__isnull=False) | Q(provision_note__icontains='续费'))
    if order_filter == 'new':
        return queryset.filter(replacement_for__isnull=True).exclude(Q(status='renew_pending') | Q(last_renewed_at__isnull=False) | Q(provision_note__icontains='续费'))
    return queryset


@sync_to_async
def list_cloud_orders(user_id: int, page: int = 1, per_page: int = 8, order_filter: str = 'all'):
    """个人中心订单查询：只按订单表展示，不复用代理列表逻辑。"""
    queryset = (
        CloudServerOrder.objects
        .filter(user_id=user_id)
        .exclude(status__in={'deleted'})
        .order_by('-created_at', '-id')
    )
    queryset = _cloud_order_filter_queryset(queryset, order_filter)
    total = queryset.count()
    start = max(0, (page - 1) * per_page)
    return list(queryset[start:start + per_page]), total


@sync_to_async
def get_cloud_order(order_id: int, user_id: int | None = None):
    qs = CloudServerOrder.objects.filter(id=order_id)
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    return qs.first()


@sync_to_async
def list_balance_details(user_id: int, page: int = 1, per_page: int = 8, detail_filter: str = 'all'):
    user = TelegramUser.objects.filter(id=user_id).first()
    if not user:
        return [], 0
    detail_filter = str(detail_filter or 'all').strip().lower()
    recharge_items = [
        {
            'id': f'recharge-{recharge.id}',
            'kind': 'recharge',
            'title': f'充值 #{recharge.id}',
            'description': f'充值订单已完成，余额增加 {_fmt_decimal(recharge.amount)} {recharge.currency}',
            'currency': recharge.currency,
            'direction': 'in',
            'amount': _fmt_decimal(recharge.amount),
            'before_balance': None,
            'after_balance': None,
            'created_at': recharge.completed_at or recharge.created_at,
        }
        for recharge in Recharge.objects.filter(user_id=user_id, status='completed').order_by('-completed_at', '-created_at')[:200]
    ]
    if detail_filter not in {'all', 'in', 'recharge'}:
        recharge_items = []
    ledger_items = [
        {
            'id': f'ledger-{ledger.id}',
            'kind': 'ledger',
            'title': ledger.get_type_display(),
            'description': ledger.description or ledger.get_type_display(),
            'currency': ledger.currency,
            'direction': ledger.direction,
            'amount': _fmt_decimal(ledger.amount),
            'before_balance': _fmt_decimal(ledger.before_balance),
            'after_balance': _fmt_decimal(ledger.after_balance),
            'created_at': ledger.created_at,
        }
        for ledger in BalanceLedger.objects.filter(user_id=user_id).order_by('-created_at', '-id')[:300]
    ]
    if detail_filter == 'in':
        ledger_items = [item for item in ledger_items if item['direction'] == 'in']
    elif detail_filter == 'out':
        ledger_items = [item for item in ledger_items if item['direction'] == 'out']
    elif detail_filter == 'recharge':
        ledger_items = [item for item in ledger_items if item['title'] == '充值入账']
    elif detail_filter == 'pay':
        ledger_items = [item for item in ledger_items if item['direction'] == 'out' or '支付' in item['title']]
    items = sorted([*ledger_items, *recharge_items], key=lambda item: item['created_at'] or timezone.now(), reverse=True)
    paginator = Paginator(items, per_page)
    page_obj = paginator.get_page(page)
    return list(page_obj.object_list), paginator.count


@sync_to_async
def get_balance_detail(user_id: int, raw_item_id: str):
    if raw_item_id.startswith('ledger-'):
        try:
            ledger_id = int(raw_item_id.split('-', 1)[1])
        except (ValueError, IndexError):
            return None
        ledger = BalanceLedger.objects.filter(user_id=user_id, id=ledger_id).first()
        if not ledger:
            return None
        return {
            'id': raw_item_id,
            'title': ledger.get_type_display(),
            'description': ledger.description or ledger.get_type_display(),
            'currency': ledger.currency,
            'direction': ledger.direction,
            'amount': _fmt_decimal(ledger.amount),
            'before_balance': _fmt_decimal(ledger.before_balance),
            'after_balance': _fmt_decimal(ledger.after_balance),
            'created_at': ledger.created_at,
        }
    if raw_item_id.startswith('recharge-'):
        try:
            recharge_id = int(raw_item_id.split('-', 1)[1])
        except (ValueError, IndexError):
            return None
        recharge = Recharge.objects.filter(user_id=user_id, id=recharge_id).first()
        if not recharge:
            return None
        return {
            'id': raw_item_id,
            'title': f'充值 #{recharge.id}',
            'description': f'充值订单已完成，余额增加 {_fmt_decimal(recharge.amount)} {recharge.currency}',
            'currency': recharge.currency,
            'direction': 'in',
            'amount': _fmt_decimal(recharge.amount),
            'before_balance': None,
            'after_balance': None,
            'created_at': recharge.completed_at or recharge.created_at,
        }
    return None


@sync_to_async
def create_address_order(user_id: int, product_id: int, quantity: int, total: Decimal, currency: str):
    product = Product.objects.get(id=product_id)
    pay_amount = _generate_unique_pay_amount(total, currency)
    expired_at = timezone.now() + timezone.timedelta(minutes=15)
    return Order.objects.create(
        order_no=_generate_order_no(), user_id=user_id, product=product, product_name=product.name,
        quantity=quantity, currency=currency, total_amount=total, pay_amount=pay_amount,
        pay_method='address', status='pending', expired_at=expired_at,
    )


@sync_to_async
def create_cart_balance_orders(user_id: int, currency: str = 'USDT'):
    items = list(CartItem.objects.select_related('product').filter(user_id=user_id, item_type='product', product__is_active=True))
    created_orders = []
    with transaction.atomic():
        user = TelegramUser.objects.select_for_update().get(id=user_id)
        balance_field = 'balance_trx' if currency == 'TRX' else 'balance'
        current_balance = Decimal(str(getattr(user, balance_field, 0) or 0))
        total_cost = sum((Decimal(str(item.product.price or 0)) * item.quantity) for item in items)
        if current_balance < total_cost:
            return None, f'{currency} 余额不足'
        for item in items:
            product = Product.objects.select_for_update().get(id=item.product_id)
            if product.stock != -1 and product.stock < item.quantity:
                return None, f'商品 {product.name} 库存不足'
        old_balance = current_balance
        setattr(user, balance_field, current_balance - total_cost)
        user.save(update_fields=[balance_field, 'updated_at'])
        running_balance = old_balance
        for item in items:
            product = Product.objects.select_for_update().get(id=item.product_id)
            total = Decimal(str(product.price or 0)) * item.quantity
            if product.stock != -1:
                product.stock -= item.quantity
                product.save(update_fields=['stock', 'updated_at'])
            order = Order.objects.create(
                order_no=_generate_order_no(), user=user, product=product, product_name=product.name,
                quantity=item.quantity, currency=currency, total_amount=total, pay_amount=total,
                pay_method='balance', status='delivered', paid_at=timezone.now(),
            )
            next_balance = running_balance - total
            record_balance_ledger(
                user,
                ledger_type='order_balance_pay',
                currency=currency,
                old_balance=running_balance,
                new_balance=next_balance,
                related_type='order',
                related_id=order.id,
                description=f'商品订单 #{order.order_no} 余额支付',
            )
            running_balance = next_balance
            created_orders.append(order)
        if items:
            CartItem.objects.filter(user_id=user_id, item_type='product', id__in=[item.id for item in items]).delete()
    return created_orders, None


@sync_to_async
def buy_with_balance(user_id: int, product_id: int, quantity: int, total: Decimal, currency: str):
    with transaction.atomic():
        user = TelegramUser.objects.select_for_update().get(id=user_id)
        product = Product.objects.select_for_update().get(id=product_id)
        balance_field = 'balance_trx' if currency == 'TRX' else 'balance'
        balance = getattr(user, balance_field)
        if balance < total:
            return None, '余额不足'
        if product.stock != -1 and product.stock < quantity:
            return None, '库存不足'
        old_balance = balance
        setattr(user, balance_field, balance - total)
        user.save(update_fields=[balance_field, 'updated_at'])
        if product.stock != -1:
            product.stock -= quantity
            product.save(update_fields=['stock', 'updated_at'])
        order = Order.objects.create(
            order_no=_generate_order_no(), user=user, product=product, product_name=product.name,
            quantity=quantity, currency=currency, total_amount=total, pay_amount=total,
            pay_method='balance', status='delivered', paid_at=timezone.now(),
        )
        record_balance_ledger(
            user,
            ledger_type='order_balance_pay',
            currency=currency,
            old_balance=old_balance,
            new_balance=getattr(user, balance_field),
            related_type='order',
            related_id=order.id,
            description=f'商品订单 #{order.order_no} 余额支付',
        )
        return order, None


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
def set_monitor_threshold(monitor_id: int, user_id: int, currency: str, amount: Decimal | int) -> bool:
    field_map = {
        'USDT': 'usdt_threshold',
        'TRX': 'trx_threshold',
        'ENERGY': 'energy_threshold',
        'BANDWIDTH': 'bandwidth_threshold',
    }
    field = field_map.get(str(currency or '').upper(), 'trx_threshold')
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
    'get_recharge',
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
