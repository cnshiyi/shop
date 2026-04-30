import logging
import os
import re
from collections import defaultdict
from decimal import Decimal
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
from cloud.services import RenewalPriceMissingError, _cloud_order_lifecycle_fields, _renewal_price, create_cloud_server_renewal, pay_cloud_server_renewal_with_balance, record_cloud_ip_log
from bot.keyboards import cloud_auto_renew_notice_actions, cloud_expiry_actions, cloud_lifecycle_notice_actions

logger = logging.getLogger(__name__)


def _cloud_text_format(key: str, default: str, **kwargs) -> str:
    template = site_text(key, default)
    try:
        return template.format(**kwargs)
    except Exception:
        return default.format(**kwargs)


def _ensure_plan_text(text: str, plan_text: str) -> str:
    text = str(text or '')
    plan_text = str(plan_text or '').strip()
    if not plan_text:
        return text
    required_labels = ('到期时间:', '关机计划:')
    if all(label in text for label in required_labels):
        return text
    return f'{text.rstrip()}\n\n{plan_text}'


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


_NOTICE_ASSET_EXCLUDED_STATUSES = {
    CloudAsset.STATUS_DELETED,
    CloudAsset.STATUS_DELETING,
    CloudAsset.STATUS_TERMINATED,
    CloudAsset.STATUS_TERMINATING,
}


def _notice_asset_queryset():
    return (
        CloudAsset.objects.select_related('order', 'order__user')
        .filter(kind=CloudAsset.KIND_SERVER, order__isnull=False, actual_expires_at__isnull=False)
        .exclude(public_ip__isnull=True)
        .exclude(public_ip='')
        .exclude(status__in=_NOTICE_ASSET_EXCLUDED_STATUSES)
        .order_by('actual_expires_at', '-updated_at', '-id')
    )


def _notice_schedule(order: CloudServerOrder, asset: CloudAsset) -> dict:
    expires_at = asset.actual_expires_at
    schedule = _cloud_order_lifecycle_fields(expires_at, getattr(order, 'renew_extension_days', 0))
    return {
        'ip': asset.public_ip,
        'expires_at': expires_at,
        'suspend_at': schedule.get('suspend_at'),
        'delete_at': schedule.get('delete_at'),
        'ip_recycle_at': schedule.get('ip_recycle_at'),
        'auto_renew_enabled': bool(getattr(order, 'auto_renew_enabled', False)),
        'asset_id': asset.id,
    }


def _append_due(bucket: dict, key: str, order: CloudServerOrder):
    bucket[key].setdefault(order.id, order)


@sync_to_async
def _get_due_orders():
    now = timezone.now()
    renew_notice_days = max(1, _config_int('cloud_renew_notice_days', 5))
    renew_notice_debug_repeat = _config_bool('cloud_renew_notice_debug_repeat', '0')
    renew_notice_at = now + timezone.timedelta(days=renew_notice_days)
    auto_renew_notice_at = now + timezone.timedelta(days=2)
    auto_renew_at = now + timezone.timedelta(days=1)
    delete_notice_at = now + timezone.timedelta(days=1)
    recycle_notice_at = now + timezone.timedelta(days=1)
    due = defaultdict(dict)
    for asset in _notice_asset_queryset():
        order = asset.order
        if not order:
            continue
        schedule = _notice_schedule(order, asset)
        expires_at = schedule['expires_at']
        suspend_at = schedule['suspend_at']
        delete_at = schedule['delete_at']
        ip_recycle_at = schedule['ip_recycle_at']
        active_order = order.status in ['completed', 'expiring', 'renew_pending']
        if active_order and order.cloud_reminder_enabled and expires_at <= renew_notice_at and expires_at > now:
            if renew_notice_debug_repeat or not order.renew_notice_sent_at:
                _append_due(due, 'renew_notice', order)
        if active_order and order.auto_renew_enabled and expires_at <= auto_renew_notice_at and expires_at > auto_renew_at and not order.auto_renew_notice_sent_at:
            _append_due(due, 'auto_renew_notice', order)
        if active_order and order.auto_renew_enabled and expires_at <= auto_renew_at and expires_at > now:
            _append_due(due, 'auto_renew', order)
        if order.status in ['suspended', 'deleting'] and order.delete_reminder_enabled and delete_at and delete_at <= delete_notice_at and delete_at > now and not order.delete_notice_sent_at:
            _append_due(due, 'delete_notice', order)
        if order.status == 'deleted' and order.ip_recycle_reminder_enabled and ip_recycle_at and ip_recycle_at <= recycle_notice_at and ip_recycle_at > now and not order.recycle_notice_sent_at:
            _append_due(due, 'recycle_notice', order)
        if order.provider != 'aliyun_simple':
            if order.status == 'completed' and expires_at <= now and not order.renew_notice_sent_at:
                _append_due(due, 'expire', order)
            if active_order and suspend_at and suspend_at <= now:
                _append_due(due, 'suspend', order)
            if order.status in ['suspended', 'deleting'] and delete_at and delete_at <= now:
                _append_due(due, 'delete', order)
            if order.status == 'deleted' and ip_recycle_at and ip_recycle_at <= now:
                _append_due(due, 'recycle', order)
    return {
        'renew_notice': list(due['renew_notice'].values()),
        'auto_renew_notice': list(due['auto_renew_notice'].values()),
        'auto_renew': list(due['auto_renew'].values()),
        'delete_notice': list(due['delete_notice'].values()),
        'recycle_notice': list(due['recycle_notice'].values()),
        'expire': list(due['expire'].values()),
        'suspend': list(due['suspend'].values()),
        'delete': list(due['delete'].values()),
        'recycle': list(due['recycle'].values()),
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
def _mark_many_notice_sent(order_ids: list[int], field_name: str):
    if order_ids:
        CloudServerOrder.objects.filter(id__in=order_ids).update(**{field_name: timezone.now(), 'updated_at': timezone.now()})


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


def _notice_plan_text(order, notice: dict | None = None, *, include_expiry: bool = True, include_renewal_amount: bool = True) -> str:
    notice = notice or {}
    expires_at = notice.get('expires_at') or getattr(order, 'service_expires_at', None)
    suspend_at = notice.get('suspend_at') or getattr(order, 'suspend_at', None)
    delete_at = notice.get('delete_at') or getattr(order, 'delete_at', None)
    ip_recycle_at = notice.get('ip_recycle_at') or getattr(order, 'ip_recycle_at', None)
    auto_renew_enabled = bool(notice.get('auto_renew_enabled') if 'auto_renew_enabled' in notice else getattr(order, 'auto_renew_enabled', False))
    auto_renew_at = expires_at - timezone.timedelta(days=1) if expires_at else None
    lines = []
    if include_expiry:
        lines.append(f'到期时间: {_format_notice_dt(expires_at)}')
    if include_renewal_amount:
        try:
            lines.append(f'续费金额: {_renewal_price(order, getattr(order, "user", None)):.2f} USDT')
        except RenewalPriceMissingError:
            lines.append('续费金额: 未设置，请联系客服确认')
    if auto_renew_enabled:
        lines.append(f'自动续费: 已开启，预计 {_format_notice_dt(auto_renew_at)} 自动续费')
    else:
        lines.append('自动续费: 本IP未开启自动续费')
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


def _group_orders_by_user(orders):
    grouped = defaultdict(list)
    for order in orders:
        grouped[getattr(order, 'user_id', None)].append(order)
    return {user_id: items for user_id, items in grouped.items() if user_id}


def _active_notice_asset_for_order(order) -> CloudAsset | None:
    return _notice_asset_queryset().filter(order=order).first()


def _notice_payload_for_order(order) -> dict | None:
    asset = _active_notice_asset_for_order(order)
    if not asset:
        return None
    return _notice_schedule(order, asset)


def _apply_notice_schedule_to_order(order: CloudServerOrder, notice: dict) -> CloudServerOrder:
    updates = {
        'service_expires_at': notice.get('expires_at'),
        'suspend_at': notice.get('suspend_at'),
        'renew_grace_expires_at': notice.get('suspend_at'),
        'delete_at': notice.get('delete_at'),
        'ip_recycle_at': notice.get('ip_recycle_at'),
        'updated_at': timezone.now(),
    }
    updates = {key: value for key, value in updates.items() if value is not None}
    if updates:
        CloudServerOrder.objects.filter(id=order.id).update(**updates)
        for key, value in updates.items():
            setattr(order, key, value)
    return order


def _order_notice_ip(order, notice: dict | None = None) -> str:
    notice = notice or _notice_payload_for_order(order) or {}
    return str(notice.get('ip') or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '未分配')


@sync_to_async
def _renew_notice_batch_payload(order_ids: list[int]) -> dict:
    orders = list(CloudServerOrder.objects.filter(id__in=order_ids).order_by('service_expires_at', 'id'))
    if not orders:
        return {'text': '', 'order_ids': [], 'first_order_id': None, 'count': 0}
    lines = ['⏰ IP到期提醒', '', '以下 IP 即将到期，请按计划及时续费：']
    kept_order_ids = []
    for order in orders:
        notice = _notice_payload_for_order(order)
        if not notice:
            logger.info('CLOUD_NOTICE_SKIP_NO_ACTIVE_ASSET type=renew_batch order_id=%s order_no=%s', order.id, order.order_no)
            continue
        kept_order_ids.append(order.id)
        lines.append('')
        lines.append(f'IP: {_order_notice_ip(order, notice)}')
        lines.append(_notice_plan_text(order, notice))
    if not kept_order_ids:
        return {'text': '', 'order_ids': [], 'first_order_id': None, 'count': 0}
    lines.append('')
    lines.append('如需续费，请进入“到期时间查询 → 代理列表”选择对应 IP。')
    return {'text': '\n'.join(lines), 'order_ids': kept_order_ids, 'first_order_id': kept_order_ids[0], 'count': len(kept_order_ids)}


@sync_to_async
def _auto_renew_notice_batch_payload(order_ids: list[int]) -> dict:
    orders = list(CloudServerOrder.objects.select_related('user').filter(id__in=order_ids).order_by('service_expires_at', 'id'))
    if not orders:
        return {'text': '', 'order_ids': [], 'first_order_id': None, 'count': 0}
    user = orders[0].user
    balance = Decimal(str(getattr(user, 'balance', 0) or 0))
    total = Decimal('0')
    lines = ['⚡ 自动续费预提醒', '', '以下 IP 已开启自动续费，将在到期前 1 天自动使用钱包 USDT 续费 31 天；本提醒在到期前 2 天发送：']
    kept_order_ids = []
    for order in orders:
        notice = _notice_payload_for_order(order)
        if not notice:
            logger.info('CLOUD_NOTICE_SKIP_NO_ACTIVE_ASSET type=auto_renew_notice order_id=%s order_no=%s', order.id, order.order_no)
            continue
        kept_order_ids.append(order.id)
        try:
            amount = _renewal_price(order, user)
        except RenewalPriceMissingError:
            logger.info('CLOUD_AUTO_RENEW_NOTICE_SKIP_NO_PRICE order_id=%s order_no=%s', order.id, order.order_no)
            continue
        total += amount
        expires_at = notice.get('expires_at')
        auto_renew_at = expires_at - timezone.timedelta(days=1) if expires_at else None
        lines.append(f'- IP: {_order_notice_ip(order, notice)} | 到期: {_format_notice_dt(expires_at)} | 自动续费时间: {_format_notice_dt(auto_renew_at)} | 预计扣款: {amount:.2f} USDT')
    if not kept_order_ids:
        return {'text': '', 'order_ids': [], 'first_order_id': None, 'count': 0}
    lines.extend(['', f'当前 USDT 余额: {balance:.6f}', f'预计总扣款: {total:.2f} USDT'])
    if balance >= total:
        lines.append('余额检查: 充足。')
    else:
        lines.append(f'余额检查: 不足，预计还差 {(total - balance):.6f} USDT。请在自动续费时间前充值，避免续费失败。')
    lines.append('如不需要自动续费，请进入“到期时间查询 → 自动续费查询”关闭对应 IP。')
    return {'text': '\n'.join(lines), 'order_ids': kept_order_ids, 'first_order_id': kept_order_ids[0], 'count': len(kept_order_ids)}


@sync_to_async
def _lifecycle_notice_batch_text(title: str, order_ids: list[int], closing: str) -> str:
    orders = list(CloudServerOrder.objects.filter(id__in=order_ids).order_by('service_expires_at', 'delete_at', 'ip_recycle_at', 'id'))
    if not orders:
        return ''
    lines = [title, '']
    kept = 0
    for order in orders:
        notice = _notice_payload_for_order(order)
        if not notice:
            logger.info('CLOUD_NOTICE_SKIP_NO_ACTIVE_ASSET type=lifecycle_batch order_id=%s order_no=%s', order.id, order.order_no)
            continue
        kept += 1
        lines.append(f'IP: {_order_notice_ip(order, notice)}')
        lines.append(f'订单号: {order.order_no}')
        lines.append(_notice_plan_text(order, notice))
        lines.append('')
    if not kept:
        return ''
    if closing:
        lines.append(closing)
    return '\n'.join(lines).strip()


@sync_to_async
def _auto_renew_result_batch_text(results: list[dict]) -> str:
    lines = ['⚡ 自动续费执行结果', '']
    for item in results:
        order_id = item.get('order_id')
        order = CloudServerOrder.objects.filter(id=order_id).first()
        if not order:
            continue
        ip = item.get('ip') or _order_notice_ip(order)
        if item.get('ok'):
            lines.append(f'✅ IP: {ip} 自动续费成功')
            lines.append(f'新的到期时间: {_format_notice_dt(order.service_expires_at)}')
            lines.append(_notice_plan_text(order, {'expires_at': order.service_expires_at, 'suspend_at': order.suspend_at, 'delete_at': order.delete_at, 'ip_recycle_at': order.ip_recycle_at, 'auto_renew_enabled': order.auto_renew_enabled}))
        else:
            if getattr(order, 'auto_renew_failure_notice_sent_at', None):
                continue
            lines.append(f'❌ IP: {ip} 自动续费失败')
            lines.append(f'失败原因: {_public_cloud_error_text(item.get("error"))}')
            lines.append(_notice_plan_text(order, {'expires_at': order.service_expires_at, 'suspend_at': order.suspend_at, 'delete_at': order.delete_at, 'ip_recycle_at': order.ip_recycle_at, 'auto_renew_enabled': order.auto_renew_enabled}))
        lines.append('')
    return '\n'.join(lines).strip()


async def _send_cloud_notice(notify, user_id: int, text: str, reply_markup=None) -> bool:
    if not notify:
        return False
    result = await notify(user_id, text, reply_markup)
    return result is not False


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
        return {'ip': '未分配', 'expires_at': None, 'suspend_at': None, 'valid': False}
    notice = _notice_payload_for_order(order)
    if not notice:
        logger.info('CLOUD_NOTICE_SKIP_NO_ACTIVE_ASSET type=single_payload order_id=%s order_no=%s', order.id, order.order_no)
        return {'ip': '未分配', 'expires_at': None, 'suspend_at': None, 'valid': False}
    notice['valid'] = True
    return notice


@sync_to_async
def _run_auto_renew(order_id: int) -> tuple[CloudServerOrder | None, str | None, dict]:
    order = CloudServerOrder.objects.filter(id=order_id).first()
    if not order:
        return None, '订单不存在', {}
    if not order.auto_renew_enabled:
        return None, '自动续费已关闭', {}
    notice = _notice_payload_for_order(order)
    if not notice:
        return None, 'IP已删除或不在代理列表，跳过自动续费', {}
    order = _apply_notice_schedule_to_order(order, notice)
    if order.status != 'renew_pending':
        try:
            renewal = create_cloud_server_renewal.__wrapped__(order.id, order.user_id, 31)
        except RenewalPriceMissingError as exc:
            return None, str(exc), {}
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


def _active_lifecycle_sync_accounts(provider: str):
    return list(CloudAccountConfig.objects.filter(provider=provider, is_active=True).order_by('id'))


async def sync_server_status_tick():
    regions = [
        ('aliyun_simple', os.getenv('ALIYUN_REGION', 'cn-hongkong') or 'cn-hongkong'),
        ('aws_lightsail', os.getenv('AWS_REGION', 'ap-southeast-1') or 'ap-southeast-1'),
    ]
    for provider, region in regions:
        try:
            if provider == 'aliyun_simple':
                accounts = await sync_to_async(_active_lifecycle_sync_accounts)(CloudAccountConfig.PROVIDER_ALIYUN)
                command_name = 'sync_aliyun_assets'
            else:
                accounts = await sync_to_async(_active_lifecycle_sync_accounts)(CloudAccountConfig.PROVIDER_AWS)
                command_name = 'sync_aws_assets'
            for account in accounts:
                await sync_to_async(call_command, thread_sensitive=False)(command_name, region=region, account_id=str(account.id))
                logger.info('云服务器状态同步完成: provider=%s region=%s account_id=%s', provider, region, account.id)
        except Exception as exc:
            logger.warning('云服务器状态同步失败: provider=%s region=%s error=%s', provider, region, exc)


async def sync_cloud_accounts_tick():
    try:
        results = await check_cloud_accounts_status()
        logger.info('云账号状态巡检完成: total=%s ok=%s error=%s', len(results), len([x for x in results if x['status'] == 'ok']), len([x for x in results if x['status'] == 'error']))
    except Exception as exc:
        logger.warning('云账号状态巡检失败: %s', exc)


def _order_action_ip(order) -> str:
    return str(
        getattr(order, 'public_ip', None)
        or getattr(order, 'previous_public_ip', None)
        or getattr(order, 'mtproxy_host', None)
        or getattr(order, 'server_name', None)
        or '未分配'
    )


@sync_to_async
def _defer_startup_lifecycle_actions(due, migration_due_orders, orphan_asset_delete_due, delay_seconds: int):
    run_at = timezone.now() + timezone.timedelta(seconds=delay_seconds)
    for order in due.get('suspend') or []:
        CloudServerOrder.objects.filter(id=order.id).update(suspend_at=run_at, updated_at=timezone.now())
        logger.warning(
            'CLOUD_STARTUP_DEFER_SUSPEND order_id=%s order_no=%s ip=%s old_suspend_at=%s deferred_until=%s message="启动检查命中到期关机，IP %s 将在 1 小时后执行关机"',
            order.id, order.order_no, _order_action_ip(order), order.suspend_at, run_at, _order_action_ip(order),
        )
    for order in due.get('delete') or []:
        CloudServerOrder.objects.filter(id=order.id).update(delete_at=run_at, updated_at=timezone.now())
        logger.warning(
            'CLOUD_STARTUP_DEFER_DELETE order_id=%s order_no=%s ip=%s old_delete_at=%s deferred_until=%s message="启动检查命中到期删机，IP %s 将在 1 小时后执行删机"',
            order.id, order.order_no, _order_action_ip(order), order.delete_at, run_at, _order_action_ip(order),
        )
    for order in migration_due_orders or []:
        CloudServerOrder.objects.filter(id=order.id).update(migration_due_at=run_at, updated_at=timezone.now())
        logger.warning(
            'CLOUD_STARTUP_DEFER_MIGRATION_DELETE order_id=%s order_no=%s ip=%s old_migration_due_at=%s deferred_until=%s message="启动检查命中迁移旧机删机，IP %s 将在 1 小时后执行删机"',
            order.id, order.order_no, _order_action_ip(order), order.migration_due_at, run_at, _order_action_ip(order),
        )
    for asset in orphan_asset_delete_due or []:
        CloudAsset.objects.filter(id=asset.id).update(actual_expires_at=run_at, updated_at=timezone.now())
        logger.warning(
            'CLOUD_STARTUP_DEFER_ORPHAN_ASSET_DELETE asset_id=%s ip=%s old_actual_expires_at=%s deferred_until=%s message="启动检查命中无订单资产删机，IP %s 将在 1 小时后执行删机"',
            asset.id, asset.public_ip or asset.previous_public_ip or '未分配', asset.actual_expires_at, run_at, asset.public_ip or asset.previous_public_ip or '未分配',
        )
    return run_at


async def lifecycle_tick(notify=None, defer_destructive_seconds: int = 0):
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

    if defer_destructive_seconds and (due['suspend'] or due['delete'] or migration_due_orders or orphan_asset_delete_due):
        run_at = await _defer_startup_lifecycle_actions(due, migration_due_orders, orphan_asset_delete_due, defer_destructive_seconds)
        logger.warning(
            'CLOUD_STARTUP_DEFER_DONE suspend=%s delete=%s migration_delete=%s orphan_asset_delete=%s deferred_until=%s',
            len(due['suspend']), len(due['delete']), len(migration_due_orders), len(orphan_asset_delete_due), run_at,
        )
        due['suspend'] = []
        due['delete'] = []
        migration_due_orders = []
        orphan_asset_delete_due = []

    for user_id, orders in _group_orders_by_user(due['renew_notice']).items():
        if len(orders) == 1:
            order = orders[0]
            if notify and await _user_can_receive_cloud_notice(order.user_id):
                notice = await _cloud_expiry_notice_payload(order.id)
                if not notice.get('valid'):
                    continue
                auto_renew_text = '<b>本单已开启自动续费，将在到期前 1 天自动续费；到期前 2 天会发送自动续费预提醒。</b>' if notice['auto_renew_enabled'] else '<b>本单未开启自动续费。</b>'
                text = f'⏰ IP到期提醒\n\nIP: {notice["ip"]}\n\n{_notice_plan_text(order, notice)}\n\n{auto_renew_text}\n\n如需续费，请点击下方“立即续费”。'
                _log_cloud_notice('renew_notice', order, notice, text, 'cloud_expiry_actions')
                sent = await _send_cloud_notice(notify, order.user_id, text, cloud_expiry_actions(order.id))
                if sent:
                    await _mark_notice_sent(order.id, 'renew_notice_sent_at')
            continue
        payload = await _renew_notice_batch_payload([order.id for order in orders])
        if notify and payload['text'] and await _user_can_receive_cloud_notice(user_id):
            _log_cloud_notice('renew_notice_batch', orders[0], {'ip': f'{payload["count"]} 个IP'}, payload['text'], 'cloud_expiry_actions')
            sent = await _send_cloud_notice(notify, user_id, payload['text'], None)
            if sent:
                for order_id in payload.get('order_ids') or []:
                    await _mark_notice_sent(order_id, 'renew_notice_sent_at')

    for user_id, orders in _group_orders_by_user(due['auto_renew_notice']).items():
        payload = await _auto_renew_notice_batch_payload([order.id for order in orders])
        if notify and payload['text'] and await _user_can_receive_cloud_notice(user_id):
            first_order = orders[0]
            _log_cloud_notice('auto_renew_prenotice_batch', first_order, {'ip': f'{payload["count"]} 个IP'}, payload['text'], 'cloud_auto_renew_notice_actions')
            sent = await _send_cloud_notice(notify, user_id, payload['text'], cloud_auto_renew_notice_actions(payload['first_order_id']) if payload['count'] == 1 else None)
            if sent:
                for order_id in payload.get('order_ids') or []:
                    await _mark_notice_sent(order_id, 'auto_renew_notice_sent_at')

    auto_renew_results_by_user = defaultdict(list)
    for order in due['auto_renew']:
        renewed, err, balance_change = await _run_auto_renew(order.id)
        notice = await _cloud_expiry_notice_payload(order.id)
        if not notice.get('valid'):
            continue
        logger.info('CLOUD_AUTO_RENEW_EXEC user_id=%s order_id=%s order_no=%s ip=%s ok=%s error=%s', order.user_id, order.id, order.order_no, notice['ip'], not bool(err), err)
        auto_renew_results_by_user[order.user_id].append({
            'order_id': getattr(renewed, 'id', None) or order.id,
            'original_order_id': order.id,
            'ip': notice['ip'],
            'ok': not bool(err),
            'error': err,
            'balance_change': balance_change,
        })
    if notify:
        for user_id, results in auto_renew_results_by_user.items():
            if not await _user_can_receive_cloud_notice(user_id):
                continue
            text = await _auto_renew_result_batch_text(results)
            if text:
                sent = await _send_cloud_notice(notify, user_id, text, cloud_expiry_actions(results[0]['order_id']) if len(results) == 1 else None)
                if sent:
                    failed_order_ids = [item['original_order_id'] for item in results if not item.get('ok')]
                    await _mark_many_notice_sent(failed_order_ids, 'auto_renew_failure_notice_sent_at')

    for user_id, orders in _group_orders_by_user(due['delete_notice']).items():
        if len(orders) == 1:
            order = orders[0]
            if notify and await _user_can_receive_cloud_notice(order.user_id):
                notice = await _cloud_expiry_notice_payload(order.id)
                if not notice.get('valid'):
                    continue
                text = _cloud_text_format(
                    'cloud_delete_notice',
                    '⚠️ 云服务器删机提醒\n\nIP: {ip}\n订单号: {order_no}\n\n{plan_text}\n\n请务必在 {delete_at} 之前完成续费，避免实例删除；不需要提醒可点击下方关闭提醒。',
                    ip=notice['ip'],
                    order_no=order.order_no,
                    plan_text=_notice_plan_text(order, notice),
                    delete_at=_format_notice_dt(notice.get('delete_at')),
                )
                text = _ensure_notice_ip(text, notice['ip'])
                _log_cloud_notice('delete_notice', order, notice, text, 'cloud_lifecycle_notice_actions')
                sent = await _send_cloud_notice(notify, order.user_id, text, cloud_lifecycle_notice_actions(order.id, 'cloud_delete_notice'))
                if sent:
                    await _mark_notice_sent(order.id, 'delete_notice_sent_at')
            continue
        text = await _lifecycle_notice_batch_text('⚠️ 云服务器删机提醒', [order.id for order in orders], '请务必在各自删除计划前完成续费，避免实例删除。')
        if notify and text and await _user_can_receive_cloud_notice(user_id):
            _log_cloud_notice('delete_notice_batch', orders[0], {'ip': f'{len(orders)} 个IP'}, text, 'cloud_lifecycle_notice_actions')
            sent = await _send_cloud_notice(notify, user_id, text, None)
            if sent:
                for order in orders:
                    await _mark_notice_sent(order.id, 'delete_notice_sent_at')

    for user_id, orders in _group_orders_by_user(due['recycle_notice']).items():
        if len(orders) == 1:
            order = orders[0]
            if notify and await _user_can_receive_cloud_notice(order.user_id):
                notice = await _cloud_expiry_notice_payload(order.id)
                if not notice.get('valid'):
                    continue
                text = _cloud_text_format(
                    'cloud_ip_recycle_notice',
                    '📦 固定IP删除提醒\n\nIP: {ip}\n订单号: {order_no}\n\n{plan_text}\n\n请务必在 {ip_recycle_at} 之前完成续费恢复，避免固定 IP 删除；不需要提醒可点击下方关闭提醒。',
                    ip=notice['ip'],
                    order_no=order.order_no,
                    plan_text=_notice_plan_text(order, notice),
                    ip_recycle_at=_format_notice_dt(notice.get('ip_recycle_at')),
                )
                text = _ensure_notice_ip(text, notice['ip'])
                _log_cloud_notice('ip_recycle_notice', order, notice, text, 'cloud_lifecycle_notice_actions')
                sent = await _send_cloud_notice(notify, order.user_id, text, cloud_lifecycle_notice_actions(order.id, 'cloud_ip_recycle_notice'))
                if sent:
                    await _mark_notice_sent(order.id, 'recycle_notice_sent_at')
            continue
        text = await _lifecycle_notice_batch_text('📦 固定IP删除提醒', [order.id for order in orders], '请务必在各自固定IP删除计划前完成续费恢复，避免固定 IP 删除。')
        if notify and text and await _user_can_receive_cloud_notice(user_id):
            _log_cloud_notice('ip_recycle_notice_batch', orders[0], {'ip': f'{len(orders)} 个IP'}, text, 'cloud_lifecycle_notice_actions')
            sent = await _send_cloud_notice(notify, user_id, text, None)
            if sent:
                for order in orders:
                    await _mark_notice_sent(order.id, 'recycle_notice_sent_at')

    for order in due['expire']:
        notice = await _cloud_expiry_notice_payload(order.id)
        if not notice.get('valid'):
            continue
        updated = await _mark_expiring(order.id)
        if notify and getattr(updated, 'cloud_reminder_enabled', True):
            plan_text = _notice_plan_text(updated, notice)
            text = _cloud_text_format(
                'cloud_expiring_notice',
                '⏰ 云服务器即将到期\n\nIP: {ip}\n订单号: {order_no}\n\n{plan_text}\n\n请尽快续费；不需要提醒可点击下方关闭提醒。',
                ip=notice['ip'],
                order_no=updated.order_no,
                plan_text=plan_text,
            )
            text = _ensure_plan_text(_ensure_notice_ip(text, notice['ip']), plan_text)
            _log_cloud_notice('expiring_notice', updated, notice, text, 'cloud_lifecycle_notice_actions')
            sent = await _send_cloud_notice(notify, updated.user_id, text, cloud_lifecycle_notice_actions(updated.id, 'cloud_expiring'))
            if sent:
                await _mark_notice_sent(updated.id, 'renew_notice_sent_at')

    for order in due['suspend']:
        result = await _stop_instance(order)
        note = _action_note(result)
        if _action_ok(result):
            notice = await _cloud_expiry_notice_payload(order.id)
            if not notice.get('valid'):
                continue
            updated = await _mark_suspended(order.id, note)
            if notify and getattr(updated, 'suspend_reminder_enabled', True):
                text = _cloud_text_format(
                    'cloud_suspended_notice',
                    '⚠️ 云服务器已关机\n\nIP: {ip}\n订单号: {order_no}\n\n{plan_text}\n\n请务必在 {delete_at} 之前完成续费，避免实例删除；不需要提醒可点击下方关闭提醒。',
                    ip=notice['ip'],
                    order_no=updated.order_no,
                    plan_text=_notice_plan_text(updated, notice),
                    delete_at=_format_notice_dt(notice.get('delete_at')),
                )
                text = _ensure_notice_ip(text, notice['ip'])
                _log_cloud_notice('suspended_notice', updated, notice, text, 'cloud_lifecycle_notice_actions')
                await _send_cloud_notice(notify, updated.user_id, text, cloud_lifecycle_notice_actions(updated.id, 'cloud_suspended'))
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
            if not notice.get('valid'):
                continue
            updated = await _mark_deleted(order.id, note)
            if notify and getattr(updated, 'delete_reminder_enabled', True):
                text = _cloud_text_format(
                    'cloud_instance_deleted_notice',
                    '🗑 云服务器实例已删除\n\nIP: {ip}\n订单号: {order_no}\n\n{plan_text}\n\n固定 IP 仍保留，请务必在 {ip_recycle_at} 之前续费恢复；不需要提醒可点击下方关闭提醒。',
                    ip=notice['ip'],
                    order_no=updated.order_no,
                    plan_text=_notice_plan_text(updated, notice),
                    ip_recycle_at=_format_notice_dt(notice.get('ip_recycle_at')),
                )
                text = _ensure_notice_ip(text, notice['ip'])
                _log_cloud_notice('deleted_notice', updated, notice, text, 'cloud_lifecycle_notice_actions')
                await _send_cloud_notice(notify, updated.user_id, text, cloud_lifecycle_notice_actions(updated.id, 'cloud_deleted'))
        else:
            await _record_lifecycle_action_failed(order.id, 'delete_failed', note)

    for order in due['recycle']:
        notice = await _cloud_expiry_notice_payload(order.id)
        if not notice.get('valid'):
            continue
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
            await _send_cloud_notice(notify, updated.user_id, text, cloud_lifecycle_notice_actions(updated.id, 'cloud_ip_retention_ended'))

    for order in migration_due_orders:
        if not _is_cloud_delete_safe_time():
            logger.warning('CLOUD_MIGRATION_DELETE_SKIP_UNSAFE_TIME order_id=%s order_no=%s migration_due_at=%s now=%s', order.id, order.order_no, order.migration_due_at, timezone.now())
            continue
        result = await _delete_replaced_server(order)
        note = _action_note(result)
        if _action_ok(result):
            notice = await _cloud_expiry_notice_payload(order.id)
            if not notice.get('valid'):
                continue
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
                await _send_cloud_notice(notify, updated.user_id, text, cloud_lifecycle_notice_actions(updated.id, 'cloud_migration_old_deleted'))
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
