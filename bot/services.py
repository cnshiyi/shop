import random
import time
from decimal import Decimal, ROUND_DOWN

from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from monitors.models import AddressMonitor
from payments.models import Recharge
from shopbiz.models import Order, Product
from users.models import TelegramUser


def _generate_order_no() -> str:
    return f'ORD{int(time.time() * 1000)}{random.randint(1000, 9999)}'


@sync_to_async
def get_or_create_user(tg_user_id: int, username: str | None, first_name: str | None) -> TelegramUser:
    user, _ = TelegramUser.objects.get_or_create(
        tg_user_id=tg_user_id,
        defaults={'username': username, 'first_name': first_name},
    )
    changed = False
    if user.username != username:
        user.username = username
        changed = True
    if user.first_name != first_name:
        user.first_name = first_name
        changed = True
    if changed:
        user.save(update_fields=['username', 'first_name', 'updated_at'])
    return user


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
def list_recharges(user_id: int, page: int = 1, per_page: int = 5):
    qs = Recharge.objects.filter(user_id=user_id).order_by('-created_at')
    total = qs.count()
    return list(qs[(page - 1) * per_page: page * per_page]), total


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
def create_recharge(user_id: int, amount: Decimal, currency: str, receive_address: str):
    pay_amount = _generate_unique_pay_amount(amount, currency)
    return Recharge.objects.create(user_id=user_id, amount=amount, pay_amount=pay_amount, currency=currency, status='pending')


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
        return order, None
