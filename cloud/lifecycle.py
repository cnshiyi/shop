import logging
import os
import re
from html import escape

from aiogram.types import InlineKeyboardMarkup
from asgiref.sync import sync_to_async
from django.core.management import call_command
from django.utils import timezone

from core.models import CloudAccountConfig
from core.runtime_config import get_runtime_config
from core.texts import site_text

from orders.models import BalanceLedger
from bot.models import TelegramUser
from cloud.models import CloudAsset, CloudServerOrder, Server
from cloud.services import create_cloud_server_renewal, pay_cloud_server_renewal_with_balance, record_cloud_ip_log
from bot.keyboards import cloud_auto_renew_notice_actions, cloud_expiry_actions, cloud_lifecycle_notice_actions

logger = logging.getLogger(__name__)


def _cloud_text_format(key: str, default: str, **kwargs) -> str:
    template = site_text(key, default)
    try:
        return template.format(**kwargs)
    except Exception:
        return default.format(**kwargs)


def _aws_client(region: str, account=None):
    import boto3
    from core.cloud_accounts import get_active_cloud_account
    account = account or get_active_cloud_account('aws', region)
    access_key = ''
    secret_key = ''
    if account:
        ak = (account.access_key_plain or '').strip()
        sk = (account.secret_key_plain or '').strip()
        if ak and sk and len(ak) >= 16 and len(sk) >= 36:
            access_key, secret_key = ak, sk
    if not access_key or not secret_key:
        access_key = os.getenv('AWS_ACCESS_KEY_ID', '')
        secret_key = os.getenv('AWS_SECRET_ACCESS_KEY', '')
    return boto3.client(
        'lightsail',
        region_name=region,
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _config_bool(key: str, default: str = '0') -> bool:
    return str(get_runtime_config(key, default)).strip().lower() in {'1', 'true', 'yes', 'on'}


def _config_int(key: str, default: int) -> int:
    try:
        return int(str(get_runtime_config(key, str(default))).strip() or default)
    except (TypeError, ValueError):
        return default


@sync_to_async
def _get_due_orders():
    now = timezone.now()
    renew_notice_days = max(1, _config_int('cloud_renew_notice_days', 5))
    renew_notice_debug_repeat = _config_bool('cloud_renew_notice_debug_repeat', '0')
    renew_notice_at = now + timezone.timedelta(days=renew_notice_days)
    auto_renew_notice_at = now + timezone.timedelta(days=3, hours=2)
    auto_renew_at = now + timezone.timedelta(days=3)
    delete_notice_at = now + timezone.timedelta(days=1)
    recycle_notice_at = now + timezone.timedelta(days=1)
    renew_notice_qs = CloudServerOrder.objects.filter(status__in=['completed', 'expiring', 'renew_pending'], cloud_reminder_enabled=True, service_expires_at__lte=renew_notice_at, service_expires_at__gt=now)
    if not renew_notice_debug_repeat:
        renew_notice_qs = renew_notice_qs.filter(renew_notice_sent_at__isnull=True)
    return {
        'renew_notice': list(renew_notice_qs),
        'auto_renew_notice': list(CloudServerOrder.objects.filter(status__in=['completed', 'expiring', 'renew_pending'], auto_renew_enabled=True, service_expires_at__lte=auto_renew_notice_at, service_expires_at__gt=auto_renew_at)),
        'auto_renew': list(CloudServerOrder.objects.filter(status__in=['completed', 'expiring', 'renew_pending'], auto_renew_enabled=True, service_expires_at__lte=auto_renew_at, service_expires_at__gt=now)),
        'delete_notice': list(CloudServerOrder.objects.filter(status__in=['suspended', 'deleting'], delete_reminder_enabled=True, delete_at__lte=delete_notice_at, delete_at__gt=now, delete_notice_sent_at__isnull=True)),
        'recycle_notice': list(CloudServerOrder.objects.filter(status='deleted', ip_recycle_reminder_enabled=True, ip_recycle_at__lte=recycle_notice_at, ip_recycle_at__gt=now, recycle_notice_sent_at__isnull=True)),
        'expire': list(CloudServerOrder.objects.filter(status='completed', service_expires_at__lte=now).exclude(provider='aliyun_simple')),
        'suspend': list(CloudServerOrder.objects.filter(status__in=['completed', 'expiring', 'renew_pending'], suspend_at__lte=now).exclude(provider='aliyun_simple')),
        'delete': list(CloudServerOrder.objects.filter(status__in=['suspended', 'deleting'], delete_at__lte=now).exclude(provider='aliyun_simple')),
        'recycle': list(CloudServerOrder.objects.filter(status='deleted', ip_recycle_at__lte=now).exclude(provider='aliyun_simple')),
        'config': {'renew_notice_days': renew_notice_days, 'renew_notice_debug_repeat': renew_notice_debug_repeat},
    }


@sync_to_async
def _mark_expiring(order_id: int):
    order = CloudServerOrder.objects.get(id=order_id)
    if order.status == 'completed':
        order.status = 'expiring'
        order.save(update_fields=['status', 'updated_at'])
        asset = CloudAsset.objects.filter(order=order).order_by('-updated_at', '-id').first()
        server = Server.objects.filter(order=order).order_by('-updated_at', '-id').first()
        CloudAsset.objects.filter(order=order).update(updated_at=timezone.now())
        Server.objects.filter(order=order).update(updated_at=timezone.now())
        record_cloud_ip_log(event_type='expired', order=order, asset=asset, server=server, note='服务器到期，进入到期处理阶段')
    return order


@sync_to_async
def _mark_suspended(order_id: int, note: str):
    now = timezone.now()
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'suspended'
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    order.save(update_fields=['status', 'provision_note', 'updated_at'])
    asset = CloudAsset.objects.filter(order=order).order_by('-updated_at', '-id').first()
    server = Server.objects.filter(order=order).order_by('-updated_at', '-id').first()
    CloudAsset.objects.filter(order=order).update(is_active=False, note=order.provision_note, updated_at=now)
    Server.objects.filter(order=order).update(is_active=False, note=order.provision_note, updated_at=now)
    record_cloud_ip_log(event_type='suspended', order=order, asset=asset, server=server, note=note or '服务器进入延停状态')
    return order


@sync_to_async
def _mark_deleted(order_id: int, note: str):
    now = timezone.now()
    order = CloudServerOrder.objects.get(id=order_id)
    asset = CloudAsset.objects.filter(order=order).order_by('-updated_at', '-id').first()
    server = Server.objects.filter(order=order).order_by('-updated_at', '-id').first()
    previous_public_ip = order.public_ip or order.previous_public_ip
    order.status = 'deleted'
    order.public_ip = previous_public_ip
    order.previous_public_ip = previous_public_ip
    retention_note = _retained_static_ip_note(order, previous_public_ip, note)
    order.provision_note = '\n'.join(filter(None, [order.provision_note, retention_note]))
    order.instance_id = ''
    order.provider_resource_id = ''
    order.save(update_fields=['status', 'public_ip', 'previous_public_ip', 'provision_note', 'instance_id', 'provider_resource_id', 'updated_at'])
    CloudAsset.objects.filter(order=order).update(
        public_ip=previous_public_ip,
        previous_public_ip=previous_public_ip,
        instance_id=None,
        provider_resource_id=None,
        provider_status='固定IP保留中-实例已删除',
        is_active=False,
        note=order.provision_note,
        updated_at=now,
    )
    Server.objects.filter(order=order).update(
        public_ip=previous_public_ip,
        previous_public_ip=previous_public_ip,
        instance_id=None,
        provider_resource_id=None,
        provider_status='固定IP保留中-实例已删除',
        is_active=False,
        note=order.provision_note,
        updated_at=now,
    )
    record_cloud_ip_log(event_type='deleted', order=order, asset=asset, server=server, previous_public_ip=previous_public_ip, public_ip=previous_public_ip, note=retention_note)
    return order


@sync_to_async
def _mark_recycled(order_id: int, note: str):
    now = timezone.now()
    order = CloudServerOrder.objects.get(id=order_id)
    asset = CloudAsset.objects.filter(order=order).order_by('-updated_at', '-id').first()
    server = Server.objects.filter(order=order).order_by('-updated_at', '-id').first()
    previous_public_ip = order.public_ip or order.previous_public_ip
    order.previous_public_ip = previous_public_ip
    order.public_ip = ''
    order.static_ip_name = ''
    order.mtproxy_host = ''
    order.ip_recycle_at = None
    order.recycle_notice_sent_at = now
    order.ip_recycle_reminder_enabled = False
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    order.save(update_fields=['previous_public_ip', 'public_ip', 'static_ip_name', 'mtproxy_host', 'ip_recycle_at', 'recycle_notice_sent_at', 'ip_recycle_reminder_enabled', 'provision_note', 'updated_at'])
    CloudAsset.objects.filter(order=order).update(
        previous_public_ip=previous_public_ip,
        public_ip=None,
        mtproxy_host=None,
        note=order.provision_note,
        updated_at=now,
    )
    Server.objects.filter(order=order).update(
        previous_public_ip=previous_public_ip,
        public_ip=None,
        note=order.provision_note,
        updated_at=now,
    )
    record_cloud_ip_log(event_type='recycled', order=order, asset=asset, server=server, previous_public_ip=previous_public_ip, public_ip=None, note=note or '公网IP已回收')
    return order




@sync_to_async
def _mark_notice_sent(order_id: int, field_name: str):
    CloudServerOrder.objects.filter(id=order_id).update(**{field_name: timezone.now(), 'updated_at': timezone.now()})


@sync_to_async
def _user_can_receive_cloud_notice(user_id: int) -> bool:
    user = TelegramUser.objects.filter(id=user_id).first()
    if not user:
        return False
    muted_until = getattr(user, 'cloud_reminder_muted_until', None)
    return not muted_until or muted_until <= timezone.now()


def _retained_static_ip_note(order: CloudServerOrder, ip: str, action_note: str = '') -> str:
    return (
        f'实例已删除，固定 IP 保留中；IP={ip or "缺失"}；端口={order.mtproxy_port or "-"}；'
        f'secret={order.mtproxy_secret or "-"}；服务到期={_format_notice_dt(order.service_expires_at)}；'
        f'宽限删机={_format_notice_dt(order.delete_at)}；未附加 IP 计划回收={_format_notice_dt(order.ip_recycle_at)}；'
        f'用户续费/重装时必须用旧 IP、旧端口、旧 secret 与用户提供链接逐项对照。{("；" + action_note) if action_note else ""}'
    )


def _format_notice_dt(value) -> str:
    if not value:
        return '未设置'
    try:
        return timezone.localtime(value).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return str(value)


def _fmt_money3(value) -> str:
    if value is None:
        return '-'
    try:
        return f'{value:.3f}'
    except Exception:
        return str(value)


def _public_cloud_error_text(error) -> str:
    raw = str(error or '')
    if not raw:
        return '任务暂未完成，请联系人工客服处理。'
    sensitive_markers = ('account', '账号', 'instance', '实例', 'server_name', 'instance_id', 'arn:', 'aws+', 'aliyun+', 'CloudAccount')
    if any(marker.lower() in raw.lower() for marker in sensitive_markers):
        return '云服务器任务执行失败，内部诊断信息已记录；请联系人工客服处理。'
    text = re.sub(r'aws\+[^\s，；,。)）]+', '云账号', raw)
    text = re.sub(r'aliyun\+[^\s，；,。)）]+', '云账号', text)
    text = re.sub(r'\b\d{12,}\b', '***', text)
    return text[:180]


def _ensure_notice_ip(text: str, ip: str) -> str:
    ip = str(ip or '').strip()
    if ip and ip != '未分配' and ip not in str(text or ''):
        return f'IP: {ip}\n' + str(text or '')
    return str(text or '')


def _notice_plan_text(order, notice: dict | None = None, *, include_expiry: bool = True) -> str:
    notice = notice or {}
    expires_at = notice.get('expires_at') or getattr(order, 'service_expires_at', None)
    suspend_at = notice.get('suspend_at') or getattr(order, 'suspend_at', None)
    delete_at = notice.get('delete_at') or getattr(order, 'delete_at', None)
    ip_recycle_at = notice.get('ip_recycle_at') or getattr(order, 'ip_recycle_at', None)
    lines = []
    if include_expiry:
        lines.append(f'到期时间: {_format_notice_dt(expires_at)}')
    lines.append(f'关机计划: {_format_notice_dt(suspend_at)}')
    lines.append(f'删除计划: {_format_notice_dt(delete_at)}')
    if ip_recycle_at:
        lines.append(f'固定IP删除计划: {_format_notice_dt(ip_recycle_at)}')
    if suspend_at:
        lines.append(f'请务必在 {_format_notice_dt(suspend_at)} 之前完成续费，避免关机。')
    if delete_at:
        lines.append(f'如已关机，请务必在 {_format_notice_dt(delete_at)} 之前完成续费，避免实例删除。')
    if ip_recycle_at:
        lines.append(f'实例删除后仍需保留 IP 的，请务必在 {_format_notice_dt(ip_recycle_at)} 之前续费恢复。')
    return '\n'.join(lines)


def _log_cloud_notice(event: str, order, notice: dict | None, text: str, keyboard: str):
    notice = notice or {}
    logger.info(
        'CLOUD_NOTICE_SEND event=%s user_id=%s order_id=%s order_no=%s ip=%s status=%s keyboard=%s has_ip=%s has_mute_button=%s has_support_button=%s text_preview=%s',
        event,
        getattr(order, 'user_id', None),
        getattr(order, 'id', None),
        getattr(order, 'order_no', None),
        notice.get('ip') or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '-',
        getattr(order, 'status', None),
        keyboard,
        bool(str(notice.get('ip') or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '').strip()),
        True,
        True,
        str(text or '').replace('\n', ' ')[:240],
    )


def _proxy_links_notice_text(order) -> str:
    links = []
    seen = set()
    main_link = str(getattr(order, 'mtproxy_link', '') or '').strip()
    if main_link:
        links.append(('主代理', main_link))
        seen.add(main_link)
    for item in getattr(order, 'proxy_links', None) or []:
        if not isinstance(item, dict):
            continue
        link = str(item.get('url') or '').strip()
        if not link or link in seen:
            continue
        links.append((str(item.get('name') or f"端口 {item.get('port') or '-'}"), link))
        seen.add(link)
    if not links:
        return '\n\n代理链接: 尚未生成'
    lines = ['\n\n代理链接:']
    for label, link in links:
        lines.append(f'{escape(label)}: {escape(link)}')
    return '\n'.join(lines)


def _config_time(key: str, default: str = '15:00') -> tuple[int, int]:
    try:
        raw = str(get_runtime_config(key, default) or default).strip()
        hour_text, minute_text = raw.split(':', 1)
        return min(max(int(hour_text), 0), 23), min(max(int(minute_text), 0), 59)
    except Exception:
        hour_text, minute_text = default.split(':', 1)
        return int(hour_text), int(minute_text)


def _is_cloud_delete_safe_time(now=None) -> bool:
    local_now = timezone.localtime(now or timezone.now())
    hour, minute = _config_time('cloud_delete_time', '15:00')
    scheduled = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return scheduled <= local_now < scheduled + timezone.timedelta(hours=1)


def _action_note(result) -> str:
    if isinstance(result, tuple):
        return str(result[1] if len(result) > 1 else result[0])
    return str(result or '')


def _action_ok(result) -> bool:
    return not isinstance(result, tuple) or bool(result[0])


@sync_to_async
def _record_lifecycle_action_failed(order_id: int, event_type: str, note: str):
    order = CloudServerOrder.objects.get(id=order_id)
    asset = CloudAsset.objects.filter(order=order).order_by('-updated_at', '-id').first()
    server = Server.objects.filter(order=order).order_by('-updated_at', '-id').first()
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    order.save(update_fields=['provision_note', 'updated_at'])
    record_cloud_ip_log(event_type=event_type, order=order, asset=asset, server=server, public_ip=order.public_ip, previous_public_ip=order.previous_public_ip, note=note)
    return order


@sync_to_async
def _cloud_expiry_notice_payload(order_id: int) -> dict:
    order = CloudServerOrder.objects.filter(id=order_id).first()
    if not order:
        return {'ip': '未分配', 'expires_at': None, 'suspend_at': None}
    asset = CloudAsset.objects.filter(order=order).order_by('-updated_at', '-id').first()
    server = Server.objects.filter(order=order).order_by('-updated_at', '-id').first()
    ip = (
        getattr(asset, 'public_ip', None)
        or getattr(server, 'public_ip', None)
        or order.public_ip
        or getattr(asset, 'previous_public_ip', None)
        or getattr(server, 'previous_public_ip', None)
        or order.previous_public_ip
        or '未分配'
    )
    expires_at = getattr(asset, 'actual_expires_at', None) or getattr(server, 'expires_at', None) or order.service_expires_at
    computed_suspend_at = order.suspend_at or (expires_at + timezone.timedelta(days=3 + max(int(order.renew_extension_days or 0), 0)) if expires_at else None)
    suspend_at = order.suspend_at
    if computed_suspend_at and (not suspend_at or suspend_at < expires_at):
        suspend_at = computed_suspend_at
    return {'ip': ip, 'expires_at': expires_at, 'suspend_at': suspend_at, 'delete_at': order.delete_at, 'ip_recycle_at': order.ip_recycle_at, 'auto_renew_enabled': bool(order.auto_renew_enabled)}


@sync_to_async
def _run_auto_renew(order_id: int) -> tuple[CloudServerOrder | None, str | None, dict]:
    order = CloudServerOrder.objects.filter(id=order_id).first()
    if not order:
        return None, '订单不存在', {}
    if not order.auto_renew_enabled:
        return None, '自动续费已关闭', {}
    if order.status != 'renew_pending':
        renewal = create_cloud_server_renewal.__wrapped__(order.id, order.user_id, 31)
        if not renewal:
            return None, '订单当前不可续费', {}
    renewed, err = pay_cloud_server_renewal_with_balance.__wrapped__(order.id, order.user_id, 'USDT', 31)
    ledger = None
    if renewed and not err:
        ledger = BalanceLedger.objects.filter(
            user_id=order.user_id,
            related_type='cloud_order',
            related_id=order.id,
            type='cloud_order_balance_pay',
            currency='USDT',
        ).order_by('-created_at', '-id').first()
    balance_change = {
        'currency': getattr(ledger, 'currency', 'USDT') if ledger else 'USDT',
        'amount': getattr(ledger, 'amount', None) if ledger else None,
        'before': getattr(ledger, 'before_balance', None) if ledger else None,
        'after': getattr(ledger, 'after_balance', None) if ledger else None,
    }
    return renewed, err, balance_change


@sync_to_async
def _get_migration_due_orders():
    now = timezone.now()
    return list(
        CloudServerOrder.objects.filter(
            replacement_orders__isnull=False,
            migration_due_at__lte=now,
        ).exclude(status__in=['deleted'])
    )


@sync_to_async
def _get_orphan_asset_delete_due():
    now = timezone.now()
    return list(
        CloudAsset.objects.select_related('cloud_account').filter(
            kind=CloudAsset.KIND_SERVER,
            order__isnull=True,
            actual_expires_at__lte=now,
        ).exclude(status__in=[CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED])
    )


def _stop_instance_sync(order: CloudServerOrder) -> tuple[bool, str]:
    if order.provider != 'aws_lightsail' or not order.server_name:
        return False, '非 AWS 资源，暂未执行真实关机。'
    try:
        client = _aws_client(order.region_code, getattr(order, 'cloud_account', None))
        client.stop_instance(instanceName=order.server_name, force=True)
        return True, 'AWS 实例已执行关机。'
    except Exception as exc:
        return False, f'AWS 实例关机失败: {exc}'


async def _stop_instance(order: CloudServerOrder) -> tuple[bool, str]:
    return await sync_to_async(_stop_instance_sync, thread_sensitive=False)(order)


def _delete_instance_sync(order: CloudServerOrder) -> tuple[bool, str]:
    if order.provider != 'aws_lightsail' or not order.server_name:
        return False, '非 AWS 资源，暂未执行真实删机。'
    try:
        client = _aws_client(order.region_code, getattr(order, 'cloud_account', None))
        client.delete_instance(instanceName=order.server_name)
        return True, 'AWS 实例已执行删除，固定 IP 继续保留。'
    except Exception as exc:
        return False, f'AWS 实例删除失败: {exc}'


async def _delete_instance(order: CloudServerOrder) -> tuple[bool, str]:
    return await sync_to_async(_delete_instance_sync, thread_sensitive=False)(order)


def _delete_replaced_server_sync(order: CloudServerOrder) -> tuple[bool, str]:
    if order.provider != 'aws_lightsail' or not order.server_name:
        return False, '迁移期结束，但旧服务器不满足真实删机条件。'
    try:
        client = _aws_client(order.region_code, getattr(order, 'cloud_account', None))
        client.delete_instance(instanceName=order.server_name)
        return True, '迁移期结束，旧 AWS 实例已删除。'
    except Exception as exc:
        return False, f'迁移期结束，旧实例删除失败: {exc}'


async def _delete_replaced_server(order: CloudServerOrder) -> tuple[bool, str]:
    return await sync_to_async(_delete_replaced_server_sync, thread_sensitive=False)(order)


def _delete_orphan_asset_instance_sync(asset: CloudAsset) -> tuple[bool, str]:
    if asset.provider != 'aws_lightsail' or not asset.asset_name:
        return False, '无订单资产到期，非 AWS 或缺少实例名，已执行本地删除标记。'
    try:
        client = _aws_client(asset.region_code, getattr(asset, 'cloud_account', None))
        client.delete_instance(instanceName=asset.asset_name)
        return True, '无订单 AWS 资产到期，已执行真实删机。'
    except Exception as exc:
        return False, f'无订单 AWS 资产到期，真实删机失败: {exc}'


async def _delete_orphan_asset_instance(asset: CloudAsset) -> tuple[bool, str]:
    return await sync_to_async(_delete_orphan_asset_instance_sync, thread_sensitive=False)(asset)


@sync_to_async
def _mark_orphan_asset_deleted(asset_id: int, note: str):
    now = timezone.now()
    asset = CloudAsset.objects.get(id=asset_id)
    previous_public_ip = asset.public_ip or asset.previous_public_ip
    asset.previous_public_ip = previous_public_ip
    asset.status = CloudAsset.STATUS_DELETED
    asset.provider_status = 'expired-deleted'
    asset.is_active = False
    asset.note = '\n'.join(filter(None, [asset.note, note]))
    asset.save(update_fields=['previous_public_ip', 'status', 'provider_status', 'is_active', 'note', 'updated_at'])
    Server.objects.filter(order__isnull=True).filter(
        provider=asset.provider,
        region_code=asset.region_code,
    ).filter(
        instance_id__in=[value for value in [asset.instance_id, asset.provider_resource_id] if value]
    ).update(
        previous_public_ip=previous_public_ip,
        status=CloudAsset.STATUS_DELETED,
        is_active=False,
        note=asset.note,
        updated_at=now,
    )
    record_cloud_ip_log(event_type='deleted', asset=asset, previous_public_ip=previous_public_ip, public_ip=None, note=note or '无订单资产到期删除')
    return asset


@sync_to_async
def _mark_replaced_order_deleted(order_id: int, note: str):
    now = timezone.now()
    order = CloudServerOrder.objects.get(id=order_id)
    asset = CloudAsset.objects.filter(order=order).order_by('-updated_at', '-id').first()
    server = Server.objects.filter(order=order).order_by('-updated_at', '-id').first()
    previous_public_ip = order.public_ip or order.previous_public_ip
    order.status = 'deleted'
    order.previous_public_ip = previous_public_ip
    order.instance_id = ''
    order.provider_resource_id = ''
    order.public_ip = ''
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    order.save(update_fields=['status', 'previous_public_ip', 'instance_id', 'provider_resource_id', 'public_ip', 'provision_note', 'updated_at'])
    CloudAsset.objects.filter(order=order).update(is_active=False, public_ip=None, previous_public_ip=previous_public_ip, note=order.provision_note, updated_at=now)
    Server.objects.filter(order=order).update(is_active=False, public_ip=None, previous_public_ip=previous_public_ip, note=order.provision_note, updated_at=now)
    record_cloud_ip_log(event_type='deleted', order=order, asset=asset, server=server, previous_public_ip=previous_public_ip, public_ip=None, note=note or '迁移结束，旧实例删除')
    return order


@sync_to_async
def check_cloud_accounts_status(queryset=None):
    items = list(queryset if queryset is not None else CloudAccountConfig.objects.filter(is_active=True).order_by('provider', 'name', 'id'))
    results = []
    for item in items:
        if not item.is_active:
            results.append({'id': item.id, 'provider': item.provider, 'name': item.name, 'status': item.status, 'note': '账号已停用，跳过巡检'})
            continue
        status = CloudAccountConfig.STATUS_OK
        note = '验证成功'
        try:
            if item.provider == CloudAccountConfig.PROVIDER_AWS:
                import boto3
                client = boto3.client(
                    'lightsail',
                    region_name=item.region_hint or 'ap-southeast-1',
                    aws_access_key_id=item.access_key_plain,
                    aws_secret_access_key=item.secret_key_plain,
                )
                response = client.get_instances()
                count = len(response.get('instances') or [])
                note = f'验证成功，实例数 {count}，地区 {item.region_hint or "ap-southeast-1"}'
            elif item.provider == CloudAccountConfig.PROVIDER_ALIYUN:
                from alibabacloud_swas_open20200601 import models as swas_models
                from cloud.aliyun_simple import _region_endpoint, _runtime_options
                from cloud.aliyun_simple import _build_client as _default_build_client
                old_key = os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_ID')
                old_secret = os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_SECRET')
                try:
                    client = _default_build_client(_region_endpoint(item.region_hint or 'cn-hongkong'), account=item)
                    if not client:
                        raise ValueError('无法创建阿里云客户端')
                    response = client.list_instances_with_options(
                        swas_models.ListInstancesRequest(region_id=item.region_hint or 'cn-hongkong', page_size=1),
                        _runtime_options(),
                    )
                    count = len(response.body.to_map().get('Instances', []) or [])
                    note = f'验证成功，实例数 {count}，地区 {item.region_hint or "cn-hongkong"}'
                finally:
                    if old_key is None:
                        os.environ.pop('ALIBABA_CLOUD_ACCESS_KEY_ID', None)
                    else:
                        os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'] = old_key
                    if old_secret is None:
                        os.environ.pop('ALIBABA_CLOUD_ACCESS_KEY_SECRET', None)
                    else:
                        os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'] = old_secret
            else:
                status = CloudAccountConfig.STATUS_UNSUPPORTED
                note = '暂不支持该平台巡检'
        except Exception as exc:
            status = CloudAccountConfig.STATUS_ERROR
            note = str(exc)
        item.mark_status(status, note)
        results.append({'id': item.id, 'provider': item.provider, 'name': item.name, 'status': status, 'note': note})
    return results


async def sync_server_status_tick():
    regions = [
        ('aliyun_simple', os.getenv('ALIYUN_REGION', 'cn-hongkong') or 'cn-hongkong'),
        ('aws_lightsail', os.getenv('AWS_REGION', 'ap-southeast-1') or 'ap-southeast-1'),
    ]
    for provider, region in regions:
        try:
            if provider == 'aliyun_simple':
                await sync_to_async(call_command)('sync_aliyun_assets', region=region)
            else:
                await sync_to_async(call_command)('sync_aws_assets', region=region)
            logger.info('云服务器状态同步完成: provider=%s region=%s', provider, region)
        except Exception as exc:
            logger.warning('云服务器状态同步失败: provider=%s region=%s error=%s', provider, region, exc)


async def sync_cloud_accounts_tick():
    try:
        results = await check_cloud_accounts_status()
        logger.info('云账号状态巡检完成: total=%s ok=%s error=%s', len(results), len([x for x in results if x['status'] == 'ok']), len([x for x in results if x['status'] == 'error']))
    except Exception as exc:
        logger.warning('云账号状态巡检失败: %s', exc)


async def lifecycle_tick(notify=None):
    due = await _get_due_orders()
    migration_due_orders = await _get_migration_due_orders()
    orphan_asset_delete_due = await _get_orphan_asset_delete_due()
    logger.info(
        'CLOUD_LIFECYCLE_DUE renew_notice=%s auto_renew_notice=%s auto_renew=%s delete_notice=%s recycle_notice=%s expire=%s suspend=%s delete=%s recycle=%s migration_due=%s orphan_asset_delete=%s renew_notice_days=%s renew_notice_debug_repeat=%s',
        len(due['renew_notice']),
        len(due['auto_renew_notice']),
        len(due['auto_renew']),
        len(due['delete_notice']),
        len(due['recycle_notice']),
        len(due['expire']),
        len(due['suspend']),
        len(due['delete']),
        len(due['recycle']),
        len(migration_due_orders),
        len(orphan_asset_delete_due),
        due.get('config', {}).get('renew_notice_days'),
        due.get('config', {}).get('renew_notice_debug_repeat'),
    )

    for order in due['renew_notice']:
        if notify and await _user_can_receive_cloud_notice(order.user_id):
            notice = await _cloud_expiry_notice_payload(order.id)
            auto_renew_text = '<b>本单已开启自动续费，将在到期前 3 天自动续费；自动续费前 2 小时会再次通知。</b>' if notice['auto_renew_enabled'] else '<b>本单未开启自动续费。</b>'
            text = f'⏰ IP到期提醒\n\nIP: {notice["ip"]}\n\n{_notice_plan_text(order, notice)}\n\n{auto_renew_text}\n\n如需续费，请点击下方“立即续费”。'
            _log_cloud_notice('renew_notice', order, notice, text, 'cloud_expiry_actions')
            await notify(order.user_id, text, cloud_expiry_actions(order.id))
        await _mark_notice_sent(order.id, 'renew_notice_sent_at')

    for order in due['auto_renew_notice']:
        if notify and await _user_can_receive_cloud_notice(order.user_id):
            notice = await _cloud_expiry_notice_payload(order.id)
            text = f'⚡ 自动续费提醒\n\nIP: {notice["ip"]}\n\n{_notice_plan_text(order, notice)}\n\n将在约 2 小时后使用钱包余额自动续费 31 天。\n\n如不需要，请点击下方按钮关闭自动续费。'
            _log_cloud_notice('auto_renew_prenotice', order, notice, text, 'cloud_auto_renew_notice_actions')
            await notify(order.user_id, text, cloud_auto_renew_notice_actions(order.id))

    for order in due['auto_renew']:
        renewed, err, balance_change = await _run_auto_renew(order.id)
        notice = await _cloud_expiry_notice_payload(order.id)
        logger.info('CLOUD_AUTO_RENEW_EXEC user_id=%s order_id=%s order_no=%s ip=%s ok=%s error=%s', order.user_id, order.id, order.order_no, notice['ip'], not bool(err), err)
        if notify:
            if err:
                text = _cloud_text_format('cloud_auto_renew_failed', '❌ 自动续费失败\n\nIP: {ip}\n\n{plan_text}\n\n失败原因: {error}\n\n请务必在关机/删除计划前完成续费；如余额不足，请先充值或联系人工客服处理。', ip=notice['ip'], plan_text=_notice_plan_text(order, notice), error=_public_cloud_error_text(err))
                text = _ensure_notice_ip(text, notice['ip'])
                _log_cloud_notice('auto_renew_failed', order, notice, text, 'cloud_expiry_actions')
                await notify(order.user_id, text, cloud_expiry_actions(order.id))
            else:
                balance_text = ''
                if balance_change.get('before') is not None and balance_change.get('after') is not None:
                    currency = balance_change.get('currency')
                    balance_text = f'\n\n续费金额: {_fmt_money3(balance_change.get("amount"))} {currency}\n\n续费前余额: {_fmt_money3(balance_change.get("before"))} {currency}\n\n续费后余额: {_fmt_money3(balance_change.get("after"))} {currency}'
                links_text = _proxy_links_notice_text(renewed)
                renewed_notice = await _cloud_expiry_notice_payload(renewed.id)
                text = f'✅ 自动续费成功\n\nIP: {renewed_notice["ip"]}\n\n{_notice_plan_text(renewed, renewed_notice)}{balance_text}{links_text}'
                _log_cloud_notice('auto_renew_success', order, renewed_notice, text, 'cloud_auto_renew_notice_actions')
                await notify(order.user_id, text, cloud_auto_renew_notice_actions(order.id))

    for order in due['delete_notice']:
        if notify and await _user_can_receive_cloud_notice(order.user_id):
            notice = await _cloud_expiry_notice_payload(order.id)
            text = _cloud_text_format(
                'cloud_delete_notice',
                '⚠️ 云服务器删机提醒\n\nIP: {ip}\n订单号: {order_no}\n\n{plan_text}\n\n请务必在 {delete_at} 之前完成续费，避免实例删除；不需要提醒可点击下方关闭提醒。',
                ip=notice['ip'],
                order_no=order.order_no,
                plan_text=_notice_plan_text(order, notice),
                delete_at=_format_notice_dt(order.delete_at),
            )
            text = _ensure_notice_ip(text, notice['ip'])
            _log_cloud_notice('delete_notice', order, notice, text, 'cloud_lifecycle_notice_actions')
            await notify(order.user_id, text, cloud_lifecycle_notice_actions(order.id, 'cloud_delete_notice'))
        await _mark_notice_sent(order.id, 'delete_notice_sent_at')

    for order in due['recycle_notice']:
        if notify and await _user_can_receive_cloud_notice(order.user_id):
            notice = await _cloud_expiry_notice_payload(order.id)
            text = _cloud_text_format(
                'cloud_ip_recycle_notice',
                '📦 固定IP删除提醒\n\nIP: {ip}\n订单号: {order_no}\n\n{plan_text}\n\n请务必在 {ip_recycle_at} 之前完成续费恢复，避免固定 IP 删除；不需要提醒可点击下方关闭提醒。',
                ip=notice['ip'],
                order_no=order.order_no,
                plan_text=_notice_plan_text(order, notice),
                ip_recycle_at=_format_notice_dt(order.ip_recycle_at),
            )
            text = _ensure_notice_ip(text, notice['ip'])
            _log_cloud_notice('ip_recycle_notice', order, notice, text, 'cloud_lifecycle_notice_actions')
            await notify(order.user_id, text, cloud_lifecycle_notice_actions(order.id, 'cloud_ip_recycle_notice'))
        await _mark_notice_sent(order.id, 'recycle_notice_sent_at')

    for order in due['expire']:
        notice = await _cloud_expiry_notice_payload(order.id)
        updated = await _mark_expiring(order.id)
        if notify and getattr(updated, 'cloud_reminder_enabled', True):
            text = _cloud_text_format(
                'cloud_expiring_notice',
                '⏰ 云服务器即将到期\n\nIP: {ip}\n订单号: {order_no}\n\n{plan_text}\n\n请尽快续费；不需要提醒可点击下方关闭提醒。',
                ip=notice['ip'],
                order_no=updated.order_no,
                plan_text=_notice_plan_text(updated, notice),
            )
            text = _ensure_notice_ip(text, notice['ip'])
            _log_cloud_notice('expiring_notice', updated, notice, text, 'cloud_lifecycle_notice_actions')
            await notify(updated.user_id, text, cloud_lifecycle_notice_actions(updated.id, 'cloud_expiring'))

    for order in due['suspend']:
        result = await _stop_instance(order)
        note = _action_note(result)
        if _action_ok(result):
            notice = await _cloud_expiry_notice_payload(order.id)
            updated = await _mark_suspended(order.id, note)
            if notify and getattr(updated, 'suspend_reminder_enabled', True):
                text = _cloud_text_format(
                    'cloud_suspended_notice',
                    '⚠️ 云服务器已关机\n\nIP: {ip}\n订单号: {order_no}\n\n{plan_text}\n\n请务必在 {delete_at} 之前完成续费，避免实例删除；不需要提醒可点击下方关闭提醒。',
                    ip=notice['ip'],
                    order_no=updated.order_no,
                    plan_text=_notice_plan_text(updated, notice),
                    delete_at=_format_notice_dt(updated.delete_at),
                )
                text = _ensure_notice_ip(text, notice['ip'])
                _log_cloud_notice('suspended_notice', updated, notice, text, 'cloud_lifecycle_notice_actions')
                await notify(updated.user_id, text, cloud_lifecycle_notice_actions(updated.id, 'cloud_suspended'))
        else:
            await _record_lifecycle_action_failed(order.id, 'suspend_failed', note)

    for order in due['delete']:
        if not _is_cloud_delete_safe_time():
            logger.warning('CLOUD_DELETE_SKIP_UNSAFE_TIME order_id=%s order_no=%s delete_at=%s now=%s', order.id, order.order_no, order.delete_at, timezone.now())
            continue
        result = await _delete_instance(order)
        note = _action_note(result)
        if _action_ok(result):
            notice = await _cloud_expiry_notice_payload(order.id)
            updated = await _mark_deleted(order.id, note)
            if notify and getattr(updated, 'delete_reminder_enabled', True):
                text = _cloud_text_format(
                    'cloud_instance_deleted_notice',
                    '🗑 云服务器实例已删除\n\nIP: {ip}\n订单号: {order_no}\n\n{plan_text}\n\n固定 IP 仍保留，请务必在 {ip_recycle_at} 之前续费恢复；不需要提醒可点击下方关闭提醒。',
                    ip=notice['ip'],
                    order_no=updated.order_no,
                    plan_text=_notice_plan_text(updated, notice),
                    ip_recycle_at=_format_notice_dt(updated.ip_recycle_at),
                )
                text = _ensure_notice_ip(text, notice['ip'])
                _log_cloud_notice('deleted_notice', updated, notice, text, 'cloud_lifecycle_notice_actions')
                await notify(updated.user_id, text, cloud_lifecycle_notice_actions(updated.id, 'cloud_deleted'))
        else:
            await _record_lifecycle_action_failed(order.id, 'delete_failed', note)

    for order in due['recycle']:
        notice = await _cloud_expiry_notice_payload(order.id)
        updated = await _mark_recycled(order.id, '固定 IP 保留期结束，已释放数据库占位。')
        if notify and getattr(updated, 'ip_recycle_reminder_enabled', True):
            text = _cloud_text_format(
                'cloud_ip_retention_ended_notice',
                '📦 云服务器固定 IP 保留期已结束\n\nIP: {ip}\n订单号: {order_no}\n\n{plan_text}\n\n固定 IP 已超过删除计划，如有疑问请联系人工客服。',
                ip=notice['ip'],
                order_no=updated.order_no,
                plan_text=_notice_plan_text(updated, notice),
            )
            text = _ensure_notice_ip(text, notice['ip'])
            _log_cloud_notice('ip_retention_ended_notice', updated, notice, text, 'cloud_lifecycle_notice_actions')
            await notify(updated.user_id, text, cloud_lifecycle_notice_actions(updated.id, 'cloud_ip_retention_ended'))

    for order in migration_due_orders:
        if not _is_cloud_delete_safe_time():
            logger.warning('CLOUD_MIGRATION_DELETE_SKIP_UNSAFE_TIME order_id=%s order_no=%s migration_due_at=%s now=%s', order.id, order.order_no, order.migration_due_at, timezone.now())
            continue
        result = await _delete_replaced_server(order)
        note = _action_note(result)
        if _action_ok(result):
            notice = await _cloud_expiry_notice_payload(order.id)
            updated = await _mark_replaced_order_deleted(order.id, note)
            if notify and getattr(updated, 'delete_reminder_enabled', True):
                text = _cloud_text_format(
                    'cloud_migration_old_deleted_notice',
                    '🧹 迁移期已结束，旧服务器已删除\n\nIP: {ip}\n订单号: {order_no}\n\n{plan_text}\n\n如仍需使用，请联系人工客服处理；不需要提醒可点击下方关闭提醒。',
                    ip=notice['ip'],
                    order_no=updated.order_no,
                    plan_text=_notice_plan_text(updated, notice),
                )
                text = _ensure_notice_ip(text, notice['ip'])
                _log_cloud_notice('migration_old_deleted_notice', updated, notice, text, 'cloud_lifecycle_notice_actions')
                await notify(updated.user_id, text, cloud_lifecycle_notice_actions(updated.id, 'cloud_migration_old_deleted'))
        else:
            await _record_lifecycle_action_failed(order.id, 'delete_failed', note)

    for asset in orphan_asset_delete_due:
        if not _is_cloud_delete_safe_time():
            logger.warning('CLOUD_ORPHAN_ASSET_DELETE_SKIP_UNSAFE_TIME asset_id=%s ip=%s actual_expires_at=%s now=%s', asset.id, asset.public_ip, asset.actual_expires_at, timezone.now())
            continue
        result = await _delete_orphan_asset_instance(asset)
        note = _action_note(result)
        if _action_ok(result):
            updated = await _mark_orphan_asset_deleted(asset.id, note)
            logger.info('CLOUD_ORPHAN_ASSET_DELETE asset_id=%s ip=%s provider=%s region=%s note=%s', updated.id, updated.previous_public_ip, updated.provider, updated.region_code, note)
        else:
            logger.warning('CLOUD_ORPHAN_ASSET_DELETE_FAILED asset_id=%s ip=%s provider=%s region=%s note=%s', asset.id, asset.public_ip, asset.provider, asset.region_code, note)
