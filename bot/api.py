"""bot 域后台 API。"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import time
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from urllib.parse import quote

from asgiref.sync import async_to_sync

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.models import Session
from django.db import ProgrammingError, transaction
from django.db.models import Q, CharField, Count, Max, Sum
from django.db.models.functions import Cast
from django.http import JsonResponse
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from bot.models import BotOperationLog, TelegramChatArchive, TelegramChatMessage, TelegramGroupFilter, TelegramLoginAccount, TelegramUser
from bot.services import _get_or_create_user_sync
from cloud.lifecycle import _delete_instance, _delete_orphan_asset_instance, _get_due_orders, _get_orphan_asset_delete_due, _is_cloud_delete_safe_time, _mark_deleted, _mark_orphan_asset_deleted, _mark_unattached_static_ip_deleted, _record_lifecycle_action_failed, _release_unattached_static_ip, _shutdown_enabled_for_order
from cloud.models import AddressMonitor, CloudAsset, CloudIpLog, CloudServerOrder
from core.models import CloudAccountConfig, SiteConfig
from core.button_config import init_button_config, load_button_config, save_button_config
from core.runtime_config import CONFIG_HELP, SENSITIVE_CONFIG_KEYS, get_runtime_config
from core.trongrid import parse_trongrid_api_keys
from core.texts import TEXT_GROUPS, all_text_keys, init_texts, text_default, text_description
from orders.models import BalanceLedger, Order, Product, Recharge


@ensure_csrf_cookie
@require_GET
def csrf(request):
    return _ok({'csrf': True})


def _decimal_to_str(value, places=3):
    if value is None:
        value = Decimal('0')
    elif not isinstance(value, Decimal):
        value = Decimal(str(value))
    if places is None:
        places = 3
    places = min(int(places), 3)
    quantizer = Decimal('1').scaleb(-places)
    value = value.quantize(quantizer, rounding=ROUND_DOWN)
    text = format(value, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def _ok(data):
    return JsonResponse({'code': 0, 'data': data, 'message': 'ok'})


def _error(message, code=1, status=400):
    return JsonResponse({'code': code, 'message': message, 'data': None}, status=status)


def _iso(value):
    if not value:
        return None
    return timezone.localtime(value).isoformat() if timezone.is_aware(value) else value.isoformat()


DASHBOARD_SESSION_IDLE_SECONDS = 60 * 60


def _staff_required(user):
    return user.is_active and (user.is_staff or user.is_superuser)


def _refresh_dashboard_session(request, session_key: str | None = None):
    session = getattr(request, 'session', None)
    if session is not None:
        session.set_expiry(DASHBOARD_SESSION_IDLE_SECONDS)
    if session_key:
        Session.objects.filter(session_key=session_key, expire_date__gt=timezone.now()).update(
            expire_date=timezone.now() + timezone.timedelta(seconds=DASHBOARD_SESSION_IDLE_SECONDS)
        )


def _session_token_for_request(request) -> str:
    if not request.session.session_key:
        request.session.save()
    return f'session-{request.session.session_key}'


def _dashboard_session_payload(request):
    return {
        'accessToken': _session_token_for_request(request),
        'expiresIn': max(0, request.session.get_expiry_age()),
    }


def _json_payload(request):
    try:
        return json.loads(request.body.decode('utf-8') or '{}')
    except Exception:
        return {}


def _normalize_totp_secret(secret: str) -> str:
    return ''.join(ch for ch in str(secret or '').upper() if ch.isalnum()).rstrip('=')


def _totp_secret():
    return _normalize_totp_secret(get_runtime_config('dashboard_totp_secret', ''))


def _generate_totp_secret():
    alphabet = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ234567'
    return ''.join(secrets.choice(alphabet) for _ in range(32))


def _totp_otpauth_url(secret: str, username: str = 'admin'):
    issuer = 'Shop Admin'
    account = username or 'admin'
    label = f'{quote(issuer, safe="")}:{quote(account, safe="")}'
    normalized_secret = _normalize_totp_secret(secret)
    return (
        'otpauth://totp/'
        f'{label}?secret={quote(normalized_secret, safe="")}&issuer={quote(issuer, safe="")}&algorithm=SHA1&digits=6&period=30'
    )


def _totp_code(secret: str, counter: int) -> str:
    secret = _normalize_totp_secret(secret)
    padding = '=' * ((8 - len(secret) % 8) % 8)
    key = base64.b32decode((secret + padding).upper(), casefold=True)
    digest = hmac.new(key, struct.pack('>Q', counter), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f'{value % 1_000_000:06d}'


def _verify_totp_token(token: str, secret: str) -> bool:
    token = ''.join(ch for ch in str(token or '') if ch.isdigit())
    secret = _normalize_totp_secret(secret)
    if len(token) != 6 or not secret:
        return False
    try:
        current_counter = int(time.time()) // 30
        for drift in (-1, 0, 1):
            if hmac.compare_digest(_totp_code(secret, current_counter + drift), token):
                return True
    except (binascii.Error, ValueError):
        return False
    return False


def _user_from_bearer_session(token: str):
    user, _session_key = _user_and_session_key_from_bearer_session(token)
    return user


def _user_and_session_key_from_bearer_session(token: str):
    prefix = 'session-'
    if not token.startswith(prefix):
        return None, None
    session_key = token[len(prefix):].strip()
    if not session_key or session_key.isdigit():
        return None, None
    session = Session.objects.filter(session_key=session_key, expire_date__gt=timezone.now()).first()
    if not session:
        return None, None
    data = session.get_decoded()
    raw_user_id = data.get('_auth_user_id')
    if not raw_user_id:
        return None, None
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=raw_user_id, is_active=True).first()
    return user, session_key if user else None


def _authenticate_dashboard_request(request):
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        if _staff_required(user):
            _refresh_dashboard_session(request)
            return user
        return None
    auth_header = request.headers.get('Authorization') or ''
    prefix = 'Bearer '
    if not auth_header.startswith(prefix):
        return None
    user, session_key = _user_and_session_key_from_bearer_session(auth_header[len(prefix):].strip())
    if user and _staff_required(user):
        request.user = user
        _refresh_dashboard_session(request, session_key=session_key)
        return user
    return None


def dashboard_login_required(view_func):
    def wrapped(request, *args, **kwargs):
        if not _authenticate_dashboard_request(request):
            return _error('请先登录', status=401)
        return view_func(request, *args, **kwargs)
    return wrapped


def _status_label(status, choices=()):
    hardcoded = {
        CloudAsset.STATUS_RUNNING: '运行中',
        CloudAsset.STATUS_PENDING: '等待中',
        CloudAsset.STATUS_STARTING: '启动中',
        CloudAsset.STATUS_STOPPING: '停止中',
        CloudAsset.STATUS_STOPPED: '已关机',
        CloudAsset.STATUS_SUSPENDED: '已停机',
        CloudAsset.STATUS_TERMINATING: '终止中',
        CloudAsset.STATUS_TERMINATED: '已终止',
        CloudAsset.STATUS_DELETING: '删除中',
        CloudAsset.STATUS_DELETED: '已删除',
        CloudAsset.STATUS_EXPIRED: '已过期',
        CloudAsset.STATUS_EXPIRED_GRACE: '到期延停',
        CloudAsset.STATUS_UNKNOWN: '未知状态',
    }
    mapping = dict(choices or [])
    return hardcoded.get(status) or mapping.get(status, status or '-')


def _runtime_int(key: str, default: int) -> int:
    try:
        return max(int(str(get_runtime_config(key, str(default)) or default).strip()), 0)
    except (TypeError, ValueError):
        return default


def _runtime_time(key: str, default: str = '15:00') -> tuple[int, int]:
    try:
        raw = str(get_runtime_config(key, default) or default).strip()
        hour_text, minute_text = raw.split(':', 1)
        return min(max(int(hour_text), 0), 23), min(max(int(minute_text), 0), 59)
    except Exception:
        hour_text, minute_text = default.split(':', 1)
        return int(hour_text), int(minute_text)


def _with_runtime_time(value, key: str, default: str = '15:00'):
    if not value:
        return None
    hour, minute = _runtime_time(key, default)
    local_value = timezone.localtime(value) if timezone.is_aware(value) else value
    local_value = local_value.replace(hour=hour, minute=minute, second=0, microsecond=0)
    return local_value if timezone.is_aware(local_value) else timezone.make_aware(local_value, timezone.get_current_timezone())


def _next_runtime_time(key: str, default: str = '15:00', now=None):
    now = now or timezone.now()
    hour, minute = _runtime_time(key, default)
    local_now = timezone.localtime(now)
    next_at = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if next_at <= local_now:
        next_at += timezone.timedelta(days=1)
    return next_at if timezone.is_aware(next_at) else timezone.make_aware(next_at, timezone.get_current_timezone())


def _cloud_account_labels(item):
    account = getattr(item, 'cloud_account', None)
    account_name = getattr(account, 'name', '') or ''
    external_account_id = getattr(account, 'external_account_id', '') or getattr(item, 'account_label', '') or ''
    return account_name, external_account_id


def _asset_is_unattached_ip(asset):
    return bool(
        asset
        and (
            ('未附加' in (asset.provider_status or ''))
            or ('未附加IP' in (asset.note or ''))
            or ('未附加固定IP' in (asset.note or ''))
        )
    )


def _active_cloud_asset_queryset():
    active_account_ids = list(CloudAccountConfig.objects.filter(is_active=True).values_list('id', flat=True))
    active_account_labels = list(
        CloudAccountConfig.objects.filter(is_active=True)
        .exclude(external_account_id__isnull=True)
        .exclude(external_account_id='')
        .values_list('external_account_id', flat=True)
    )
    inactive_account_labels = list(
        CloudAccountConfig.objects.filter(is_active=False)
        .exclude(external_account_id__isnull=True)
        .exclude(external_account_id='')
        .values_list('external_account_id', flat=True)
    )
    return (
        CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER, is_active=True)
        .exclude(status__in=[
            CloudAsset.STATUS_DELETED,
            CloudAsset.STATUS_DELETING,
            CloudAsset.STATUS_TERMINATED,
            CloudAsset.STATUS_TERMINATING,
        ])
        .exclude(Q(cloud_account__is_active=False) | Q(account_label__in=inactive_account_labels))
        .filter(
            Q(cloud_account_id__in=active_account_ids)
            | Q(account_label__in=active_account_labels)
            | Q(cloud_account__isnull=True, account_label__isnull=True)
            | Q(cloud_account__isnull=True, account_label='')
            | Q(cloud_account__isnull=True, account_label__in=active_account_labels)
            | Q(cloud_account_id__isnull=False, cloud_account__is_active=True)
        )
    )


def _fmt_dashboard_dt(value):
    if not value:
        return '-'
    try:
        return timezone.localtime(value).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(value)


def _extract_failure_reason(note):
    text = str(note or '').strip()
    if not text:
        return '-'
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if any(keyword in line for keyword in ['失败', '异常', '错误', 'error', 'Error', 'ERROR']):
            return line[:300]
    return '-'


def _compact_failure_reason(note, *, fallback='失败/跳过'):
    text = _cloud_ip_trace_note_newest_first(note)
    if not text:
        return fallback
    normalized = text.lower()
    business_rules = [
        (['关闭关机计划', '关机计划关闭', '已关闭关机', '停用关机计划'], '账号已关闭关机计划'),
        (['关闭删机计划', '删机计划关闭', '关闭删除计划', '删除计划关闭', '停用删机计划'], '账号已关闭删除计划'),
        (['delete_at 未到', '删除时间未到', '删机时间未到', '服务器删除时间未到'], '未到服务器删除时间'),
        (['不在后台配置', '不在删除执行时间窗口', 'safe time'], '不在服务器删除执行时间窗口'),
        (['not found', '不存在', 'does not exist', 'not exist'], '云端资源不存在'),
        (['unauthorized', 'forbidden', 'accessdenied', 'access denied', '权限不足', '拒绝访问'], '云账号权限不足'),
        (['timeout', 'timed out', '超时'], '云接口请求超时'),
        (['throttl', 'rate exceeded', '限流', '频率'], '云接口限流'),
        (['invalid state', '状态不允许', '当前状态'], '云服务器状态不允许删除'),
        (['未配置', '缺少', 'missing'], '云账号或实例信息不完整'),
    ]
    for keywords, label in business_rules:
        if any(keyword.lower() in normalized for keyword in keywords):
            return label
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), '')
    for source in [first_line, text]:
        match = re.search(r'失败原因：([^；\n]+)', source)
        if match:
            reason = match.group(1).strip()
            if not reason or reason == '-':
                return fallback
            return reason[:180]
    for source in [first_line, text]:
        for pattern in [
            r'(?:错误|异常|失败)[:：]\s*([^；\n]+)',
            r'((?:AWS|Lightsail|请求|接口|实例|服务器|固定IP|权限|资源|删除|停止)[^；\n]*(?:失败|异常|错误|不存在|拒绝|超时)[^；\n]*)',
        ]:
            match = re.search(pattern, source, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()[:180]
    return fallback


def _shutdown_execution_note(*, status_label, is_success, executed_at, action, failure_reason):
    return '；'.join([
        f'执行状态：{status_label or "-"}',
        f'是否成功：{"成功" if is_success else "失败"}',
        f'执行时间：{_fmt_dashboard_dt(executed_at)}',
        f'执行内容：{action or "-"}',
        f'失败原因：{failure_reason or "-"}',
    ])


def _cloud_ip_trace_note_newest_first(note):
    text = str(note or '').strip()
    if not text:
        return ''
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return text

    def _line_time(line):
        match = re.search(r'执行时间：(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        return match.group(1) if match else ''

    first_time = _line_time(lines[0])
    last_time = _line_time(lines[-1])
    if first_time and last_time and first_time < last_time:
        lines = list(reversed(lines))
    return '\n'.join(lines)


def _cloud_ip_trace_logged_at(note, fallback=None):
    text = _cloud_ip_trace_note_newest_first(note)
    first_line = next((line for line in text.splitlines() if line.strip()), '')
    match = re.search(r'执行时间：(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', first_line)
    if not match:
        return fallback
    parsed = parse_datetime(match.group(1))
    if parsed is None:
        return fallback
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _cloud_ip_trace_for_asset_or_order(asset=None, order=None):
    lookup = CloudIpLog.objects.select_related('order', 'asset', 'user').all()
    if asset is not None:
        is_unattached_static_asset = (
            '未附加固定IP' in str(getattr(asset, 'provider_status', '') or '')
            or 'StaticIp' in str(getattr(asset, 'provider_resource_id', '') or '')
            or not getattr(asset, 'instance_id', None)
        )
        if is_unattached_static_asset:
            exact = lookup.filter(Q(asset=asset) | Q(asset_name=asset.asset_name, public_ip=asset.public_ip)).order_by('-id').first()
            if exact:
                return exact
        conditions = Q(asset=asset)
        if getattr(asset, 'order_id', None):
            conditions |= Q(order_id=asset.order_id)
        if getattr(asset, 'public_ip', None):
            conditions |= Q(public_ip=asset.public_ip) | Q(previous_public_ip=asset.public_ip)
        lookup = lookup.filter(conditions)
    elif order is not None:
        conditions = Q(order=order)
        if getattr(order, 'public_ip', None):
            conditions |= Q(public_ip=order.public_ip) | Q(previous_public_ip=order.public_ip)
        if getattr(order, 'previous_public_ip', None):
            conditions |= Q(public_ip=order.previous_public_ip) | Q(previous_public_ip=order.previous_public_ip)
        lookup = lookup.filter(conditions)
    else:
        return None
    return lookup.order_by('-id').first()


def _shutdown_log_items(limit=100):
    cutoff = timezone.now() - timezone.timedelta(days=7)
    suspend_days = _runtime_int('cloud_suspend_after_days', 3)
    delete_days = _runtime_int('cloud_delete_after_days', 0)

    items = []
    assets = list(
        _active_cloud_asset_queryset()
        .filter(Q(actual_expires_at__isnull=False) | Q(order__service_expires_at__isnull=False))
        .order_by('actual_expires_at', '-updated_at')[:500]
    )
    seen_trace_ids = set()
    for asset in assets:
        if _asset_is_unattached_ip(asset) and asset.status in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_TERMINATING}:
            continue
        order = asset.order if asset.order_id and asset.order else None
        trace = _cloud_ip_trace_for_asset_or_order(asset=asset, order=order)
        expires_at = getattr(order, 'service_expires_at', None) or asset.actual_expires_at
        user_display_name, username_label = _telegram_user_labels(asset.user or (order.user if order else None))
        account_name, external_account_id = _cloud_account_labels(asset)
        if not external_account_id and asset.order_id and asset.order:
            order_account_name, order_external_account_id = _cloud_account_labels(asset.order)
            account_name = account_name or order_account_name
            external_account_id = order_external_account_id
        if asset.provider == 'aliyun_simple' or not expires_at:
            suspend_at = None
            delete_at = None
        elif order and (order.suspend_at or order.delete_at):
            suspend_at = order.suspend_at
            delete_at = order.delete_at
        else:
            suspend_at = _with_runtime_time(expires_at + timezone.timedelta(days=suspend_days), 'cloud_suspend_time')
            delete_at = _with_runtime_time(suspend_at + timezone.timedelta(days=delete_days), 'cloud_delete_time')
            if delete_at and suspend_at and delete_at < suspend_at:
                delete_at = suspend_at
        status = trace.event_type if trace else asset.status
        status_label = _status_label(status, CloudIpLog.EVENT_CHOICES if trace else CloudAsset.STATUS_CHOICES)
        is_terminal_failure = asset.status in {CloudAsset.STATUS_UNKNOWN, CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED}
        is_success = bool(asset.status in {CloudAsset.STATUS_RUNNING, 'completed'} and not is_terminal_failure)
        if trace:
            note = _cloud_ip_trace_note_newest_first(trace.note)
            logged_at = _cloud_ip_trace_logged_at(trace.note, trace.created_at)
            seen_trace_ids.add(trace.id)
        else:
            if asset.status in {CloudAsset.STATUS_STOPPED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATING}:
                action = '到期关机/删机流程执行中'
            elif asset.status in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED}:
                action = '到期删机已执行'
            elif suspend_at and timezone.now() >= suspend_at:
                action = '到期关机待执行或已执行'
            else:
                action = '等待到期关机计划'
            source_note = asset.note or getattr(order, 'provision_note', '') or ''
            note = _shutdown_execution_note(
                status_label=status_label,
                is_success=is_success,
                executed_at=asset.updated_at,
                action=action,
                failure_reason=_extract_failure_reason(source_note),
            )
            logged_at = asset.updated_at
        items.append({
            'id': f'trace-{trace.id}' if trace else f'asset-{asset.id}',
            'order_id': order.id if order else asset.order_id,
            'asset_id': asset.id,
            'order_no': (order.order_no if order else '') or asset.asset_name or asset.instance_id or f'asset-{asset.id}',
            'order_detail_path': f'/admin/cloud-orders/{order.id}' if order else '',
            'asset_detail_path': f'/admin/cloud-assets/{asset.id}',
            'detail_path': f'/admin/cloud-orders/{order.id}' if order else f'/admin/cloud-assets/{asset.id}',
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': (trace.public_ip if trace else None) or asset.public_ip or asset.previous_public_ip or '',
            'provider': (trace.provider if trace else None) or asset.provider or '',
            'provider_label': _provider_label((trace.provider if trace else None) or asset.provider),
            'cloud_account_id': asset.cloud_account_id or (order.cloud_account_id if order else None),
            'cloud_account_name': account_name,
            'external_account_id': external_account_id,
            'account_label': asset.account_label or (order.account_label if order else '') or '',
            'status': status,
            'status_label': status_label,
            'service_expires_at': expires_at,
            'suspend_at': suspend_at,
            'delete_at': delete_at,
            'note': note,
            'logged_at': logged_at,
        })

    history_traces = CloudIpLog.objects.select_related('order', 'asset', 'user').filter(
        Q(note__icontains='执行内容：AWS 实例已执行关机')
        | Q(note__icontains='执行内容：实例已删除')
        | Q(note__icontains='执行内容：固定 IP 保留期结束')
    ).order_by('-id')[:300]
    for trace in history_traces:
        if trace.id in seen_trace_ids:
            continue
        order = trace.order
        asset = trace.asset
        user_display_name, username_label = _telegram_user_labels(trace.user or (order.user if order else None))
        account_name, external_account_id = _cloud_account_labels(asset or order or trace)
        suspend_at = getattr(order, 'suspend_at', None)
        delete_at = getattr(order, 'delete_at', None)
        expires_at = getattr(order, 'service_expires_at', None) or getattr(asset, 'actual_expires_at', None)
        note = _cloud_ip_trace_note_newest_first(trace.note)
        logged_at = _cloud_ip_trace_logged_at(trace.note, trace.created_at)
        items.append({
            'id': f'trace-{trace.id}',
            'order_id': trace.order_id,
            'asset_id': trace.asset_id,
            'order_no': trace.order_no or getattr(asset, 'asset_name', '') or getattr(order, 'server_name', '') or f'trace-{trace.id}',
            'order_detail_path': f'/admin/cloud-orders/{trace.order_id}' if trace.order_id else '',
            'asset_detail_path': f'/admin/cloud-assets/{trace.asset_id}' if trace.asset_id else '',
            'detail_path': f'/admin/cloud-orders/{trace.order_id}' if trace.order_id else (f'/admin/cloud-assets/{trace.asset_id}' if trace.asset_id else ''),
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': trace.public_ip or trace.previous_public_ip or '',
            'provider': trace.provider or '',
            'provider_label': _provider_label(trace.provider),
            'cloud_account_id': getattr(asset, 'cloud_account_id', None) or getattr(order, 'cloud_account_id', None),
            'cloud_account_name': account_name,
            'external_account_id': external_account_id,
            'account_label': getattr(asset, 'account_label', '') or getattr(order, 'account_label', '') or '',
            'status': trace.event_type,
            'status_label': _status_label(trace.event_type, CloudIpLog.EVENT_CHOICES),
            'service_expires_at': expires_at,
            'suspend_at': suspend_at,
            'delete_at': delete_at,
            'note': note,
            'logged_at': logged_at,
        })

    deduped = {}
    for item in items:
        deduped[item['id']] = item
    items = list(deduped.values())

    def sort_key(item):
        suspend_at = item['suspend_at']
        sort_at = item['logged_at'] or item['service_expires_at']
        is_old_shutdown = bool(suspend_at and suspend_at < cutoff)
        timestamp = sort_at.timestamp() if sort_at else float('inf')
        return (1 if is_old_shutdown else 0, -timestamp if is_old_shutdown else -timestamp, str(item['id']))

    sorted_items = sorted(items, key=sort_key)[:limit]
    return [
        {
            **item,
            'service_expires_at': _iso(item['service_expires_at']),
            'suspend_at': _iso(item['suspend_at']),
            'delete_at': _iso(item['delete_at']),
            'is_old_shutdown': bool(item['suspend_at'] and item['suspend_at'] < cutoff),
            'logged_at': _iso(item['logged_at']),
        }
        for item in sorted_items
    ]


def _cloud_asset_deleted_or_missing_q():
    return (
        Q(status__in=[
            CloudAsset.STATUS_DELETED,
            CloudAsset.STATUS_DELETING,
            CloudAsset.STATUS_TERMINATED,
            CloudAsset.STATUS_TERMINATING,
        ])
        | Q(provider_status__icontains='云上未找到')
        | Q(provider_status__icontains='已到期删除')
        | Q(provider_status__icontains='已删除')
        | Q(note__icontains='云上不存在')
        | Q(note__icontains='已标记删除')
    )


def _unattached_ip_delete_history_q():
    terminal_q = Q(event_type__in=[CloudIpLog.EVENT_DELETED, CloudIpLog.EVENT_RECYCLED])
    explicit_note_q = (
        Q(note__icontains='未附加固定IP')
        | Q(note__icontains='未附加 IP')
        | Q(note__icontains='未附加IP')
        | Q(note__icontains='AWS 同步删除未附加固定 IP')
        | Q(note__icontains='IP校验发现云上不存在，已标记删除')
        | Q(note__icontains='固定 IP 已释放')
        | Q(note__icontains='固定 IP 云端已不存在')
        | Q(note__icontains='release_static_ip')
        | Q(note__icontains='真机测试：未附加IP删除')
    )
    asset_q = (
        Q(asset__provider_status__icontains='未附加')
        | Q(asset__provider_status__icontains='固定IP')
        | Q(asset__note__icontains='未附加固定IP')
        | Q(asset__provider_resource_id__icontains='StaticIp')
    ) & (Q(asset__instance_id__isnull=True) | Q(asset__instance_id=''))
    return terminal_q & (explicit_note_q | asset_q)


def _unattached_ip_delete_items(limit=50):
    now = timezone.now()
    limit = max(1, min(int(limit or 50), 1000))
    delete_days = _runtime_int('cloud_unattached_ip_delete_after_days', 15)
    assets = list(
        CloudAsset.objects.select_related('user', 'cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER)
        .filter(Q(provider_status__icontains='未附加') | Q(note__icontains='未附加IP') | Q(note__icontains='未附加固定IP'))
        .filter(Q(instance_id__isnull=True) | Q(instance_id=''))
        .exclude(_cloud_asset_deleted_or_missing_q())
        .order_by('actual_expires_at', 'created_at', '-updated_at')[:limit]
    )
    items = []
    seen_trace_ids = set()
    active_unattached_ips = {str(asset.public_ip or '').strip() for asset in assets if str(asset.public_ip or '').strip()}
    for asset in assets:
        user_display_name, username_label = _telegram_user_labels(asset.user)
        if asset.actual_expires_at:
            delete_at = asset.actual_expires_at
        else:
            base_at = asset.updated_at or asset.created_at or now
            delete_at = _with_runtime_time(base_at + timezone.timedelta(days=delete_days), 'cloud_unattached_ip_delete_time')
        trace = _cloud_ip_trace_for_asset_or_order(asset=asset)
        if trace:
            note = _cloud_ip_trace_note_newest_first(trace.note)
            logged_at = _cloud_ip_trace_logged_at(trace.note, trace.created_at)
            seen_trace_ids.add(trace.id)
        else:
            note = asset.note or ''
            logged_at = asset.updated_at
        asset_name = asset.asset_name or getattr(asset, 'static_ip_name', '') or asset.instance_id or f'asset-{asset.id}'
        items.append({
            'id': asset.id,
            'asset_name': asset_name,
            'asset_detail_path': f'/admin/cloud-assets/{asset.id}',
            'detail_path': f'/admin/cloud-assets/{asset.id}',
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': (trace.public_ip if trace else None) or asset.public_ip or asset.previous_public_ip or '',
            'provider_status': asset.provider_status or (_status_label(trace.event_type, CloudIpLog.EVENT_CHOICES) if trace else ''),
            'service_expires_at': _iso(asset.actual_expires_at),
            'delete_at': _iso(delete_at),
            'logged_at': _iso(logged_at),
            'note': note,
            'is_overdue': bool(delete_at and delete_at <= now),
            'is_history': False,
        })

    history_traces = CloudIpLog.objects.select_related('asset', 'order', 'user').filter(
        _unattached_ip_delete_history_q()
    ).order_by('-id')[:limit]
    for trace in history_traces:
        if trace.id in seen_trace_ids:
            continue
        trace_ip = str(trace.public_ip or trace.previous_public_ip or '').strip()
        if trace_ip and trace_ip in active_unattached_ips:
            continue
        asset = trace.asset
        order = trace.order
        user_display_name, username_label = _telegram_user_labels(trace.user or getattr(asset, 'user', None))
        asset_name = trace.asset_name or getattr(order, 'static_ip_name', '') or getattr(asset, 'asset_name', '') or trace.instance_id or f'trace-{trace.id}'
        logged_at = _cloud_ip_trace_logged_at(trace.note, trace.created_at)
        delete_at = logged_at or getattr(order, 'ip_recycle_at', None) or getattr(asset, 'actual_expires_at', None)
        items.append({
            'id': trace.id,
            'asset_name': asset_name,
            'asset_detail_path': f'/admin/cloud-assets/{trace.asset_id}' if trace.asset_id else '',
            'detail_path': f'/admin/cloud-orders/{trace.order_id}' if trace.order_id else (f'/admin/cloud-assets/{trace.asset_id}' if trace.asset_id else ''),
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': trace.public_ip or trace.previous_public_ip or '',
            'provider_status': _status_label(trace.event_type, CloudIpLog.EVENT_CHOICES),
            'service_expires_at': _iso(getattr(asset, 'actual_expires_at', None) or getattr(order, 'service_expires_at', None)),
            'delete_at': _iso(delete_at),
            'logged_at': _iso(logged_at),
            'note': _cloud_ip_trace_note_newest_first(trace.note),
            'is_overdue': True,
            'is_history': True,
        })
    return sorted(items, key=lambda item: (0 if item['is_overdue'] else 1, item.get('delete_at') or '', str(item['id'])))[:limit]


def _region_label(region_code, region_name=None):
    mapping = {
        'cn-qingdao': '华北1（青岛）',
        'cn-beijing': '华北2（北京）',
        'cn-zhangjiakou': '华北3（张家口）',
        'cn-huhehaote': '华北5（呼和浩特）',
        'cn-wulanchabu': '华北6（乌兰察布）',
        'cn-hangzhou': '华东1（杭州）',
        'cn-shanghai': '华东2（上海）',
        'cn-nanjing': '华东5（南京）',
        'cn-fuzhou': '华东6（福州）',
        'cn-shenzhen': '华南1（深圳）',
        'cn-heyuan': '华南2（河源）',
        'cn-guangzhou': '华南3（广州）',
        'cn-chengdu': '西南1（成都）',
        'cn-hongkong': '中国香港',
        'ap-southeast-1': '新加坡',
        'ap-southeast-2': '澳大利亚（悉尼）',
        'ap-southeast-3': '马来西亚（吉隆坡）',
        'ap-southeast-5': '印度尼西亚（雅加达）',
        'ap-southeast-6': '菲律宾（马尼拉）',
        'ap-southeast-7': '泰国（曼谷）',
        'ap-northeast-1': '日本（东京）',
        'ap-northeast-2': '韩国（首尔）',
        'us-west-1': '美国西部（硅谷）',
        'us-east-1': '美国东部（弗吉尼亚）',
        'eu-central-1': '德国（法兰克福）',
        'eu-west-1': '英国（伦敦）',
        'me-east-1': '阿联酋（迪拜）',
        'us-east-2': '美国东部（俄亥俄）',
        'us-west-2': '美国西部（俄勒冈）',
        'af-south-1': '非洲（开普敦）',
        'ap-east-1': '亚太（香港）',
        'ap-south-1': '亚太（孟买）',
        'ap-south-2': '亚太（海得拉巴）',
        'ap-southeast-4': '亚太（墨尔本）',
        'ap-southeast-8': '亚太（台北）',
        'ap-southeast-9': '亚太（新西兰）',
        'ap-northeast-3': '亚太（大阪）',
        'ca-central-1': '加拿大（中部）',
        'ca-west-1': '加拿大西部（卡尔加里）',
        'eu-north-1': '欧洲（斯德哥尔摩）',
        'eu-south-1': '欧洲（米兰）',
        'eu-south-2': '欧洲（西班牙）',
        'eu-west-2': '欧洲（伦敦）',
        'eu-west-3': '欧洲（巴黎）',
        'il-central-1': '以色列（特拉维夫）',
        'me-central-1': '中东（阿联酋）',
        'me-south-1': '中东（巴林）',
        'sa-east-1': '南美洲（圣保罗）',
    }
    code = (region_code or '').strip()
    name = (region_name or '').strip()
    if code and code in mapping:
        return mapping[code]
    if name in mapping:
        return mapping[name]
    return name or code or '-'


def _split_usernames(username):
    if not username:
        return []
    normalized = str(username).replace('，', ',').replace(' / ', ',').replace('/', ',')
    parts = [item.strip().lstrip('@') for item in normalized.split(',')]
    result = []
    seen = set()
    for item in parts:
        key = item.lower()
        if item and key not in seen:
            result.append(item)
            seen.add(key)
    return result


def _user_payload(item):
    usernames = item.get('usernames') or _split_usernames(item.get('username'))
    first_name = (item.get('first_name') or '').strip()
    primary_username = item.get('primary_username') or (usernames[0] if usernames else '')
    display_name = first_name or (f'@{primary_username}' if primary_username else str(item.get('tg_user_id') or ''))
    return {
        **item,
        'display_name': display_name,
        'primary_username': primary_username,
        'usernames': usernames,
        'username_label': ' / '.join(f'@{name}' for name in usernames) if usernames else '-',
    }


def _telegram_user_labels(user):
    if not user:
        return '未绑定用户', '-'
    usernames = _split_usernames(getattr(user, 'username', '') or getattr(user, 'primary_username', ''))
    first_name = (getattr(user, 'first_name', '') or '').strip()
    primary_username = getattr(user, 'primary_username', '') or (usernames[0] if usernames else '')
    display_name = first_name or (f'@{primary_username}' if primary_username else str(getattr(user, 'tg_user_id', '') or getattr(user, 'id', '')))
    username_label = ' / '.join(f'@{name}' for name in usernames) if usernames else '-'
    return display_name, username_label


def _parse_decimal(value, field_label):
    raw = str(value or '').strip()
    if raw == '':
        raise ValueError(f'{field_label}不能为空')
    try:
        return Decimal(raw).quantize(Decimal('0.001'), rounding=ROUND_DOWN)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'{field_label}格式不正确')


def _site_config_payload(item):
    is_sensitive = item.key in SENSITIVE_CONFIG_KEYS
    value = SiteConfig.get(item.key, '')
    value_preview = value if item.key == 'trongrid_api_key' else (item.masked_value() if is_sensitive else (item.value or ''))
    return {
        'id': item.id,
        'key': item.key,
        'value': value,
        'value_preview': value_preview,
        'is_sensitive': is_sensitive,
        'description': CONFIG_HELP.get(item.key, '') or text_description(item.key, ''),
        'sort_order': item.sort_order,
    }


def _default_cloud_account_region(provider: str) -> str:
    normalized = str(provider or '').strip()
    if normalized == CloudAccountConfig.PROVIDER_AWS:
        return 'ap-southeast-1'
    if normalized == CloudAccountConfig.PROVIDER_ALIYUN:
        return 'cn-hongkong'
    return ''


def _normalize_cloud_account_region(provider: str, region_hint) -> str | None:
    region = str(region_hint or '').strip()
    return region or (_default_cloud_account_region(provider) or None)


def _fetch_aliyun_account_id(item) -> str:
    from alibabacloud_tea_openapi.client import Client
    from alibabacloud_tea_openapi import models as openapi_models, utils_models
    from alibabacloud_tea_util.models import RuntimeOptions

    client = Client(openapi_models.Config(
        access_key_id=item.access_key_plain,
        access_key_secret=item.secret_key_plain,
        endpoint='sts.cn-hongkong.aliyuncs.com',
    ))
    params = utils_models.Params(
        action='GetCallerIdentity',
        version='2015-04-01',
        protocol='HTTPS',
        pathname='/',
        method='POST',
        auth_type='AK',
        style='RPC',
        req_body_type='formData',
        body_type='json',
    )
    response = client.call_api(params, utils_models.OpenApiRequest(query={}), RuntimeOptions())
    body = response.get('body') or {}
    return str(body.get('AccountId') or body.get('UserId') or body.get('PrincipalId') or '').strip()


def _mask_secret(value, visible=4):
    text = str(value or '')
    if not text:
        return ''
    if len(text) <= visible * 2:
        return '*' * len(text)
    return f'{text[:visible]}****{text[-visible:]}'


def _cloud_account_payload(item):
    return {
        'id': item.id,
        'provider': item.provider,
        'provider_label': item.get_provider_display(),
        'name': item.name,
        'external_account_id': item.external_account_id or '',
        'access_key': '',
        'secret_key': '',
        'access_key_preview': _mask_secret(item.access_key_plain),
        'secret_key_preview': _mask_secret(item.secret_key_plain),
        'region_hint': item.region_hint,
        'effective_region': item.region_hint or _default_cloud_account_region(item.provider),
        'is_active': item.is_active,
        'shutdown_enabled': bool(getattr(item, 'shutdown_enabled', True)),
        'status': item.status,
        'status_label': item.status_label,
        'status_note': item.status_note,
        'last_checked_at': _iso(item.last_checked_at),
    }


def _external_sync_log_payload(item):
    return {
        'id': item.id,
        'source': item.source,
        'source_label': item.get_source_display(),
        'action': item.action,
        'target': item.target or '',
        'is_success': bool(item.is_success),
        'error_message': item.error_message or '',
        'request_payload': item.request_payload or '',
        'response_payload': item.response_payload or '',
        'created_at': _iso(item.created_at),
    }


def _cloud_account_detail_payload(item):
    logs = list(
        item.sync_logs.order_by('-created_at', '-id')[:50]
    )
    latest_success_log = next((log for log in logs if log.is_success), None)
    latest_failed_log = next((log for log in logs if not log.is_success), None)
    return {
        **_cloud_account_payload(item),
        'created_at': _iso(item.created_at),
        'updated_at': _iso(item.updated_at),
        'cloud_asset_count': CloudAsset.objects.filter(cloud_account=item).count(),
        'active_cloud_asset_count': CloudAsset.objects.filter(cloud_account=item, is_active=True).count(),
        'cloud_order_count': CloudServerOrder.objects.filter(cloud_account=item).count(),
        'running_cloud_order_count': CloudServerOrder.objects.filter(cloud_account=item).filter(status__in=['completed', 'expiring', 'renew_pending', 'suspended', 'deleting']).count(),
        'sync_log_count': item.sync_logs.count(),
        'latest_success_log_at': _iso(getattr(latest_success_log, 'created_at', None)),
        'latest_failed_log_at': _iso(getattr(latest_failed_log, 'created_at', None)),
        'recent_logs': [_external_sync_log_payload(log) for log in logs],
    }


def _telegram_login_account_payload(item):
    return {
        'id': item.id,
        'label': item.label,
        'phone': item.phone or '',
        'username': item.username or '',
        'status': item.status,
        'note': item.note or '',
        'notify_enabled': bool(getattr(item, 'notify_enabled', True)),
        'listener_push_enabled': bool(getattr(item, 'listener_push_enabled', True)),
        'has_session': bool(getattr(item, 'session_string', None)),
        'last_synced_at': _iso(item.last_synced_at),
        'created_at': _iso(item.created_at),
        'updated_at': _iso(item.updated_at),
    }


def _telegram_chat_user_payload(user, latest=None, message_count=0):
    return {
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'display_name': user.first_name or user.primary_username or str(user.tg_user_id),
        'first_name': user.first_name or '',
        'primary_username': user.primary_username,
        'username_label': f'@{user.primary_username}' if user.primary_username else '-',
        'usernames': user.usernames,
        'message_count': message_count,
        'latest_message': latest.text if latest else '',
        'latest_at': _iso(latest.created_at) if latest else None,
    }


def _telegram_chat_payload(chat_id, latest, message_count, archived_ids=None):
    username = latest.username_snapshot or ''
    title = latest.chat_title or latest.first_name_snapshot or username or str(chat_id)
    subtitle = ''
    if latest.chat_title and (latest.first_name_snapshot or username):
        subtitle = latest.first_name_snapshot or f'@{username}'
    elif latest.login_account_id and latest.login_account:
        subtitle = latest.login_account.label
    return {
        'chat_id': chat_id,
        'is_group': int(chat_id) < 0,
        'login_account_id': latest.login_account_id,
        'login_account_label': latest.login_account.label if latest.login_account_id and latest.login_account else '',
        'title': title,
        'subtitle': subtitle,
        'latest_message': latest.text or f'[{latest.content_type}]',
        'latest_at': _iso(latest.created_at),
        'message_count': message_count,
        'archived': chat_id in (archived_ids or set()),
        'source': latest.source or 'bot',
        'source_label': '个人号' if str(latest.source or '').startswith('account') else '机器人',
    }


def _telegram_message_payload(item):
    return {
        'id': item.id,
        'tg_user_id': item.tg_user_id,
        'chat_id': item.chat_id,
        'message_id': item.message_id,
        'login_account_id': item.login_account_id,
        'login_account_label': item.login_account.label if item.login_account_id and item.login_account else '',
        'direction': item.direction,
        'direction_label': item.get_direction_display(),
        'content_type': item.content_type,
        'text': item.text or '',
        'username_snapshot': item.username_snapshot or '',
        'first_name_snapshot': item.first_name_snapshot or '',
        'chat_title': item.chat_title or '',
        'source': item.source or 'bot',
        'source_label': '个人号' if str(item.source or '').startswith('account') else '机器人',
        'created_at': _iso(item.created_at),
    }


def _telegram_group_filter_payload(item):
    return {
        'id': item.id,
        'chat_id': item.chat_id,
        'title': item.title or '',
        'username': item.username or '',
        'enabled': bool(item.enabled),
        'push_enabled': bool(getattr(item, 'push_enabled', False)),
        'collapsed': bool(item.collapsed),
        'updated_at': _iso(item.updated_at),
        'created_at': _iso(item.created_at),
    }


def _telegram_group_member_payload(item, latest):
    username = latest.username_snapshot or (latest.user.primary_username if latest.user_id and latest.user else '')
    first_name = latest.first_name_snapshot or (latest.user.first_name if latest.user_id and latest.user else '')
    display_name = first_name or (f'@{username}' if username else str(item['tg_user_id']))
    return {
        'tg_user_id': item['tg_user_id'],
        'username': username or '',
        'first_name': first_name or '',
        'display_name': display_name,
        'display_label': f'{display_name} (ID: {item["tg_user_id"]})',
        'message_count': item['message_count'],
        'last_seen_at': _iso(item['last_seen_at']),
    }


def _limited_username_string(value):
    result = []
    for username in TelegramUser.normalize_usernames(value):
        candidate = ','.join([*result, username])
        if len(candidate) > 191:
            continue
        result.append(username)
    return ','.join(result) or None


def _merge_login_account_usernames(current, incoming):
    return _limited_username_string([incoming, current])


def _normalize_telegram_group_username(value):
    return str(value or '').strip().lstrip('@')


def _telegram_group_identity_label(chat_id, title='', username=''):
    normalized_username = _normalize_telegram_group_username(username)
    if normalized_username:
        return f'@{normalized_username}'
    if str(title or '').strip():
        return str(title).strip()
    return str(chat_id)


def _validate_telegram_group_filter_payload(payload, *, current_id=None):
    raw_chat_id = payload.get('chat_id')
    title = str(payload.get('title') or '').strip()
    username = _normalize_telegram_group_username(payload.get('username'))
    try:
        chat_id = int(raw_chat_id)
    except (TypeError, ValueError):
        raise ValueError('群组 Chat ID 无效')
    if chat_id >= 0:
        raise ValueError('只允许保存群组/频道 Chat ID')
    if not title:
        raise ValueError('群组名称不能为空')
    if username and not username.replace('_', '').isalnum():
        raise ValueError(f'用户名格式不正确：@{username}')
    duplicate = TelegramGroupFilter.objects.filter(chat_id=chat_id)
    if current_id:
        duplicate = duplicate.exclude(id=current_id)
    duplicate = duplicate.first()
    if duplicate:
        raise ValueError(f'群组已保存：{_telegram_group_identity_label(duplicate.chat_id, duplicate.title, duplicate.username)}')
    if username:
        duplicate_username = TelegramGroupFilter.objects.filter(username__iexact=username)
        if current_id:
            duplicate_username = duplicate_username.exclude(id=current_id)
        duplicate_username = duplicate_username.first()
        if duplicate_username:
            raise ValueError(f'用户名已保存：@{duplicate_username.username}')
    return chat_id, title[:191], username[:191] or None


def _admin_user_payload(user):
    return {
        'id': user.id,
        'username': user.username,
        'email': user.email or '',
        'is_active': bool(user.is_active),
        'is_staff': bool(user.is_staff),
        'is_superuser': bool(user.is_superuser),
        'date_joined': _iso(getattr(user, 'date_joined', None)),
        'last_login': _iso(getattr(user, 'last_login', None)),
    }


def _read_payload(request):
    try:
        import json
        payload = json.loads(request.body.decode('utf-8') or '{}') if request.body else {}
    except Exception:
        payload = {}
    if payload:
        return payload
    return request.POST.dict() if hasattr(request.POST, 'dict') else request.POST


def _payload_bool(payload, key, default=False):
    if key not in payload:
        return default
    value = payload.get(key)
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    return str(value).strip().lower() in {'1', 'true', 'yes', 'on'}


def _get_keyword(request):
    return (request.GET.get('keyword') or request.GET.get('q') or request.GET.get('search') or '').strip()


def _apply_keyword_filter(queryset, keyword, fields):
    if not keyword:
        return queryset
    condition = Q()
    for field in fields:
        condition |= Q(**{f'{field}__icontains': keyword})
    return queryset.filter(condition)


def _proxy_asset_count(asset):
    return 1 if asset.kind == CloudAsset.KIND_SERVER else 0


def _active_proxy_counts_by_user(user_ids=None):
    active_statuses = [
        CloudAsset.STATUS_RUNNING,
        CloudAsset.STATUS_PENDING,
        CloudAsset.STATUS_STARTING,
        CloudAsset.STATUS_STOPPED,
        CloudAsset.STATUS_SUSPENDED,
        CloudAsset.STATUS_EXPIRED_GRACE,
    ]
    qs = CloudAsset.objects.filter(is_active=True, status__in=active_statuses).filter(
        Q(user_id__isnull=False) | Q(order__user_id__isnull=False)
    ).select_related('order')
    if user_ids is not None:
        user_ids = set(user_ids)
        qs = qs.filter(Q(user_id__in=user_ids) | Q(order__user_id__in=user_ids))
    counts = {}
    for asset in qs.only('user_id', 'order__user_id', 'proxy_links', 'mtproxy_link'):
        user_id = asset.user_id or (asset.order.user_id if asset.order_id and asset.order else None)
        if not user_id:
            continue
        counts[user_id] = counts.get(user_id, 0) + _proxy_asset_count(asset)
    return counts


def _days_left(value):
    if not value:
        return None
    delta = value - timezone.now()
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        overdue_days = ((-total_seconds) + 86399) // 86400
        return -max(overdue_days, 1)
    return delta.days + (1 if delta.seconds > 0 or delta.microseconds > 0 else 0)


def _countdown_label(value):
    if not value:
        return '-'
    delta = value - timezone.now()
    total_seconds = int(delta.total_seconds())
    if total_seconds <= 0:
        overdue_days = ((-total_seconds) + 86399) // 86400
        return f'已过期 {max(overdue_days, 1)} 天'
    total_hours = (total_seconds + 3599) // 3600
    if total_hours < 24:
        return f'剩余 {total_hours} 小时'
    total_days = (total_seconds + 86399) // 86400
    return f'剩余 {total_days} 天'


def _provider_label(provider):
    mapping = {
        'aliyun_simple': '阿里云',
        'aws_lightsail': 'AWS Lightsail',
    }
    return mapping.get(provider, provider or '-')


def _provider_status_label(value):
    if not value:
        return '-'
    mapping = {
        'running': '运行中',
        'normal': '正常',
        'starting': '启动中',
        'pending': '等待中',
        'stopping': '停止中',
        'stopped': '已关机',
        'disabled': '已禁用',
        'expired': '已过期',
        'deleting': '删除中',
        'deleted': '已删除',
        'terminated': '已终止',
        'terminating': '终止中',
        'shutting-down': '关机中',
        'missing': '未发现',
    }
    parts = [part.strip() for part in str(value).split('/')]
    translated = []
    for part in parts:
        key = part.strip().lower()
        translated.append(mapping.get(key, part.strip() or '-'))
    return ' / '.join([part for part in translated if part]) or '-'


def _server_source_label(source):
    mapping = {
        'aliyun': '阿里云自动同步',
        'aws_manual': 'AWS 手工录入',
        'aws_sync': 'AWS 自动同步',
        'order': '订单创建',
    }
    return mapping.get(source, source or '-')


def _record_balance_ledger(user, *, currency, old_balance, new_balance, ledger_type='manual_adjust', related_type=None, related_id=None, description='', operator=None):
    old_balance = Decimal(str(old_balance or 0))
    new_balance = Decimal(str(new_balance or 0))
    delta = new_balance - old_balance
    if delta == 0:
        return None
    return BalanceLedger.objects.create(
        user=user,
        type=ledger_type,
        direction=BalanceLedger.DIRECTION_IN if delta > 0 else BalanceLedger.DIRECTION_OUT,
        currency=currency,
        amount=abs(delta),
        before_balance=old_balance,
        after_balance=new_balance,
        related_type=related_type,
        related_id=related_id,
        description=description,
        operator=operator,
    )


def _ledger_payload(ledger):
    related_path = None
    if ledger.related_type == 'recharge' and ledger.related_id:
        related_path = f'/admin/recharges/{ledger.related_id}'
    elif ledger.related_type == 'cloud_order' and ledger.related_id:
        related_path = f'/admin/cloud-orders/{ledger.related_id}'
    return {
        'id': f'ledger-{ledger.id}',
        'type': ledger.type,
        'type_label': _status_label(ledger.type, BalanceLedger.TYPE_CHOICES),
        'currency': ledger.currency,
        'direction': ledger.direction,
        'direction_label': _status_label(ledger.direction, BalanceLedger.DIRECTION_CHOICES),
        'amount': _decimal_to_str(ledger.amount),
        'before_balance': _decimal_to_str(ledger.before_balance),
        'after_balance': _decimal_to_str(ledger.after_balance),
        'balance_field': 'balance_trx' if ledger.currency == 'TRX' else 'balance',
        'title': _status_label(ledger.type, BalanceLedger.TYPE_CHOICES),
        'description': ledger.description or _status_label(ledger.type, BalanceLedger.TYPE_CHOICES),
        'related_id': ledger.related_id,
        'related_path': related_path,
        'created_at': _iso(ledger.created_at),
    }


@dashboard_login_required
@require_GET
def overview(request):
    users_total = TelegramUser.objects.count()
    server_assets_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, is_active=True).exclude(status__in=[
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_TERMINATED,
        CloudAsset.STATUS_TERMINATING,
        CloudAsset.STATUS_EXPIRED,
        CloudAsset.STATUS_UNKNOWN,
    ]).count()
    products_total = Product.objects.count()
    cloud_orders_total = CloudServerOrder.objects.count()
    recharges_total = Recharge.objects.count()
    monitors_total = AddressMonitor.objects.count()
    orders_total = Order.objects.count()

    today = timezone.localdate()
    today_start = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
    renew_before = timezone.now() + timezone.timedelta(days=7)
    cloud_pending = CloudServerOrder.objects.filter(status='pending').count()
    recharge_pending = Recharge.objects.filter(status='pending').count()
    today_end = today_start + timezone.timedelta(days=1)
    new_orders_today = CloudServerOrder.objects.filter(created_at__gte=today_start).count()
    active_server_assets = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, status__in=[CloudAsset.STATUS_RUNNING, CloudAsset.STATUS_UNKNOWN])
    due_today = active_server_assets.filter(
        (Q(actual_expires_at__gte=today_start, actual_expires_at__lt=today_end))
        | (Q(actual_expires_at__isnull=True) & Q(order__service_expires_at__gte=today_start, order__service_expires_at__lt=today_end))
    ).count()
    renew_due = active_server_assets.filter(
        Q(actual_expires_at__lte=renew_before)
        | (Q(actual_expires_at__isnull=True) & Q(order__service_expires_at__lte=renew_before))
    ).exclude(Q(actual_expires_at__isnull=True) & Q(order__service_expires_at__isnull=True)).count()
    paid_orders = CloudServerOrder.objects.filter(status__in=['paid', 'completed'])
    revenue = paid_orders.aggregate(total=Sum('pay_amount'))['total'] or Decimal('0')
    cost = Decimal('0')
    for order in paid_orders.select_related('plan').only('quantity', 'plan__cost_price'):
        cost += Decimal(str(getattr(order.plan, 'cost_price', 0) or 0)) * Decimal(str(order.quantity or 1))
    profit = revenue - cost

    latest_cloud_orders = list(
        CloudServerOrder.objects.select_related('user', 'plan')
        .order_by('-created_at')[:8]
        .values('id', 'order_no', 'status', 'region_name', 'plan_name', 'total_amount', 'created_at')
    )
    latest_recharges = list(
        Recharge.objects.select_related('user')
        .order_by('-created_at')[:8]
        .values('id', 'amount', 'status', 'tx_hash', 'created_at')
    )
    shutdown_logs = _shutdown_log_items(limit=80)
    unattached_ip_delete_plans = _unattached_ip_delete_items(limit=30)

    month_start = today.replace(day=1)
    next_month = (month_start.replace(day=28) + timezone.timedelta(days=4)).replace(day=1)
    trend_start = timezone.make_aware(timezone.datetime.combine(month_start, timezone.datetime.min.time()))
    trend_end = timezone.make_aware(timezone.datetime.combine(next_month, timezone.datetime.min.time()))
    trend_labels = [str(day) for day in range(1, 32)]
    users_growth = [0 for _ in trend_labels]
    orders_growth = [0 for _ in trend_labels]
    servers_growth = [0 for _ in trend_labels]
    expiry_trend = [0 for _ in trend_labels]
    profit_trend = [0 for _ in trend_labels]

    for created_at in TelegramUser.objects.filter(created_at__gte=trend_start, created_at__lt=trend_end).values_list('created_at', flat=True):
        day = timezone.localtime(created_at).day if timezone.is_aware(created_at) else created_at.day
        users_growth[day - 1] += 1
    for created_at in CloudServerOrder.objects.filter(created_at__gte=trend_start, created_at__lt=trend_end).values_list('created_at', flat=True):
        day = timezone.localtime(created_at).day if timezone.is_aware(created_at) else created_at.day
        orders_growth[day - 1] += 1
    for created_at in CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, created_at__gte=trend_start, created_at__lt=trend_end).values_list('created_at', flat=True):
        day = timezone.localtime(created_at).day if timezone.is_aware(created_at) else created_at.day
        servers_growth[day - 1] += 1
    trend_assets = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).select_related('order').only('actual_expires_at', 'order__service_expires_at')
    for asset in trend_assets:
        expires_at = asset.actual_expires_at or getattr(asset.order, 'service_expires_at', None)
        if not expires_at or expires_at < trend_start or expires_at >= trend_end:
            continue
        day = timezone.localtime(expires_at).day if timezone.is_aware(expires_at) else expires_at.day
        expiry_trend[day - 1] += 1

    daily_paid_orders = list(
        CloudServerOrder.objects.filter(status__in=['paid', 'completed'], created_at__gte=trend_start, created_at__lt=trend_end)
        .select_related('plan')
        .only('created_at', 'pay_amount', 'quantity', 'plan__cost_price')
    )
    for order in daily_paid_orders:
        day = timezone.localtime(order.created_at).day if timezone.is_aware(order.created_at) else order.created_at.day
        order_revenue = Decimal(str(order.pay_amount or 0))
        order_cost = Decimal(str(getattr(order.plan, 'cost_price', 0) or 0)) * Decimal(str(order.quantity or 1))
        profit_trend[day - 1] = float(_decimal_to_str(Decimal(str(profit_trend[day - 1])) + order_revenue - order_cost))

    return _ok({
        'summary': {
            'users_total': users_total,
            'server_assets_total': server_assets_total,
            'products_total': products_total,
            'cloud_orders_total': cloud_orders_total,
            'recharges_total': recharges_total,
            'monitors_total': monitors_total,
            'orders_total': orders_total,
            'cloud_pending': cloud_pending,
            'recharge_pending': recharge_pending,
            'new_orders_today': new_orders_today,
            'due_today': due_today,
            'renew_due': renew_due,
            'revenue_total': _decimal_to_str(revenue, 2),
            'cost_total': _decimal_to_str(cost, 2),
            'profit_total': _decimal_to_str(profit, 2),
        },
        'latest_cloud_orders': [
            {
                **item,
                'region_label': _region_label(item.get('region_name'), item.get('region_name')),
                'status_label': _status_label(item['status'], CloudServerOrder.STATUS_CHOICES),
                'total_amount': _decimal_to_str(item['total_amount']),
                'created_at': _iso(item['created_at']),
            }
            for item in latest_cloud_orders
        ],
        'charts': {
            'trend': {
                'labels': trend_labels,
                'users': users_growth,
                'orders': orders_growth,
                'servers': servers_growth,
                'profit': profit_trend,
                'expiry': expiry_trend,
            },
        },
        'latest_recharges': [
            {
                **item,
                'status_label': _status_label(item['status'], Recharge.STATUS_CHOICES),
                'amount': _decimal_to_str(item['amount']),
                'created_at': _iso(item['created_at']),
            }
            for item in latest_recharges
        ],
        'shutdown_logs': shutdown_logs,
        'unattached_ip_delete_plans': unattached_ip_delete_plans,
    })


@dashboard_login_required
@require_GET
def ip_delete_logs(request):
    try:
        limit = int(request.GET.get('limit') or 100)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 300))
    return _ok(_unattached_ip_delete_items(limit=limit))


def _shutdown_plan_item_payload(order, *, queue_status='scheduled_future', queue_status_label='未来计划', next_run_at=None, last_failure_reason=None):
    user_display_name, username_label = _telegram_user_labels(order.user)
    notice_ip = order.public_ip or order.previous_public_ip or '未分配'
    plan_at = next_run_at or order.delete_at
    shutdown_enabled = _shutdown_enabled_for_order(order)
    if not shutdown_enabled:
        execution_status = '关机计划关闭，禁止真实关机和删机'
        queue_status = 'shutdown_disabled'
        queue_status_label = '关机计划关闭'
    elif queue_status == 'retry_failed':
        execution_status = '上次删除失败，等待重试'
    elif queue_status == 'fallback_retry':
        execution_status = '已过删除时间，等待兜底重试'
    elif queue_status == 'due_now':
        execution_status = '已到删除时间，待执行删除服务器'
    elif queue_status == 'within_window':
        execution_status = '即将进入删除执行窗口'
    else:
        execution_status = '等待服务器删除计划'
    execution_plan = f'删除服务器 {_fmt_dashboard_dt(plan_at)}' if plan_at else '等待删除时间'
    return {
        'id': f'order-{order.id}',
        'item_type': 'order',
        'asset_id': None,
        'order_id': order.id,
        'order_no': order.order_no,
        'ip': notice_ip,
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'queue_status': queue_status,
        'queue_status_label': queue_status_label,
        'user_id': order.user_id,
        'tg_user_id': getattr(order.user, 'tg_user_id', None) if order.user else None,
        'user_display_name': user_display_name,
        'username_label': username_label,
        'service_expires_at': _iso(order.service_expires_at),
        'suspend_at': _iso(order.suspend_at),
        'delete_at': _iso(order.delete_at),
        'ip_recycle_at': _iso(order.ip_recycle_at),
        'next_run_at': _iso(next_run_at),
        'last_failure_reason': last_failure_reason,
        'execution_status': execution_status,
        'execution_plan': execution_plan,
        'related_path': f'/admin/cloud-orders/{order.id}',
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
    }


def _orphan_asset_delete_plan_item_payload(asset, *, queue_status='orphan_due', queue_status_label='无订单资产待删除'):
    account_name, external_account_id = _cloud_account_labels(asset)
    ip = asset.public_ip or asset.previous_public_ip or '未分配'
    plan_at = asset.actual_expires_at
    shutdown_enabled = _asset_shutdown_enabled(asset)
    execution_status = '无订单同步资产已到期，待执行删除服务器'
    if not shutdown_enabled:
        queue_status = 'shutdown_disabled'
        queue_status_label = '关机计划关闭'
        execution_status = '关机计划关闭，禁止真实关机和删机'
    elif queue_status == 'within_window':
        execution_status = '即将进入删除执行窗口'
    elif queue_status == 'scheduled_future':
        execution_status = '等待服务器删除计划'
    user_display_name, username_label = _telegram_user_labels(asset.user if getattr(asset, 'user', None) else None)
    return {
        'id': f'asset-{asset.id}',
        'item_type': 'orphan_asset',
        'asset_id': asset.id,
        'order_id': None,
        'order_no': '-',
        'ip': ip,
        'provider': asset.provider,
        'provider_label': _provider_label(asset.provider),
        'status': asset.status,
        'status_label': _status_label(asset.status, CloudAsset.STATUS_CHOICES),
        'queue_status': queue_status,
        'queue_status_label': queue_status_label,
        'user_id': getattr(asset, 'user_id', None),
        'tg_user_id': getattr(asset.user, 'tg_user_id', None) if getattr(asset, 'user', None) else None,
        'user_display_name': user_display_name,
        'username_label': username_label,
        'service_expires_at': _iso(asset.actual_expires_at),
        'suspend_at': None,
        'delete_at': _iso(plan_at),
        'ip_recycle_at': None,
        'next_run_at': _iso(plan_at),
        'last_failure_reason': None,
        'execution_status': execution_status,
        'execution_plan': f'删除服务器 {_fmt_dashboard_dt(plan_at)}' if plan_at else '等待删除时间',
        'cloud_account_id': asset.cloud_account_id,
        'cloud_account_name': account_name,
        'external_account_id': external_account_id,
        'asset_name': asset.asset_name,
        'related_path': f'/admin/cloud-assets/{asset.id}',
        'detail_path': f'/admin/cloud-assets/{asset.id}',
        'order_detail_path': '',
        'order_link_path': '',
    }


def _asset_shutdown_enabled(asset):
    account = getattr(asset, 'cloud_account', None)
    if not account:
        return True
    return bool(getattr(account, 'shutdown_enabled', True))


def _collect_shutdown_plan_queue(now):
    due = async_to_sync(_get_due_orders)()
    due_orders = list(due.get('delete') or [])
    due_ids = {order.id for order in due_orders}
    disabled_due_orders = list(
        CloudServerOrder.objects.select_related('user', 'cloud_account').filter(
            status__in=['suspended', 'deleting'],
            delete_at__isnull=False,
            delete_at__lte=now,
            cloud_account__shutdown_enabled=False,
        ).exclude(id__in=list(due_ids)).order_by('delete_at', 'id')[:100]
    )
    for order in disabled_due_orders:
        due_orders.append(order)
        due_ids.add(order.id)
    waiting_manual_time_q = Q(provider_status__icontains='待人工添加时间') | Q(note__icontains='等待人工添加真实到期时间') | Q(note__icontains='等待人工添加时间')
    unattached_static_ip_q = Q(provider_status__icontains='未附加固定IP') | Q(note__icontains='未附加固定IP') | Q(provider_resource_id__icontains='StaticIp')
    orphan_due_assets = list(
        CloudAsset.objects.select_related('cloud_account', 'user').filter(
            kind=CloudAsset.KIND_SERVER,
            order__isnull=True,
            actual_expires_at__lte=now,
        ).exclude(waiting_manual_time_q)
        .exclude(unattached_static_ip_q)
        .exclude(_cloud_asset_deleted_or_missing_q())
        .order_by('actual_expires_at', 'id')[:1000]
    )
    recent_failures = {}
    failure_logs = CloudIpLog.objects.select_related('order').filter(
        event_type__in=['delete_failed', 'delete_skipped'],
        created_at__gte=now - timezone.timedelta(days=7),
        order__isnull=False,
    ).order_by('-created_at', '-id')
    for log in failure_logs:
        if log.order_id in recent_failures:
            continue
        order = log.order
        if not order or order.id in due_ids:
            continue
        if order.status not in {'suspended', 'deleting', 'failed'}:
            continue
        if order.delete_at and order.delete_at <= now:
            recent_failures[order.id] = (order, log.note or log.event_type)
            due_ids.add(order.id)

    fallback_orders = []
    fallback_qs = CloudServerOrder.objects.select_related('user', 'cloud_account').filter(
        status__in=['suspended', 'deleting'],
        delete_at__isnull=False,
        delete_at__lte=now,
    ).exclude(id__in=list(due_ids)).order_by('delete_at', 'id')[:1000]
    for order in fallback_qs:
        fallback_orders.append(order)
        due_ids.add(order.id)

    next_run_at = None
    future_items = []
    orphan_due_asset_ids = [asset.id for asset in orphan_due_assets]
    future_orphan_assets = list(
        CloudAsset.objects.select_related('cloud_account', 'user').filter(
            kind=CloudAsset.KIND_SERVER,
            order__isnull=True,
            actual_expires_at__gt=now,
        ).exclude(id__in=orphan_due_asset_ids)
        .exclude(waiting_manual_time_q)
        .exclude(unattached_static_ip_q)
        .exclude(_cloud_asset_deleted_or_missing_q())
        .order_by('actual_expires_at', 'id')[:1000]
    )
    future_qs = CloudServerOrder.objects.select_related('user', 'cloud_account').filter(
        status__in=['suspended', 'deleting'],
        delete_at__isnull=False,
    ).exclude(id__in=list(due_ids)).order_by('delete_at', 'id')[:1000]
    for order in future_qs:
        if not next_run_at or order.delete_at < next_run_at:
            next_run_at = order.delete_at
        if order.delete_at <= now + timezone.timedelta(days=1):
            queue_status, queue_status_label = 'within_window', '24小时内进入删机窗口'
        else:
            queue_status, queue_status_label = 'scheduled_future', '未来计划'
        future_items.append(_shutdown_plan_item_payload(order, queue_status=queue_status, queue_status_label=queue_status_label, next_run_at=order.delete_at))

    due_items = [_shutdown_plan_item_payload(order, queue_status='due_now', queue_status_label='本轮待删除', next_run_at=order.delete_at) for order in due_orders]
    due_items.extend(_orphan_asset_delete_plan_item_payload(asset) for asset in orphan_due_assets)
    due_items.extend([
        _shutdown_plan_item_payload(order, queue_status='retry_failed', queue_status_label='删除失败待重试', next_run_at=order.delete_at, last_failure_reason=reason)
        for order, reason in recent_failures.values()
    ])
    due_items.extend([
        _shutdown_plan_item_payload(order, queue_status='fallback_retry', queue_status_label='过期兜底重试', next_run_at=order.delete_at)
        for order in fallback_orders
    ])
    for asset in future_orphan_assets:
        if not next_run_at or asset.actual_expires_at < next_run_at:
            next_run_at = asset.actual_expires_at
        if asset.actual_expires_at <= now + timezone.timedelta(days=1):
            queue_status, queue_status_label = 'within_window', '24小时内进入删机窗口'
        else:
            queue_status, queue_status_label = 'scheduled_future', '未来计划'
        future_items.append(_orphan_asset_delete_plan_item_payload(asset, queue_status=queue_status, queue_status_label=queue_status_label))

    return {
        'due_orders': due_orders,
        'retry_orders': [item[0] for item in recent_failures.values()],
        'fallback_orders': fallback_orders,
        'orphan_due_assets': orphan_due_assets,
        'due_items': due_items,
        'future_plan_items': future_items,
        'next_run_at': next_run_at or (now + timezone.timedelta(minutes=30)),
    }


def _shutdown_history_item_payload(log):
    order = log.order
    user = log.user or getattr(order, 'user', None)
    user_display_name, username_label = _telegram_user_labels(user)
    ok = log.event_type == 'deleted'
    return {
        'id': f'log-{log.id}',
        'order_id': log.order_id,
        'order_no': log.order_no or getattr(order, 'order_no', '') or '-',
        'ip': log.public_ip or log.previous_public_ip or getattr(order, 'public_ip', '') or getattr(order, 'previous_public_ip', '') or '未分配',
        'provider': log.provider or getattr(order, 'provider', ''),
        'provider_label': _provider_label(log.provider or getattr(order, 'provider', '')),
        'user_id': getattr(user, 'id', None) if user else None,
        'tg_user_id': getattr(user, 'tg_user_id', None) if user else None,
        'user_display_name': user_display_name,
        'username_label': username_label,
        'is_success': ok,
        'result_label': '成功' if ok else '失败/跳过',
        'failure_reason': '' if ok else _compact_failure_reason(log.note, fallback=_status_label(log.event_type, CloudIpLog.EVENT_CHOICES) or '失败/跳过'),
        'execution_status': _status_label(log.event_type, CloudIpLog.EVENT_CHOICES),
        'executed_at': _iso(log.created_at),
        'service_expires_at': _iso(getattr(order, 'service_expires_at', None)),
        'suspend_at': _iso(getattr(order, 'suspend_at', None)),
        'delete_at': _iso(getattr(order, 'delete_at', None)),
        'related_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
        'detail_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
        'order_detail_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
        'order_link_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
    }


def _shutdown_history_order_payload(order):
    user_display_name, username_label = _telegram_user_labels(order.user)
    ip = order.public_ip or order.previous_public_ip or '未分配'
    executed_at = order.updated_at or order.delete_at
    return {
        'id': f'order-{order.id}',
        'order_id': order.id,
        'order_no': order.order_no or '-',
        'ip': ip,
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'user_id': order.user_id,
        'tg_user_id': getattr(order.user, 'tg_user_id', None) if order.user else None,
        'user_display_name': user_display_name,
        'username_label': username_label,
        'is_success': True,
        'result_label': '成功',
        'failure_reason': '',
        'execution_status': '服务器已删除',
        'executed_at': _iso(executed_at),
        'service_expires_at': _iso(order.service_expires_at),
        'suspend_at': _iso(order.suspend_at),
        'delete_at': _iso(order.delete_at),
        'related_path': f'/admin/cloud-orders/{order.id}',
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
    }


@dashboard_login_required
@require_GET
def lifecycle_plans(request):
    try:
        limit = int(request.GET.get('limit') or 1000)
    except (TypeError, ValueError):
        limit = 1000
    limit = max(1, min(limit, 1000))
    shutdown_items = _shutdown_log_items(limit=limit)
    ip_delete_items = _unattached_ip_delete_items(limit=limit)
    now = timezone.now()

    def decorate_plan_item(item):
        note = str(item.get('note') or '')
        first_line = next((line.strip() for line in note.splitlines() if line.strip()), '')
        content_match = re.search(r'执行内容：([^\n]+?)(?:；(?:时间|账号|地区|IP|固定IP名|端口|secret|服务到期|宽限删机|用户续费)|$)', first_line)
        plan_match = re.search(r'执行计划：([^；\n]+)', first_line)
        status_text = (content_match.group(1).strip() if content_match else '') or item.get('status_label') or ''
        item['execution_status'] = status_text
        item['execution_plan'] = plan_match.group(1).strip() if plan_match else ''
        return item

    shutdown_items = [decorate_plan_item(item) for item in shutdown_items]
    ip_delete_items = [decorate_plan_item(item) for item in ip_delete_items]

    def is_due(value):
        if not value:
            return False
        parsed = parse_datetime(value)
        if not parsed:
            return False
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed <= now

    shutdown_queue = _collect_shutdown_plan_queue(now)
    history_qs = CloudIpLog.objects.select_related('order', 'user').filter(
        event_type__in=['deleted', 'delete_failed', 'delete_skipped']
    ).order_by('-created_at', '-id')[:limit]
    history_items = [_shutdown_history_item_payload(log) for log in history_qs]
    history_order_ids = {item.get('order_id') for item in history_items if item.get('order_id')}
    fallback_deleted_orders = CloudServerOrder.objects.select_related('user').filter(status='deleted').exclude(id__in=list(history_order_ids)).order_by('-updated_at', '-id')[:limit]
    history_items.extend(_shutdown_history_order_payload(order) for order in fallback_deleted_orders)
    history_items.sort(key=lambda item: parse_datetime(item.get('executed_at') or '') or datetime.min.replace(tzinfo=dt_timezone.utc), reverse=True)
    history_items = history_items[:limit]
    ip_delete_pending_until = now + timezone.timedelta(days=7)

    def is_ip_delete_pending(item):
        if item.get('is_history'):
            return False
        if item.get('is_overdue'):
            return True
        delete_at = item.get('delete_at')
        if not delete_at:
            return False
        parsed = parse_datetime(delete_at)
        if not parsed:
            return False
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed <= ip_delete_pending_until

    pending_ip_delete_items = [item for item in ip_delete_items if is_ip_delete_pending(item)]
    ip_delete_history_items = [item for item in ip_delete_items if item.get('is_history')]
    recent_since = now - timezone.timedelta(days=1)
    recent_history = [item for item in history_items if item.get('executed_at') and parse_datetime(item['executed_at']) and parse_datetime(item['executed_at']) >= recent_since]
    return _ok({
        'task_key': 'server_delete_plans',
        'task_label': '服务器删除计划',
        'status_label': '按后台删机时间执行',
        'interval_minutes': 1440,
        'last_run_at': history_items[0]['executed_at'] if history_items else None,
        'next_run_at': _iso(_next_runtime_time('cloud_delete_time', '15:00', now)),
        'due_count': len(shutdown_queue['due_items']),
        'recent_success_count': sum(1 for item in recent_history if item.get('is_success')),
        'recent_failure_count': sum(1 for item in recent_history if not item.get('is_success')),
        'pending_ip_delete_count': len(pending_ip_delete_items),
        'server_delete_history_count': len(history_items),
        'ip_delete_history_count': len(ip_delete_history_items),
        'shutdown_count': len(shutdown_queue['due_items']) + len(shutdown_queue['future_plan_items']),
        'shutdown_due_count': len(shutdown_queue['due_items']),
        'ip_delete_count': len(pending_ip_delete_items),
        'ip_delete_due_count': len(pending_ip_delete_items),
        'due_items': shutdown_queue['due_items'],
        'future_plan_items': shutdown_queue['future_plan_items'],
        'history_items': history_items,
        'shutdown_items': shutdown_items,
        'ip_delete_items': ip_delete_items,
    })


def _run_shutdown_order_sync(order_id: int, queue_status='manual_single', enforce_schedule: bool = True):
    order = CloudServerOrder.objects.select_related('user', 'cloud_account').filter(id=order_id).first()
    if not order:
        return {'order_id': order_id, 'order_no': '', 'ip': '', 'queue_status': queue_status, 'ok': False, 'error': '订单不存在'}
    ip = order.public_ip or order.previous_public_ip or '未分配'
    now = timezone.now()
    if order.status not in {'suspended', 'deleting', 'failed'}:
        reason = f'当前状态为 {_status_label(order.status, CloudServerOrder.STATUS_CHOICES)}，未进入服务器删除阶段'
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    if not _shutdown_enabled_for_order(order):
        reason = '云账号关机计划已关闭，跳过真实删机。'
        async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    if enforce_schedule:
        if order.delete_at and order.delete_at > now:
            reason = f'服务器删除时间未到：{timezone.localtime(order.delete_at).strftime("%Y-%m-%d %H:%M:%S")}'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
        if not _is_cloud_delete_safe_time(now):
            reason = '当前不在后台配置的服务器删除执行时间窗口'
            async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_skipped', reason)
            return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': reason}
    ok, note = async_to_sync(_delete_instance)(order)
    if ok:
        async_to_sync(_mark_deleted)(order.id, note)
        return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': True, 'error': None}
    async_to_sync(_record_lifecycle_action_failed)(order.id, 'delete_failed', note)
    return {'order_id': order.id, 'order_no': order.order_no, 'ip': ip, 'queue_status': queue_status, 'ok': False, 'error': note}


@csrf_exempt
@dashboard_login_required
@require_POST
def run_shutdown_plan_order(request, order_id):
    result = _run_shutdown_order_sync(order_id, 'manual_single', enforce_schedule=False)
    return _ok({
        'batch_id': secrets.token_hex(8),
        'items': [result],
        'total': 1,
        'success_count': 1 if result['ok'] else 0,
        'failure_count': 0 if result['ok'] else 1,
        'message': '服务器删除任务已执行' if result['ok'] else result.get('error') or '服务器删除任务执行失败',
    })


def _run_orphan_asset_delete_sync(asset_id: int, enforce_schedule: bool = True):
    asset = CloudAsset.objects.select_related('cloud_account').filter(id=asset_id, order__isnull=True).first()
    if not asset:
        return {'asset_id': asset_id, 'ip': '', 'ok': False, 'error': '无订单服务器资产不存在'}
    ip = asset.public_ip or asset.previous_public_ip or ''
    now = timezone.now()
    if asset.status in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_TERMINATING}:
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '该服务器资产已删除，不需要重复执行'}
    if not _asset_shutdown_enabled(asset):
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '云账号关机计划已关闭，跳过真实删机。'}
    if _asset_is_unattached_ip(asset) or not str(asset.instance_id or asset.provider_resource_id or asset.asset_name or '').strip():
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '该资产不是可删服务器，请走未附加 IP 删除'}
    if enforce_schedule:
        if asset.actual_expires_at and asset.actual_expires_at > now:
            return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': f'未到服务器删除时间：{timezone.localtime(asset.actual_expires_at).strftime("%Y-%m-%d %H:%M:%S")}' }
        if not _is_cloud_delete_safe_time(now):
            return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '当前不在后台配置的服务器删除执行时间窗口'}
    ok, note = async_to_sync(_delete_orphan_asset_instance)(asset)
    if ok:
        async_to_sync(_mark_orphan_asset_deleted)(asset.id, note)
        return {'asset_id': asset.id, 'ip': ip, 'ok': True, 'error': None}
    return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': note}


@csrf_exempt
@dashboard_login_required
@require_POST
def run_orphan_asset_delete_plan(request, asset_id):
    result = _run_orphan_asset_delete_sync(asset_id, enforce_schedule=False)
    return _ok({
        'batch_id': secrets.token_hex(8),
        'items': [result],
        'total': 1,
        'success_count': 1 if result['ok'] else 0,
        'failure_count': 0 if result['ok'] else 1,
        'message': '服务器删除任务已执行' if result['ok'] else result.get('error') or '服务器删除任务执行失败',
    })


def _run_unattached_ip_delete_sync(asset_id: int, enforce_schedule: bool = True):
    asset = CloudAsset.objects.select_related('cloud_account').filter(id=asset_id).first()
    if not asset:
        return {'asset_id': asset_id, 'ip': '', 'ok': False, 'error': 'IP 资产不存在'}
    ip = asset.public_ip or asset.previous_public_ip or ''
    now = timezone.now()
    if asset.status in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_TERMINATING}:
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '该 IP 已删除，不需要重复执行'}
    if asset.instance_id:
        return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '该 IP 仍有关联实例，不能按未附加 IP 删除'}
    if enforce_schedule:
        if asset.actual_expires_at and asset.actual_expires_at > now:
            return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': f'未到 IP 删除时间：{timezone.localtime(asset.actual_expires_at).strftime("%Y-%m-%d %H:%M:%S")}' }
        if not _is_cloud_delete_safe_time(now):
            return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': '当前不在后台配置的删除执行时间窗口'}
    ok, note = async_to_sync(_release_unattached_static_ip)(asset)
    if ok:
        async_to_sync(_mark_unattached_static_ip_deleted)(asset.id, note)
        return {'asset_id': asset.id, 'ip': ip, 'ok': True, 'error': None}
    return {'asset_id': asset.id, 'ip': ip, 'ok': False, 'error': note}


@csrf_exempt
@dashboard_login_required
@require_POST
def run_unattached_ip_delete_plan(request, asset_id):
    result = _run_unattached_ip_delete_sync(asset_id, enforce_schedule=False)
    return _ok({
        'batch_id': secrets.token_hex(8),
        'items': [result],
        'total': 1,
        'success_count': 1 if result['ok'] else 0,
        'failure_count': 0 if result['ok'] else 1,
        'message': 'IP 删除任务已执行' if result['ok'] else result.get('error') or 'IP 删除任务执行失败',
    })


@csrf_exempt
@require_POST
def auth_login(request):
    payload = _json_payload(request)
    username = request.POST.get('username') or request.headers.get('x-username') or payload.get('username')
    password = request.POST.get('password') or request.headers.get('x-password') or payload.get('password')
    otp_token = request.POST.get('otp_token') or request.POST.get('otpToken') or payload.get('otp_token') or payload.get('otpToken')

    user = authenticate(request, username=username, password=password)
    if not user:
        return _error('用户名或密码错误', status=401)
    if not user.is_active:
        return _error('用户已禁用', status=403)
    if not _staff_required(user):
        return _error('没有后台权限', status=403)

    totp_secret = _totp_secret()
    if totp_secret and not _verify_totp_token(otp_token, totp_secret):
        return _error('Google 验证码错误或已过期', status=401)

    login(request, user)
    request.session.set_expiry(DASHBOARD_SESSION_IDLE_SECONDS)
    return _ok(_dashboard_session_payload(request))


@csrf_exempt
@dashboard_login_required
@require_POST
def auth_logout(request):
    logout(request)
    return _ok(True)


@csrf_exempt
@dashboard_login_required
@require_POST
def auth_refresh(request):
    return _ok(_dashboard_session_payload(request))


@dashboard_login_required
@require_GET
def auth_codes(request):
    return _ok(['dashboard', 'users', 'cloud', 'finance', 'monitoring', 'settings'])


@csrf_exempt
@dashboard_login_required
@require_POST
def auth_totp_start(request):
    payload = _json_payload(request)
    current_secret = _totp_secret()
    replacing_existing = bool(current_secret)
    if replacing_existing:
        old_token = payload.get('old_otp_token') or payload.get('oldOtpToken')
        if not _verify_totp_token(old_token, current_secret):
            return _error('更换 TOTP 密钥前，请先输入当前 Google Authenticator 的 6 位动态码', status=400)
    secret = _normalize_totp_secret(_generate_totp_secret())
    request.session['dashboard_totp_pending_secret'] = secret
    request.session['dashboard_totp_replacing_existing'] = replacing_existing
    request.session.set_expiry(10 * 60)
    username = request.user.get_username() or 'admin'
    return _ok({
        'enabled': replacing_existing,
        'otpauthUrl': _totp_otpauth_url(secret, username),
        'secret': secret,
    })


@csrf_exempt
@dashboard_login_required
@require_POST
def auth_totp_bind(request):
    payload = _json_payload(request)
    token = payload.get('otp_token') or payload.get('otpToken')
    secret = request.session.get('dashboard_totp_pending_secret')
    if not secret:
        return _error('请先生成 Google 验证器二维码', status=400)
    if _totp_secret() and not request.session.get('dashboard_totp_replacing_existing'):
        return _error('更换 TOTP 密钥前，请先验证当前 Google Authenticator 动态码并重新生成二维码', status=400)
    if not _verify_totp_token(token, secret):
        return _error('新 Google 验证码错误或已过期', status=400)
    SiteConfig.set('dashboard_totp_secret', secret, sensitive=True)
    request.session.pop('dashboard_totp_pending_secret', None)
    request.session.pop('dashboard_totp_replacing_existing', None)
    request.session.set_expiry(DASHBOARD_SESSION_IDLE_SECONDS)
    return _ok({'enabled': True})


@dashboard_login_required
@require_GET
def user_info(request):
    username = request.user.get_username() or 'admin'
    return _ok({
        'userId': str(request.user.pk),
        'username': username,
        'realName': request.user.get_full_name() or username,
        'avatar': '',
        'desc': 'Shop 管理后台管理员',
        'homePath': '/admin/analytics',
        'token': _session_token_for_request(request),
    })


@login_required
@require_GET
def me(request):
    return _ok({
        'id': request.user.id,
        'username': request.user.get_username(),
        'is_superuser': request.user.is_superuser,
        'is_staff': request.user.is_staff,
    })


@dashboard_login_required
@require_GET
def site_configs_list(request):
    queryset = SiteConfig.objects.order_by('sort_order', 'key')
    return _ok([_site_config_payload(item) for item in queryset])


@dashboard_login_required
@require_GET
def button_config_detail(request):
    return _ok(load_button_config())


@csrf_exempt
@dashboard_login_required
@require_POST
def update_button_config(request):
    payload = _read_payload(request)
    return _ok(save_button_config(payload))


@csrf_exempt
@dashboard_login_required
@require_POST
def init_button_config_view(request):
    return _ok(init_button_config())


def site_config_groups(request):
    groups = {
        'database': ['mysql_host', 'mysql_port', 'mysql_database', 'mysql_user', 'mysql_password', 'redis_host', 'redis_port', 'redis_password', 'redis_db'],
        'system': [
            'bot_token',
            'telegram_api_id',
            'telegram_api_hash',
            'dashboard_totp_secret',
        ],
        'payment': [
            'receive_address',
            'trongrid_api_key',
        ],
        'logs': [
            'scanner_block_log_enabled',
            'scanner_verbose',
        ],
        'notifications': [
            'telegram_listener_push_enabled',
            'telegram_listener_push_bark_url',
            'telegram_listener_push_private_enabled',
            'telegram_listener_push_bark_encryption_key',
            'telegram_listener_push_bark_encryption_iv',
            'bot_admin_chat_id',
            'cloud_auto_renew_execution_notify_enabled',
            'cloud_auto_renew_execution_notify_chat_ids',
            'cloud_auto_renew_execution_notify_events',
            'cloud_daily_expiry_summary_enabled',
            'cloud_daily_expiry_summary_chat_ids',
        ],
        'lifecycle': [
            'cloud_suspend_after_days',
            'cloud_suspend_time',
            'cloud_delete_after_days',
            'cloud_delete_time',
            'cloud_unattached_ip_delete_after_days',
            'cloud_unattached_ip_delete_time',
            'cloud_asset_sync_interval_seconds',
            'cloud_sync_missing_delete_confirmations',
        ],
        **TEXT_GROUPS,
    }
    existing = {item.key: item for item in SiteConfig.objects.all()}
    payload = []
    for group_key, keys in groups.items():
        items = []
        ordered_keys = sorted(
            keys,
            key=lambda candidate: (
                existing[candidate].sort_order if candidate in existing and existing[candidate].sort_order else keys.index(candidate) + 1,
                keys.index(candidate),
            ),
        )
        for key in ordered_keys:
            obj = existing.get(key)
            is_sensitive = key in SENSITIVE_CONFIG_KEYS
            stored_value = SiteConfig.get(key, '') if obj else ''
            effective_value = stored_value or get_runtime_config(key, '')
            value_preview = effective_value
            if is_sensitive and effective_value and key != 'trongrid_api_key':
                plain = effective_value
                if len(plain) <= 6:
                    value_preview = '*' * len(plain)
                else:
                    value_preview = f'{plain[:3]}***{plain[-3:]}'
            items.append({
                'key': key,
                'id': obj.id if obj else None,
                'value': effective_value,
                'value_preview': value_preview,
                'default_value': text_default(key, ''),
                'is_sensitive': is_sensitive,
                'description': CONFIG_HELP.get(key, '') or text_description(key, ''),
                'sort_order': obj.sort_order if obj else keys.index(key) + 1,
            })
        payload.append({'group': group_key, 'items': items})
    return _ok(payload)


@csrf_exempt
@dashboard_login_required
@require_POST
def init_site_configs(request):
    payload = _read_payload(request)
    scope = (payload.get('scope') or 'all').strip() or 'all'
    created = 0
    for key in CONFIG_HELP.keys():
        item, was_created = SiteConfig.objects.get_or_create(
            key=key,
            defaults={'value': get_runtime_config(key, ''), 'is_sensitive': key in SENSITIVE_CONFIG_KEYS, 'sort_order': list(CONFIG_HELP.keys()).index(key) + 1},
        )
        if not was_created and not SiteConfig.get(key, ''):
            runtime_value = get_runtime_config(key, '')
            if runtime_value:
                SiteConfig.set(key, runtime_value, sensitive=key in SENSITIVE_CONFIG_KEYS)
        created += int(was_created)
    if scope == 'all':
        text_result = init_texts(get_runtime_config('text_init_mode', 'missing_only'))
        return _ok({'created': created + text_result['created'], 'updated': text_result['updated'], 'scope': scope})
    return _ok({'created': created, 'updated': 0, 'scope': scope})


@csrf_exempt
@dashboard_login_required
@require_POST
def init_text_site_configs(request):
    enabled = str(get_runtime_config('text_init_enabled', '1')).lower() not in {'0', 'false', 'no', 'off'}
    if not enabled:
        return _error('文案初始化当前已禁用', status=400)
    payload = _read_payload(request)
    mode = (payload.get('mode') or get_runtime_config('text_init_mode', 'missing_only')).strip() or 'missing_only'
    if mode not in {'missing_only', 'reset_defaults'}:
        return _error('初始化模式不正确', status=400)
    result = init_texts(mode)
    return _ok({'mode': mode, **result})


@csrf_exempt
@dashboard_login_required
@require_POST
def test_daily_expiry_summary_notification(request):
    token = SiteConfig.get('bot_token', '') or get_runtime_config('bot_token', '')
    if not str(token or '').strip():
        return _error('测试通知发送失败：未配置 Telegram 机器人 Token', status=400)

    async def _send():
        from aiogram import Bot

        from cloud.lifecycle import daily_expiry_summary_tick

        bot = Bot(str(token).strip())

        async def _notify_target(chat_id, text: str, reply_markup=None):
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode='HTML')
            return True

        try:
            return await daily_expiry_summary_tick(notify_target=_notify_target, force=True, sync_cloud=False)
        finally:
            await bot.session.close()

    try:
        result = async_to_sync(_send)()
    except Exception as exc:
        return _error(f'测试通知发送失败：{exc}', status=400)
    if result.get('skipped') == 'disabled':
        return _error('每日到期汇总通知未开启或未配置通知目标', status=400)
    if result.get('skipped') == 'missing_notify_target':
        return _error('通知发送器不可用', status=400)
    if not result.get('sent'):
        return _error('测试通知未送达，请检查 Chat ID / 群组 / 频道权限', status=400)
    return _ok(result)


@csrf_exempt
@dashboard_login_required
@require_POST
def update_site_config(request, config_id: int):
    item = SiteConfig.objects.filter(id=config_id).first()
    if not item:
        return _error('配置不存在', status=404)
    data = _read_payload(request)
    is_sensitive = item.key in SENSITIVE_CONFIG_KEYS
    preserve_existing = str(data.get('preserve_existing', '')).lower() in {'1', 'true', 'yes', 'on'}
    value = data.get('value')
    if preserve_existing:
        plain_value = SiteConfig.get(item.key, '')
    else:
        plain_value = '' if value is None else str(value).strip()
    if item.key == 'trongrid_api_key' and plain_value:
        plain_value = '\n'.join(parse_trongrid_api_keys(plain_value))
        if not plain_value:
            return _error('TRON API Key 至少要有一个有效值', status=400)
    if item.key == 'cloud_asset_sync_interval_seconds':
        try:
            interval_seconds = int(plain_value)
        except (TypeError, ValueError):
            return _error('代理同步间隔必须是秒数整数', status=400)
        if interval_seconds < 60:
            return _error('代理同步间隔不能小于60秒', status=400)
        plain_value = str(interval_seconds)
    if item.key == 'bot_admin_chat_id':
        if not plain_value:
            return _error('管理员转发 Chat ID 不能为空', status=400)
        normalized = (
            plain_value
            .replace('，', ',')
            .replace('；', ',')
            .replace(';', ',')
            .replace('\n', ',')
        )
        parsed_ids = []
        for part in normalized.split(','):
            candidate = part.strip()
            if not candidate:
                continue
            try:
                parsed_ids.append(str(int(candidate)))
            except Exception:
                return _error(f'管理员转发 Chat ID 格式不正确：{candidate}', status=400)
        if not parsed_ids:
            return _error('管理员转发 Chat ID 至少要有一个有效值', status=400)
        plain_value = ','.join(dict.fromkeys(parsed_ids))
    sort_order_raw = data.get('sort_order')
    if sort_order_raw is not None:
        try:
            item.sort_order = int(sort_order_raw)
        except (TypeError, ValueError):
            return _error('排序必须是整数', status=400)
        item.save(update_fields=['sort_order'])
    SiteConfig.set(item.key, plain_value, sensitive=is_sensitive)
    try:
        from core.cache import _cached_config
        _cached_config[item.key] = plain_value
    except Exception:
        pass
    item = SiteConfig.objects.get(id=item.id)
    return _ok(_site_config_payload(item))


@dashboard_login_required
@require_GET
def cloud_accounts_list(request):
    queryset = CloudAccountConfig.objects.order_by('provider', 'name', 'id')
    return _ok([_cloud_account_payload(item) for item in queryset])


@csrf_exempt
@dashboard_login_required
@require_POST
def create_cloud_account(request):
    payload = _read_payload(request)
    provider = (payload.get('provider') or '').strip()
    name = (payload.get('name') or '').strip()
    access_key = (payload.get('access_key') or '').strip()
    secret_key = (payload.get('secret_key') or '').strip()
    external_account_id = (payload.get('external_account_id') or '').strip()
    region_hint = _normalize_cloud_account_region(provider, payload.get('region_hint'))
    is_active = str(payload.get('is_active', 'true')).lower() in {'1', 'true', 'yes', 'on'}
    shutdown_enabled = str(payload.get('shutdown_enabled', 'true')).lower() in {'1', 'true', 'yes', 'on'}
    if provider not in {CloudAccountConfig.PROVIDER_AWS, CloudAccountConfig.PROVIDER_ALIYUN}:
        return _error('云平台类型不正确', status=400)
    if not name:
        return _error('账户名称不能为空', status=400)
    if not access_key or not secret_key:
        return _error('Access Key 和 Secret Key 不能为空', status=400)
    item = CloudAccountConfig.objects.create(
        provider=provider,
        name=name,
        external_account_id=external_account_id or None,
        access_key=access_key,
        secret_key=secret_key,
        region_hint=region_hint,
        is_active=is_active,
        shutdown_enabled=shutdown_enabled,
    )
    return _ok(_cloud_account_payload(item))


@csrf_exempt
@dashboard_login_required
def update_cloud_account(request, account_id: int):
    item = CloudAccountConfig.objects.filter(id=account_id).first()
    if not item:
        return _error('云账号不存在', status=404)
    if request.method == 'GET':
        return _ok(_cloud_account_detail_payload(item))
    if request.method != 'POST':
        return _error('请求方法不支持', status=405)
    payload = _read_payload(request)
    provider = (payload.get('provider') or item.provider).strip()
    name = (payload.get('name') or item.name).strip()
    external_account_id = payload.get('external_account_id')
    access_key = payload.get('access_key')
    secret_key = payload.get('secret_key')
    region_hint = payload.get('region_hint')
    is_active = payload.get('is_active')
    shutdown_enabled = payload.get('shutdown_enabled')
    if provider not in {CloudAccountConfig.PROVIDER_AWS, CloudAccountConfig.PROVIDER_ALIYUN}:
        return _error('云平台类型不正确', status=400)
    if not name:
        return _error('账户名称不能为空', status=400)
    item.provider = provider
    item.name = name
    if external_account_id is not None:
        item.external_account_id = str(external_account_id or '').strip() or None
    if access_key not in (None, ''):
        item.access_key = str(access_key).strip()
    if secret_key not in (None, ''):
        item.secret_key = str(secret_key).strip()
    item.region_hint = _normalize_cloud_account_region(provider, region_hint)
    if is_active is not None:
        item.is_active = str(is_active).lower() in {'1', 'true', 'yes', 'on'}
    if shutdown_enabled is not None:
        item.shutdown_enabled = str(shutdown_enabled).lower() in {'1', 'true', 'yes', 'on'}
    item.save()
    return _ok(_cloud_account_payload(item))


@csrf_exempt
@dashboard_login_required
@require_POST
def delete_cloud_account(request, account_id: int):
    item = CloudAccountConfig.objects.filter(id=account_id).first()
    if not item:
        return _error('云账号不存在', status=404)
    item.delete()
    return _ok(True)


@csrf_exempt
@dashboard_login_required
@require_POST
def verify_cloud_account(request, account_id: int):
    item = CloudAccountConfig.objects.filter(id=account_id).first()
    if not item:
        return _error('云账号不存在', status=404)
    payload = _read_payload(request)
    region = _normalize_cloud_account_region(
        item.provider,
        payload.get('region') or request.POST.get('region') or request.GET.get('region') or item.region_hint,
    ) or ''
    try:
        if item.provider == CloudAccountConfig.PROVIDER_AWS:
            import boto3
            client = boto3.client(
                'lightsail',
                region_name=region or 'ap-southeast-1',
                aws_access_key_id=item.access_key_plain,
                aws_secret_access_key=item.secret_key_plain,
            )
            response = client.get_instances()
            count = len(response.get('instances') or [])
            account_id = ''
            try:
                sts = boto3.client(
                    'sts',
                    aws_access_key_id=item.access_key_plain,
                    aws_secret_access_key=item.secret_key_plain,
                )
                account_id = str(sts.get_caller_identity().get('Account') or '').strip()
            except Exception:
                account_id = ''
            if account_id and item.external_account_id != account_id:
                item.external_account_id = account_id
                item.save(update_fields=['external_account_id', 'updated_at'])
            item.mark_status(CloudAccountConfig.STATUS_OK, f'验证成功，账号ID {account_id or "-"}，实例数 {count}，地区 {region or "ap-southeast-1"}')
            return _ok({'valid': True, 'provider': item.provider, 'region': region or 'ap-southeast-1', 'instance_count': count, 'account': _cloud_account_payload(item)})
        if item.provider == CloudAccountConfig.PROVIDER_ALIYUN:
            from alibabacloud_swas_open20200601 import models as swas_models
            from cloud.aliyun_simple import _build_client as _default_build_client
            from cloud.aliyun_simple import _region_endpoint, _runtime_options

            old_key = os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_ID')
            old_secret = os.environ.get('ALIBABA_CLOUD_ACCESS_KEY_SECRET')
            try:
                os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'] = item.access_key_plain
                os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'] = item.secret_key_plain
                client = _default_build_client(_region_endpoint(region or 'cn-hongkong'))
                if not client:
                    raise ValueError('无法创建阿里云客户端')
                response = client.list_instances_with_options(
                    swas_models.ListInstancesRequest(region_id=region or 'cn-hongkong', page_size=10),
                    _runtime_options(),
                )
                instances = response.body.to_map().get('Instances', []) or []
                count = len(instances)
                account_id = _fetch_aliyun_account_id(item)
                if account_id and item.external_account_id != account_id:
                    item.external_account_id = account_id
                    item.save(update_fields=['external_account_id', 'updated_at'])
                item.mark_status(CloudAccountConfig.STATUS_OK, f'验证成功，账号ID {account_id or "-"}，实例数 {count}，地区 {region or "cn-hongkong"}')
                return _ok({'valid': True, 'provider': item.provider, 'region': region or 'cn-hongkong', 'instance_count': count, 'account': _cloud_account_payload(item)})
            finally:
                if old_key is None:
                    os.environ.pop('ALIBABA_CLOUD_ACCESS_KEY_ID', None)
                else:
                    os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'] = old_key
                if old_secret is None:
                    os.environ.pop('ALIBABA_CLOUD_ACCESS_KEY_SECRET', None)
                else:
                    os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET'] = old_secret
        item.mark_status(CloudAccountConfig.STATUS_UNSUPPORTED, '暂不支持该云平台验证')
        return _error('暂不支持该云平台', status=400)
    except Exception as exc:
        item.mark_status(CloudAccountConfig.STATUS_ERROR, str(exc))
        return _error(f'验证失败: {exc}', status=400)


@dashboard_login_required
@require_GET
def admin_users_list(request):
    User = get_user_model()
    queryset = User.objects.filter(is_staff=True).order_by('id')
    return _ok([_admin_user_payload(item) for item in queryset])


@csrf_exempt
@dashboard_login_required
@require_POST
def create_admin_user(request):
    User = get_user_model()
    payload = _read_payload(request)
    username = (payload.get('username') or '').strip()
    email = (payload.get('email') or '').strip()
    password = str(payload.get('password') or '').strip()
    is_active = str(payload.get('is_active', 'true')).lower() in {'1', 'true', 'yes', 'on'}
    is_superuser = str(payload.get('is_superuser', 'false')).lower() in {'1', 'true', 'yes', 'on'}
    if not username:
        return _error('管理员用户名不能为空', status=400)
    if not password:
        return _error('管理员密码不能为空', status=400)
    if User.objects.filter(username=username).exists():
        return _error('管理员用户名已存在', status=400)
    user = User(username=username, email=email, is_active=is_active, is_staff=True, is_superuser=is_superuser)
    try:
        validate_password(password, user)
    except Exception as exc:
        return _error('; '.join(exc.messages) if hasattr(exc, 'messages') else str(exc), status=400)
    user.set_password(password)
    user.save()
    return _ok(_admin_user_payload(user))


@csrf_exempt
@dashboard_login_required
@require_POST
def update_admin_user(request, user_id: int):
    User = get_user_model()
    user = User.objects.filter(id=user_id, is_staff=True).first()
    if not user:
        return _error('管理员不存在', status=404)
    payload = _read_payload(request)
    username = payload.get('username')
    email = payload.get('email')
    password = payload.get('password')
    is_active = payload.get('is_active')
    is_superuser = payload.get('is_superuser')
    if username is not None:
        username = str(username).strip()
        if not username:
            return _error('管理员用户名不能为空', status=400)
        exists = User.objects.filter(username=username).exclude(id=user.id).exists()
        if exists:
            return _error('管理员用户名已存在', status=400)
        user.username = username
    if email is not None:
        user.email = str(email).strip()
    if is_active is not None:
        user.is_active = str(is_active).lower() in {'1', 'true', 'yes', 'on'}
    user.is_staff = True
    if is_superuser is not None:
        user.is_superuser = str(is_superuser).lower() in {'1', 'true', 'yes', 'on'}
    if password not in (None, ''):
        password = str(password).strip()
        try:
            validate_password(password, user)
        except Exception as exc:
            return _error('; '.join(exc.messages) if hasattr(exc, 'messages') else str(exc), status=400)
        user.set_password(password)
    if request.user.id == user.id and not user.is_active:
        return _error('不能停用当前登录管理员', status=400)
    user.save()
    return _ok(_admin_user_payload(user))


@csrf_exempt
@dashboard_login_required
@require_POST
def delete_admin_user(request, user_id: int):
    User = get_user_model()
    user = User.objects.filter(id=user_id, is_staff=True).first()
    if not user:
        return _error('管理员不存在', status=404)
    if request.user.id == user.id:
        return _error('不能删除当前登录管理员', status=400)
    remaining = User.objects.filter(is_staff=True).exclude(id=user.id).count()
    if remaining <= 0:
        return _error('至少保留一个管理员', status=400)
    user.delete()
    return _ok(True)


@csrf_exempt
@dashboard_login_required
@require_POST
def change_my_password(request):
    payload = _read_payload(request)
    old_password = str(payload.get('old_password') or '')
    new_password = str(payload.get('new_password') or '')
    confirm_password = str(payload.get('confirm_password') or '')
    if not old_password or not new_password:
        return _error('旧密码和新密码不能为空', status=400)
    if new_password != confirm_password:
        return _error('两次输入的新密码不一致', status=400)
    if not request.user.check_password(old_password):
        return _error('旧密码不正确', status=400)
    try:
        validate_password(new_password, request.user)
    except Exception as exc:
        return _error('; '.join(exc.messages) if hasattr(exc, 'messages') else str(exc), status=400)
    request.user.set_password(new_password)
    request.user.save(update_fields=['password'])
    return _ok(True)


def _bot_operation_log_payload(item):
    user_payload = _user_payload({
        'id': item.user.id,
        'tg_user_id': item.user.tg_user_id,
        'username': item.user.username,
        'first_name': item.user.first_name,
        'usernames': item.user.usernames,
        'primary_username': item.user.primary_username,
    }) if item.user_id else None
    return {
        'id': item.id,
        'created_at': _iso(item.created_at),
        'action_type': item.action_type,
        'action_label': item.get_action_type_display() if hasattr(item, 'get_action_type_display') else item.action_label,
        'payload': item.payload,
        'chat_id': item.chat_id,
        'message_id': item.message_id,
        'tg_user_id': item.tg_user_id,
        'user_id': item.user_id,
        'user_display_name': user_payload['display_name'] if user_payload else (item.first_name_snapshot or str(item.tg_user_id)),
        'username_label': user_payload['username_label'] if user_payload else (f"@{item.username_snapshot}" if item.username_snapshot else '-'),
    }


@dashboard_login_required
@require_GET
def bot_operation_logs(request):
    keyword = _get_keyword(request)
    queryset = BotOperationLog.objects.select_related('user').order_by('-created_at', '-id')
    if keyword:
        keyword_filter = (
            Q(payload__icontains=keyword)
            | Q(action_label__icontains=keyword)
            | Q(username_snapshot__icontains=keyword)
            | Q(first_name_snapshot__icontains=keyword)
            | Q(user__username__icontains=keyword)
            | Q(user__first_name__icontains=keyword)
        )
        if keyword.isdigit():
            keyword_filter |= Q(tg_user_id=int(keyword)) | Q(chat_id=int(keyword)) | Q(message_id=int(keyword))
        queryset = queryset.filter(keyword_filter)
    return _ok([_bot_operation_log_payload(item) for item in queryset[:200]])


@dashboard_login_required
@require_GET
def users_list(request):
    keyword = _get_keyword(request)
    try:
        queryset = TelegramUser.objects.order_by('-id')
        if keyword and keyword.isdigit():
            queryset = queryset.annotate(tg_user_id_text=Cast('tg_user_id', output_field=CharField()))
            queryset = queryset.filter(
                Q(id=int(keyword))
                | Q(tg_user_id=int(keyword))
                | Q(tg_user_id_text__icontains=keyword)
                | Q(username__icontains=keyword)
                | Q(first_name__icontains=keyword)
            )
        else:
            queryset = _apply_keyword_filter(queryset, keyword, ['username', 'first_name'])
        users = list(queryset.distinct())
    except ProgrammingError:
        queryset = TelegramUser.objects.order_by('-id')
        if keyword and keyword.isdigit():
            queryset = queryset.annotate(tg_user_id_text=Cast('tg_user_id', output_field=CharField()))
            queryset = queryset.filter(
                Q(id=int(keyword)) | Q(tg_user_id=int(keyword)) | Q(tg_user_id_text__icontains=keyword)
            )
        else:
            queryset = _apply_keyword_filter(queryset, keyword, ['username', 'first_name'])
        users = list(queryset.distinct())
    proxy_counts = _active_proxy_counts_by_user([user.id for user in users])
    users.sort(key=lambda user: (proxy_counts.get(user.id, 0), user.id), reverse=True)
    users = users[:50]
    return _ok([
        {
            **_user_payload({
                'id': user.id,
                'tg_user_id': user.tg_user_id,
                'username': user.username,
                'first_name': user.first_name,
                'balance': user.balance,
                'balance_trx': user.balance_trx,
                'cloud_discount_rate': user.cloud_discount_rate,
                'created_at': user.created_at,
                'usernames': user.usernames,
                'primary_username': user.usernames[0] if user.usernames else '',
            }),
            'balance': _decimal_to_str(user.balance),
            'balance_trx': _decimal_to_str(user.balance_trx),
            'cloud_discount_rate': _decimal_to_str(user.cloud_discount_rate),
            'created_at': _iso(user.created_at),
            'proxy_count': proxy_counts.get(user.id, 0),
        }
        for user in users
    ])


@csrf_exempt
@dashboard_login_required
@require_POST
def update_user_balance(request, user_id):
    payload = _read_payload(request)
    try:
        balance = _parse_decimal(payload.get('balance'), 'USDT余额')
        balance_trx = _parse_decimal(payload.get('balance_trx'), 'TRX余额')
    except ValueError as exc:
        return _error(str(exc), status=400)
    if balance < 0 or balance_trx < 0:
        return _error('余额不能为负数', status=400)

    try:
        with transaction.atomic():
            user = TelegramUser.objects.select_for_update().get(pk=user_id)
            old_balance = user.balance
            old_balance_trx = user.balance_trx
            user.balance = balance
            user.balance_trx = balance_trx
            user.save(update_fields=['balance', 'balance_trx', 'updated_at'])
            operator = getattr(request.user, 'username', '') or str(getattr(request.user, 'id', '') or '')
            _record_balance_ledger(
                user,
                currency='USDT',
                old_balance=old_balance,
                new_balance=balance,
                description='Dashboard 手动编辑 USDT 余额',
                operator=operator,
            )
            _record_balance_ledger(
                user,
                currency='TRX',
                old_balance=old_balance_trx,
                new_balance=balance_trx,
                description='Dashboard 手动编辑 TRX 余额',
                operator=operator,
            )
    except TelegramUser.DoesNotExist:
        return _error('用户不存在', status=404)

    return _ok({
        'id': user.id,
        'balance': _decimal_to_str(user.balance),
        'balance_trx': _decimal_to_str(user.balance_trx),
    })


@csrf_exempt
@dashboard_login_required
@require_POST
def update_user_discount(request, user_id):
    payload = _read_payload(request)
    try:
        discount = _parse_decimal(payload.get('cloud_discount_rate'), '云服务器折扣')
    except ValueError as exc:
        return _error(str(exc), status=400)
    if discount <= 0 or discount > 100:
        return _error('云服务器折扣必须大于 0 且小于等于 100', status=400)
    user = TelegramUser.objects.filter(pk=user_id).first()
    if not user:
        return _error('用户不存在', status=404)
    user.cloud_discount_rate = discount
    user.save(update_fields=['cloud_discount_rate', 'updated_at'])
    return _ok({
        'id': user.id,
        'cloud_discount_rate': _decimal_to_str(user.cloud_discount_rate),
    })


@dashboard_login_required
@require_GET
def user_balance_details(request, user_id):
    user = TelegramUser.objects.filter(pk=user_id).first()
    if not user:
        return _error('用户不存在', status=404)

    items = []

    recharges = Recharge.objects.filter(user_id=user_id, status='completed').order_by('-completed_at', '-created_at')[:200]
    for recharge in recharges:
        items.append({
            'id': f'recharge-{recharge.id}',
            'type': 'recharge',
            'type_label': '充值入账',
            'currency': recharge.currency,
            'direction': 'in',
            'direction_label': '收入',
            'amount': _decimal_to_str(recharge.amount),
            'balance_field': 'balance_trx' if recharge.currency == 'TRX' else 'balance',
            'title': f'充值 #{recharge.id}',
            'description': f'充值订单已完成，余额增加 {_decimal_to_str(recharge.amount)} {recharge.currency}',
            'related_id': recharge.id,
            'related_path': f'/admin/recharges/{recharge.id}',
            'created_at': _iso(recharge.completed_at or recharge.created_at),
        })

    orders = Order.objects.filter(user_id=user_id, pay_method='balance').exclude(status='pending').order_by('-paid_at', '-created_at')[:200]
    for order in orders:
        amount = order.pay_amount if order.pay_amount is not None else order.total_amount
        items.append({
            'id': f'order-{order.id}',
            'type': 'order_balance_pay',
            'type_label': '商品余额支付',
            'currency': order.currency,
            'direction': 'out',
            'direction_label': '支出',
            'amount': _decimal_to_str(amount),
            'balance_field': 'balance_trx' if order.currency == 'TRX' else 'balance',
            'title': f'商品订单 #{order.order_no}',
            'description': f'余额支付商品：{order.product_name}',
            'related_id': order.id,
            'related_path': None,
            'created_at': _iso(order.paid_at or order.created_at),
        })

    cloud_orders = CloudServerOrder.objects.filter(user_id=user_id, pay_method='balance').exclude(status='pending').order_by('-paid_at', '-created_at')[:200]
    for order in cloud_orders:
        amount = order.pay_amount if order.pay_amount is not None else order.total_amount
        items.append({
            'id': f'cloud-order-{order.id}',
            'type': 'cloud_order_balance_pay',
            'type_label': '云服务器余额支付',
            'currency': order.currency,
            'direction': 'out',
            'direction_label': '支出',
            'amount': _decimal_to_str(amount),
            'balance_field': 'balance_trx' if order.currency == 'TRX' else 'balance',
            'title': f'云订单 #{order.order_no}',
            'description': f'余额支付云服务器：{order.plan_name}',
            'related_id': order.id,
            'related_path': f'/admin/cloud-orders/{order.id}',
            'created_at': _iso(order.paid_at or order.created_at),
        })

    ledger_items = [_ledger_payload(ledger) for ledger in BalanceLedger.objects.filter(user_id=user_id).order_by('-created_at', '-id')[:300]]

    items.sort(key=lambda item: item['created_at'] or '', reverse=True)
    combined_items = [*ledger_items, *items]
    combined_items.sort(key=lambda item: item['created_at'] or '', reverse=True)

    return _ok({
        'user': {
            **_user_payload({
                'id': user.id,
                'tg_user_id': user.tg_user_id,
                'username': user.username,
                'first_name': user.first_name,
                'balance': user.balance,
                'balance_trx': user.balance_trx,
                'created_at': user.created_at,
                'usernames': user.usernames,
                'primary_username': user.usernames[0] if user.usernames else '',
            }),
            'balance': _decimal_to_str(user.balance),
            'balance_trx': _decimal_to_str(user.balance_trx),
            'created_at': _iso(user.created_at),
        },
        'items': combined_items[:300],
    })


@dashboard_login_required
@require_GET
def telegram_accounts_overview(request):
    keyword = (request.GET.get('keyword') or '').strip().lstrip('@')
    include_archived = (request.GET.get('archived') or '').strip() in {'1', 'true', 'yes'}
    archived_ids = set(TelegramChatArchive.objects.values_list('chat_id', flat=True))
    accounts = TelegramLoginAccount.objects.order_by('-updated_at', '-id')
    users = TelegramUser.objects.order_by('-updated_at', '-id')
    messages = TelegramChatMessage.objects.select_related('user', 'login_account').order_by('-created_at', '-id')
    if keyword:
        accounts = accounts.filter(Q(label__icontains=keyword) | Q(phone__icontains=keyword) | Q(username__icontains=keyword))
        user_filter = Q(username__icontains=keyword) | Q(first_name__icontains=keyword)
        message_filter = Q(text__icontains=keyword) | Q(username_snapshot__icontains=keyword) | Q(first_name_snapshot__icontains=keyword)
        if keyword.isdigit():
            user_filter |= Q(tg_user_id=int(keyword))
            message_filter |= Q(tg_user_id=int(keyword))
        users = users.filter(user_filter)
        messages = messages.filter(message_filter)
    counts = dict(TelegramChatMessage.objects.values('tg_user_id').annotate(total=Count('id')).values_list('tg_user_id', 'total'))
    if not include_archived and archived_ids:
        messages = messages.exclude(chat_id__in=archived_ids)
    chat_counts = dict(messages.values('chat_id').annotate(total=Count('id')).values_list('chat_id', 'total'))
    latest_by_user = {}
    latest_by_chat = {}
    for msg in TelegramChatMessage.objects.select_related('login_account').order_by('-created_at', '-id')[:2000]:
        latest_by_user.setdefault(msg.tg_user_id, msg)
    for msg in messages.select_related('login_account').iterator(chunk_size=500):
        latest_by_chat.setdefault(msg.chat_id, msg)
        if len(latest_by_chat) >= 100:
            break
    return _ok({
        'accounts': [_telegram_login_account_payload(item) for item in accounts[:50]],
        'chats': [_telegram_chat_payload(chat_id, latest, chat_counts.get(chat_id, 0), archived_ids) for chat_id, latest in list(latest_by_chat.items())[:100]],
        'users': [_telegram_chat_user_payload(user, latest_by_user.get(user.tg_user_id), counts.get(user.tg_user_id, 0)) for user in users[:100]],
        'messages': [_telegram_message_payload(item) for item in messages[:200]],
    })


def _telegram_api_credentials():
    api_id = SiteConfig.get('telegram_api_id', '') or get_runtime_config('telegram_api_id', '')
    api_hash = SiteConfig.get('telegram_api_hash', '') or get_runtime_config('telegram_api_hash', '')
    if not str(api_id or '').strip() or not str(api_hash or '').strip():
        raise ValueError('请先在系统设置中配置 Telegram API ID 和 API Hash')
    try:
        return int(str(api_id).strip()), str(api_hash).strip()
    except ValueError as exc:
        raise ValueError('Telegram API ID 必须是数字') from exc


async def _telegram_send_code(phone: str, api_id: int, api_hash: str):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.connect()
    try:
        sent = await client.send_code_request(phone)
        session = client.session.save()
        return sent.phone_code_hash, session
    finally:
        await client.disconnect()


async def _telegram_sign_in_code(session_string: str, phone: str, code: str, phone_code_hash: str, api_id: int, api_hash: str):
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_string or ''), api_id, api_hash)
    await client.connect()
    try:
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            return {'requires_password': True, 'session_string': client.session.save(), 'user': None}
        me = await client.get_me()
        return {'requires_password': False, 'session_string': client.session.save(), 'user': me}
    finally:
        await client.disconnect()


async def _telegram_check_session(session_string: str, api_id: int, api_hash: str):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_string or ''), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            return {'ok': False, 'user': None, 'note': 'Telegram 会话已失效，请重新登录'}
        me = await client.get_me()
        return {'ok': True, 'user': me, 'note': '状态正常'}
    finally:
        await client.disconnect()


async def _telegram_send_message(session_string: str, chat_id: int, text: str, api_id: int, api_hash: str):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_string or ''), api_id, api_hash)
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise ValueError('Telegram 会话已失效，请重新登录')
        return await client.send_message(chat_id, text)
    finally:
        await client.disconnect()


async def _telegram_sign_in_password(session_string: str, password: str, api_id: int, api_hash: str):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_string or ''), api_id, api_hash)
    await client.connect()
    try:
        await client.sign_in(password=password)
        me = await client.get_me()
        return {'session_string': client.session.save(), 'user': me}
    finally:
        await client.disconnect()


def _update_login_account_from_me(item, me, status='logged_in'):
    item.status = status
    item.username = _merge_login_account_usernames(item.username, getattr(me, 'username', None)) or item.username
    item.label = getattr(me, 'first_name', None) or item.username or item.phone or item.label
    _get_or_create_user_sync(getattr(me, 'id', 0), getattr(me, 'username', None), getattr(me, 'first_name', None))
    item.note = '登录成功'
    item.last_synced_at = timezone.now()
    item.save(update_fields=['status', 'username', 'label', 'note', 'last_synced_at', 'updated_at'])
    return item


@csrf_exempt
@dashboard_login_required
@require_POST
def check_telegram_login_account_status(request, account_id: int):
    item = TelegramLoginAccount.objects.filter(id=account_id).first()
    if not item:
        return _error('账号不存在', status=404)
    if not item.session_string_plain:
        item.status = 'session_expired'
        item.note = '缺少 Telegram 会话，请重新登录'
        item.save(update_fields=['status', 'note', 'updated_at'])
        return _ok(_telegram_login_account_payload(item))
    try:
        api_id, api_hash = _telegram_api_credentials()
        result = async_to_sync(_telegram_check_session)(item.session_string_plain, api_id, api_hash)
    except Exception as exc:
        item.status = 'listener_error'
        item.note = f'状态检查失败：{exc}'[:1000]
        item.save(update_fields=['status', 'note', 'updated_at'])
        return _ok(_telegram_login_account_payload(item))
    if result.get('ok'):
        _update_login_account_from_me(item, result.get('user'), status='logged_in')
    else:
        item.status = 'session_expired'
        item.note = str(result.get('note') or 'Telegram 会话已失效，请重新登录')[:1000]
        item.save(update_fields=['status', 'note', 'updated_at'])
    return _ok(_telegram_login_account_payload(item))


@csrf_exempt
@dashboard_login_required
@require_POST
def update_telegram_account_notify(request, account_id: int):
    payload = _read_payload(request)
    item = TelegramLoginAccount.objects.filter(id=account_id).first()
    if not item:
        return _error('账号不存在', status=404)
    if 'notify_enabled' in payload:
        item.notify_enabled = _payload_bool(payload, 'notify_enabled')
    if 'listener_push_enabled' in payload:
        item.listener_push_enabled = _payload_bool(payload, 'listener_push_enabled')
    item.save(update_fields=['notify_enabled', 'listener_push_enabled', 'updated_at'])
    return _ok(_telegram_login_account_payload(item))


@csrf_exempt
@dashboard_login_required
@require_POST
def telegram_login_start(request):
    payload = _read_payload(request)
    phone = str(payload.get('phone') or '').strip()
    if not phone:
        return _error('手机号不能为空', status=400)
    try:
        api_id, api_hash = _telegram_api_credentials()
        phone_code_hash, session_string = async_to_sync(_telegram_send_code)(phone, api_id, api_hash)
    except Exception as exc:
        return _error(f'发送验证码失败：{exc}', status=400)
    item, _ = TelegramLoginAccount.objects.update_or_create(
        phone=phone,
        defaults={
            'label': phone,
            'phone_code_hash': phone_code_hash,
            'session_string': session_string,
            'status': 'code_sent',
            'note': '验证码已发送',
        },
    )
    return _ok({'account': _telegram_login_account_payload(item), 'account_id': item.id, 'next_step': 'code'})


@csrf_exempt
@dashboard_login_required
@require_POST
def telegram_login_code(request):
    payload = _read_payload(request)
    account_id = payload.get('account_id')
    code = str(payload.get('code') or '').strip().replace(' ', '')
    item = TelegramLoginAccount.objects.filter(id=account_id).first()
    if not item:
        return _error('登录账号不存在', status=404)
    if not code:
        return _error('验证码不能为空', status=400)
    try:
        api_id, api_hash = _telegram_api_credentials()
        result = async_to_sync(_telegram_sign_in_code)(item.session_string_plain, item.phone or '', code, item.phone_code_hash_plain, api_id, api_hash)
    except Exception as exc:
        item.status = 'error'
        item.note = f'验证码登录失败：{exc}'
        item.save(update_fields=['status', 'note', 'updated_at'])
        return _error(f'验证码登录失败：{exc}', status=400)
    item.session_string = result['session_string']
    if result['requires_password']:
        item.status = 'password_required'
        item.note = '需要二级密码'
        item.save(update_fields=['session_string', 'status', 'note', 'updated_at'])
        return _ok({'account': _telegram_login_account_payload(item), 'account_id': item.id, 'requires_password': True, 'next_step': 'password'})
    item = _update_login_account_from_me(item, result['user'])
    item.save(update_fields=['session_string', 'updated_at'])
    return _ok({'account': _telegram_login_account_payload(item), 'account_id': item.id, 'requires_password': False, 'next_step': 'done'})


@csrf_exempt
@dashboard_login_required
@require_POST
def telegram_login_password(request):
    payload = _read_payload(request)
    account_id = payload.get('account_id')
    password = str(payload.get('password') or '')
    item = TelegramLoginAccount.objects.filter(id=account_id).first()
    if not item:
        return _error('登录账号不存在', status=404)
    if item.status != 'password_required' and not password:
        return _ok({'account': _telegram_login_account_payload(item), 'account_id': item.id, 'next_step': 'done'})
    if not password:
        return _error('该账号需要二级密码；如果没有二级密码，请返回检查验证码登录结果', status=400)
    try:
        api_id, api_hash = _telegram_api_credentials()
        result = async_to_sync(_telegram_sign_in_password)(item.session_string_plain, password, api_id, api_hash)
    except Exception as exc:
        item.status = 'error'
        item.note = f'二级密码登录失败：{exc}'
        item.save(update_fields=['status', 'note', 'updated_at'])
        return _error(f'二级密码登录失败：{exc}', status=400)
    item.session_string = result['session_string']
    item = _update_login_account_from_me(item, result['user'])
    item.save(update_fields=['session_string', 'updated_at'])
    return _ok({'account': _telegram_login_account_payload(item), 'account_id': item.id, 'next_step': 'done'})


@dashboard_login_required
@require_GET
def telegram_group_filters_list(request):
    keyword = _get_keyword(request).lstrip('@')
    binding_only = str(request.GET.get('binding_only') or '').lower() in {'1', 'true', 'yes', 'on'}
    queryset = TelegramGroupFilter.objects.order_by('-updated_at', '-id')
    if binding_only:
        queryset = queryset.filter(collapsed=False)
    if keyword:
        query = Q(title__icontains=keyword) | Q(username__icontains=keyword)
        try:
            query |= Q(chat_id=int(keyword))
        except ValueError:
            pass
        queryset = queryset.filter(query)
    return _ok([_telegram_group_filter_payload(item) for item in queryset[:300]])


@csrf_exempt
@dashboard_login_required
@require_POST
def create_telegram_group_filter(request):
    payload = _read_payload(request)
    try:
        chat_id, title, username = _validate_telegram_group_filter_payload(payload)
    except ValueError as exc:
        return _error(str(exc), status=400)
    item = TelegramGroupFilter.objects.create(
        chat_id=chat_id,
        title=title,
        username=username,
        enabled=_payload_bool(payload, 'enabled'),
        push_enabled=_payload_bool(payload, 'push_enabled'),
        collapsed=_payload_bool(payload, 'collapsed'),
    )
    return _ok(_telegram_group_filter_payload(item))


@dashboard_login_required
@require_GET
def telegram_group_filter_detail(request, group_id: int):
    item = TelegramGroupFilter.objects.filter(id=group_id).first()
    if not item:
        return _error('群组不存在', status=404)
    messages = list(
        TelegramChatMessage.objects.filter(chat_id=item.chat_id)
        .select_related('user', 'login_account')
        .order_by('-created_at', '-id')[:100]
    )
    latest_by_user = {}
    for message_item in messages:
        latest_by_user.setdefault(message_item.tg_user_id, message_item)
    member_rows = list(
        TelegramChatMessage.objects.filter(chat_id=item.chat_id)
        .values('tg_user_id')
        .annotate(message_count=Count('id'), last_seen_at=Max('created_at'))
        .order_by('-last_seen_at')[:100]
    )
    missing_user_ids = [row['tg_user_id'] for row in member_rows if row['tg_user_id'] not in latest_by_user]
    if missing_user_ids:
        for message_item in (
            TelegramChatMessage.objects.filter(chat_id=item.chat_id, tg_user_id__in=missing_user_ids)
            .select_related('user')
            .order_by('-created_at', '-id')
        ):
            latest_by_user.setdefault(message_item.tg_user_id, message_item)
            if len(latest_by_user) >= len(member_rows):
                break
    return _ok({
        'group': _telegram_group_filter_payload(item),
        'members': [_telegram_group_member_payload(row, latest_by_user[row['tg_user_id']]) for row in member_rows if row['tg_user_id'] in latest_by_user],
        'messages': [_telegram_message_payload(message_item) for message_item in messages],
    })


@csrf_exempt
@dashboard_login_required
@require_POST
def update_telegram_group_filter(request, group_id: int):
    item = TelegramGroupFilter.objects.filter(id=group_id).first()
    if not item:
        return _error('群组不存在', status=404)
    payload = _read_payload(request)
    changed = []
    if any(key in payload for key in ('chat_id', 'title', 'username')):
        try:
            chat_id, title, username = _validate_telegram_group_filter_payload(payload, current_id=group_id)
        except ValueError as exc:
            return _error(str(exc), status=400)
        for field, value in {'chat_id': chat_id, 'title': title, 'username': username}.items():
            if getattr(item, field) != value:
                setattr(item, field, value)
                changed.append(field)
    if 'enabled' in payload:
        enabled = _payload_bool(payload, 'enabled')
        if item.enabled != enabled:
            item.enabled = enabled
            changed.append('enabled')
    if 'push_enabled' in payload:
        push_enabled = _payload_bool(payload, 'push_enabled')
        if getattr(item, 'push_enabled', False) != push_enabled:
            item.push_enabled = push_enabled
            changed.append('push_enabled')
    if 'collapsed' in payload:
        collapsed = _payload_bool(payload, 'collapsed')
        if item.collapsed != collapsed:
            item.collapsed = collapsed
            changed.append('collapsed')
    if changed:
        changed.append('updated_at')
        item.save(update_fields=changed)
    return _ok(_telegram_group_filter_payload(item))


@csrf_exempt
@dashboard_login_required
@require_POST
def send_telegram_chat_message(request):
    payload = _read_payload(request)
    text = str(payload.get('text') or '').strip()
    raw_chat_id = payload.get('chat_id')
    raw_account_id = payload.get('login_account_id')
    try:
        chat_id = int(raw_chat_id)
    except (TypeError, ValueError):
        return _error('会话ID无效', status=400)
    if not text:
        return _error('消息内容不能为空', status=400)
    account = None
    if raw_account_id:
        account = TelegramLoginAccount.objects.filter(id=raw_account_id, status='logged_in').first()
    if not account:
        latest = TelegramChatMessage.objects.filter(chat_id=chat_id, login_account__status='logged_in').select_related('login_account').order_by('-created_at', '-id').first()
        account = latest.login_account if latest else None
    if not account:
        account = TelegramLoginAccount.objects.filter(status='logged_in').exclude(session_string__isnull=True).exclude(session_string='').order_by('-updated_at', '-id').first()
    if not account or not account.session_string_plain:
        return _error('没有可用的已登录 Telegram 账号', status=400)
    try:
        api_id, api_hash = _telegram_api_credentials()
        sent = async_to_sync(_telegram_send_message)(account.session_string_plain, chat_id, text, api_id, api_hash)
    except Exception as exc:
        return _error(f'发送失败：{exc}', status=400)
    item = TelegramChatMessage.objects.create(
        login_account=account,
        tg_user_id=chat_id,
        chat_id=chat_id,
        message_id=getattr(sent, 'id', None),
        direction=TelegramChatMessage.DIRECTION_OUT,
        content_type='text',
        text=text[:4000],
        chat_title=str(chat_id),
        source='account',
    )
    account.last_synced_at = timezone.now()
    account.save(update_fields=['last_synced_at', 'updated_at'])
    return _ok(_telegram_message_payload(item))


@csrf_exempt
@dashboard_login_required
@require_POST
def archive_telegram_chat(request):
    payload = _read_payload(request)
    raw_chat_id = payload.get('chat_id')
    archived = bool(payload.get('archived', True))
    try:
        chat_id = int(raw_chat_id)
    except (TypeError, ValueError):
        return _error('会话ID无效', status=400)
    latest = TelegramChatMessage.objects.filter(chat_id=chat_id).order_by('-created_at', '-id').first()
    title = payload.get('title') or (latest.chat_title if latest else '') or str(chat_id)
    if archived:
        TelegramChatArchive.objects.update_or_create(chat_id=chat_id, defaults={'title': title})
    else:
        TelegramChatArchive.objects.filter(chat_id=chat_id).delete()
    return _ok({'chat_id': chat_id, 'archived': archived})


@csrf_exempt
@dashboard_login_required
@require_POST
def create_telegram_login_account(request):
    payload = _read_payload(request)
    label = str(payload.get('label') or '').strip()
    phone = str(payload.get('phone') or '').strip()
    username = _limited_username_string(payload.get('username'))
    note = str(payload.get('note') or '').strip()
    if not label:
        return _error('账号备注不能为空', status=400)
    item = TelegramLoginAccount.objects.create(
        label=label,
        phone=phone or None,
        username=username,
        note=note or '已登记。自动采集仅限 bot 会话内收到的用户资料和聊天记录；不会后台登录个人 Telegram 账号抓取私聊。',
        status='registered',
    )
    return _ok(_telegram_login_account_payload(item))


@dashboard_login_required
@require_GET
def telegram_chat_messages(request):
    keyword = (request.GET.get('keyword') or '').strip().lstrip('@')
    user_id = request.GET.get('user_id')
    tg_user_id = request.GET.get('tg_user_id')
    chat_id = request.GET.get('chat_id')
    qs = TelegramChatMessage.objects.select_related('user', 'login_account').order_by('-created_at', '-id')
    if user_id:
        qs = qs.filter(user_id=user_id)
    if tg_user_id:
        qs = qs.filter(tg_user_id=tg_user_id)
    if chat_id:
        qs = qs.filter(chat_id=chat_id)
    if keyword:
        message_filter = Q(text__icontains=keyword) | Q(username_snapshot__icontains=keyword) | Q(first_name_snapshot__icontains=keyword)
        if keyword.isdigit():
            message_filter |= Q(tg_user_id=int(keyword))
        qs = qs.filter(message_filter)
    return _ok([_telegram_message_payload(item) for item in qs[:300]])


@dashboard_login_required
@require_GET
def products_list(request):
    keyword = _get_keyword(request)
    queryset = Product.objects.order_by('-sort_order', '-id')
    queryset = _apply_keyword_filter(queryset, keyword, ['name', 'description', 'content_text'])
    items = list(queryset[:200])
    return _ok([
        {
            'id': item.id,
            'name': item.name,
            'description': item.description,
            'price': _decimal_to_str(item.price),
            'content_type': item.content_type,
            'content_text': item.content_text,
            'content_image': item.content_image,
            'content_video': item.content_video,
            'stock': item.stock,
            'is_active': item.is_active,
            'sort_order': item.sort_order,
            'created_at': _iso(item.created_at),
            'updated_at': _iso(item.updated_at),
        }
        for item in items
    ])


@csrf_exempt
@dashboard_login_required
@require_POST
def create_product(request):
    payload = _read_payload(request)
    name = (payload.get('name') or '').strip()
    if not name:
        return _error('商品名称不能为空', status=400)
    try:
        price = _parse_decimal(payload.get('price'), '商品价格')
        stock = int(payload.get('stock', -1))
        sort_order = int(payload.get('sort_order', 0))
    except (ValueError, TypeError):
        return _error('商品价格或库存格式不正确', status=400)
    content_type = (payload.get('content_type') or Product.CONTENT_TEXT).strip()
    if content_type not in {choice[0] for choice in Product.CONTENT_CHOICES}:
        return _error('商品内容类型不正确', status=400)
    item = Product.objects.create(
        name=name,
        description=(payload.get('description') or '').strip() or None,
        price=price,
        content_type=content_type,
        content_text=payload.get('content_text') or None,
        content_image=payload.get('content_image') or None,
        content_video=payload.get('content_video') or None,
        stock=stock,
        is_active=str(payload.get('is_active', 'true')).lower() in {'1', 'true', 'yes', 'on'},
        sort_order=sort_order,
    )
    return _ok({'id': item.id})


@csrf_exempt
@dashboard_login_required
@require_POST
def update_product(request, product_id: int):
    item = Product.objects.filter(id=product_id).first()
    if not item:
        return _error('商品不存在', status=404)
    payload = _read_payload(request)
    if 'name' in payload:
        name = (payload.get('name') or '').strip()
        if not name:
            return _error('商品名称不能为空', status=400)
        item.name = name
    if 'description' in payload:
        item.description = (payload.get('description') or '').strip() or None
    if 'price' in payload:
        try:
            item.price = _parse_decimal(payload.get('price'), '商品价格')
        except ValueError as exc:
            return _error(str(exc), status=400)
    if 'content_type' in payload:
        content_type = (payload.get('content_type') or '').strip()
        if content_type not in {choice[0] for choice in Product.CONTENT_CHOICES}:
            return _error('商品内容类型不正确', status=400)
        item.content_type = content_type
    for field in ('content_text', 'content_image', 'content_video'):
        if field in payload:
            value = payload.get(field)
            setattr(item, field, value or None)
    if 'stock' in payload:
        try:
            item.stock = int(payload.get('stock'))
        except (ValueError, TypeError):
            return _error('库存格式不正确', status=400)
    if 'sort_order' in payload:
        try:
            item.sort_order = int(payload.get('sort_order'))
        except (ValueError, TypeError):
            return _error('排序值格式不正确', status=400)
    if 'is_active' in payload:
        item.is_active = str(payload.get('is_active')).lower() in {'1', 'true', 'yes', 'on'}
    item.save()
    return _ok({'id': item.id})


__all__ = [
    'auth_codes',
    'auth_login',
    'auth_totp_bind',
    'auth_totp_start',
    'auth_logout',
    'auth_refresh',
    'bot_operation_logs',
    'cloud_accounts_list',
    'create_cloud_account',
    'create_product',
    'csrf',
    'delete_cloud_account',
    'init_site_configs',
    'ip_delete_logs',
    'lifecycle_plans',
    'run_orphan_asset_delete_plan',
    'run_shutdown_plan_order',
    'run_unattached_ip_delete_plan',
    'me',
    'overview',
    'products_list',
    'site_config_groups',
    'site_configs_list',
    'test_daily_expiry_summary_notification',
    'update_cloud_account',
    'update_product',
    'update_site_config',
    'update_user_balance',
    'update_user_discount',
    'user_balance_details',
    'user_info',
    'users_list',
    'verify_cloud_account',
]
