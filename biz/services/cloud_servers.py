from decimal import Decimal

from asgiref.sync import sync_to_async
from django.utils import timezone

from biz.models import CloudServerOrder
from .commerce import _generate_unique_pay_amount


@sync_to_async
def create_cloud_server_renewal(order_id: int, user_id: int, days: int = 31):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    order.status = 'renew_pending'
    order.lifecycle_days = days
    order.pay_amount = _generate_unique_pay_amount(Decimal(order.total_amount), order.currency)
    order.expired_at = timezone.now() + timezone.timedelta(minutes=30)
    order.save(update_fields=['status', 'lifecycle_days', 'pay_amount', 'expired_at', 'updated_at'])
    return order


@sync_to_async
def apply_cloud_server_renewal(order_id: int, days: int = 31):
    order = CloudServerOrder.objects.get(id=order_id)
    base = order.service_expires_at or timezone.now()
    if base < timezone.now():
        base = timezone.now()
    order.service_expires_at = base + timezone.timedelta(days=days)
    order.last_renewed_at = timezone.now()
    order.status = 'completed'
    order.save(update_fields=['service_expires_at', 'last_renewed_at', 'status', 'updated_at'])
    return order


@sync_to_async
def rebind_cloud_server_user(order_id: int, new_user_id: int):
    order = CloudServerOrder.objects.get(id=order_id)
    order.user_id = new_user_id
    order.last_user_id = order.user.tg_user_id if hasattr(order.user, 'tg_user_id') else order.last_user_id
    order.save(update_fields=['user', 'last_user_id', 'updated_at'])
    return order


@sync_to_async
def mark_cloud_server_ip_change_requested(order_id: int):
    order = CloudServerOrder.objects.get(id=order_id)
    order.provision_note = '\n'.join(filter(None, [order.provision_note, '已提交更换 IP 请求，待后台处理。']))
    order.save(update_fields=['provision_note', 'updated_at'])
    return order
