"""过渡层：统一暴露 cloud 域服务，后续逐步从 biz/services 迁入这里。"""

from decimal import Decimal

from cloud.models import CloudIpLog
from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from bot.models import TelegramUser
from cloud.models import CloudAsset, CloudIpLog, CloudServerOrder, CloudServerPlan, Server
from orders.ledger import record_balance_ledger
from orders.services import _generate_unique_pay_amount
from biz.services.custom import (
    build_cloud_server_name,
    buy_cloud_server_with_balance,
    create_cloud_server_order,
    ensure_cloud_server_pricing,
    ensure_unique_cloud_server_name,
    get_cloud_plan,
    list_custom_regions,
    list_region_plans,
    pay_cloud_server_order_with_balance,
    refresh_custom_plan_cache,
    set_cloud_server_port,
)

def _can_order_be_renewed(order: CloudServerOrder) -> bool:
    if order.status in {'deleted', 'deleting'}:
        return False
    if not order.public_ip:
        return False
    return True


def _renewal_price(order: CloudServerOrder, user: TelegramUser | None = None) -> Decimal:
    discount_rate = Decimal(str(getattr(user, 'cloud_discount_rate', 100) or 100)) if user else Decimal('100')
    total_amount = Decimal(str(order.total_amount or 0))
    if discount_rate <= 0:
        discount_rate = Decimal('100')
    return (total_amount * discount_rate / Decimal('100')).quantize(Decimal('0.01'))


def record_cloud_ip_log(*, event_type, order=None, asset=None, server=None, public_ip=None, previous_public_ip=None, note=''):
    asset_obj = asset
    server_obj = server
    order_obj = order or getattr(asset_obj, 'order', None) or getattr(server_obj, 'order', None)
    user_obj = (
        getattr(order_obj, 'user', None)
        or getattr(asset_obj, 'user', None)
        or getattr(server_obj, 'user', None)
    )
    provider = (
        getattr(order_obj, 'provider', None)
        or getattr(asset_obj, 'provider', None)
        or getattr(server_obj, 'provider', None)
    )
    region_code = (
        getattr(order_obj, 'region_code', None)
        or getattr(asset_obj, 'region_code', None)
        or getattr(server_obj, 'region_code', None)
    )
    region_name = (
        getattr(order_obj, 'region_name', None)
        or getattr(asset_obj, 'region_name', None)
        or getattr(server_obj, 'region_name', None)
    )
    asset_name = (
        getattr(asset_obj, 'asset_name', None)
        or getattr(server_obj, 'server_name', None)
        or getattr(order_obj, 'server_name', None)
    )
    instance_id = (
        getattr(asset_obj, 'instance_id', None)
        or getattr(server_obj, 'instance_id', None)
        or getattr(order_obj, 'instance_id', None)
    )
    provider_resource_id = (
        getattr(asset_obj, 'provider_resource_id', None)
        or getattr(server_obj, 'provider_resource_id', None)
        or getattr(order_obj, 'provider_resource_id', None)
    )
    current_ip = public_ip
    if current_ip is None:
        current_ip = (
            getattr(asset_obj, 'public_ip', None)
            or getattr(server_obj, 'public_ip', None)
            or getattr(order_obj, 'public_ip', None)
        )
    previous_ip = previous_public_ip
    if previous_ip is None:
        previous_ip = (
            getattr(asset_obj, 'previous_public_ip', None)
            or getattr(server_obj, 'previous_public_ip', None)
            or getattr(order_obj, 'previous_public_ip', None)
        )
    return CloudIpLog.objects.create(
        order=order_obj,
        asset=asset_obj,
        server=server_obj,
        user=user_obj,
        provider=provider,
        region_code=region_code,
        region_name=region_name,
        order_no=getattr(order_obj, 'order_no', None),
        asset_name=asset_name,
        instance_id=instance_id,
        provider_resource_id=provider_resource_id,
        public_ip=current_ip,
        previous_public_ip=previous_ip,
        event_type=event_type,
        note=note or '',
    )


@sync_to_async
def create_cloud_server_renewal(order_id: int, user_id: int, days: int = 31):
    order = CloudServerOrder.objects.select_related('user').filter(id=order_id).first()
    if not order:
        return None
    if not _can_order_be_renewed(order):
        return False
    order.status = 'renew_pending'
    order.lifecycle_days = days
    renewal_user = TelegramUser.objects.filter(id=user_id).first()
    order.pay_amount = _generate_unique_pay_amount(_renewal_price(order, renewal_user), order.currency)
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
    order.delay_quota = max(int(order.delay_quota or 0), 0) + 1
    order.status = 'completed'
    order.save(update_fields=['service_expires_at', 'last_renewed_at', 'delay_quota', 'status', 'updated_at'])
    CloudAsset.objects.filter(order=order).update(actual_expires_at=order.service_expires_at, updated_at=timezone.now())
    Server.objects.filter(order=order).update(expires_at=order.service_expires_at, updated_at=timezone.now())
    return order


@sync_to_async
def pay_cloud_server_renewal_with_balance(order_id: int, user_id: int, currency: str = 'USDT', days: int = 31):
    with transaction.atomic():
        order = CloudServerOrder.objects.select_related('user').select_for_update().filter(id=order_id, user_id=user_id).first()
        if not order:
            return None, '订单不存在'
        if order.status not in {'renew_pending', 'pending'}:
            return None, '当前订单状态不可钱包支付'
        balance_field = 'balance_trx' if currency == 'TRX' else 'balance'
        user = TelegramUser.objects.select_for_update().get(id=user_id)
        total = Decimal(str(order.pay_amount or order.total_amount or 0))
        current_balance = Decimal(str(getattr(user, balance_field, 0) or 0))
        if current_balance < total:
            return None, f'{currency} 余额不足'
        old_balance = current_balance
        setattr(user, balance_field, current_balance - total)
        user.save(update_fields=[balance_field, 'updated_at'])
        order.currency = currency
        order.pay_method = 'balance'
        order.pay_amount = total
        order.paid_at = timezone.now()
        order.expired_at = None
        order.save(update_fields=['currency', 'pay_method', 'pay_amount', 'paid_at', 'expired_at', 'updated_at'])
        order = apply_cloud_server_renewal.__wrapped__(order.id, days)
        record_balance_ledger(
            user,
            ledger_type='cloud_order_balance_pay',
            currency=currency,
            old_balance=old_balance,
            new_balance=getattr(user, balance_field),
            related_type='cloud_order',
            related_id=order.id,
            description=f'云服务器续费订单 #{order.order_no} 钱包支付',
        )
        return order, None


@sync_to_async
def rebind_cloud_server_user(order_id: int, new_user_id: int):
    order = CloudServerOrder.objects.select_related('user').get(id=order_id)
    order.user_id = new_user_id
    order.last_user_id = order.user.tg_user_id if hasattr(order.user, 'tg_user_id') else order.last_user_id
    order.save(update_fields=['user', 'last_user_id', 'updated_at'])
    return order


@sync_to_async
def mark_cloud_server_ip_change_requested(order_id: int, user_id: int, region_code: str | None = None, port: int | None = None):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    if order.status not in {'completed', 'expiring', 'suspended'}:
        return False
    target_region_code = region_code or order.region_code
    provider = 'aliyun_simple' if target_region_code == 'cn-hongkong' else 'aws_lightsail'
    fallback_plan = CloudServerPlan.objects.filter(
        provider=provider,
        region_code=target_region_code,
        is_active=True,
    ).order_by('-sort_order', 'id').first()
    if not fallback_plan:
        fallback_plan = CloudServerPlan.objects.filter(
            provider=provider,
            region_code=target_region_code,
            plan_name=order.plan_name,
            is_active=True,
        ).order_by('-sort_order', 'id').first()
    if not fallback_plan:
        return False
    target_port = port or order.mtproxy_port or 9528
    migration_due_at = timezone.now() + timezone.timedelta(days=5)
    new_order = CloudServerOrder.objects.create(
        user_id=order.user_id,
        order_no=f'{order.order_no}-IP',
        plan_id=fallback_plan.id,
        provider=fallback_plan.provider,
        region_code=fallback_plan.region_code,
        region_name=fallback_plan.region_name,
        plan_name=fallback_plan.plan_name,
        quantity=1,
        currency=order.currency,
        total_amount=order.total_amount,
        pay_amount=order.pay_amount,
        pay_method=order.pay_method,
        status='paid',
        lifecycle_days=order.lifecycle_days,
        mtproxy_port=target_port,
        service_started_at=timezone.now(),
        service_expires_at=migration_due_at,
        migration_due_at=migration_due_at,
        replacement_for=order,
        renew_extension_days=order.renew_extension_days,
        last_user_id=order.last_user_id,
        server_name=order.server_name,
        image_name=order.image_name,
        provision_note='\n'.join(filter(None, [order.provision_note, f'由订单 {order.order_no} 发起更换 IP，新服务器地区: {fallback_plan.region_name}，端口: {target_port}，需在 5 天内完成迁移。'])),
    )
    order.provision_note = '\n'.join(filter(None, [order.provision_note, f'已发起更换 IP，新实例订单: {new_order.order_no}，旧服务器将于 5 天后到期，请尽快完成迁移。']))
    order.service_expires_at = migration_due_at
    order.migration_due_at = migration_due_at
    order.save(update_fields=['provision_note', 'service_expires_at', 'migration_due_at', 'updated_at'])
    CloudAsset.objects.filter(order=order).update(actual_expires_at=order.service_expires_at, updated_at=timezone.now())
    Server.objects.filter(order=order).update(expires_at=order.service_expires_at, updated_at=timezone.now())
    return new_order


@sync_to_async
def mark_cloud_server_reinit_requested(order_id: int, user_id: int):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    if not order.public_ip or not order.login_password:
        return False
    order.provision_note = '\n'.join(filter(None, [order.provision_note, '用户发起重试初始化请求。']))
    order.save(update_fields=['provision_note', 'updated_at'])
    return order


@sync_to_async
def mute_cloud_reminders(user_id: int, days: int = 3):
    user = TelegramUser.objects.filter(id=user_id).first()
    if not user:
        return None
    user.cloud_reminder_muted_until = timezone.now() + timezone.timedelta(days=days)
    user.save(update_fields=['cloud_reminder_muted_until', 'updated_at'])
    return user


@sync_to_async
def set_cloud_server_auto_renew(order_id: int, user_id: int, enabled: bool):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    order.auto_renew_enabled = enabled
    order.save(update_fields=['auto_renew_enabled', 'updated_at'])
    return order


@sync_to_async
def get_cloud_server_auto_renew(order_id: int, user_id: int):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    return bool(order.auto_renew_enabled)


@sync_to_async
def delay_cloud_server_expiry(order_id: int, user_id: int, days: int = 5):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    expires_at = order.service_expires_at
    if not expires_at:
        return False, '当前订单未设置到期时间'
    now = timezone.now()
    if expires_at < now:
        return False, '服务器已到期，不能延期'
    if expires_at > now + timezone.timedelta(days=5):
        return False, '仅允许在到期前5天内使用延期'
    delay_quota = max(int(order.delay_quota or 0), 0)
    if delay_quota <= 0:
        return False, '暂无可用延期次数'
    order.renew_extension_days = max(int(order.renew_extension_days or 0), days)
    order.delay_quota = delay_quota - 1
    order.save(update_fields=['renew_extension_days', 'delay_quota', 'updated_at'])
    return order, None


__all__ = [
    'apply_cloud_server_renewal',
    'build_cloud_server_name',
    'buy_cloud_server_with_balance',
    'create_cloud_server_order',
    'create_cloud_server_renewal',
    'delay_cloud_server_expiry',
    'ensure_cloud_server_pricing',
    'ensure_unique_cloud_server_name',
    'get_cloud_plan',
    'get_cloud_server_auto_renew',
    'list_custom_regions',
    'list_region_plans',
    'mark_cloud_server_ip_change_requested',
    'mark_cloud_server_reinit_requested',
    'mute_cloud_reminders',
    'pay_cloud_server_order_with_balance',
    'pay_cloud_server_renewal_with_balance',
    'rebind_cloud_server_user',
    'refresh_custom_plan_cache',
    'record_cloud_ip_log',
    'set_cloud_server_auto_renew',
    'set_cloud_server_port',
]
