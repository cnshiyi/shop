from decimal import Decimal

from asgiref.sync import sync_to_async

from biz.models import Recharge
from .commerce import _generate_unique_pay_amount


@sync_to_async
def list_recharges(user_id: int, page: int = 1, per_page: int = 5):
    qs = Recharge.objects.filter(user_id=user_id).order_by('-created_at')
    total = qs.count()
    return list(qs[(page - 1) * per_page: page * per_page]), total


@sync_to_async
def create_recharge(user_id: int, amount: Decimal, currency: str, receive_address: str):
    pay_amount = _generate_unique_pay_amount(amount, currency)
    return Recharge.objects.create(user_id=user_id, amount=amount, pay_amount=pay_amount, currency=currency, status='pending')
