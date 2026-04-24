import random
import time
from decimal import Decimal, ROUND_DOWN

from asgiref.sync import sync_to_async
from django.core.paginator import Paginator
from django.db import transaction
from django.utils import timezone

from orders.ledger import record_balance_ledger
from bot.models import TelegramUser
from orders.models import BalanceLedger, Order, Product, Recharge


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


def add_to_cart(*args, **kwargs):
    from orders.services import add_to_cart as impl
    return impl(*args, **kwargs)


def list_cart_items(*args, **kwargs):
    from orders.services import list_cart_items as impl
    return impl(*args, **kwargs)


def remove_cart_item(*args, **kwargs):
    from orders.services import remove_cart_item as impl
    return impl(*args, **kwargs)


def clear_cart(*args, **kwargs):
    from orders.services import clear_cart as impl
    return impl(*args, **kwargs)


def create_cart_address_orders(*args, **kwargs):
    from orders.services import create_cart_address_orders as impl
    return impl(*args, **kwargs)


def list_products(*args, **kwargs):
    from orders.services import list_products as impl
    return impl(*args, **kwargs)


def get_product(*args, **kwargs):
    from orders.services import get_product as impl
    return impl(*args, **kwargs)


def list_orders(*args, **kwargs):
    from orders.services import list_orders as impl
    return impl(*args, **kwargs)


def get_order(*args, **kwargs):
    from orders.services import get_order as impl
    return impl(*args, **kwargs)


def list_cloud_orders(*args, **kwargs):
    from orders.services import list_cloud_orders as impl
    return impl(*args, **kwargs)


def get_cloud_order(*args, **kwargs):
    from orders.services import get_cloud_order as impl
    return impl(*args, **kwargs)


@sync_to_async
def list_balance_details(user_id: int, page: int = 1, per_page: int = 8):
    user = TelegramUser.objects.filter(id=user_id).first()
    if not user:
        return [], 0
    recharge_items = [
        {
            'id': f'recharge-{recharge.id}',
            'title': f'充值 #{recharge.id}',
            'description': f'充值订单已完成，余额增加 {recharge.amount} {recharge.currency}',
            'currency': recharge.currency,
            'direction': 'in',
            'amount': str(recharge.amount),
            'before_balance': None,
            'after_balance': None,
            'created_at': recharge.completed_at or recharge.created_at,
        }
        for recharge in Recharge.objects.filter(user_id=user_id, status='completed').order_by('-completed_at', '-created_at')[:200]
    ]
    ledger_items = [
        {
            'id': f'ledger-{ledger.id}',
            'title': ledger.get_type_display(),
            'description': ledger.description or ledger.get_type_display(),
            'currency': ledger.currency,
            'direction': ledger.direction,
            'amount': str(ledger.amount),
            'before_balance': str(ledger.before_balance),
            'after_balance': str(ledger.after_balance),
            'created_at': ledger.created_at,
        }
        for ledger in BalanceLedger.objects.filter(user_id=user_id).order_by('-created_at', '-id')[:300]
    ]
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
            'amount': str(ledger.amount),
            'before_balance': str(ledger.before_balance),
            'after_balance': str(ledger.after_balance),
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
            'description': f'充值订单已完成，余额增加 {recharge.amount} {recharge.currency}',
            'currency': recharge.currency,
            'direction': 'in',
            'amount': str(recharge.amount),
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
    from orders.models import CartItem

    items = list(CartItem.objects.select_related('product').filter(user_id=user_id, product__is_active=True))
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
        new_balance = getattr(user, balance_field)
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
            record_balance_ledger(
                user,
                ledger_type='order_balance_pay',
                currency=currency,
                old_balance=old_balance,
                new_balance=new_balance,
                related_type='order',
                related_id=order.id,
                description=f'商品订单 #{order.order_no} 余额支付',
            )
            old_balance = new_balance
            created_orders.append(order)
        CartItem.objects.filter(user_id=user_id).delete()
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
