import asyncio
import hashlib
import logging
import os
import re
import uuid
from collections import defaultdict
from decimal import Decimal, ROUND_HALF_UP
from html import escape

from aiogram.types import InlineKeyboardMarkup
from asgiref.sync import async_to_sync, sync_to_async
from django.core.management import call_command
from django.db.models import Q
from django.utils import timezone

from core.models import CloudAccountConfig, SiteConfig
from core.runtime_config import get_runtime_config
from core.texts import site_text

from orders.models import BalanceLedger
from orders.services import usdt_to_trx
from bot.models import TelegramUser
from cloud.models import CloudAsset, CloudAutoRenewPatrolLog, CloudAutoRenewRetryTask, CloudServerOrder, CloudUserNoticeLog, Server, _with_runtime_time
from cloud.note_utils import append_note, prepend_note
from cloud.services import RenewalPriceMissingError, _cloud_order_lifecycle_fields, _hydrate_order_from_proxy_asset, _order_primary_asset, _order_primary_server, _renewal_price, _resolve_aws_static_ip_name_for_order, create_cloud_server_renewal_for_user, pay_cloud_server_renewal_with_balance, record_cloud_ip_log
from bot.keyboards import cloud_expiry_actions

logger = logging.getLogger(__name__)

AUTO_RENEW_BEFORE_EXPIRY_WINDOW = timezone.timedelta(days=1)
AUTO_RENEW_FAILURE_NOTICE_COOLDOWN = timezone.timedelta(hours=1)
AUTO_RENEW_RETRY_CHECK_INTERVAL = timezone.timedelta(minutes=10)
AUTO_RENEW_RETRY_MAX_ATTEMPTS = 144


def _amount_2(value) -> str:
    return f'{Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)}'


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


def _parse_notify_targets(raw: str) -> list[int | str]:
    targets = []
    seen = set()
    for item in re.split(r'[\n,;，；\s]+', str(raw or '')):
        value = item.strip()
        if not value:
            continue
        target = int(value) if re.fullmatch(r'-?\d+', value) else value
        marker = str(target)
        if marker in seen:
            continue
        seen.add(marker)
        targets.append(target)
    return targets


@sync_to_async
def _auto_renew_execution_notify_config() -> dict:
    enabled = _config_bool('cloud_auto_renew_execution_notify_enabled', '0')
    event_mode = str(get_runtime_config('cloud_auto_renew_execution_notify_events', 'all') or 'all').strip().lower()
    if event_mode not in {'all', 'success', 'failure'}:
        event_mode = 'all'
    targets = _parse_notify_targets(get_runtime_config('cloud_auto_renew_execution_notify_chat_ids', ''))
    return {'enabled': enabled, 'events': event_mode, 'targets': targets}


@sync_to_async
def _daily_expiry_summary_config() -> dict:
    return {
        'enabled': _config_bool('cloud_daily_expiry_summary_enabled', '0'),
        'targets': _parse_notify_targets(get_runtime_config('cloud_daily_expiry_summary_chat_ids', '')),
    }


def _filter_auto_renew_notify_results(results: list[dict], event_mode: str) -> list[dict]:
    if event_mode == 'success':
        return [item for item in results if item.get('ok')]
    if event_mode == 'failure':
        return [item for item in results if not item.get('ok')]
    return list(results)


_NOTICE_ASSET_EXCLUDED_STATUSES = {
    CloudAsset.STATUS_DELETED,
    CloudAsset.STATUS_DELETING,
    CloudAsset.STATUS_TERMINATED,
    CloudAsset.STATUS_TERMINATING,
}


def _notice_asset_queryset():
    return (
        CloudAsset.objects.select_related('order', 'order__user', 'cloud_account', 'order__cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER, order__isnull=False)
        .filter(Q(actual_expires_at__isnull=False) | Q(order__service_expires_at__isnull=False))
        .exclude(public_ip__isnull=True)
        .exclude(public_ip='')
        .exclude(status__in=_NOTICE_ASSET_EXCLUDED_STATUSES)
        .order_by('actual_expires_at', '-updated_at', '-id')
    )


def _deferred_lifecycle_time(stored_at, computed_at, now=None):
    now = now or timezone.now()
    if stored_at and stored_at > now and (not computed_at or computed_at <= now):
        return stored_at
    return computed_at or stored_at


def _notice_schedule(order: CloudServerOrder, asset: CloudAsset) -> dict:
    order = _hydrate_order_from_proxy_asset(order, asset=asset)
    expires_at = getattr(order, 'service_expires_at', None) or asset.actual_expires_at
    schedule = _cloud_order_lifecycle_fields(expires_at, getattr(order, 'renew_extension_days', 0))
    now = timezone.now()
    suspend_at = _deferred_lifecycle_time(getattr(order, 'suspend_at', None), schedule.get('suspend_at'), now)
    delete_at = _deferred_lifecycle_time(getattr(order, 'delete_at', None), schedule.get('delete_at'), now)
    ip_recycle_at = _deferred_lifecycle_time(getattr(order, 'ip_recycle_at', None), schedule.get('ip_recycle_at'), now)
    return {
        'ip': getattr(order, 'public_ip', None) or asset.public_ip,
        'expires_at': expires_at,
        'suspend_at': suspend_at,
        'delete_at': delete_at,
        'ip_recycle_at': ip_recycle_at,
        'auto_renew_enabled': bool(getattr(order, 'auto_renew_enabled', False)),
        'asset_id': asset.id,
    }


def _append_due(bucket: dict, key: str, order: CloudServerOrder):
    bucket[key].setdefault(order.id, order)


def _shutdown_enabled_for_order(order: CloudServerOrder, asset: CloudAsset | None = None) -> bool:
    account = asset.cloud_account if asset and getattr(asset, 'cloud_account_id', None) else getattr(order, 'cloud_account', None)
    if not account:
        return True
    return bool(getattr(account, 'shutdown_enabled', True))


@sync_to_async
def _get_due_orders():
    now = timezone.now()
    renew_notice_days = max(1, _config_int('cloud_renew_notice_days', 5))
    renew_notice_debug_repeat = _config_bool('cloud_renew_notice_debug_repeat', '0')
    renew_notice_at = now + timezone.timedelta(days=renew_notice_days)
    auto_renew_notice_at = now + timezone.timedelta(days=2)
    auto_renew_at = now + AUTO_RENEW_BEFORE_EXPIRY_WINDOW
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
        shutdown_enabled = _shutdown_enabled_for_order(order, asset)
        if active_order and order.cloud_reminder_enabled and expires_at <= renew_notice_at and expires_at > now:
            if renew_notice_debug_repeat or not order.renew_notice_sent_at:
                _append_due(due, 'renew_notice', order)
        if active_order and order.auto_renew_enabled and expires_at <= auto_renew_notice_at and expires_at > auto_renew_at and not order.auto_renew_notice_sent_at:
            _append_due(due, 'auto_renew_notice', order)
        auto_renew_before_expiry = expires_at <= auto_renew_at and expires_at > now
        auto_renew_shutdown_fallback = (
            expires_at <= now
            and suspend_at
            and suspend_at > now
        )
        if active_order and order.auto_renew_enabled and (auto_renew_before_expiry or auto_renew_shutdown_fallback):
            _append_due(due, 'auto_renew', order)
        if shutdown_enabled and order.status in ['suspended', 'deleting'] and order.delete_reminder_enabled and delete_at and delete_at <= delete_notice_at and delete_at > now and not order.delete_notice_sent_at:
            _append_due(due, 'delete_notice', order)
        if order.status == 'deleted' and order.ip_recycle_reminder_enabled and ip_recycle_at and ip_recycle_at <= recycle_notice_at and ip_recycle_at > now and not order.recycle_notice_sent_at:
            _append_due(due, 'recycle_notice', order)
        if order.provider != 'aliyun_simple':
            if order.status == 'completed' and expires_at <= now and not order.renew_notice_sent_at:
                _append_due(due, 'expire', order)
            if active_order and shutdown_enabled and suspend_at and suspend_at <= now:
                _append_due(due, 'suspend', order)
            if shutdown_enabled and order.status in ['suspended', 'deleting'] and delete_at and delete_at <= now:
                _append_due(due, 'delete', order)
            if order.status == 'deleted' and ip_recycle_at and ip_recycle_at <= now:
                _append_due(due, 'recycle', order)

    retained_ip_orders = CloudServerOrder.objects.filter(
        status='deleted',
        ip_recycle_at__isnull=False,
    ).filter(
        Q(static_ip_name__gt='') | Q(public_ip__gt='') | Q(previous_public_ip__gt='')
    ).select_related('user', 'cloud_account')
    for order in retained_ip_orders:
        if order.ip_recycle_reminder_enabled and order.ip_recycle_at <= recycle_notice_at and order.ip_recycle_at > now and not order.recycle_notice_sent_at:
            _append_due(due, 'recycle_notice', order)
        if order.provider != 'aliyun_simple' and order.ip_recycle_at <= now:
            _append_due(due, 'recycle', order)

    failed_cleanup_orders = CloudServerOrder.objects.filter(status='failed', delete_at__isnull=False, delete_at__lte=now).filter(Q(server_name__gt='') | Q(instance_id__gt='')).select_related('user')
    for order in failed_cleanup_orders:
        _append_due(due, 'delete', order)
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
        now = timezone.now()
        order.status = 'expiring'
        order.save(update_fields=['status', 'updated_at'])
        asset = _order_primary_asset(order)
        server = _order_primary_server(order)
        if asset:
            asset.updated_at = now
            asset.save(update_fields=['updated_at'])
        if server:
            server.updated_at = now
            server.save(update_fields=['updated_at'])
        record_cloud_ip_log(event_type='expired', order=order, asset=asset, server=server, note='服务器到期，进入到期处理阶段')
    return order


@sync_to_async
def _mark_suspended(order_id: int, note: str):
    now = timezone.now()
    order = CloudServerOrder.objects.get(id=order_id)
    order.status = 'suspended'
    order.provision_note = prepend_note(order.provision_note, note)
    order.save(update_fields=['status', 'provision_note', 'updated_at'])
    asset = _order_primary_asset(order)
    server = _order_primary_server(order)
    if asset:
        asset.is_active = False
        asset.note = append_note(asset.note, order.provision_note)
        asset.updated_at = now
        asset.save(update_fields=['is_active', 'note', 'updated_at'])
    if server:
        server.is_active = False
        server.note = append_note(server.note, order.provision_note)
        server.updated_at = now
        server.save(update_fields=['is_active', 'note', 'updated_at'])
    record_cloud_ip_log(event_type='suspended', order=order, asset=asset, server=server, note=note or '服务器进入延停状态')
    return order


@sync_to_async
def _mark_deleted(order_id: int, note: str):
    now = timezone.now()
    order = CloudServerOrder.objects.get(id=order_id)
    asset = _order_primary_asset(order)
    server = _order_primary_server(order)
    previous_public_ip = order.public_ip or order.previous_public_ip
    if order.provider == 'aws_lightsail' and not order.static_ip_name:
        resolved_static_ip_name = _resolve_aws_static_ip_name_for_order(order)
        if resolved_static_ip_name:
            order.static_ip_name = resolved_static_ip_name
    unattached_release_at = _with_runtime_time(
        now + timezone.timedelta(days=max(1, _config_int('cloud_unattached_ip_delete_after_days', 15))),
        'cloud_unattached_ip_delete_time',
    )
    order.ip_recycle_at = unattached_release_at
    order.status = 'deleted'
    order.public_ip = previous_public_ip
    order.previous_public_ip = previous_public_ip
    retention_note = _retained_static_ip_note(order, previous_public_ip, note)
    order.provision_note = prepend_note(order.provision_note, retention_note)
    order.instance_id = ''
    order.provider_resource_id = ''
    order.save(update_fields=['status', 'public_ip', 'previous_public_ip', 'static_ip_name', 'ip_recycle_at', 'provision_note', 'instance_id', 'provider_resource_id', 'updated_at'])
    if asset:
        asset.public_ip = previous_public_ip
        asset.previous_public_ip = previous_public_ip
        asset.instance_id = None
        asset.provider_resource_id = None
        asset.actual_expires_at = order.ip_recycle_at or asset.actual_expires_at
        asset.status = CloudAsset.STATUS_DELETED
        asset.provider_status = '固定IP保留中-实例已删除'
        asset.is_active = False
        asset.note = append_note(asset.note, order.provision_note)
        asset.updated_at = now
        asset.save(update_fields=['public_ip', 'previous_public_ip', 'instance_id', 'provider_resource_id', 'actual_expires_at', 'status', 'provider_status', 'is_active', 'note', 'updated_at'])
    if server:
        server.public_ip = previous_public_ip
        server.previous_public_ip = previous_public_ip
        server.instance_id = None
        server.provider_resource_id = None
        server.expires_at = order.ip_recycle_at or server.expires_at
        server.status = Server.STATUS_DELETED
        server.provider_status = '固定IP保留中-实例已删除'
        server.is_active = False
        server.note = append_note(server.note, order.provision_note)
        server.updated_at = now
        server.save(update_fields=['public_ip', 'previous_public_ip', 'instance_id', 'provider_resource_id', 'expires_at', 'status', 'provider_status', 'is_active', 'note', 'updated_at'])
    record_cloud_ip_log(event_type='deleted', order=order, asset=asset, server=server, previous_public_ip=previous_public_ip, public_ip=previous_public_ip, note=retention_note)
    return order


@sync_to_async
def _mark_recycled(order_id: int, note: str):
    now = timezone.now()
    order = CloudServerOrder.objects.get(id=order_id)
    asset = _order_primary_asset(order)
    server = _order_primary_server(order)
    previous_public_ip = order.public_ip or order.previous_public_ip
    order.previous_public_ip = previous_public_ip
    order.public_ip = ''
    order.static_ip_name = ''
    order.mtproxy_host = ''
    order.ip_recycle_at = None
    order.recycle_notice_sent_at = now
    order.ip_recycle_reminder_enabled = False
    order.provision_note = prepend_note(order.provision_note, note)
    CloudServerOrder.objects.filter(id=order.id).update(
        previous_public_ip=previous_public_ip,
        public_ip='',
        static_ip_name='',
        mtproxy_host='',
        ip_recycle_at=None,
        recycle_notice_sent_at=now,
        ip_recycle_reminder_enabled=False,
        provision_note=order.provision_note,
        updated_at=now,
    )
    if asset:
        asset.previous_public_ip = previous_public_ip
        asset.public_ip = None
        asset.mtproxy_host = None
        asset.note = append_note(asset.note, order.provision_note)
        asset.updated_at = now
        asset.save(update_fields=['previous_public_ip', 'public_ip', 'mtproxy_host', 'note', 'updated_at'])
    if server:
        server.previous_public_ip = previous_public_ip
        server.public_ip = None
        server.note = append_note(server.note, order.provision_note)
        server.updated_at = now
        server.save(update_fields=['previous_public_ip', 'public_ip', 'note', 'updated_at'])
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


def _secret_notice_hint(secret: str | None) -> str:
    text = str(secret or '').strip()
    if not text:
        return '-'
    if len(text) <= 8:
        return '***'
    return f'{text[:4]}…{text[-4:]}'


def _retained_static_ip_note(order: CloudServerOrder, ip: str, action_note: str = '') -> str:
    return (
        f'实例已删除，固定 IP 保留中；IP={ip or "缺失"}；固定IP名={order.static_ip_name or "-"}；端口={order.mtproxy_port or "-"}；'
        f'secret={_secret_notice_hint(order.mtproxy_secret)}；服务到期={_format_notice_dt(order.service_expires_at)}；'
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


def _code_text(value) -> str:
    return f'<code>{escape(str(value or "-"))}</code>'


def _notice_plan_text(order, notice: dict | None = None, *, include_expiry: bool = True, include_renewal_amount: bool = True) -> str:
    notice = notice or {}
    expires_at = notice.get('expires_at') or getattr(order, 'service_expires_at', None)
    auto_renew_enabled = bool(notice.get('auto_renew_enabled') if 'auto_renew_enabled' in notice else getattr(order, 'auto_renew_enabled', False))
    auto_renew_at = expires_at - timezone.timedelta(days=1) if expires_at else None
    lines = []
    if include_expiry:
        lines.append(f'到期时间: {_code_text(_format_notice_dt(expires_at))}')
    if include_renewal_amount:
        try:
            lines.append(f'价格: {_code_text(f"{_renewal_price(order, getattr(order, "user", None)):.2f}")} USDT')
        except RenewalPriceMissingError:
            lines.append(f'价格: {_code_text("未设置")}，请联系客服确认')
    auto_renew_text = f'已开启，预计 {_code_text(_format_notice_dt(auto_renew_at))} 自动续费' if auto_renew_enabled else '未开启'
    lines.append(f'自动续费状态: {auto_renew_text}')
    return '\n'.join(lines)


def _renew_notice_plan_text(order, notice: dict | None = None) -> str:
    notice = notice or {}
    expires_at = notice.get('expires_at') or getattr(order, 'service_expires_at', None)
    suspend_at = notice.get('suspend_at') or getattr(order, 'suspend_at', None)
    try:
        amount_text = f'{_renewal_price(order, getattr(order, "user", None)):.2f}'
        amount_line = f'续费金额: {_code_text(amount_text)} USDT'
    except RenewalPriceMissingError:
        amount_line = f'续费金额: {_code_text("未设置")}，请联系客服确认'
    lines = [
        f'到期时间: {_code_text(_format_notice_dt(expires_at))}',
        amount_line,
        f'关机计划: {_code_text(_format_notice_dt(suspend_at))}',
        '请尽快完成续费，避免关机。',
    ]
    return '\n\n'.join(lines)


def _user_display_label(user) -> str:
    if not user:
        return '未绑定用户'
    usernames = list(getattr(user, 'usernames', []) or [])
    primary_username = usernames[0] if usernames else (getattr(user, 'username', None) or '')
    display_name = getattr(user, 'display_name', None) or getattr(user, 'first_name', None) or primary_username or str(getattr(user, 'tg_user_id', '') or getattr(user, 'id', ''))
    return f'{display_name} (@{primary_username})' if primary_username else str(display_name or '未命名用户')


def _group_orders_by_user(orders):
    grouped = defaultdict(list)
    for order in orders:
        grouped[getattr(order, 'user_id', None)].append(order)
    return {user_id: items for user_id, items in grouped.items() if user_id}


def _order_telegram_group_id(order) -> int | None:
    asset = CloudAsset.objects.filter(order_id=getattr(order, 'id', None), telegram_group_id__isnull=False).order_by('-updated_at', '-id').first()
    return asset.telegram_group_id if asset else None


def _auto_renew_candidate_users(order) -> list[TelegramUser]:
    candidates = []
    seen = set()
    primary_user = getattr(order, 'user', None) or TelegramUser.objects.filter(id=getattr(order, 'user_id', None)).first()
    if primary_user:
        candidates.append(primary_user)
        seen.add(primary_user.id)
    group_id = _order_telegram_group_id(order)
    if group_id:
        group_users = (
            TelegramUser.objects
            .filter(cloudasset__telegram_group_id=group_id, cloudasset__kind=CloudAsset.KIND_SERVER)
            .distinct()
            .order_by('-balance', 'id')
        )
        for user in group_users:
            if user.id in seen:
                continue
            candidates.append(user)
            seen.add(user.id)
    return candidates


def _group_balance_lines_for_orders(orders: list[CloudServerOrder]) -> list[str]:
    users = []
    seen = set()
    for order in orders:
        for user in _auto_renew_candidate_users(order):
            if user.id in seen:
                continue
            seen.add(user.id)
            users.append(user)
    if len(users) <= 1:
        return []
    return ['多用户 USDT 余额:', *[f'- {_user_display_label(user)}: {_amount_2(getattr(user, "balance", 0))}' for user in users]]


def _active_notice_asset_for_order(order) -> CloudAsset | None:
    return (
        CloudAsset.objects.select_related('order', 'order__user')
        .filter(kind=CloudAsset.KIND_SERVER, order=order)
        .filter(Q(actual_expires_at__isnull=False) | Q(order__service_expires_at__isnull=False))
        .exclude(public_ip__isnull=True)
        .exclude(public_ip='')
        .exclude(status__in=_NOTICE_ASSET_EXCLUDED_STATUSES)
        .order_by('-updated_at', '-id')
        .first()
    )


def _retained_notice_payload_for_order(order) -> dict | None:
    asset = _order_primary_asset(order)
    server = _order_primary_server(order)
    expires_at = getattr(order, 'service_expires_at', None) or getattr(asset, 'actual_expires_at', None) or getattr(server, 'expires_at', None)
    ip_value = (
        getattr(order, 'public_ip', None)
        or getattr(order, 'previous_public_ip', None)
        or getattr(asset, 'public_ip', None)
        or getattr(asset, 'previous_public_ip', None)
        or getattr(server, 'public_ip', None)
        or getattr(server, 'previous_public_ip', None)
    )
    if not expires_at and not ip_value:
        return None
    return {
        'ip': ip_value or '未分配',
        'expires_at': expires_at,
        'suspend_at': getattr(order, 'suspend_at', None),
        'delete_at': getattr(order, 'delete_at', None),
        'ip_recycle_at': getattr(order, 'ip_recycle_at', None),
        'auto_renew_enabled': bool(getattr(order, 'auto_renew_enabled', False)),
        'asset_id': getattr(asset, 'id', None),
    }


def _notice_payload_for_order(order) -> dict | None:
    asset = _active_notice_asset_for_order(order)
    if asset:
        return _notice_schedule(order, asset)
    if getattr(order, 'status', None) == 'deleted':
        return _retained_notice_payload_for_order(order)
    return None


def _apply_notice_schedule_to_order(order: CloudServerOrder, notice: dict) -> CloudServerOrder:
    updates = {
        'suspend_at': notice.get('suspend_at'),
        'renew_grace_expires_at': notice.get('suspend_at'),
        'delete_at': notice.get('delete_at'),
        'ip_recycle_at': notice.get('ip_recycle_at'),
        'updated_at': timezone.now(),
    }
    if getattr(order, 'provider', None) != 'aws_lightsail':
        updates['service_expires_at'] = notice.get('expires_at')
    updates = {key: value for key, value in updates.items() if value is not None}
    if updates:
        CloudServerOrder.objects.filter(id=order.id).update(**updates)
        for key, value in updates.items():
            setattr(order, key, value)
    return order


def _order_notice_ip(order, notice: dict | None = None) -> str:
    notice = notice or _notice_payload_for_order(order) or {}
    return str(notice.get('ip') or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '未分配')


def _renew_notice_ip_summary(order, notice: dict) -> str:
    expires_at = notice.get('expires_at') or getattr(order, 'service_expires_at', None)
    auto_renew_enabled = bool(notice.get('auto_renew_enabled') if 'auto_renew_enabled' in notice else getattr(order, 'auto_renew_enabled', False))
    auto_renew_at = expires_at - timezone.timedelta(days=1) if expires_at else None
    auto_renew_text = f'已开启，预计 {_code_text(_format_notice_dt(auto_renew_at))} 自动续费' if auto_renew_enabled else '未开启'
    return '\n'.join([
        f'到期时间: {_code_text(_format_notice_dt(expires_at))}',
        f'自动续费状态: {auto_renew_text}',
    ])


def _total_charge_line(total: Decimal) -> tuple[str, Decimal | None]:
    try:
        trx_total = async_to_sync(usdt_to_trx)(total)
    except Exception as exc:
        logger.warning('CLOUD_RENEW_NOTICE_TRX_ESTIMATE_FAILED total=%s error=%s', total, exc)
        return f'预计总扣款: {_code_text(_amount_2(total))} USDT', None
    return f'预计总扣款: {_code_text(_amount_2(total))} USDT / 约 {_code_text(_amount_2(trx_total))} TRX', trx_total


@sync_to_async
def _renew_notice_batch_payload(order_ids: list[int]) -> dict:
    orders = list(CloudServerOrder.objects.select_related('user').filter(id__in=order_ids).order_by('service_expires_at', 'id'))
    if not orders:
        return {'text': '', 'order_ids': [], 'first_order_id': None, 'count': 0}
    user = orders[0].user
    balance = Decimal(str(getattr(user, 'balance', 0) or 0)) if user else Decimal('0')
    total = Decimal('0')
    lines = ['⏰ IP到期提醒', '', '以下 IP 即将到期，请按计划及时续费：']
    kept_order_ids = []
    for order in orders:
        notice = _notice_payload_for_order(order)
        if not notice:
            logger.info('CLOUD_NOTICE_SKIP_NO_ACTIVE_ASSET type=renew_batch order_id=%s order_no=%s', order.id, order.order_no)
            continue
        kept_order_ids.append(order.id)
        try:
            amount = _renewal_price(order, user)
        except RenewalPriceMissingError:
            logger.info('CLOUD_RENEW_NOTICE_SKIP_NO_PRICE order_id=%s order_no=%s', order.id, order.order_no)
            amount = None
        if amount is not None:
            total += amount
        lines.append('')
        lines.append(f'IP: {_code_text(_order_notice_ip(order, notice))}')
        lines.append('')
        lines.append(f'价格: {_code_text(f"{amount:.2f}")} USDT' if amount is not None else f'价格: {_code_text("未设置")}，请联系客服确认')
        lines.append('')
        lines.append(_renew_notice_ip_summary(order, notice))
    if not kept_order_ids:
        return {'text': '', 'order_ids': [], 'first_order_id': None, 'count': 0}
    balance_lines = _group_balance_lines_for_orders(orders)
    if balance_lines:
        lines.extend(['', *balance_lines])
    trx_balance = Decimal(str(getattr(user, 'balance_trx', 0) or 0)) if user else Decimal('0')
    total_charge_line, trx_total = _total_charge_line(total)
    lines.extend(['', f'当前 USDT 余额: {_code_text(_amount_2(balance))} / TRX 余额: {_code_text(_amount_2(trx_balance))}', total_charge_line])
    if balance >= total or (trx_total is not None and trx_balance >= trx_total):
        lines.append('余额检查: 充足。')
    elif trx_total is not None:
        lines.append(f'余额检查: 不足，预计还差 {_code_text(_amount_2(max(total - balance, Decimal(0))))} USDT 或 {_code_text(_amount_2(max(trx_total - trx_balance, Decimal(0))))} TRX。请在到期前充值，避免续费失败。')
    else:
        lines.append(f'余额检查: 不足，预计还差 {_code_text(_amount_2(total - balance))} USDT。请在到期前充值，避免续费失败。')
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
    balance_lines = _group_balance_lines_for_orders(orders)
    if balance_lines:
        lines.extend(['', *balance_lines])
    lines.extend(['', f'当前 USDT 余额: {_amount_2(balance)}', f'预计总扣款: {_amount_2(total)} USDT'])
    if balance >= total:
        lines.append('余额检查: 充足。')
    else:
        lines.append(f'余额检查: 不足，预计还差 {_amount_2(total - balance)} USDT。请在自动续费时间前充值，避免续费失败。')
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


def _balance_change_lines(balance_change: dict | None) -> list[str]:
    if not balance_change:
        return []
    currency = str(balance_change.get('currency') or 'USDT')
    before = balance_change.get('before')
    amount = balance_change.get('amount')
    after = balance_change.get('after')
    lines = []
    if before is not None:
        lines.append(f'扣款前余额: {_code_text(_amount_2(before))} {currency}')
    if amount is not None:
        lines.append(f'本次扣款: {_code_text(_amount_2(abs(Decimal(str(amount)))))} {currency}')
    if after is not None:
        lines.append(f'扣款后余额: {_code_text(_amount_2(after))} {currency}')
    return lines


@sync_to_async
def _auto_renew_result_batch_text(results: list[dict]) -> str:
    executed = []
    successes = []
    failures = []
    for item in results:
        order_id = item.get('order_id')
        order = CloudServerOrder.objects.filter(id=order_id).first()
        if not order:
            continue
        ip = item.get('ip') or _order_notice_ip(order)
        if item.get('ok'):
            executed.append(ip)
            successes.append((ip, _format_notice_dt(order.service_expires_at), item.get('balance_change')))
            continue
        fallback_retry = bool(item.get('fallback_retry'))
        if not _auto_renew_failure_notice_due(order):
            continue
        executed.append(ip)
        reason = _public_cloud_error_text(item.get('error'))
        if fallback_retry:
            reason = f'已进入过期后至关机前兜底续费检查；{reason}'
        failures.append((ip, reason))
    if not executed:
        return ''
    lines = ['⚡ 自动续费执行结果', '']
    lines.append('本次自动续费 IP：')
    lines.extend(_code_text(ip) for ip in executed)
    if successes:
        lines.extend(['', '续费成功：'])
        for ip, expires_at, balance_change in successes:
            lines.append(f'IP: {_code_text(ip)} | 新到期时间: {_code_text(expires_at)}')
            lines.extend(_balance_change_lines(balance_change))
    if failures:
        lines.extend(['', '续费失败：'])
        lines.extend(f'IP: {_code_text(ip)} | 失败原因: {reason}' for ip, reason in failures)
    return '\n'.join(lines).strip()


def _auto_renew_failure_notice_due(order) -> bool:
    failure_sent_at = getattr(order, 'auto_renew_failure_notice_sent_at', None)
    return not failure_sent_at or timezone.now() - failure_sent_at >= AUTO_RENEW_FAILURE_NOTICE_COOLDOWN


def _auto_renew_retry_should_wait_for_recharge(error: str | None, balance_change: dict | None = None) -> bool:
    text = str(error or '')
    return any(keyword in text for keyword in ('余额不足', '余额不够', '请充值', 'insufficient'))


@sync_to_async
def _enqueue_auto_renew_retry(order_id: int, *, ip: str = '', error: str | None = None, balance_change: dict | None = None) -> bool:
    if not _auto_renew_retry_should_wait_for_recharge(error, balance_change):
        return False
    order = CloudServerOrder.objects.select_related('user').filter(id=order_id).first()
    if not order or not order.auto_renew_enabled:
        return False
    if order.status not in ('completed', 'renew_pending'):
        return False
    now = timezone.now()
    task, _ = CloudAutoRenewRetryTask.objects.update_or_create(
        order=order,
        status=CloudAutoRenewRetryTask.STATUS_PENDING,
        defaults={
            'user': order.user,
            'order_no': order.order_no,
            'ip': ip or _order_notice_ip(order),
            'failure_reason': error or '自动续费失败，等待用户充值后重试。',
            'last_error': error or '',
            'next_check_at': now + AUTO_RENEW_RETRY_CHECK_INTERVAL,
        },
    )
    logger.info('CLOUD_AUTO_RENEW_RETRY_ENQUEUED task_id=%s order_id=%s order_no=%s ip=%s', task.id, order.id, order.order_no, task.ip)
    return True


def _auto_renew_retry_has_sufficient_balance(order) -> bool:
    for candidate in _auto_renew_candidate_users(order):
        try:
            amount = _renewal_price(order, candidate)
        except RenewalPriceMissingError:
            continue
        if Decimal(str(getattr(candidate, 'balance', 0) or 0)) >= amount:
            return True
    return False


@sync_to_async
def _due_auto_renew_retry_tasks(limit: int = 50) -> list[int]:
    return list(
        CloudAutoRenewRetryTask.objects
        .filter(status=CloudAutoRenewRetryTask.STATUS_PENDING, next_check_at__lte=timezone.now())
        .order_by('next_check_at', 'id')
        .values_list('id', flat=True)[:limit]
    )


@sync_to_async
def _run_auto_renew_retry_task(task_id: int) -> dict | None:
    task = CloudAutoRenewRetryTask.objects.select_related('order', 'order__user').filter(id=task_id).first()
    if not task or task.status != CloudAutoRenewRetryTask.STATUS_PENDING:
        return None
    now = timezone.now()
    order = task.order
    if not order or not order.auto_renew_enabled:
        task.status = CloudAutoRenewRetryTask.STATUS_CANCELLED
        task.cancelled_at = now
        task.last_checked_at = now
        task.last_error = '自动续费已关闭或订单不存在'
        task.save(update_fields=['status', 'cancelled_at', 'last_checked_at', 'last_error', 'updated_at'])
        return None
    if order.status not in ('completed', 'renew_pending'):
        task.status = CloudAutoRenewRetryTask.STATUS_CANCELLED
        task.cancelled_at = now
        task.last_checked_at = now
        task.last_error = f'订单状态不可重试：{order.status}'
        task.save(update_fields=['status', 'cancelled_at', 'last_checked_at', 'last_error', 'updated_at'])
        return None
    if not _auto_renew_retry_has_sufficient_balance(order):
        task.attempts += 1
        task.last_checked_at = now
        task.next_check_at = now + AUTO_RENEW_RETRY_CHECK_INTERVAL
        task.last_error = '余额仍不足，继续等待充值。'
        if task.attempts >= AUTO_RENEW_RETRY_MAX_ATTEMPTS:
            task.status = CloudAutoRenewRetryTask.STATUS_FAILED
        task.save(update_fields=['attempts', 'last_checked_at', 'next_check_at', 'last_error', 'status', 'updated_at'])
        return None
    renewed, err, balance_change = _run_auto_renew.__wrapped__(order.id)
    task.attempts += 1
    task.last_checked_at = now
    if renewed and not err:
        task.status = CloudAutoRenewRetryTask.STATUS_SUCCEEDED
        task.succeeded_at = timezone.now()
        task.last_error = ''
        task.save(update_fields=['attempts', 'last_checked_at', 'status', 'succeeded_at', 'last_error', 'updated_at'])
        return {
            'order_id': renewed.id,
            'original_order_id': order.id,
            'ip': task.ip or _order_notice_ip(renewed),
            'ok': True,
            'error': None,
            'balance_change': balance_change,
            'retry_task_id': task.id,
        }
    task.next_check_at = timezone.now() + AUTO_RENEW_RETRY_CHECK_INTERVAL
    task.last_error = err or '自动续费重试失败'
    if not _auto_renew_retry_should_wait_for_recharge(err, balance_change):
        task.status = CloudAutoRenewRetryTask.STATUS_FAILED
    elif task.attempts >= AUTO_RENEW_RETRY_MAX_ATTEMPTS:
        task.status = CloudAutoRenewRetryTask.STATUS_FAILED
    task.save(update_fields=['attempts', 'last_checked_at', 'next_check_at', 'last_error', 'status', 'updated_at'])
    return None


async def _process_auto_renew_retry_tasks(notify_target=None) -> int:
    task_ids = await _due_auto_renew_retry_tasks()
    if not task_ids:
        return 0
    results = []
    batch_id = f'retry-{uuid.uuid4().hex[:10]}'
    for task_id in task_ids:
        result = await _run_auto_renew_retry_task(task_id)
        if result:
            await _record_auto_renew_patrol_log(
                result.get('original_order_id') or result.get('order_id'),
                batch_id=batch_id,
                ip=result.get('ip') or '',
                ok=True,
                error=None,
                balance_change=result.get('balance_change') or {},
                renewed_order_id=result.get('order_id'),
            )
            results.append(result)
    if results and notify_target:
        await _send_auto_renew_execution_target_notices(notify_target, {'retry': results})
    return len(results)


def _auto_renew_notifiable_failure_order_ids(results: list[dict]) -> list[int]:
    order_ids = []
    for item in results:
        if item.get('ok'):
            continue
        order_id = item.get('original_order_id') or item.get('order_id')
        order = CloudServerOrder.objects.filter(id=order_id).first()
        if order and _auto_renew_failure_notice_due(order):
            order_ids.append(order.id)
    return order_ids


def _auto_renew_success_batch_text(results: list[dict], title: str = '✅ 自动续费巡检成功') -> str:
    lines = [title, '']
    for item in results:
        if not item.get('ok'):
            continue
        order_id = item.get('order_id')
        order = CloudServerOrder.objects.filter(id=order_id).first()
        if not order:
            continue
        ip = item.get('ip') or _order_notice_ip(order)
        balance_change = item.get('balance_change') or {}
        payer_label = balance_change.get('payer_label')
        lines.append(f'IP: {_code_text(ip)}')
        if payer_label:
            lines.append(f'扣款用户: {payer_label}')
        lines.append(f'新的到期时间: {_code_text(_format_notice_dt(order.service_expires_at))}')
        lines.extend(_balance_change_lines(balance_change))
        lines.append('')
    return '\n'.join(lines).strip()


async def _send_cloud_notice(notify, user_id: int, text: str, reply_markup=None) -> bool:
    if not notify:
        return False
    result = await notify(user_id, text, reply_markup)
    return result is not False


async def _send_logged_cloud_notice(event: str, notify, user_id: int, text: str, reply_markup=None, *, order=None, notice: dict | None = None, batch_id: str = '', is_batch: bool = False, extra: dict | None = None) -> bool:
    order_id = getattr(order, 'id', None)
    if await _cloud_notice_already_delivered(event, user_id=user_id, order_id=order_id, batch_id=batch_id):
        logger.info('CLOUD_NOTICE_SKIP_DUPLICATE event=%s user_id=%s order_id=%s batch_id=%s', event, user_id, order_id, batch_id)
        return False
    delivered = await _send_cloud_notice(notify, user_id, text, reply_markup)
    await _record_cloud_user_notice_log(
        event_type=event,
        user_id=user_id,
        order_id=order_id,
        order_no=getattr(order, 'order_no', None),
        ip=(notice or {}).get('ip') or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None),
        batch_id=batch_id,
        delivered=delivered,
        text=text,
        is_batch=is_batch,
        extra=extra or {},
    )
    return delivered


async def _send_auto_renew_execution_target_notices(notify_target, results_by_user: dict) -> dict:
    if not notify_target or not results_by_user:
        return {'sent': 0, 'failure_order_ids': []}
    config = await _auto_renew_execution_notify_config()
    if not config.get('enabled') or not config.get('targets'):
        return {'sent': 0, 'failure_order_ids': []}
    event_mode = config.get('events') or 'all'
    sent_count = 0
    all_results = []
    for results in results_by_user.values():
        all_results.extend(_filter_auto_renew_notify_results(results, event_mode))
    if not all_results:
        return {'sent': 0, 'failure_order_ids': []}
    text = await _auto_renew_result_batch_text(all_results)
    if not text:
        return {'sent': 0, 'failure_order_ids': []}
    for target in config['targets']:
        try:
            ok = await notify_target(target, text, None)
            sent_count += int(ok is not False)
        except Exception as exc:
            logger.warning('CLOUD_AUTO_RENEW_EXEC_TARGET_NOTIFY_FAILED target=%s error=%s', target, exc)
    failure_order_ids = await sync_to_async(_auto_renew_notifiable_failure_order_ids)(all_results)
    logger.info('CLOUD_AUTO_RENEW_EXEC_TARGET_NOTIFY_SENT targets=%s results=%s sent=%s events=%s', len(config['targets']), len(all_results), sent_count, event_mode)
    return {'sent': sent_count, 'failure_order_ids': failure_order_ids}


def _notice_batch_id(event: str, *order_ids: int) -> str:
    key = ':'.join([event, *[str(item) for item in order_ids]])
    return uuid.uuid5(uuid.NAMESPACE_URL, key).hex[:16]


def _target_batch_id(event: str, target, date_text: str) -> str:
    digest = hashlib.sha1(str(target).encode('utf-8')).hexdigest()[:10]
    return f'{event}:{date_text}:{digest}'


@sync_to_async
def _cloud_notice_already_delivered(event_type: str, *, user_id: int | None, order_id: int | None = None, batch_id: str = '') -> bool:
    if not user_id:
        return False
    query = CloudUserNoticeLog.objects.filter(user_id=user_id, event_type=event_type, delivered=True)
    if batch_id:
        return query.filter(batch_id=batch_id).exists()
    if order_id is not None:
        return query.filter(order_id=order_id).exists()
    return query.exists()


@sync_to_async
def _record_cloud_user_notice_log(*, event_type: str, user_id: int | None, order_id: int | None = None, order_no: str | None = None, ip: str | None = None, batch_id: str = '', target_chat_id: int | None = None, delivered: bool = False, text: str = '', is_batch: bool = False, extra: dict | None = None):
    user = TelegramUser.objects.filter(id=user_id).first() if user_id else None
    order = CloudServerOrder.objects.filter(id=order_id).first() if order_id else None
    CloudUserNoticeLog.objects.create(
        user=user,
        order=order,
        batch_id=batch_id or '',
        event_type=event_type,
        target_chat_id=target_chat_id,
        order_no=order_no or getattr(order, 'order_no', None),
        ip=ip or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None),
        is_batch=is_batch,
        delivered=delivered,
        text_preview=str(text or '')[:1000],
        extra=extra or {},
    )



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


def _is_cloud_action_time(config_key: str, default_time: str, now=None, window_minutes: int = 10) -> bool:
    local_now = timezone.localtime(now or timezone.now())
    hour, minute = _config_time(config_key, default_time)
    scheduled = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return scheduled <= local_now < scheduled + timezone.timedelta(minutes=max(1, int(window_minutes or 10)))


def _next_cloud_action_run_at(config_key: str, default_time: str, *, now=None, min_delay_seconds: int = 0):
    base = timezone.localtime((now or timezone.now()) + timezone.timedelta(seconds=max(0, int(min_delay_seconds or 0))))
    hour, minute = _config_time(config_key, default_time)
    scheduled = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if scheduled < base:
        scheduled += timezone.timedelta(days=1)
    return scheduled


def _config_time_text(config_key: str, default_time: str) -> str:
    hour, minute = _config_time(config_key, default_time)
    return f'{hour:02d}:{minute:02d}'


def _is_cloud_suspend_time(now=None) -> bool:
    return _is_cloud_action_time('cloud_suspend_time', '15:00', now=now)


def _is_cloud_delete_safe_time(now=None) -> bool:
    return _is_cloud_action_time('cloud_delete_time', '15:00', now=now)


def _action_note(result) -> str:
    if isinstance(result, tuple):
        return str(result[1] if len(result) > 1 else result[0])
    return str(result or '')


def _action_ok(result) -> bool:
    return not isinstance(result, tuple) or bool(result[0])


def _cloud_action_timeout_seconds() -> int:
    try:
        return max(10, int(str(get_runtime_config('cloud_action_timeout_seconds', '90')).strip() or 90))
    except Exception:
        return 90


async def _run_cloud_action_with_timeout(coro, *, action: str, target: str):
    timeout_seconds = _cloud_action_timeout_seconds()
    try:
        return await asyncio.wait_for(coro, timeout=timeout_seconds)
    except asyncio.TimeoutError:
        note = f'{action}超时：云 API 超过 {timeout_seconds} 秒未返回，已跳过本轮，避免卡住机器人；下次生命周期会重试。'
        logger.warning('CLOUD_ACTION_TIMEOUT action=%s target=%s timeout_seconds=%s', action, target, timeout_seconds)
        return False, note


@sync_to_async
def _record_lifecycle_action_failed(order_id: int, event_type: str, note: str):
    order = CloudServerOrder.objects.get(id=order_id)
    asset = _order_primary_asset(order)
    server = _order_primary_server(order)
    order.provision_note = prepend_note(order.provision_note, note)
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
    order = CloudServerOrder.objects.select_related('user').filter(id=order_id).first()
    if not order:
        return None, '订单不存在', {}
    if not order.auto_renew_enabled:
        return None, '自动续费已关闭', {}
    notice = _notice_payload_for_order(order)
    if not notice:
        return None, 'IP已删除或不在代理列表，跳过自动续费', {}
    order = _apply_notice_schedule_to_order(order, notice)
    candidates = _auto_renew_candidate_users(order)
    if not candidates:
        return None, '未找到可用于自动续费的绑定用户', {}
    errors = []
    for candidate in candidates:
        working_order = CloudServerOrder.objects.select_related('user').filter(id=order.id).first()
        if not working_order:
            return None, '订单不存在', {}
        if working_order.status != 'renew_pending':
            try:
                renewal = create_cloud_server_renewal_for_user.__wrapped__(working_order.id, candidate.id, 31)
            except RenewalPriceMissingError as exc:
                return None, str(exc), {}
            if not renewal:
                errors.append(f'{_user_display_label(candidate)}: 订单当前不可续费')
                continue
        renewed, err = pay_cloud_server_renewal_with_balance.__wrapped__(working_order.id, candidate.id, 'USDT', 31)
        if renewed and not err:
            ledger = BalanceLedger.objects.filter(
                user_id=candidate.id,
                related_type='cloud_order',
                related_id=working_order.id,
                type='cloud_order_balance_pay',
                currency='USDT',
            ).order_by('-created_at', '-id').first()
            balance_change = {
                'currency': getattr(ledger, 'currency', 'USDT') if ledger else 'USDT',
                'amount': getattr(ledger, 'amount', None) if ledger else None,
                'before': getattr(ledger, 'before_balance', None) if ledger else None,
                'after': getattr(ledger, 'after_balance', None) if ledger else None,
                'payer_user_id': candidate.id,
                'payer_label': _user_display_label(candidate),
            }
            return renewed, None, balance_change
        errors.append(f'{_user_display_label(candidate)}: {err or "续费失败"}')
    return None, '；'.join(errors) or 'USDT 余额不足', {'candidate_count': len(candidates)}


@sync_to_async
def _record_auto_renew_patrol_log(order_id: int, *, batch_id: str, ip: str, ok: bool, error: str | None, balance_change: dict | None, renewed_order_id: int | None = None):
    order = CloudServerOrder.objects.select_related('user').filter(id=order_id).first()
    if not order:
        return None
    payer_user_id = (balance_change or {}).get('payer_user_id')
    user = TelegramUser.objects.filter(id=payer_user_id).first() if payer_user_id else getattr(order, 'user', None)
    usernames = list(getattr(user, 'usernames', []) or []) if user else []
    primary_username = usernames[0] if usernames else (getattr(user, 'username', None) or '')
    display_name = getattr(user, 'display_name', None) or getattr(user, 'first_name', None) or primary_username or '未绑定用户'
    ledger_amount = (balance_change or {}).get('amount')
    expires_at = getattr(order, 'service_expires_at', None)
    completed_order_no = order.order_no
    if renewed_order_id and renewed_order_id != order.id:
        renewed = CloudServerOrder.objects.filter(id=renewed_order_id).first()
        if renewed:
            expires_at = getattr(renewed, 'service_expires_at', None) or expires_at
            completed_order_no = renewed.order_no or completed_order_no
    return CloudAutoRenewPatrolLog.objects.create(
        order=order,
        user=user,
        batch_id=batch_id,
        order_no=order.order_no,
        ip=str(ip or order.public_ip or order.previous_public_ip or '未分配'),
        provider=order.provider,
        user_display_name=display_name,
        username_label=f'@{primary_username}' if primary_username else '-',
        tg_user_id=getattr(user, 'tg_user_id', None) if user else None,
        is_success=bool(ok),
        failure_reason=str(error or '').strip() or None,
        currency=str((balance_change or {}).get('currency') or 'USDT'),
        balance_before=(balance_change or {}).get('before'),
        balance_after=(balance_change or {}).get('after'),
        balance_change=ledger_amount,
        service_expires_at=expires_at,
        completed_order_id=renewed_order_id or order.id,
        completed_order_no=completed_order_no,
    )


@sync_to_async
def _get_migration_due_orders():
    now = timezone.now()
    return list(
        CloudServerOrder.objects.filter(
            replacement_orders__isnull=False,
            migration_due_at__lte=now,
        ).exclude(status__in=['deleted']).distinct()
    )


@sync_to_async
def _get_orphan_asset_delete_due():
    now = timezone.now()
    waiting_manual_time_q = Q(provider_status__icontains='待人工添加时间') | Q(note__icontains='等待人工添加真实到期时间') | Q(note__icontains='等待人工添加时间')
    unattached_static_ip_q = Q(provider_status__icontains='未附加固定IP') | Q(note__icontains='未附加固定IP') | Q(provider_resource_id__icontains='StaticIp')
    return list(
        CloudAsset.objects.select_related('cloud_account').filter(
            kind=CloudAsset.KIND_SERVER,
            order__isnull=True,
            actual_expires_at__lte=now,
        ).exclude(cloud_account__shutdown_enabled=False).exclude(waiting_manual_time_q).exclude(unattached_static_ip_q).exclude(status__in=[CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED])
    )


@sync_to_async
def _get_unattached_static_ip_delete_due():
    now = timezone.now()
    waiting_manual_time_q = Q(provider_status__icontains='待人工添加时间') | Q(note__icontains='等待人工添加真实到期时间') | Q(note__icontains='等待人工添加时间')
    return list(
        CloudAsset.objects.select_related('cloud_account').filter(
            kind=CloudAsset.KIND_SERVER,
            order__isnull=True,
            provider='aws_lightsail',
            actual_expires_at__lte=now,
        ).filter(
            Q(instance_id__isnull=True) | Q(instance_id='')
        ).filter(
            Q(provider_status__icontains='未附加固定IP') | Q(note__icontains='未附加固定IP') | Q(provider_resource_id__icontains='StaticIp')
        ).exclude(waiting_manual_time_q).exclude(status__in=[CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED])
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


@sync_to_async
def _can_execute_suspend(order_id: int) -> tuple[bool, str, CloudServerOrder | None]:
    now = timezone.now()
    order = CloudServerOrder.objects.select_related('cloud_account').filter(id=order_id).first()
    if not order:
        return False, '订单不存在，跳过关机。', None
    if order.status not in {'completed', 'expiring', 'renew_pending'}:
        return False, f'订单状态为 {order.status}，不执行关机。', order
    asset = _order_primary_asset(order)
    if not _shutdown_enabled_for_order(order, asset):
        return False, '云账号关机计划已关闭，跳过真实关机。', order
    if not order.suspend_at:
        return False, '订单没有计划关机时间，跳过真实关机。', order
    if order.suspend_at > now:
        return False, f'未到计划关机时间 {order.suspend_at.isoformat()}，跳过真实关机。', order
    return True, '', order


def _delete_instance_sync(order: CloudServerOrder) -> tuple[bool, str]:
    if order.provider != 'aws_lightsail' or not order.server_name:
        return False, '非 AWS 资源，暂未执行真实删机。'
    try:
        client = _aws_client(order.region_code, getattr(order, 'cloud_account', None))
        client.delete_instance(instanceName=order.server_name)
        return True, 'AWS 实例已执行删除，固定 IP 继续保留。'
    except Exception as exc:
        if _is_aws_not_found_error(exc):
            return True, f'AWS 实例云端已不存在，按已删除处理，固定 IP 继续保留：{order.server_name}'
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
        if _is_aws_not_found_error(exc):
            return True, f'迁移期结束，旧 AWS 实例云端已不存在，按已删除处理：{order.server_name}'
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
        if _is_aws_not_found_error(exc):
            return True, f'无订单 AWS 资产云端已不存在，按已删除处理：{asset.asset_name}'
        return False, f'无订单 AWS 资产到期，真实删机失败: {exc}'


async def _delete_orphan_asset_instance(asset: CloudAsset) -> tuple[bool, str]:
    return await sync_to_async(_delete_orphan_asset_instance_sync, thread_sensitive=False)(asset)


def _is_aws_not_found_error(exc) -> bool:
    text = str(exc or '')
    return 'NotFoundException' in text or 'ResourceNotFoundException' in text or 'does not exist' in text or 'not be found' in text


def _release_order_static_ip_sync(order: CloudServerOrder) -> tuple[bool, str]:
    if order.provider != 'aws_lightsail':
        return True, '固定 IP 保留期结束，已释放数据库占位。'
    release_name = str(order.static_ip_name or '').strip()
    if not release_name:
        release_name = _resolve_aws_static_ip_name_for_order(order)
        if release_name:
            order.static_ip_name = release_name
            order.save(update_fields=['static_ip_name', 'updated_at'])
    if not release_name:
        return False, '固定 IP 保留期结束，但缺少固定 IP 名称，无法执行真实释放。'
    try:
        client = _aws_client(order.region_code, getattr(order, 'cloud_account', None))
        client.release_static_ip(staticIpName=release_name)
        return True, f'固定 IP 保留期结束，AWS 固定 IP 已真实释放：{release_name}'
    except Exception as exc:
        if _is_aws_not_found_error(exc):
            return True, f'固定 IP 保留期结束，AWS 固定 IP 云端已不存在，按已释放处理：{release_name}'
        return False, f'固定 IP 保留期结束，AWS 固定 IP 真实释放失败: {exc}'


async def _release_order_static_ip(order: CloudServerOrder) -> tuple[bool, str]:
    return await sync_to_async(_release_order_static_ip_sync, thread_sensitive=False)(order)


def _release_unattached_static_ip_sync(asset: CloudAsset) -> tuple[bool, str]:
    if asset.provider != 'aws_lightsail':
        return False, '未附加固定IP到期，非 AWS 资源，暂未执行真实释放。'
    release_name = str(asset.asset_name or '').strip()
    if not release_name:
        release_name = str(asset.provider_resource_id or '').rsplit('/', 1)[-1].strip()
    if not release_name:
        return False, '未附加固定IP到期，但缺少固定 IP 名称，无法执行真实释放。'
    try:
        client = _aws_client(asset.region_code, getattr(asset, 'cloud_account', None))
        client.release_static_ip(staticIpName=release_name)
        return True, f'未附加固定IP到期，AWS 固定 IP 已真实释放：{release_name}'
    except Exception as exc:
        if _is_aws_not_found_error(exc):
            return True, f'未附加固定IP到期，AWS 固定 IP 云端已不存在，按已释放处理：{release_name}'
        return False, f'未附加固定IP到期，AWS 固定 IP 真实释放失败: {exc}'


async def _release_unattached_static_ip(asset: CloudAsset) -> tuple[bool, str]:
    return await sync_to_async(_release_unattached_static_ip_sync, thread_sensitive=False)(asset)


@sync_to_async
def _mark_orphan_asset_deleted(asset_id: int, note: str):
    now = timezone.now()
    asset = CloudAsset.objects.get(id=asset_id)
    previous_public_ip = asset.public_ip or asset.previous_public_ip
    asset.previous_public_ip = previous_public_ip
    asset.status = CloudAsset.STATUS_DELETED
    asset.provider_status = 'expired-deleted'
    asset.is_active = False
    asset.note = append_note(asset.note, note)
    asset.save(update_fields=['previous_public_ip', 'status', 'provider_status', 'is_active', 'note', 'updated_at'])
    server_queryset = Server.objects.filter(order__isnull=True).filter(
        provider=asset.provider,
        region_code=asset.region_code,
    ).filter(
        instance_id__in=[value for value in [asset.instance_id, asset.provider_resource_id] if value]
    )
    for server in server_queryset:
        server.previous_public_ip = previous_public_ip
        server.status = CloudAsset.STATUS_DELETED
        server.is_active = False
        server.note = append_note(server.note, asset.note)
        server.updated_at = now
        server.save(update_fields=['previous_public_ip', 'status', 'is_active', 'note', 'updated_at'])
    record_cloud_ip_log(event_type='deleted', asset=asset, previous_public_ip=previous_public_ip, public_ip=None, note=note or '无订单资产到期删除')
    return asset


@sync_to_async
def _mark_unattached_static_ip_deleted(asset_id: int, note: str):
    now = timezone.now()
    asset = CloudAsset.objects.get(id=asset_id)
    previous_public_ip = asset.public_ip or asset.previous_public_ip
    asset.previous_public_ip = previous_public_ip
    asset.public_ip = None
    asset.status = CloudAsset.STATUS_DELETED
    asset.provider_status = '未附加固定IP-已到期删除'
    asset.is_active = False
    asset.note = append_note(asset.note, note)
    asset.save(update_fields=['previous_public_ip', 'public_ip', 'status', 'provider_status', 'is_active', 'note', 'updated_at'])
    server_queryset = Server.objects.filter(order__isnull=True).filter(
        provider=asset.provider,
        region_code=asset.region_code,
        public_ip=previous_public_ip,
    )
    for server in server_queryset:
        server.previous_public_ip = previous_public_ip
        server.public_ip = None
        server.status = CloudAsset.STATUS_DELETED
        server.provider_status = '未附加固定IP-已到期删除'
        server.is_active = False
        server.note = append_note(server.note, asset.note)
        server.updated_at = now
        server.save(update_fields=['previous_public_ip', 'public_ip', 'status', 'provider_status', 'is_active', 'note', 'updated_at'])
    record_cloud_ip_log(event_type='recycled', asset=asset, previous_public_ip=previous_public_ip, public_ip=None, note=note or '未附加固定IP已到期删除')
    return asset


@sync_to_async
def _mark_replaced_order_deleted(order_id: int, note: str):
    now = timezone.now()
    order = CloudServerOrder.objects.get(id=order_id)
    asset = _order_primary_asset(order)
    server = _order_primary_server(order)
    previous_public_ip = order.public_ip or order.previous_public_ip
    order.status = 'deleted'
    order.previous_public_ip = previous_public_ip
    order.instance_id = ''
    order.provider_resource_id = ''
    order.public_ip = ''
    order.provision_note = prepend_note(order.provision_note, note)
    order.save(update_fields=['status', 'previous_public_ip', 'instance_id', 'provider_resource_id', 'public_ip', 'provision_note', 'updated_at'])
    if asset:
        asset.is_active = False
        asset.public_ip = None
        asset.previous_public_ip = previous_public_ip
        asset.note = append_note(asset.note, order.provision_note)
        asset.updated_at = now
        asset.save(update_fields=['is_active', 'public_ip', 'previous_public_ip', 'note', 'updated_at'])
    if server:
        server.is_active = False
        server.public_ip = None
        server.previous_public_ip = previous_public_ip
        server.note = append_note(server.note, order.provision_note)
        server.updated_at = now
        server.save(update_fields=['is_active', 'public_ip', 'previous_public_ip', 'note', 'updated_at'])
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


_LIFECYCLE_SYNC_CURSOR_KEY = 'cloud_asset_sync_next_account_cursor'


def _active_lifecycle_sync_accounts(provider: str):
    return list(CloudAccountConfig.objects.filter(provider=provider, is_active=True).order_by('id'))


def _next_lifecycle_sync_target():
    sync_targets = [
        (CloudAccountConfig.PROVIDER_ALIYUN, 'aliyun_simple', 'sync_aliyun_assets'),
        (CloudAccountConfig.PROVIDER_AWS, 'aws_lightsail', 'sync_aws_assets'),
    ]
    candidates = []
    for account_provider, provider, command_name in sync_targets:
        for account in _active_lifecycle_sync_accounts(account_provider):
            candidates.append({
                'key': f'{account_provider}:{account.id}',
                'account_provider': account_provider,
                'provider': provider,
                'command_name': command_name,
                'account': account,
            })
    if not candidates:
        SiteConfig.set(_LIFECYCLE_SYNC_CURSOR_KEY, '')
        return None
    cursor = SiteConfig.get(_LIFECYCLE_SYNC_CURSOR_KEY, '')
    next_index = 0
    if cursor:
        for index, item in enumerate(candidates):
            if item['key'] == cursor:
                next_index = (index + 1) % len(candidates)
                break
    target = candidates[next_index]
    SiteConfig.set(_LIFECYCLE_SYNC_CURSOR_KEY, target['key'])
    return target


async def sync_server_status_tick():
    target = await sync_to_async(_next_lifecycle_sync_target)()
    if not target:
        logger.info('云服务器状态同步跳过：没有启用的云账号')
        return
    account = target['account']
    account_provider = target['account_provider']
    provider = target['provider']
    command_name = target['command_name']
    try:
        if account_provider == CloudAccountConfig.PROVIDER_AWS:
            region = os.getenv('AWS_REGION', '').strip()
        else:
            region = (getattr(account, 'region_hint', '') or os.getenv('ALIYUN_REGION', '') or 'cn-hongkong').strip()
        kwargs = {'account_id': str(account.id)}
        if region:
            kwargs['region'] = region
        await sync_to_async(call_command, thread_sensitive=False)(command_name, **kwargs)
        logger.info('云服务器状态同步完成: provider=%s region=%s account_id=%s cursor=%s', provider, region or 'all', account.id, target['key'])
    except Exception as exc:
        logger.warning('云服务器状态同步失败: provider=%s account_id=%s error=%s', provider, account.id, exc)


_DAILY_EXPIRY_EVENT = 'daily_expiry_summary'
_DAILY_EXPIRY_EXCLUDED_ORDER_STATUSES = {'pending', 'paid', 'provisioning', 'failed', 'cancelled', 'deleted'}
_DAILY_EXPIRY_TERMINAL_STATUSES = {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_TERMINATING}


def _cloud_runtime_status_label(status: str, provider_status: str = '') -> str:
    status = (status or '').strip().lower()
    provider_status = (provider_status or '').strip()
    if status == CloudAsset.STATUS_RUNNING:
        return '正在运行'
    if status in {CloudAsset.STATUS_STOPPED, CloudAsset.STATUS_SUSPENDED}:
        return '已关机'
    if status == CloudAsset.STATUS_STARTING:
        return '启动中'
    if status == CloudAsset.STATUS_STOPPING:
        return '关机中'
    if status in {CloudAsset.STATUS_TERMINATING, CloudAsset.STATUS_DELETING}:
        return '删除中'
    if status in {CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_DELETED}:
        return '已删除'
    if status == CloudAsset.STATUS_EXPIRED:
        return '已过期'
    if status == CloudAsset.STATUS_EXPIRED_GRACE:
        return '到期延停'
    if provider_status:
        return provider_status
    return dict(CloudAsset.STATUS_CHOICES).get(status, '未知状态')


def _daily_expiry_person_label(value) -> str:
    text = str(value or '').strip().lstrip('@')
    return escape(text) if text else '-'


def _daily_expiry_user_label(item: dict) -> str:
    username = _daily_expiry_person_label(item.get('username') or item.get('tg_user_id'))
    first_name = _daily_expiry_person_label(item.get('first_name'))
    return f'所属用户: {username}｜姓名: {first_name}'


def _daily_expiry_user_key(item: dict) -> tuple[str, str, str]:
    return (
        str(item.get('tg_user_id') or ''),
        str(item.get('username') or ''),
        str(item.get('first_name') or ''),
    )


def _daily_expiry_line(item: dict, index: int) -> str:
    expires_text = _format_notice_dt(item.get('expires_at'))
    region = item.get('region_name') or item.get('region_code') or '-'
    provider_status = item.get('provider_status') or '-'
    return (
        f'{index}. IP: <code>{escape(str(item.get("ip") or "-"))}</code>\n'
        f'   地区: {escape(str(region))}\n'
        f'   到期: {escape(expires_text)}｜状态: {escape(str(item.get("status_label") or "未知状态"))}\n'
        f'   云端原始状态: {escape(str(provider_status))}'
    )


def _daily_expiry_grouped_lines(items: list[dict]) -> list[str]:
    grouped = []
    current_key = None
    current_items = []
    for item in items:
        key = _daily_expiry_user_key(item)
        if current_key is not None and key != current_key:
            grouped.append(current_items)
            current_items = []
        current_key = key
        current_items.append(item)
    if current_items:
        grouped.append(current_items)
    lines = []
    for group_index, group_items in enumerate(grouped):
        if group_index > 0:
            lines.append('────────────')
        lines.append(_daily_expiry_user_label(group_items[0]))
        lines.extend(_daily_expiry_line(item, index) for index, item in enumerate(group_items, 1))
    return lines


def _daily_expiry_message_chunks(title: str, items: list[dict], date_text: str, total_counts: str) -> list[str]:
    header = [
        f'{title}（{escape(date_text)} 12:00）',
        '状态来自数据库当前记录。',
        total_counts,
    ]
    if not items:
        return ['\n'.join([*header, '', '暂无记录。'])]
    messages = []
    lines = [*header, '']
    max_length = 3900
    for line in _daily_expiry_grouped_lines(items):
        candidate = '\n'.join([*lines, line])
        if len(candidate) > max_length and len(lines) > len(header) + 1:
            messages.append('\n'.join(lines).rstrip())
            lines = [*header, '', line]
        else:
            lines.append(line)
    if lines:
        messages.append('\n'.join(lines).rstrip())
    return messages


@sync_to_async
def _daily_expiry_summary_items() -> dict:
    now = timezone.now()
    today = timezone.localdate(now)
    today_start = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()), timezone.get_current_timezone())
    today_end = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.max.time()), timezone.get_current_timezone())
    queryset = (
        CloudServerOrder.objects.select_related('user', 'cloud_account')
        .filter(service_expires_at__isnull=False, service_expires_at__lte=today_end)
        .exclude(status__in=_DAILY_EXPIRY_EXCLUDED_ORDER_STATUSES)
        .order_by('service_expires_at', 'id')
    )
    today_items = []
    expired_items = []
    for order in queryset:
        asset = _order_primary_asset(order)
        server = _order_primary_server(order)
        source = asset or server
        if source and getattr(source, 'status', '') in _DAILY_EXPIRY_TERMINAL_STATUSES and order.status in {'deleted', 'deleting'}:
            continue
        ip = (getattr(source, 'public_ip', None) or order.public_ip or order.previous_public_ip or '').strip()
        if not ip:
            continue
        status = getattr(source, 'status', '') or CloudAsset.STATUS_UNKNOWN
        provider_status = getattr(source, 'provider_status', '') or ''
        user = getattr(order, 'user', None)
        item = {
            'order_id': order.id,
            'order_no': order.order_no,
            'ip': ip,
            'expires_at': order.service_expires_at,
            'status': status,
            'status_label': _cloud_runtime_status_label(status, provider_status),
            'provider_status': provider_status,
            'region_code': order.region_code,
            'region_name': order.region_name,
            'tg_user_id': getattr(user, 'tg_user_id', None),
            'username': getattr(user, 'primary_username', '') or '',
            'first_name': getattr(user, 'first_name', '') or '',
        }
        if order.service_expires_at >= today_start:
            today_items.append(item)
        else:
            expired_items.append(item)
    return {'date': today.isoformat(), 'today': today_items, 'expired': expired_items}


def _daily_expiry_summary_texts(summary: dict) -> list[str]:
    today_items = summary.get('today') or []
    expired_items = summary.get('expired') or []
    date_text = summary.get('date') or timezone.localdate().isoformat()
    total_counts = f'今日到期: {len(today_items)} 台｜已经到期: {len(expired_items)} 台'
    if not today_items and not expired_items:
        return _daily_expiry_message_chunks('📌 云服务器到期汇总', [], date_text, total_counts)
    texts = []
    if today_items:
        texts.extend(_daily_expiry_message_chunks('🟡 今日到期服务器', today_items, date_text, total_counts))
    if expired_items:
        texts.extend(_daily_expiry_message_chunks('🔴 已经过期服务器', expired_items, date_text, total_counts))
    return texts


@sync_to_async
def _daily_expiry_summary_already_sent(batch_id: str) -> bool:
    return CloudUserNoticeLog.objects.filter(event_type=_DAILY_EXPIRY_EVENT, batch_id=batch_id, delivered=True).exists()


@sync_to_async
def _record_daily_expiry_summary(target, batch_id: str, delivered: bool, text: str, summary: dict):
    target_chat_id = target if isinstance(target, int) else None
    CloudUserNoticeLog.objects.create(
        batch_id=batch_id,
        event_type=_DAILY_EXPIRY_EVENT,
        target_chat_id=target_chat_id,
        is_batch=True,
        delivered=delivered,
        text_preview=str(text or '')[:1000],
        extra={
            'target': str(target),
            'date': summary.get('date'),
            'today_count': len(summary.get('today') or []),
            'expired_count': len(summary.get('expired') or []),
        },
    )


async def daily_expiry_summary_tick(notify_target=None, *, force: bool = False, sync_cloud: bool = False):
    if not notify_target:
        return {'sent': 0, 'skipped': 'missing_notify_target'}
    config = await _daily_expiry_summary_config()
    targets = config.get('targets') or []
    if not config.get('enabled') or not targets:
        return {'sent': 0, 'skipped': 'disabled'}
    if sync_cloud:
        await sync_server_status_tick()
    summary = await _daily_expiry_summary_items()
    texts = _daily_expiry_summary_texts(summary)
    sent = 0
    for target in targets:
        batch_id = _target_batch_id(_DAILY_EXPIRY_EVENT, target, summary.get('date') or timezone.localdate().isoformat())
        if not force and await _daily_expiry_summary_already_sent(batch_id):
            logger.info('CLOUD_DAILY_EXPIRY_SUMMARY_SKIP_DUPLICATE target=%s batch_id=%s', target, batch_id)
            continue
        delivered = False
        delivered_parts = 0
        try:
            for text in texts:
                ok = await notify_target(target, text, None) is not False
                delivered_parts += int(ok)
            delivered = delivered_parts == len(texts)
            sent += int(delivered)
        except Exception as exc:
            logger.warning('CLOUD_DAILY_EXPIRY_SUMMARY_NOTIFY_FAILED target=%s error=%s', target, exc)
        await _record_daily_expiry_summary(target, batch_id, delivered, '\n\n'.join(texts), summary)
    logger.info('CLOUD_DAILY_EXPIRY_SUMMARY_DONE targets=%s sent=%s today=%s expired=%s', len(targets), sent, len(summary.get('today') or []), len(summary.get('expired') or []))
    return {'sent': sent, 'today': len(summary.get('today') or []), 'expired': len(summary.get('expired') or [])}


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
    suspend_run_at = _next_cloud_action_run_at('cloud_suspend_time', '15:00', min_delay_seconds=delay_seconds)
    delete_run_at = _next_cloud_action_run_at('cloud_delete_time', '15:00', min_delay_seconds=delay_seconds)
    for order in due.get('suspend') or []:
        CloudServerOrder.objects.filter(id=order.id).update(suspend_at=suspend_run_at, updated_at=timezone.now())
        logger.warning(
            'CLOUD_STARTUP_DEFER_SUSPEND order_id=%s order_no=%s ip=%s old_suspend_at=%s deferred_until=%s message="启动检查命中到期关机，IP %s 将顺延到后台设定关机时间执行"',
            order.id, order.order_no, _order_action_ip(order), order.suspend_at, suspend_run_at, _order_action_ip(order),
        )
    for order in due.get('delete') or []:
        CloudServerOrder.objects.filter(id=order.id).update(delete_at=delete_run_at, updated_at=timezone.now())
        logger.warning(
            'CLOUD_STARTUP_DEFER_DELETE order_id=%s order_no=%s ip=%s old_delete_at=%s deferred_until=%s message="启动检查命中到期删机，IP %s 将顺延到后台设定删机时间执行"',
            order.id, order.order_no, _order_action_ip(order), order.delete_at, delete_run_at, _order_action_ip(order),
        )
    for order in migration_due_orders or []:
        CloudServerOrder.objects.filter(id=order.id).update(migration_due_at=delete_run_at, updated_at=timezone.now())
        logger.warning(
            'CLOUD_STARTUP_DEFER_MIGRATION_DELETE order_id=%s order_no=%s ip=%s old_migration_due_at=%s deferred_until=%s message="启动检查命中迁移旧机删机，IP %s 将顺延到后台设定删机时间执行"',
            order.id, order.order_no, _order_action_ip(order), order.migration_due_at, delete_run_at, _order_action_ip(order),
        )
    for asset in orphan_asset_delete_due or []:
        CloudAsset.objects.filter(id=asset.id).update(actual_expires_at=delete_run_at, updated_at=timezone.now())
        logger.warning(
            'CLOUD_STARTUP_DEFER_ORPHAN_ASSET_DELETE asset_id=%s ip=%s old_actual_expires_at=%s deferred_until=%s message="启动检查命中无订单资产删机，IP %s 将顺延到后台设定删机时间执行"',
            asset.id, asset.public_ip or asset.previous_public_ip or '未分配', asset.actual_expires_at, delete_run_at, asset.public_ip or asset.previous_public_ip or '未分配',
        )
    return {'suspend': suspend_run_at, 'delete': delete_run_at}


async def auto_renew_patrol_tick(notify=None, notify_target=None):
    await _process_auto_renew_retry_tasks(notify_target)
    due = await _get_due_orders()
    results_by_user = defaultdict(list)
    batch_id = uuid.uuid4().hex[:16]
    for order in due['auto_renew']:
        renewed, err, balance_change = await _run_auto_renew(order.id)
        notice = await _cloud_expiry_notice_payload(order.id)
        if not notice.get('valid'):
            continue
        ok = not bool(err)
        renewed_order_id = getattr(renewed, 'id', None) or order.id
        logger.info(
            'CLOUD_AUTO_RENEW_PATROL user_id=%s order_id=%s order_no=%s ip=%s ok=%s error=%s',
            order.user_id,
            order.id,
            order.order_no,
            notice['ip'],
            ok,
            err,
        )
        await _record_auto_renew_patrol_log(
            order.id,
            batch_id=batch_id,
            ip=notice['ip'],
            ok=ok,
            error=err,
            balance_change=balance_change,
            renewed_order_id=renewed_order_id,
        )
        if not ok:
            await _enqueue_auto_renew_retry(order.id, ip=notice['ip'], error=err, balance_change=balance_change)
        results_by_user[order.user_id].append({
            'order_id': renewed_order_id,
            'original_order_id': order.id,
            'ip': notice['ip'],
            'ok': ok,
            'error': err,
            'balance_change': balance_change,
        })
    target_result = await _send_auto_renew_execution_target_notices(notify_target, results_by_user)
    if target_result.get('sent'):
        await _mark_many_notice_sent(target_result.get('failure_order_ids') or [], 'auto_renew_failure_notice_sent_at')


async def lifecycle_tick(notify=None, notify_target=None, defer_destructive_seconds: int = 0):
    due = await _get_due_orders()
    migration_due_orders = await _get_migration_due_orders()
    orphan_asset_delete_due = await _get_orphan_asset_delete_due()
    unattached_static_ip_delete_due = await _get_unattached_static_ip_delete_due()
    logger.info(
        '云服务器生命周期扫描：续费提醒=%s 自动续费预提醒=%s 自动续费=%s 删机提醒=%s IP回收提醒=%s 到期标记=%s 待关机=%s 待删机=%s 待回收IP=%s 迁移旧机待删=%s 孤儿资源待删=%s 续费提醒天数=%s 调试重复提醒=%s',
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

    await _process_auto_renew_retry_tasks(notify_target)

    if defer_destructive_seconds and (due['suspend'] or due['delete'] or migration_due_orders or orphan_asset_delete_due):
        deferred_at = await _defer_startup_lifecycle_actions(due, migration_due_orders, orphan_asset_delete_due, defer_destructive_seconds)
        logger.warning(
            'CLOUD_STARTUP_DEFER_DONE suspend=%s delete=%s migration_delete=%s orphan_asset_delete=%s deferred_suspend_until=%s deferred_delete_until=%s',
            len(due['suspend']), len(due['delete']), len(migration_due_orders), len(orphan_asset_delete_due), deferred_at['suspend'], deferred_at['delete'],
        )
        due['suspend'] = []
        due['delete'] = []
        migration_due_orders = []
        orphan_asset_delete_due = []

    for user_id, orders in _group_orders_by_user(due['renew_notice']).items():
        payload = await _renew_notice_batch_payload([order.id for order in orders])
        if notify and payload['text'] and await _user_can_receive_cloud_notice(user_id):
            _log_cloud_notice('renew_notice_batch', orders[0], {'ip': f'{payload["count"]} 个IP'}, payload['text'], 'cloud_expiry_actions')
            sent = await _send_logged_cloud_notice('renew_notice_batch', notify, user_id, payload['text'], cloud_expiry_actions(payload['first_order_id']) if payload['count'] == 1 else None, order=orders[0], notice={'ip': f"{payload['count']} 个IP"}, batch_id=_notice_batch_id('renew_notice_batch', *(payload.get('order_ids') or [])), is_batch=True, extra={'order_ids': payload.get('order_ids') or []})
            if sent:
                for order_id in payload.get('order_ids') or []:
                    await _mark_notice_sent(order_id, 'renew_notice_sent_at')

    for order in due['expire']:
        notice = await _cloud_expiry_notice_payload(order.id)
        if not notice.get('valid'):
            continue
        await _mark_expiring(order.id)

    for order in due['suspend']:
        if not _is_cloud_suspend_time():
            logger.warning('跳过云服务器关机：不在允许执行时间窗口 订单ID=%s 订单号=%s 计划关机=%s 当前时间=%s', order.id, order.order_no, order.suspend_at, timezone.now())
            continue
        can_suspend, skip_reason, checked_order = await _can_execute_suspend(order.id)
        if not can_suspend:
            logger.warning('跳过云服务器关机：安全检查未通过 订单ID=%s 订单号=%s 原因=%s 当前时间=%s', order.id, order.order_no, skip_reason, timezone.now())
            if checked_order:
                await _record_lifecycle_action_failed(checked_order.id, 'suspend_skipped', skip_reason)
            continue
        order = checked_order
        result = await _run_cloud_action_with_timeout(_stop_instance(order), action='AWS 实例关机', target=order.order_no)
        note = _action_note(result)
        if _action_ok(result):
            notice = await _cloud_expiry_notice_payload(order.id)
            if not notice.get('valid'):
                continue
            await _mark_suspended(order.id, note)
        else:
            await _record_lifecycle_action_failed(order.id, 'suspend_failed', note)

    for order in due['delete']:
        if not _is_cloud_delete_safe_time():
            logger.warning('跳过云服务器删机：不在允许执行时间窗口 订单ID=%s 订单号=%s 计划删机=%s 当前时间=%s', order.id, order.order_no, order.delete_at, timezone.now())
            continue
        result = await _run_cloud_action_with_timeout(_delete_instance(order), action='AWS 实例删除', target=order.order_no)
        note = _action_note(result)
        if _action_ok(result):
            if order.status == 'failed':
                await _mark_deleted(order.id, note or '失败新实例已按计划自动删除。')
                continue
            notice = await _cloud_expiry_notice_payload(order.id)
            if not notice.get('valid'):
                continue
            await _mark_deleted(order.id, note)
        else:
            await _record_lifecycle_action_failed(order.id, 'delete_failed', note)

    for order in due['recycle']:
        notice = await _cloud_expiry_notice_payload(order.id)
        if not notice.get('valid'):
            continue
        result = await _run_cloud_action_with_timeout(_release_order_static_ip(order), action='AWS 固定 IP 释放', target=order.order_no)
        note = _action_note(result)
        if _action_ok(result):
            await _mark_recycled(order.id, note)
        else:
            await _record_lifecycle_action_failed(order.id, 'recycle_failed', note)

    for order in migration_due_orders:
        if not _is_cloud_delete_safe_time():
            logger.warning('跳过迁移旧服务器删机：不在允许执行时间窗口 订单ID=%s 订单号=%s 迁移清理时间=%s 当前时间=%s', order.id, order.order_no, order.migration_due_at, timezone.now())
            continue
        result = await _run_cloud_action_with_timeout(_delete_replaced_server(order), action='AWS 迁移旧实例删除', target=order.order_no)
        note = _action_note(result)
        if _action_ok(result):
            notice = await _cloud_expiry_notice_payload(order.id)
            if not notice.get('valid'):
                continue
            await _mark_replaced_order_deleted(order.id, note)
        else:
            await _record_lifecycle_action_failed(order.id, 'delete_failed', note)

    for asset in orphan_asset_delete_due:
        if not _is_cloud_delete_safe_time():
            logger.warning('跳过孤儿云资源删除：不在允许执行时间窗口 资源ID=%s IP=%s 实际到期=%s 当前时间=%s', asset.id, asset.public_ip, asset.actual_expires_at, timezone.now())
            continue
        result = await _run_cloud_action_with_timeout(_delete_orphan_asset_instance(asset), action='AWS 无订单实例删除', target=str(asset.id))
        note = _action_note(result)
        if _action_ok(result):
            updated = await _mark_orphan_asset_deleted(asset.id, note)
            logger.info('孤儿云资源已删除：资源ID=%s IP=%s 云厂商=%s 地区=%s 备注=%s', updated.id, updated.previous_public_ip, updated.provider, updated.region_code, note)
        elif asset.provider != 'aws_lightsail':
            updated = await _mark_orphan_asset_deleted(asset.id, note)
            logger.info('孤儿云资源已本地清理：资源ID=%s IP=%s 云厂商=%s 地区=%s 备注=%s', updated.id, updated.previous_public_ip, updated.provider, updated.region_code, note)
        else:
            logger.warning('孤儿云资源删除失败：资源ID=%s IP=%s 云厂商=%s 地区=%s 备注=%s', asset.id, asset.public_ip, asset.provider, asset.region_code, note)

    for asset in unattached_static_ip_delete_due:
        if not _is_cloud_delete_safe_time():
            logger.warning('跳过未附加固定IP释放：不在允许执行时间窗口 资源ID=%s IP=%s 实际到期=%s 当前时间=%s', asset.id, asset.public_ip, asset.actual_expires_at, timezone.now())
            continue
        result = await _run_cloud_action_with_timeout(_release_unattached_static_ip(asset), action='AWS 未附加固定 IP 释放', target=str(asset.id))
        note = _action_note(result)
        if _action_ok(result):
            updated = await _mark_unattached_static_ip_deleted(asset.id, note)
            logger.info('未附加固定IP已释放：资源ID=%s IP=%s 云厂商=%s 地区=%s 备注=%s', updated.id, updated.previous_public_ip, updated.provider, updated.region_code, note)
        else:
            logger.warning('未附加固定IP释放失败：资源ID=%s IP=%s 云厂商=%s 地区=%s 备注=%s', asset.id, asset.public_ip, asset.provider, asset.region_code, note)
