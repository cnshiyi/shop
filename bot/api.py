"""bot 域后台 API。"""

import base64
import binascii
import hashlib
import hmac
import json
import re
import secrets
import struct
import time
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from urllib.parse import quote

from asgiref.sync import async_to_sync

from django.contrib.auth import authenticate, login, logout
from django.contrib.sessions.models import Session
from django.db import ProgrammingError, transaction
from django.db.models import Q, CharField, Count, Max, Sum
from django.db.models.functions import Cast
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from bot.models import BotOperationLog, TelegramChatArchive, TelegramChatMessage, TelegramGroupFilter, TelegramLoginAccount, TelegramUser
from bot.services import _get_or_create_user_sync
from cloud.lifecycle import _shutdown_enabled_for_order
from cloud.lifecycle_execution import run_orphan_asset_delete, run_shutdown_order_delete, run_unattached_ip_release
from cloud.lifecycle_schedule import compute_order_lifecycle_schedule, compute_unattached_ip_release_at
from cloud.models import AddressMonitor, CloudAsset, CloudIpLog, CloudLifecyclePlan, CloudLifecyclePlanNote, CloudServerOrder
from cloud.sync_safety import missing_confirmation_state
from core.cloud_accounts import cloud_account_label, cloud_account_label_variants
from core.models import CloudAccountConfig, SiteConfig
from core.runtime_config import get_runtime_config
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


def dashboard_superuser_required(view_func):
    @dashboard_login_required
    def wrapped(request, *args, **kwargs):
        if not getattr(request.user, 'is_superuser', False):
            return _error('需要超级管理员权限', status=403)
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


def _parse_runtime_time_point(raw: str, fallback: str = '15:00') -> tuple[int, int]:
    try:
        hour_text, minute_text = str(raw or fallback).strip().split(':', 1)
        return min(max(int(hour_text), 0), 23), min(max(int(minute_text), 0), 59)
    except Exception:
        hour_text, minute_text = fallback.split(':', 1)
        return int(hour_text), int(minute_text)


def _runtime_time(key: str, default: str = '15:00') -> tuple[int, int]:
    raw = str(get_runtime_config(key, default) or default).strip()
    if '-' in raw:
        raw = raw.split('-', 1)[0].strip()
    return _parse_runtime_time_point(raw, default)


def _server_asset_lifecycle_times(asset):
    expires_at = getattr(asset, 'actual_expires_at', None)
    if not expires_at:
        return None, None, None
    schedule = compute_order_lifecycle_schedule(expires_at)
    return expires_at, schedule.suspend_at, schedule.delete_at


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
            or ('StaticIp' in str(getattr(asset, 'provider_resource_id', '') or ''))
        )
    )


def _asset_waiting_manual_time_q():
    return (
        Q(actual_expires_at__isnull=True)
        | Q(provider_status__icontains='待人工添加时间')
        | Q(note__icontains='等待人工添加真实到期时间')
        | Q(note__icontains='等待人工添加时间')
    )


def _active_cloud_asset_plan_rows(limit=None):
    return _proxy_list_cloud_asset_plan_rows(limit=limit)


def _proxy_list_account_disabled(asset, *, active_account_labels: set[str] | None = None):
    active_account_labels = active_account_labels if active_account_labels is not None else set(_active_cloud_account_labels())
    account = getattr(asset, 'cloud_account', None)
    account_label = str(
        getattr(asset, 'account_label', '')
        or cloud_account_label(account)
        or getattr(getattr(asset, 'order', None), 'account_label', '')
        or ''
    ).strip()
    return bool(
        getattr(account, 'is_active', True) is False
        or (account_label and account_label not in active_account_labels)
    )


def _active_cloud_account_labels():
    labels = []
    for account in CloudAccountConfig.objects.filter(
        provider__in=[CloudAccountConfig.PROVIDER_AWS, CloudAccountConfig.PROVIDER_ALIYUN],
        is_active=True,
    ):
        labels.extend(cloud_account_label_variants(account))
    return list(dict.fromkeys(labels))


def _proxy_list_cloud_asset_queryset():
    unattached_ip_values = list(
        CloudAsset.objects.filter(
            kind=CloudAsset.KIND_SERVER,
            provider_status__contains='未附加固定IP',
            public_ip__isnull=False,
        ).exclude(public_ip='').values_list('public_ip', flat=True)[:1000]
    )
    return (
        CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER)
        .exclude(
            Q(status__in=[CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED])
            & (Q(public_ip__in=unattached_ip_values) | Q(previous_public_ip__in=unattached_ip_values))
        )
    )


def _proxy_list_cloud_asset_plan_rows(limit=None):
    queryset = _proxy_list_cloud_asset_queryset().distinct().order_by('-sort_order', 'actual_expires_at', '-updated_at', '-id')
    rows = _dedupe_cloud_asset_plan_rows(list(queryset))
    active_account_labels = set(_active_cloud_account_labels())
    rows = [
        asset for asset in rows
        if not _proxy_list_account_disabled(asset, active_account_labels=active_account_labels)
    ]
    if limit:
        return rows[: max(1, int(limit))]
    return rows


def _asset_is_sync_only_lifecycle(asset):
    return getattr(asset, 'provider', '') == 'aliyun_simple'


def _asset_deleted_or_missing(asset):
    status = getattr(asset, 'status', '')
    provider_status = str(getattr(asset, 'provider_status', '') or '')
    note = str(getattr(asset, 'note', '') or '')
    if status in {
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_TERMINATED,
        CloudAsset.STATUS_TERMINATING,
    }:
        return True
    if any(marker in provider_status for marker in ['云上未找到', '已到期删除', '已删除']):
        return True
    if any(marker in note for marker in ['云上不存在', '已标记删除']) and status not in {
        CloudAsset.STATUS_RUNNING,
        CloudAsset.STATUS_PENDING,
        CloudAsset.STATUS_STARTING,
        CloudAsset.STATUS_STOPPED,
        CloudAsset.STATUS_SUSPENDED,
        CloudAsset.STATUS_UNKNOWN,
    }:
        return True
    return False


def _cloud_asset_plan_stats(assets=None):
    plan_assets = list(assets) if assets is not None else _active_cloud_asset_plan_rows()
    server_assets = [
        asset for asset in plan_assets
        if not _asset_is_unattached_ip(asset)
        and 'StaticIp' not in str(getattr(asset, 'provider_resource_id', '') or '')
    ]
    unattached_assets = [
        asset for asset in plan_assets
        if _asset_is_unattached_ip(asset)
        or 'StaticIp' in str(getattr(asset, 'provider_resource_id', '') or '')
    ]
    missing_expiry_assets = [
        asset for asset in server_assets
        if not getattr(asset, 'actual_expires_at', None)
        or '待人工添加时间' in str(getattr(asset, 'provider_status', '') or '')
        or '等待人工添加真实到期时间' in str(getattr(asset, 'note', '') or '')
        or '等待人工添加时间' in str(getattr(asset, 'note', '') or '')
    ]
    return {
        'source_asset_count': len(plan_assets),
        'server_asset_count': len(server_assets),
        'missing_expiry_count': len(missing_expiry_assets),
        'unattached_ip_count': len(unattached_assets),
    }


def _visible_lifecycle_plan_stats(shutdown_items: list[dict] | None = None, ip_delete_items: list[dict] | None = None):
    shutdown_items = list(shutdown_items or [])
    ip_delete_items = list(ip_delete_items or [])
    active_shutdown = [item for item in shutdown_items if not item.get('is_history')]
    pending_ip_delete = [
        item for item in ip_delete_items
        if not item.get('is_history') and item.get('plan_state') not in {'completed'}
    ]
    missing_expiry = [
        item for item in active_shutdown
        if item.get('queue_status') == 'waiting_manual_time'
        or item.get('plan_state') == 'waiting_manual_time'
        or not item.get('delete_at')
    ]
    return {
        'source_asset_count': len(active_shutdown) + len(pending_ip_delete),
        'server_asset_count': len(active_shutdown),
        'missing_expiry_count': len(missing_expiry),
        'unattached_ip_count': len(pending_ip_delete),
    }


def _active_cloud_asset_queryset():
    active_account_ids = list(CloudAccountConfig.objects.filter(is_active=True).values_list('id', flat=True))
    active_account_labels = [
        label
        for account in CloudAccountConfig.objects.filter(is_active=True)
        for label in cloud_account_label_variants(account)
    ]
    inactive_account_labels = [
        label
        for account in CloudAccountConfig.objects.filter(is_active=False)
        for label in cloud_account_label_variants(account)
    ]
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


def _cloud_asset_display_ip(asset):
    return str(getattr(asset, 'public_ip', '') or getattr(asset, 'previous_public_ip', '') or '').strip()


def _dedupe_cloud_asset_plan_rows(assets):
    best = {}
    for asset in assets:
        ip = _cloud_asset_display_ip(asset)
        cloud_account_id = getattr(asset, 'cloud_account_id', None)
        account_label = str(
            getattr(asset, 'account_label', '')
            or cloud_account_label(getattr(asset, 'cloud_account', None))
            or ''
        ).strip()
        account_key = f'cloud_account:{cloud_account_id}' if cloud_account_id else f'label:{account_label}'
        provider = str(getattr(asset, 'provider', '') or '').strip()
        region_code = str(getattr(asset, 'region_code', '') or '').strip()
        key = f'{provider}:{account_key}:{region_code}:{ip}' if ip else f'id:{asset.id}'
        is_unattached = _asset_is_unattached_ip(asset) or 'StaticIp' in str(getattr(asset, 'provider_resource_id', '') or '')
        is_deleted = asset.status in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED}
        score = (
            3 if is_unattached else 0,
            2 if asset.status == CloudAsset.STATUS_DELETING else 0,
            1 if not is_deleted else 0,
            1 if asset.order_id else 0,
            1 if asset.user_id else 0,
            asset.updated_at.timestamp() if asset.updated_at else 0,
            asset.id,
        )
        current = best.get(key)
        if not current or score > current[0]:
            best[key] = (score, asset)
    return [item[1] for item in best.values()]


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


def _shutdown_execution_note(*, status_label, is_success, executed_at, action, failure_reason, deletion_source=''):
    parts = [
        f'执行状态：{status_label or "-"}',
        f'是否成功：{"成功" if is_success else "失败"}',
        f'执行时间：{_fmt_dashboard_dt(executed_at)}',
        f'执行内容：{action or "-"}',
    ]
    if deletion_source:
        parts.append(f'删除来源：{deletion_source}')
    parts.append(f'失败原因：{failure_reason or "-"}')
    return '；'.join(parts)


def _delete_source_label(note='', *, default='到期自动删除'):
    text = str(note or '')
    match = re.search(r'删除来源：([^；\n]+)', text)
    if match:
        return match.group(1).strip() or default
    lowered = text.lower()
    if any(keyword in text for keyword in ['人工手动删除', '手动删除', '人工删除']) or 'manual' in lowered:
        return '人工手动删除'
    if any(keyword in text for keyword in ['云上不存在', '已标记删除', '云端已不存在', '同步删除', '同步校验']):
        return '同步校验删除'
    return default


def _with_delete_source(note, source):
    text = str(note or '').strip()
    if '删除来源：' in text:
        return text
    return f'删除来源：{source}；{text}' if text else f'删除来源：{source}'


def _compact_dashboard_note(note, *, max_chars=800):
    noisy_prefixes = (
        'Get:', 'Hit:', 'Ign:', 'Err:', 'Fetched ', 'Reading package lists',
        'Building dependency tree', 'Reading state information', 'Selecting previously',
        'Preparing to unpack', 'Unpacking ', 'Setting up ', 'Processing triggers',
        'Created symlink ', 'Synchronizing state', 'Need to get ', 'After this operation',
        'The following ', '0 upgraded,', 'debconf:', 'apt-listchanges:', 'WARNING:',
    )
    lines = []
    seen = set()
    for raw_line in str(note or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if 'tg://proxy?' in line or 'socks5://' in line:
            continue
        if line.startswith(('TG链接:', '分享链接:', '扩展链接:', 'SOCKS5链接:')):
            continue
        if line.startswith(noisy_prefixes):
            continue
        if line.startswith('状态: ') and ('最近同步:' in line or '覆盖同步时间:' in line):
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    text = '\n'.join(lines)
    if max_chars and len(text) > max_chars:
        return text[:max_chars].rstrip() + '\n...（备注过长，已折叠预览）'
    return text


def _asset_note_text(asset) -> str:
    return str(getattr(asset, 'note', '') or '').strip()


def _asset_display_note(asset, *, fallback: str = '', max_chars: int = 500) -> str:
    return _compact_dashboard_note(_asset_note_text(asset) or fallback, max_chars=max_chars)


def _sync_asset_note_to_server(asset):
    return 0


def _lifecycle_plan_note_scope(item_type='', *, order_id=None, asset_id=None):
    item_type = str(item_type or '').strip()
    if item_type == 'order' or order_id:
        return CloudLifecyclePlanNote.PLAN_KIND_SHUTDOWN_ORDER, int(order_id or 0), None
    if item_type == 'orphan_asset':
        return CloudLifecyclePlanNote.PLAN_KIND_ORPHAN_ASSET_DELETE, None, int(asset_id or 0)
    return CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE, None, int(asset_id or 0)


def _lifecycle_plan_note_maps(*, order_ids=None, orphan_asset_ids=None, unattached_asset_ids=None):
    order_ids = [int(item) for item in (order_ids or []) if int(item or 0) > 0]
    orphan_asset_ids = [int(item) for item in (orphan_asset_ids or []) if int(item or 0) > 0]
    unattached_asset_ids = [int(item) for item in (unattached_asset_ids or []) if int(item or 0) > 0]
    conditions = Q()
    if order_ids:
        conditions |= Q(plan_kind=CloudLifecyclePlanNote.PLAN_KIND_SHUTDOWN_ORDER, order_id__in=order_ids)
    if orphan_asset_ids:
        conditions |= Q(plan_kind=CloudLifecyclePlanNote.PLAN_KIND_ORPHAN_ASSET_DELETE, asset_id__in=orphan_asset_ids)
    if unattached_asset_ids:
        conditions |= Q(plan_kind=CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE, asset_id__in=unattached_asset_ids)
    if not conditions:
        return {}, {}, {}
    order_map = {}
    orphan_asset_map = {}
    unattached_asset_map = {}
    rows = CloudLifecyclePlanNote.objects.filter(conditions).order_by('-updated_at', '-id')
    for row in rows:
        if row.plan_kind == CloudLifecyclePlanNote.PLAN_KIND_SHUTDOWN_ORDER and row.order_id and row.order_id not in order_map:
            order_map[row.order_id] = row
        elif row.plan_kind == CloudLifecyclePlanNote.PLAN_KIND_ORPHAN_ASSET_DELETE and row.asset_id and row.asset_id not in orphan_asset_map:
            orphan_asset_map[row.asset_id] = row
        elif row.plan_kind == CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE and row.asset_id and row.asset_id not in unattached_asset_map:
            unattached_asset_map[row.asset_id] = row
    return order_map, orphan_asset_map, unattached_asset_map


def _lifecycle_plan_note_text(note_obj) -> str:
    return str(getattr(note_obj, 'note', '') or '').strip()


def _save_lifecycle_plan_note(*, item_type='', note='', order=None, asset=None, actor=None):
    plan_kind, order_id, asset_id = _lifecycle_plan_note_scope(
        item_type,
        order_id=getattr(order, 'id', None),
        asset_id=getattr(asset, 'id', None),
    )
    filters = {'plan_kind': plan_kind}
    if order_id:
        filters['order_id'] = order_id
    elif asset_id:
        filters['asset_id'] = asset_id
    else:
        return None
    qs = CloudLifecyclePlanNote.objects.filter(**filters).order_by('-updated_at', '-id')
    value = str(note or '').strip()
    keep = qs.first()
    if not value:
        qs.delete()
        return None
    if keep:
        updates = []
        if keep.note != value:
            keep.note = value
            updates.append('note')
        if actor and getattr(actor, 'is_authenticated', False):
            keep.updated_by = actor
            updates.append('updated_by')
        if updates:
            keep.save(update_fields=[*updates, 'updated_at'])
        qs.exclude(id=keep.id).delete()
        return keep
    create_kwargs = {'note': value}
    if order_id:
        create_kwargs['order'] = order
    if asset_id:
        create_kwargs['asset'] = asset
    if actor and getattr(actor, 'is_authenticated', False):
        create_kwargs['created_by'] = actor
        create_kwargs['updated_by'] = actor
    return CloudLifecyclePlanNote.objects.create(plan_kind=plan_kind, **create_kwargs)


def _cloud_ip_trace_note_newest_first(note):
    text = _compact_dashboard_note(note, max_chars=1200)
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


def _lifecycle_plan_row_source_key(item: dict, *, plan_kind: str, data_group: str) -> str:
    existing_source_key = str(item.get('source_key') or '').strip()
    if existing_source_key.startswith(f'{data_group}:'):
        return existing_source_key
    if item.get('asset_id'):
        return f'{data_group}:asset:{item.get("asset_id")}'
    if plan_kind == CloudLifecyclePlan.PLAN_KIND_SHUTDOWN_ORDER and item.get('order_id'):
        return f'{data_group}:order:{item.get("order_id")}'
    if item.get('id') is not None:
        return f'{data_group}:item:{item.get("id")}'
    return ''


def _lifecycle_plan_row_defaults(item: dict, *, plan_kind: str, data_group: str, source_key: str) -> dict:
    note = str(item.get('note') or '').strip()
    display_note = str(item.get('display_note') or _compact_dashboard_note(note, max_chars=500) or '').strip()
    next_run_at = item.get('next_run_at') or item.get('delete_at') or item.get('executed_at')
    return {
        'source_key': source_key,
        'plan_kind': plan_kind,
        'data_group': data_group,
        'queue_status': item.get('queue_status'),
        'queue_status_label': item.get('queue_status_label'),
        'order_id': item.get('order_id'),
        'asset_id': item.get('asset_id'),
        'user_id': item.get('user_id'),
        'user_display_name': item.get('user_display_name'),
        'username_label': item.get('username_label'),
        'ip': item.get('ip') or item.get('public_ip') or '',
        'provider': item.get('provider'),
        'provider_label': item.get('provider_label'),
        'status': item.get('status'),
        'status_label': item.get('status_label'),
        'service_expires_at': parse_datetime(item['service_expires_at']) if item.get('service_expires_at') else None,
        'suspend_at': parse_datetime(item['suspend_at']) if item.get('suspend_at') else None,
        'delete_at': parse_datetime(item['delete_at']) if item.get('delete_at') else None,
        'ip_recycle_at': parse_datetime(item['ip_recycle_at']) if item.get('ip_recycle_at') else None,
        'next_run_at': parse_datetime(next_run_at) if isinstance(next_run_at, str) and next_run_at else next_run_at,
        'logged_at': parse_datetime(item['logged_at']) if item.get('logged_at') else (parse_datetime(item['executed_at']) if item.get('executed_at') else None),
        'last_failure_reason': item.get('last_failure_reason') or item.get('failure_reason'),
        'execution_status': item.get('execution_status'),
        'execution_plan': item.get('execution_plan'),
        'note': note,
        'display_note': display_note,
        'deletion_source_label': item.get('deletion_source_label'),
        'related_path': item.get('related_path'),
        'detail_path': item.get('detail_path'),
        'order_detail_path': item.get('order_detail_path'),
        'order_link_path': item.get('order_link_path'),
        'asset_detail_path': item.get('asset_detail_path'),
        'source_snapshot': dict(item),
    }


def _lifecycle_plan_row_payload(row) -> dict:
    payload = dict(row.source_snapshot or {})
    payload.update({
        'id': row.id,
        'source_key': row.source_key,
        'plan_kind': row.plan_kind,
        'data_group': row.data_group,
        'queue_status': row.queue_status,
        'queue_status_label': row.queue_status_label,
        'order_id': row.order_id,
        'asset_id': row.asset_id,
        'user_id': row.user_id,
        'user_display_name': row.user_display_name,
        'username_label': row.username_label,
        'ip': row.ip or payload.get('ip') or payload.get('public_ip') or '',
        'provider': row.provider or payload.get('provider'),
        'provider_label': row.provider_label or payload.get('provider_label'),
        'status': row.status or payload.get('status'),
        'status_label': row.status_label or payload.get('status_label'),
        'service_expires_at': _iso(row.service_expires_at) or payload.get('service_expires_at'),
        'suspend_at': _iso(row.suspend_at) or payload.get('suspend_at'),
        'delete_at': _iso(row.delete_at) or payload.get('delete_at'),
        'ip_recycle_at': _iso(row.ip_recycle_at) or payload.get('ip_recycle_at'),
        'next_run_at': _iso(row.next_run_at) or payload.get('next_run_at'),
        'logged_at': _iso(row.logged_at) or payload.get('logged_at') or payload.get('executed_at'),
        'last_failure_reason': row.last_failure_reason,
        'failure_reason': row.last_failure_reason or payload.get('failure_reason') or '',
        'execution_status': row.execution_status,
        'execution_plan': row.execution_plan,
        'note': row.note or '',
        'display_note': row.display_note or _compact_dashboard_note(row.note, max_chars=500),
        'deletion_source_label': row.deletion_source_label,
        'related_path': row.related_path or payload.get('related_path') or '',
        'detail_path': row.detail_path or payload.get('detail_path') or '',
        'order_detail_path': row.order_detail_path or payload.get('order_detail_path') or '',
        'order_link_path': row.order_link_path or payload.get('order_link_path') or '',
        'asset_detail_path': row.asset_detail_path or payload.get('asset_detail_path') or '',
    })
    return payload


def _refresh_plan_payload_from_assets(items):
    asset_ids = [item.get('asset_id') for item in items if item.get('asset_id')]
    if not asset_ids:
        return items
    assets = {
        asset.id: asset
        for asset in CloudAsset.objects.select_related('cloud_account', 'user', 'order').filter(id__in=asset_ids)
    }
    refreshed = []
    for item in items:
        asset = assets.get(item.get('asset_id'))
        if not asset:
            refreshed.append(item)
            continue
        item = dict(item)
        item['ip'] = asset.public_ip or asset.previous_public_ip or item.get('ip') or ''
        item['provider'] = asset.provider
        item['provider_label'] = _provider_label(asset.provider)
        item['status'] = asset.status
        item['status_label'] = _status_label(asset.status, CloudAsset.STATUS_CHOICES)
        item['provider_status'] = asset.provider_status or ''
        if item.get('plan_kind') == CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE:
            item['service_expires_at'] = _iso(asset.actual_expires_at)
            item['delete_at'] = _iso(asset.actual_expires_at)
            item['next_run_at'] = _iso(asset.actual_expires_at)
        else:
            expires_at, suspend_at, delete_at = _server_asset_lifecycle_times(asset)
            item['service_expires_at'] = _iso(expires_at)
            item['suspend_at'] = _iso(suspend_at)
            item['delete_at'] = _iso(delete_at)
            item['next_run_at'] = _iso(delete_at)
            item['execution_plan'] = f'删除服务器 {_fmt_dashboard_dt(delete_at)}' if delete_at else '等待删除时间'
        item['asset_name'] = asset.asset_name
        item['source_note'] = str(item.get('source_note') or '').strip() or _asset_note_text(asset)
        if item.get('data_group') == 'active' and item.get('plan_kind') in {
            CloudLifecyclePlan.PLAN_KIND_ORPHAN_ASSET_DELETE,
            CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
        }:
            item['note'] = _asset_note_text(asset)
            item['display_note'] = _asset_display_note(asset, fallback=item.get('display_note') or item.get('source_note') or '')
        item['detail_path'] = f'/admin/cloud-assets/{asset.id}'
        item['related_path'] = f'/admin/cloud-assets/{asset.id}'
        item['asset_detail_path'] = f'/admin/cloud-assets/{asset.id}'
        account_name, external_account_id = _cloud_account_labels(asset)
        item['cloud_account_id'] = asset.cloud_account_id
        item['cloud_account_name'] = account_name
        item['external_account_id'] = external_account_id
        refreshed.append(item)
    _persist_refreshed_asset_plan_notes(refreshed)
    return refreshed


def _persist_refreshed_asset_plan_notes(items):
    row_ids = [
        item.get('id')
        for item in items
        if item.get('id')
        and item.get('data_group') == 'active'
        and item.get('plan_kind') in {
            CloudLifecyclePlan.PLAN_KIND_ORPHAN_ASSET_DELETE,
            CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
        }
    ]
    if not row_ids:
        return 0
    rows = {
        row.id: row
        for row in CloudLifecyclePlan.objects.filter(id__in=row_ids).only('id', 'note', 'display_note')
    }
    dirty_rows = []
    for item in items:
        row = rows.get(item.get('id'))
        if not row:
            continue
        note = str(item.get('note') or '').strip()
        display_note = str(item.get('display_note') or '').strip()
        if row.note == note and row.display_note == display_note:
            continue
        row.note = note
        row.display_note = display_note
        dirty_rows.append(row)
    if dirty_rows:
        CloudLifecyclePlan.objects.bulk_update(dirty_rows, ['note', 'display_note'], batch_size=500)
    return len(dirty_rows)


def _upsert_lifecycle_plan_rows(items: list[dict], *, plan_kind: str, data_group: str):
    source_keys = []
    payload_map = {}
    for item in items:
        source_key = _lifecycle_plan_row_source_key(item, plan_kind=plan_kind, data_group=data_group)
        if not source_key:
            continue
        source_keys.append(source_key)
        payload_map[source_key] = _lifecycle_plan_row_defaults(item, plan_kind=plan_kind, data_group=data_group, source_key=source_key)
    existing_rows = {row.source_key: row for row in CloudLifecyclePlan.objects.filter(plan_kind=plan_kind, data_group=data_group, source_key__in=source_keys)}
    create_rows = []
    update_rows = []
    update_fields = ['queue_status', 'queue_status_label', 'order_id', 'asset_id', 'user_id', 'user_display_name', 'username_label', 'ip', 'provider', 'provider_label', 'status', 'status_label', 'service_expires_at', 'suspend_at', 'delete_at', 'ip_recycle_at', 'next_run_at', 'logged_at', 'last_failure_reason', 'execution_status', 'execution_plan', 'note', 'display_note', 'deletion_source_label', 'related_path', 'detail_path', 'order_detail_path', 'order_link_path', 'asset_detail_path', 'source_snapshot', 'updated_at']
    for source_key, defaults in payload_map.items():
        row = existing_rows.get(source_key)
        if row:
            changed = False
            for field, value in defaults.items():
                if getattr(row, field) != value:
                    setattr(row, field, value)
                    changed = True
            if changed:
                update_rows.append(row)
        else:
            create_rows.append(CloudLifecyclePlan(**defaults))
    if create_rows:
        CloudLifecyclePlan.objects.bulk_create(create_rows, batch_size=500)
    if update_rows:
        CloudLifecyclePlan.objects.bulk_update(update_rows, update_fields, batch_size=500)
    qs = CloudLifecyclePlan.objects.filter(plan_kind=plan_kind, data_group=data_group)
    if source_keys:
        qs.exclude(source_key__in=source_keys).delete()
    else:
        qs.delete()


def _collect_lifecycle_plan_rows(*, limit=1000):
    now = timezone.now()
    shutdown_queue = _collect_shutdown_plan_queue(now, limit=limit)
    history_qs = CloudIpLog.objects.select_related('order', 'user').filter(event_type__in=['deleted', 'delete_failed', 'delete_skipped']).order_by('-created_at', '-id')[:limit]
    shutdown_history_items = [_shutdown_history_item_payload(log) for log in history_qs]
    history_order_ids = {item.get('order_id') for item in shutdown_history_items if item.get('order_id')}
    fallback_deleted_orders = CloudServerOrder.objects.select_related('user').filter(status='deleted').exclude(id__in=list(history_order_ids)).order_by('-updated_at', '-id')[:limit]
    shutdown_history_items.extend(_shutdown_history_order_payload(order) for order in fallback_deleted_orders)
    source_assets = shutdown_queue.get('source_assets') or []
    ip_delete_items = _unattached_ip_delete_items(limit=limit, assets=source_assets)
    return {'due_items': shutdown_queue['due_items'], 'future_plan_items': shutdown_queue['future_plan_items'], 'history_items': shutdown_history_items, 'shutdown_items': [*shutdown_queue['due_items'], *shutdown_queue['future_plan_items']], 'ip_delete_items': ip_delete_items}


def _sync_lifecycle_plan_table(*, limit=1000):
    archived_ip_delete_history = _completed_unattached_ip_plan_history_items()
    stored_ip_delete_history = _cloud_lifecycle_plan_items(
        plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
        data_group='history',
    )
    bundle = _collect_lifecycle_plan_rows(limit=limit)
    shutdown_active = [*bundle['due_items'], *bundle['future_plan_items']]
    shutdown_order_active = [item for item in shutdown_active if item.get('item_type') == 'order']
    orphan_asset_active = [item for item in shutdown_active if item.get('item_type') == 'orphan_asset']
    shutdown_history = bundle['history_items']
    ip_delete_active = [item for item in bundle['ip_delete_items'] if not item.get('is_history')]
    ip_delete_history = [
        *stored_ip_delete_history,
        *archived_ip_delete_history,
        *[item for item in bundle['ip_delete_items'] if item.get('is_history')],
    ]
    _upsert_lifecycle_plan_rows(shutdown_order_active, plan_kind=CloudLifecyclePlan.PLAN_KIND_SHUTDOWN_ORDER, data_group='active')
    _upsert_lifecycle_plan_rows(orphan_asset_active, plan_kind=CloudLifecyclePlan.PLAN_KIND_ORPHAN_ASSET_DELETE, data_group='active')
    _upsert_lifecycle_plan_rows(shutdown_history, plan_kind=CloudLifecyclePlan.PLAN_KIND_SHUTDOWN_ORDER, data_group='history')
    _upsert_lifecycle_plan_rows(ip_delete_active, plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE, data_group='active')
    _upsert_lifecycle_plan_rows(ip_delete_history, plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE, data_group='history')
    bundle['ip_delete_items'] = [*ip_delete_active, *ip_delete_history]
    return bundle


def _cloud_lifecycle_plan_items(*, plan_kind: str, data_group: str):
    rows = CloudLifecyclePlan.objects.filter(plan_kind=plan_kind, data_group=data_group).order_by('delete_at', 'next_run_at', '-updated_at', '-id')
    return [_lifecycle_plan_row_payload(row) for row in rows]


def _plan_item_dt(item: dict, *keys: str, default=None):
    for key in keys:
        value = item.get(key)
        if not value:
            continue
        parsed = parse_datetime(value) if isinstance(value, str) else value
        if not parsed:
            continue
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed
    return default


def _sort_lifecycle_active_items(items: list[dict]) -> list[dict]:
    far_future = datetime.max.replace(tzinfo=dt_timezone.utc)
    return sorted(
        items,
        key=lambda item: (
            _plan_item_dt(item, 'delete_at', 'next_run_at', 'suspend_at', default=far_future),
            str(item.get('user_id') or item.get('tg_user_id') or item.get('user_display_name') or item.get('username_label') or '').lower(),
            str(item.get('plan_kind') or ''),
            str(item.get('id') or item.get('asset_id') or item.get('order_id') or ''),
        ),
    )


def _sort_lifecycle_history_items(items: list[dict]) -> list[dict]:
    far_past = datetime.min.replace(tzinfo=dt_timezone.utc)
    return sorted(
        items,
        key=lambda item: _plan_item_dt(item, 'executed_at', 'logged_at', 'delete_at', default=far_past),
        reverse=True,
    )


def _lifecycle_plan_last_refresh_at():
    return CloudLifecyclePlan.objects.filter(plan_kind__in=[
        CloudLifecyclePlan.PLAN_KIND_SHUTDOWN_ORDER,
        CloudLifecyclePlan.PLAN_KIND_ORPHAN_ASSET_DELETE,
        CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
    ]).order_by('-updated_at').values_list('updated_at', flat=True).first()


def _lifecycle_plan_table_has_rows() -> bool:
    return CloudLifecyclePlan.objects.filter(plan_kind__in=[
        CloudLifecyclePlan.PLAN_KIND_SHUTDOWN_ORDER,
        CloudLifecyclePlan.PLAN_KIND_ORPHAN_ASSET_DELETE,
        CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
    ]).exists()


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


def _cloud_ip_trace_maps_for_assets(assets):
    asset_ids = [asset.id for asset in assets if getattr(asset, 'id', None)]
    order_ids = [asset.order_id for asset in assets if getattr(asset, 'order_id', None)]
    ips = [
        ip
        for asset in assets
        for ip in [str(getattr(asset, 'public_ip', '') or '').strip(), str(getattr(asset, 'previous_public_ip', '') or '').strip()]
        if ip
    ]
    conditions = Q()
    if asset_ids:
        conditions |= Q(asset_id__in=asset_ids)
    if order_ids:
        conditions |= Q(order_id__in=order_ids)
    if ips:
        conditions |= Q(public_ip__in=ips) | Q(previous_public_ip__in=ips)
    if not conditions:
        return {}, {}, {}
    logs = CloudIpLog.objects.select_related('order', 'asset', 'user').filter(conditions).order_by('-id')[:5000]
    by_asset = {}
    by_order = {}
    by_ip = {}
    for log in logs:
        if log.asset_id and log.asset_id not in by_asset:
            by_asset[log.asset_id] = log
        if log.order_id and log.order_id not in by_order:
            by_order[log.order_id] = log
        for ip in [str(log.public_ip or '').strip(), str(log.previous_public_ip or '').strip()]:
            if ip and ip not in by_ip:
                by_ip[ip] = log
    return by_asset, by_order, by_ip


def _cloud_ip_trace_from_maps(asset, trace_maps):
    by_asset, by_order, by_ip = trace_maps
    if getattr(asset, 'id', None) in by_asset:
        return by_asset[asset.id]
    if getattr(asset, 'order_id', None) in by_order:
        return by_order[asset.order_id]
    for ip in [str(getattr(asset, 'public_ip', '') or '').strip(), str(getattr(asset, 'previous_public_ip', '') or '').strip()]:
        if ip and ip in by_ip:
            return by_ip[ip]
    return None


def _shutdown_log_items(limit=100):
    cutoff = timezone.now() - timezone.timedelta(days=7)

    items = []
    assets = list(
        _active_cloud_asset_queryset()
        .filter(Q(actual_expires_at__isnull=False) | Q(order__service_expires_at__isnull=False))
        .order_by('actual_expires_at', '-updated_at')[:500]
    )
    trace_maps = _cloud_ip_trace_maps_for_assets(assets)
    seen_trace_ids = set()
    for asset in assets:
        if _asset_is_unattached_ip(asset) and asset.status in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_TERMINATING}:
            continue
        order = asset.order if asset.order_id and asset.order else None
        trace = _cloud_ip_trace_from_maps(asset, trace_maps)
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
            schedule = compute_order_lifecycle_schedule(expires_at)
            suspend_at = schedule.suspend_at
            delete_at = schedule.delete_at
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
                deletion_source=_delete_source_label(source_note),
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
            'deletion_source_label': _delete_source_label(note),
            'service_expires_at': expires_at,
            'suspend_at': suspend_at,
            'delete_at': delete_at,
            'note': note,
            'display_note': _compact_dashboard_note(note, max_chars=500),
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
            'deletion_source_label': _delete_source_label(note),
            'service_expires_at': expires_at,
            'suspend_at': suspend_at,
            'delete_at': delete_at,
            'note': note,
            'display_note': _compact_dashboard_note(note, max_chars=500),
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
    inactive_status_q = Q(status__in=[
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_TERMINATED,
        CloudAsset.STATUS_TERMINATING,
    ])
    provider_missing_q = (
        Q(provider_status__icontains='云上未找到')
        | Q(provider_status__icontains='已到期删除')
        | Q(provider_status__icontains='已删除')
    )
    dirty_note_q = (Q(note__icontains='云上不存在') | Q(note__icontains='已标记删除')) & ~Q(status__in=[
        CloudAsset.STATUS_RUNNING,
        CloudAsset.STATUS_PENDING,
        CloudAsset.STATUS_STARTING,
        CloudAsset.STATUS_STOPPED,
        CloudAsset.STATUS_SUSPENDED,
        CloudAsset.STATUS_UNKNOWN,
    ])
    return inactive_status_q | provider_missing_q | dirty_note_q


def _unattached_ip_deleted_or_missing_q():
    inactive_status_q = Q(status__in=[
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_TERMINATED,
        CloudAsset.STATUS_TERMINATING,
    ])
    final_missing_q = (
        Q(provider_status__icontains='已到期删除')
        | Q(provider_status__icontains='已删除')
        | Q(note__icontains='IP校验发现云上不存在，已标记删除')
        | Q(note__icontains='固定 IP 云端已不存在')
        | Q(note__icontains='固定IP云端已不存在')
        | Q(note__icontains='云上不存在，已标记删除')
    )
    return inactive_status_q | final_missing_q


def _unattached_ip_deleted_or_missing_text(item: dict) -> bool:
    text = '\n'.join(
        str(item.get(key) or '')
        for key in ['source_note', 'note', 'provider_status', 'execution_status', 'deletion_source_label']
    ).replace('固定 IP', '固定IP')
    status = str(item.get('status') or '')
    if status in {
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_TERMINATED,
    }:
        return True
    return any(marker in text for marker in [
        '已到期删除',
        '已删除',
        '固定IP已释放',
        '释放固定IP成功',
        '固定IP云端已不存在',
        '云端已不存在',
        '云上已不存在',
        '云上不存在，已标记删除',
        'IP校验发现云上不存在，已标记删除',
    ])


def _ip_delete_completed_active_to_history(item: dict) -> dict:
    executed_at = item.get('logged_at') or item.get('delete_at') or item.get('next_run_at')
    source_note = item.get('source_note') or item.get('note') or item.get('display_note') or '固定 IP 已删除'
    history = {
        **item,
        'is_history': True,
        'executed_at': executed_at,
        'logged_at': executed_at,
        'provider_status': item.get('provider_status') or '已删除',
        'deletion_source_label': item.get('deletion_source_label') or _delete_source_label(source_note),
        'execution_status': item.get('execution_status') or '固定 IP 已删除',
        'note': source_note,
        'display_note': item.get('display_note') or _compact_dashboard_note(source_note, max_chars=500),
    }
    history.update(_unattached_ip_delete_attempt_state(history, is_history=True))
    return history


def _completed_unattached_ip_plan_history_items() -> list[dict]:
    items = _cloud_lifecycle_plan_items(
        plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
        data_group='active',
    )
    items = _refresh_plan_payload_from_assets(items)
    return [
        _ip_delete_completed_active_to_history(item)
        for item in items
        if _unattached_ip_deleted_or_missing_text(item)
    ]


def _move_completed_ip_delete_rows_to_history(items: list[dict]) -> tuple[list[dict], list[dict], int]:
    converted_history_items = []
    active_items = []
    completed_row_ids = []
    for item in items:
        if item.get('is_history') or item.get('data_group') == 'history':
            converted_history_items.append(item)
            continue
        confirm_text = '\n'.join(
            str(item.get(key) or '')
            for key in ['source_note', 'note', 'provider_status', 'execution_status']
        )
        confirm_state = missing_confirmation_state(confirm_text)
        if 0 < confirm_state['count'] < confirm_state['threshold']:
            active_items.append(item)
            continue
        if item.get('plan_state') == 'completed' or _unattached_ip_deleted_or_missing_text(item):
            converted_history_items.append(_ip_delete_completed_active_to_history(item))
            row_id = item.get('id')
            if isinstance(row_id, int):
                completed_row_ids.append(row_id)
            continue
        active_items.append(item)

    if not completed_row_ids:
        return active_items, converted_history_items, 0

    source_rows = {
        row.id: row
        for row in CloudLifecyclePlan.objects.filter(
            id__in=completed_row_ids,
            plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
            data_group='active',
        )
    }
    history_payloads = [
        _lifecycle_plan_row_defaults(
            _ip_delete_completed_active_to_history(item),
            plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
            data_group='history',
            source_key=f'history:asset:{item.get("asset_id")}' if item.get('asset_id') else f'history:row:{item.get("id")}',
        )
        for item in items
        if item.get('id') in source_rows
    ]
    for payload in history_payloads:
        payload['data_group'] = 'history'
    existing_rows = {
        row.source_key: row
        for row in CloudLifecyclePlan.objects.filter(
            plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
            data_group='history',
            source_key__in=[payload['source_key'] for payload in history_payloads],
        )
    }
    create_rows = []
    update_rows = []
    update_fields = [
        'queue_status', 'queue_status_label', 'order_id', 'asset_id', 'user_id',
        'user_display_name', 'username_label', 'ip', 'provider', 'provider_label',
        'status', 'status_label', 'service_expires_at', 'suspend_at', 'delete_at',
        'ip_recycle_at', 'next_run_at', 'logged_at', 'last_failure_reason',
        'execution_status', 'execution_plan', 'note', 'display_note',
        'deletion_source_label', 'related_path', 'detail_path', 'order_detail_path',
        'order_link_path', 'asset_detail_path', 'source_snapshot', 'updated_at',
    ]
    for payload in history_payloads:
        row = existing_rows.get(payload['source_key'])
        if not row:
            create_rows.append(CloudLifecyclePlan(**payload))
            continue
        changed = False
        for field, value in payload.items():
            if getattr(row, field) != value:
                setattr(row, field, value)
                changed = True
        if changed:
            update_rows.append(row)
    if create_rows:
        CloudLifecyclePlan.objects.bulk_create(create_rows, batch_size=500)
    if update_rows:
        CloudLifecyclePlan.objects.bulk_update(update_rows, update_fields, batch_size=500)
    deleted, _ = CloudLifecyclePlan.objects.filter(
        id__in=completed_row_ids,
        plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE,
        data_group='active',
    ).delete()
    return active_items, converted_history_items, deleted


def _unattached_ip_delete_history_q():
    terminal_q = Q(event_type__in=[CloudIpLog.EVENT_DELETED, CloudIpLog.EVENT_RECYCLED])
    explicit_note_q = (
        Q(note__icontains='未附加固定IP')
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
        | Q(asset__note__icontains='未附加固定IP')
        | Q(asset__provider_resource_id__icontains='StaticIp')
    ) & (Q(asset__instance_id__isnull=True) | Q(asset__instance_id=''))
    return terminal_q & (explicit_note_q | asset_q)


def _ip_delete_item_quality(item: dict, duplicate_count: int = 0) -> dict:
    note = str(item.get('note') or '')
    source_note = str(item.get('source_note') or '')
    provider_status = str(item.get('provider_status') or '')
    confirm_note = '\n'.join(filter(None, [note, source_note, provider_status]))
    flags = []
    labels = []
    if duplicate_count > 0:
        flags.append('covered_duplicates')
        labels.append(f'已覆盖 {duplicate_count} 条同 IP 旧记录')
    if any(marker in confirm_note for marker in ['云上不存在', '云上未找到', '云端已不存在', '已标记删除']):
        flags.append('cloud_missing')
        labels.append('云上已不存在')
    if any(marker in confirm_note for marker in ['历史脏数据', '脏数据', '待确认', 'missing_sync_count']):
        flags.append('dirty_data')
        labels.append('脏数据')
    confirm_state = missing_confirmation_state(confirm_note)
    item['missing_confirm_count'] = confirm_state['count']
    item['missing_confirm_threshold'] = confirm_state['threshold']
    item['missing_confirm_remaining'] = confirm_state['remaining']
    item['missing_confirm_interval_minutes'] = confirm_state['interval_minutes']
    item['missing_confirm_checked_at'] = _iso(confirm_state['checked_at'])
    item['missing_confirm_next_check_at'] = _iso(confirm_state['next_check_at'])
    item['missing_confirm_due'] = confirm_state['due']
    if confirm_state['count'] > 0:
        flags.append('missing_confirming')
        labels.append(f'缺失确认 {confirm_state["count"]}/{confirm_state["threshold"]}')
        if confirm_state['next_check_at'] and not confirm_state['due']:
            labels.append(f'下次确认 {_fmt_dashboard_dt(confirm_state["next_check_at"])}')
    item['quality_flags'] = flags
    item['quality_label'] = '，'.join(labels)
    if labels:
        item['execution_status'] = f'{item.get("execution_status") or item.get("provider_status") or "-"}（{"，".join(labels)}）'
    return item


def _unattached_ip_delete_attempt_state(item: dict, *, is_history: bool | None = None) -> dict:
    text_parts = []
    seen_parts = set()
    for value in [
        item.get('source_note'),
        item.get('note'),
        item.get('provider_status'),
        item.get('execution_status'),
    ]:
        text_value = str(value or '').strip()
        if not text_value or text_value in seen_parts:
            continue
        seen_parts.add(text_value)
        text_parts.append(text_value)
    text = '\n'.join(text_parts)
    explicit_numbers = [
        int(match.group(1))
        for match in re.finditer(r'第\s*(\d+)\s*次(?:执行)?(?:删除|释放|删除确认)', text)
        if match.group(1).isdigit()
    ]
    attempt_markers = [
        'AWS API 删除失败',
        '系统已调用 AWS API 真实删除',
        'AWS 固定 IP 已真实释放',
        'AWS 固定 IP 真实释放失败',
        'AWS 固定 IP 云端已不存在',
        '已调用 AWS release_static_ip',
        'release_static_ip',
        '释放固定IP成功',
        '固定 IP 已释放',
        '固定IP已释放',
    ]
    marker_count = 0
    for line in [line.strip() for line in text.splitlines() if line.strip()]:
        if any(marker in line for marker in attempt_markers):
            marker_count += 1
    count = max([marker_count, *explicit_numbers], default=0)
    history = bool(item.get('is_history')) if is_history is None else bool(is_history)
    next_attempt = None if history else count + 1
    if history:
        label = f'第{max(count, 1)}次删除已完成'
    elif count > 0:
        label = f'已尝试{count}次，待第{next_attempt}次删除'
    else:
        label = '尚未执行，待第1次删除'
    return {
        'delete_attempt_count': count,
        'delete_next_attempt': next_attempt,
        'delete_attempt_label': label,
    }


def _dedupe_ip_delete_items_by_ip(items: list[dict]) -> list[dict]:
    buckets = {}
    no_ip_items = []
    for item in items:
        ip = str(item.get('public_ip') or '').strip()
        if not ip:
            no_ip_items.append(_ip_delete_item_quality(item))
            continue
        buckets.setdefault(ip, []).append(item)
    deduped = []
    for ip_items in buckets.values():
        ip_items = sorted(ip_items, key=lambda item: (
            parse_datetime(item.get('logged_at') or item.get('delete_at') or '') or datetime.min.replace(tzinfo=dt_timezone.utc),
            int(item.get('asset_id') or item.get('id') or 0) if str(item.get('asset_id') or item.get('id') or '').isdigit() else 0,
        ), reverse=True)
        deduped.append(_ip_delete_item_quality(ip_items[0], duplicate_count=len(ip_items) - 1))
    return deduped + no_ip_items


def _unattached_ip_delete_items(limit=50, assets=None):
    now = timezone.now()
    limit = max(1, min(int(limit or 50), 1000))
    if assets is None:
        assets = list(
            _active_cloud_asset_queryset()
            .filter(Q(provider_status__icontains='未附加') | Q(note__icontains='未附加IP') | Q(note__icontains='未附加固定IP') | Q(provider_resource_id__icontains='StaticIp'))
            .filter(Q(instance_id__isnull=True) | Q(instance_id=''))
            .exclude(_unattached_ip_deleted_or_missing_q())
            .order_by('actual_expires_at', 'created_at', '-updated_at')[:limit]
        )
    else:
        assets = [
            asset for asset in assets
            if _asset_is_unattached_ip(asset) or 'StaticIp' in str(getattr(asset, 'provider_resource_id', '') or '')
        ][:limit]
    trace_maps = _cloud_ip_trace_maps_for_assets(assets)
    items = []
    seen_trace_ids = set()
    active_unattached_ips = {str(asset.public_ip or '').strip() for asset in assets if str(asset.public_ip or '').strip()}
    for asset in assets:
        confirm_state = missing_confirmation_state('\n'.join(filter(None, [getattr(asset, 'provider_status', ''), getattr(asset, 'note', '')])))
        if confirm_state['count'] >= confirm_state['threshold']:
            continue
        user_display_name, username_label = _telegram_user_labels(asset.user)
        if asset.actual_expires_at:
            delete_at = asset.actual_expires_at
        else:
            base_at = asset.updated_at or asset.created_at or now
            delete_at = compute_unattached_ip_release_at(base_at)
        trace = _cloud_ip_trace_from_maps(asset, trace_maps)
        trace_note = ''
        if trace:
            trace_note = _cloud_ip_trace_note_newest_first(trace.note)
            logged_at = _cloud_ip_trace_logged_at(trace.note, trace.created_at)
            seen_trace_ids.add(trace.id)
        else:
            logged_at = asset.updated_at
        note = _asset_note_text(asset)
        source_note = trace_note or note
        asset_name = asset.asset_name or getattr(asset, 'static_ip_name', '') or asset.instance_id or f'asset-{asset.id}'
        item = {
            'id': asset.id,
            'asset_id': asset.id,
            'asset_name': asset_name,
            'asset_detail_path': f'/admin/cloud-assets/{asset.id}',
            'detail_path': f'/admin/cloud-assets/{asset.id}',
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': (trace.public_ip if trace else None) or asset.public_ip or asset.previous_public_ip or '',
            'provider_status': asset.provider_status or (_status_label(trace.event_type, CloudIpLog.EVENT_CHOICES) if trace else ''),
            'deletion_source_label': _delete_source_label(trace_note, default='计划自动删除'),
            'service_expires_at': _iso(asset.actual_expires_at),
            'delete_at': _iso(delete_at),
            'logged_at': _iso(logged_at),
            'source_note': source_note,
            'note': note,
            'display_note': _asset_display_note(asset, fallback=trace_note, max_chars=500),
            'is_overdue': bool(delete_at and delete_at <= now),
            'is_history': False,
        }
        if not _asset_shutdown_enabled(asset):
            item['queue_status'] = 'shutdown_disabled'
            item['queue_status_label'] = '关机计划关闭'
            item['execution_status'] = '关机计划关闭，禁止真实释放固定 IP'
        item.update(_unattached_ip_delete_attempt_state(item, is_history=False))
        items.append(item)

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
        item = {
            'id': trace.id,
            'asset_id': trace.asset_id,
            'asset_name': asset_name,
            'asset_detail_path': f'/admin/cloud-assets/{trace.asset_id}' if trace.asset_id else '',
            'detail_path': f'/admin/cloud-orders/{trace.order_id}' if trace.order_id else (f'/admin/cloud-assets/{trace.asset_id}' if trace.asset_id else ''),
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': trace.public_ip or trace.previous_public_ip or '',
            'provider_status': _status_label(trace.event_type, CloudIpLog.EVENT_CHOICES),
            'deletion_source_label': _delete_source_label(trace.note),
            'service_expires_at': _iso(getattr(asset, 'actual_expires_at', None) or getattr(order, 'service_expires_at', None)),
            'delete_at': _iso(delete_at),
            'logged_at': _iso(logged_at),
            'source_note': _cloud_ip_trace_note_newest_first(trace.note),
            'note': _cloud_ip_trace_note_newest_first(trace.note),
            'display_note': _compact_dashboard_note(trace.note, max_chars=500),
            'is_overdue': True,
            'is_history': True,
        }
        item.update(_unattached_ip_delete_attempt_state(item, is_history=True))
        items.append(item)
    def sort_key(item):
        if item.get('is_history'):
            parsed = parse_datetime(item.get('logged_at') or item.get('delete_at') or '')
            timestamp = parsed.timestamp() if parsed else 0
            return (1, -timestamp, str(item['id']))
        return (0, 0 if item['is_overdue'] else 1, item.get('delete_at') or '', str(item['id']))

    items = _dedupe_ip_delete_items_by_ip(items)
    return sorted(items, key=sort_key)[:limit]


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
    normalized = (
        str(username)
        .replace('，', ',')
        .replace('｜', ',')
        .replace('|', ',')
        .replace(' / ', ',')
        .replace('/', ',')
    )
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
        'username_label': '｜'.join(f'@{name}' for name in usernames) if usernames else '-',
    }


def _telegram_user_labels(user):
    if not user:
        return '未绑定用户', '-'
    usernames = _split_usernames(getattr(user, 'username', '') or getattr(user, 'primary_username', ''))
    first_name = (getattr(user, 'first_name', '') or '').strip()
    primary_username = getattr(user, 'primary_username', '') or (usernames[0] if usernames else '')
    display_name = first_name or (f'@{primary_username}' if primary_username else str(getattr(user, 'tg_user_id', '') or getattr(user, 'id', '')))
    username_label = '｜'.join(f'@{name}' for name in usernames) if usernames else '-'
    return display_name, username_label


def _parse_decimal(value, field_label):
    raw = str(value or '').strip()
    if raw == '':
        raise ValueError(f'{field_label}不能为空')
    try:
        return Decimal(raw).quantize(Decimal('0.001'), rounding=ROUND_DOWN)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'{field_label}格式不正确')


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


def _find_duplicate_cloud_account(*, provider: str, external_account_id: str = '', exclude_id: int | None = None):
    normalized_provider = str(provider or '').strip()
    normalized_external_id = str(external_account_id or '').strip()
    if not normalized_provider or not normalized_external_id:
        return None
    qs = CloudAccountConfig.objects.filter(provider=normalized_provider, external_account_id=normalized_external_id)
    if exclude_id:
        qs = qs.exclude(id=exclude_id)
    return qs.first()


def _cloud_account_duplicate_error(duplicate):
    return f'云厂商账号ID已存在：{duplicate.get_provider_display()} / {duplicate.name}（内部ID {duplicate.id}）'


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
    usernames = TelegramUser.normalize_usernames(item.username)
    return {
        'id': item.id,
        'label': item.label,
        'phone': item.phone or '',
        'tg_user_id': item.tg_user_id,
        'username': '｜'.join(usernames) if usernames else '',
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
    _, username_label = _telegram_user_labels(user)
    return {
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'display_name': user.first_name or user.primary_username or str(user.tg_user_id),
        'first_name': user.first_name or '',
        'primary_username': user.primary_username,
        'username_label': username_label,
        'usernames': user.usernames,
        'message_count': message_count,
        'latest_chat_id': latest.chat_id if latest else None,
        'latest_login_account_id': latest.login_account_id if latest else None,
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
        'archived': bool(getattr(item, 'archived', False)),
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
    qs = _active_cloud_asset_queryset().filter(
        Q(user_id__isnull=False) | Q(order__user_id__isnull=False)
    ).select_related('order')
    if user_ids is not None:
        user_ids = set(user_ids)
        qs = qs.filter(Q(user_id__in=user_ids) | Q(order__user_id__in=user_ids))
    counts = {}
    for asset in qs:
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


def _shutdown_plan_item_payload(order, *, queue_status='scheduled_future', queue_status_label='计划中', next_run_at=None, last_failure_reason=None, note=''):
    user_display_name, username_label = _telegram_user_labels(order.user)
    notice_ip = order.public_ip or order.previous_public_ip or '未分配'
    plan_at = next_run_at or order.delete_at
    shutdown_enabled = _shutdown_enabled_for_order(order)
    if not shutdown_enabled:
        execution_status = '关机计划关闭，禁止真实关机和删机'
        queue_status = 'shutdown_disabled'
        queue_status_label = '关机计划关闭'
    elif queue_status == 'waiting_manual_time':
        execution_status = '代理列表资产缺少到期时间，等待人工维护'
        queue_status_label = '待处理'
    elif queue_status == 'retry_failed':
        execution_status = '上次删除失败，等待重试'
    elif queue_status == 'fallback_retry':
        execution_status = '已到删除时间，待执行删除服务器'
    elif queue_status == 'due_now':
        execution_status = '已到删除时间，待执行删除服务器'
    elif queue_status == 'within_window':
        execution_status = '待执行删除服务器'
    else:
        execution_status = '删除计划已生成'
    execution_plan = f'删除服务器 {_fmt_dashboard_dt(plan_at)}' if plan_at else '等待删除时间'
    note = str(note or '').strip()
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
        'source_note': str(getattr(order, 'provision_note', '') or '').strip(),
        'note': note,
        'display_note': _compact_dashboard_note(note, max_chars=500),
        'related_path': f'/admin/cloud-orders/{order.id}',
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
    }


def _asset_delete_plan_item_payload(asset, *, queue_status='scheduled_future', queue_status_label='计划中', note=''):
    account_name, external_account_id = _cloud_account_labels(asset)
    ip = asset.public_ip or asset.previous_public_ip or '未分配'
    expires_at, suspend_at, delete_at = _server_asset_lifecycle_times(asset)
    plan_at = delete_at
    shutdown_enabled = _asset_shutdown_enabled(asset)
    linked_order = getattr(asset, 'order', None)
    if _asset_is_sync_only_lifecycle(asset):
        suspend_at = None
        delete_at = None
        plan_at = None
        queue_status = 'sync_only'
        queue_status_label = '只同步/自然释放'
        execution_status = '阿里云只同步状态，按云厂商自然释放；本系统不执行真实关机和删机'
    elif linked_order and getattr(linked_order, 'status', '') in {'deleted', 'cancelled', 'expired'}:
        queue_status_label = '待处理'
        execution_status = '关联订单已结束，服务器仍存在，待执行删除服务器'
    elif linked_order:
        execution_status = '代理列表资产待删除，订单仅作为展示信息'
    else:
        execution_status = '无订单同步资产已到期，待执行删除服务器'
    if not shutdown_enabled:
        queue_status = 'shutdown_disabled'
        queue_status_label = '关机计划关闭'
        execution_status = '关机计划关闭，禁止真实关机和删机'
    elif queue_status == 'within_window':
        execution_status = '待执行删除服务器'
    elif queue_status == 'scheduled_future':
        execution_status = '删除计划已生成'
    user_display_name, username_label = _telegram_user_labels(asset.user if getattr(asset, 'user', None) else None)
    note = str(note or _asset_note_text(asset)).strip()
    return {
        'id': f'asset-{asset.id}',
        'item_type': 'orphan_asset',
        'asset_id': asset.id,
        'order_id': getattr(linked_order, 'id', None),
        'order_no': getattr(linked_order, 'order_no', None) or '-',
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
        'service_expires_at': _iso(expires_at),
        'suspend_at': _iso(suspend_at),
        'delete_at': _iso(plan_at),
        'ip_recycle_at': _iso(getattr(linked_order, 'ip_recycle_at', None)),
        'next_run_at': _iso(plan_at),
        'last_failure_reason': None,
        'execution_status': execution_status,
        'execution_plan': '只同步/自然释放' if queue_status == 'sync_only' else (f'删除服务器 {_fmt_dashboard_dt(plan_at)}' if plan_at else '等待删除时间'),
        'source_note': _asset_note_text(asset) or str(getattr(linked_order, 'provision_note', '') or '').strip(),
        'note': note,
        'display_note': _asset_display_note(asset, fallback=note, max_chars=500),
        'cloud_account_id': asset.cloud_account_id,
        'cloud_account_name': account_name,
        'external_account_id': external_account_id,
        'asset_name': asset.asset_name,
        'related_path': f'/admin/cloud-assets/{asset.id}',
        'detail_path': f'/admin/cloud-assets/{asset.id}',
        'order_detail_path': f'/admin/cloud-orders/{linked_order.id}' if linked_order else '',
        'order_link_path': f'/admin/cloud-orders/{linked_order.id}' if linked_order else '',
        'asset_detail_path': f'/admin/cloud-assets/{asset.id}',
    }


def _orphan_asset_delete_plan_item_payload(asset, *, queue_status='orphan_due', queue_status_label='无订单资产待删除', note=''):
    return _asset_delete_plan_item_payload(asset, queue_status=queue_status, queue_status_label=queue_status_label, note=note)


def _asset_shutdown_enabled(asset):
    account = getattr(asset, 'cloud_account', None)
    if not account:
        return True
    return bool(getattr(account, 'shutdown_enabled', True))


def _collect_shutdown_plan_queue(now, limit=100):
    pending_until = now + timezone.timedelta(days=7)
    plan_assets = _active_cloud_asset_plan_rows()
    server_assets = [asset for asset in plan_assets if not _asset_is_unattached_ip(asset) and 'StaticIp' not in str(asset.provider_resource_id or '')]
    next_run_at = None
    due_items = []
    future_items = []
    for asset in server_assets[:limit]:
        if not asset.actual_expires_at:
            continue
        if _asset_deleted_or_missing(asset):
            continue
        if _asset_is_sync_only_lifecycle(asset):
            future_items.append(
                _asset_delete_plan_item_payload(
                    asset,
                    queue_status='sync_only',
                    queue_status_label='只同步/自然释放',
                )
            )
            continue
        _expires_at, _suspend_at, delete_at = _server_asset_lifecycle_times(asset)
        if not next_run_at or (delete_at and delete_at < next_run_at):
            next_run_at = delete_at
        if delete_at and delete_at <= pending_until:
            due_items.append(
                _asset_delete_plan_item_payload(
                    asset,
                    queue_status='due_now' if delete_at <= now else 'within_window',
                    queue_status_label='待执行' if delete_at <= now else '计划中',
                )
            )
            continue
        future_items.append(
            _asset_delete_plan_item_payload(
                asset,
                queue_status='scheduled_future',
                queue_status_label='计划中',
            )
        )

    due_items.sort(key=lambda item: parse_datetime(item.get('delete_at') or '') or datetime.max.replace(tzinfo=dt_timezone.utc))
    future_items.sort(key=lambda item: parse_datetime(item.get('delete_at') or '') or datetime.max.replace(tzinfo=dt_timezone.utc))
    return {
        'due_orders': [],
        'retry_orders': [],
        'fallback_orders': [],
        'orphan_due_assets': server_assets,
        'source_assets': plan_assets,
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
        'deletion_source_label': _delete_source_label(log.note),
        'source_note': log.note or '',
        'note': log.note or '',
        'display_note': _compact_dashboard_note(log.note or '', max_chars=500),
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
        'deletion_source_label': _delete_source_label(order.provision_note),
        'source_note': order.provision_note or '',
        'note': order.provision_note or '',
        'display_note': _compact_dashboard_note(order.provision_note or '', max_chars=500),
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
    now = timezone.now()

    def parse_item_dt(value, default=None):
        if not value:
            return default
        parsed = parse_datetime(value) if isinstance(value, str) else value
        if not parsed:
            return default
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed

    def decorate_plan_item(item):
        note = str(item.get('note') or '')
        incoming_display_note = str(item.get('display_note') or '')
        source_note = str(item.get('source_note') or '')
        item['display_note'] = _compact_dashboard_note(incoming_display_note or note or source_note, max_chars=500)
        is_ip_delete_item = str(item.get('plan_kind') or '') == CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE
        delete_attempt = _unattached_ip_delete_attempt_state(item) if is_ip_delete_item else {}
        if delete_attempt:
            item.update(delete_attempt)
        first_line = next((line.strip() for line in note.splitlines() if line.strip()), '')
        if first_line.startswith('执行内容：'):
            content_match = re.search(r'执行内容：([^\n]+?)(?:；(?:时间|账号|地区|IP|固定IP名|端口|secret|服务到期|宽限删机|用户续费)|$)', first_line)
            plan_match = re.search(r'执行计划：([^；\n]+)', first_line)
            status_text = (content_match.group(1).strip() if content_match else '')
            if status_text:
                item['execution_status'] = status_text[:120]
            if plan_match:
                item['execution_plan'] = plan_match.group(1).strip()[:120]

        provider_status = str(item.get('provider_status') or item.get('status_label') or item.get('status') or '')
        queue_status = str(item.get('queue_status') or '')
        item_type = str(item.get('item_type') or '')
        execution_status = str(item.get('execution_status') or '')
        merged_text = '\n'.join(filter(None, [source_note, note, provider_status, execution_status, str(item.get('deletion_source_label') or '')]))
        merged_text = merged_text.replace('固定 IP', '固定IP')
        confirm_state = missing_confirmation_state('\n'.join(filter(None, [source_note, note, provider_status, execution_status])))
        confirm_summary = ''
        if confirm_state['count'] > 0:
            confirm_summary = f'删除确认进度：第{confirm_state["count"]}/{confirm_state["threshold"]}次删除确认'
        cloud_missing = any(marker in merged_text for marker in ['云上已不存在', '云上未找到实例/IP', '云端已不存在', '已标记删除'])
        instance_deleted = any(marker in merged_text for marker in ['已执行真实删机', '实例已删除', 'AWS 实例已执行删除', '服务器已删除'])
        ip_retained = any(marker in merged_text for marker in ['固定IP保留中', '固定IP仍存在但未附加', '未附加固定IP', '固定IP已分离为未附加状态'])
        shutdown_disabled = queue_status == 'shutdown_disabled' or '关机计划关闭' in merged_text
        is_history = bool(item.get('is_history') or item.get('executed_at'))

        resource_state = 'unknown'
        resource_state_label = '状态待确认'
        plan_state = 'pending'
        plan_state_label = '待执行'
        should_execute = not is_history
        blocked_reason = ''

        if is_history:
            plan_state = 'completed'
            plan_state_label = '历史记录'
            should_execute = False
        if confirm_state['count'] > 0 and not is_history:
            resource_state = 'missing_confirming'
            resource_state_label = f'云上缺失待确认（第{confirm_state["count"]}/{confirm_state["threshold"]}次）'
            plan_state = 'blocked'
            plan_state_label = '等待确认'
            should_execute = False
            blocked_reason = f'仍在缺失确认窗口，当前为第{confirm_state["count"]}/{confirm_state["threshold"]}次删除确认'
        elif cloud_missing:
            resource_state = 'cloud_missing'
            resource_state_label = '云上已不存在'
            plan_state = 'completed'
            plan_state_label = '无需执行'
            should_execute = False
            blocked_reason = '云上已不存在，无需继续执行删机'
        elif instance_deleted and ip_retained:
            resource_state = 'instance_deleted_ip_retained'
            resource_state_label = '实例已删除（固定IP保留中）'
            plan_state = 'completed'
            plan_state_label = '等待IP回收'
            should_execute = False
            blocked_reason = '实例已删除，仅剩固定IP保留或回收计划'
        elif instance_deleted:
            resource_state = 'instance_deleted'
            resource_state_label = '实例已删除'
            plan_state = 'completed'
            plan_state_label = '无需执行'
            should_execute = False
            blocked_reason = '实例已删除，无需继续执行删机'
        elif item_type == 'orphan_asset' and ip_retained:
            resource_state = 'fixed_ip_unattached'
            resource_state_label = '固定IP未附加'
            plan_state = 'completed'
            plan_state_label = '等待IP回收'
            should_execute = False
            blocked_reason = '当前已不是待删服务器，只剩固定IP回收事项'
        elif shutdown_disabled:
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'
            plan_state = 'shutdown_disabled'
            plan_state_label = '关机计划关闭'
            should_execute = False
            blocked_reason = '关机计划关闭，禁止真实关机和删机'
        elif queue_status == 'retry_failed':
            resource_state = 'instance_present'
            resource_state_label = '实例待重试处理'
            plan_state = 'pending'
            plan_state_label = '待重试'
        elif queue_status == 'fallback_retry':
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'
            plan_state = 'pending'
            plan_state_label = '待执行'
        elif queue_status in {'due_now', 'within_window', 'orphan_due'}:
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'
            plan_state = 'pending'
            plan_state_label = '待执行'
        elif queue_status == 'waiting_manual_time':
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'
            plan_state = 'waiting_manual_time'
            plan_state_label = '待处理'
            should_execute = False
            blocked_reason = '代理列表资产缺少到期时间，请先维护到期时间'
        elif queue_status == 'sync_only':
            resource_state = 'sync_only'
            resource_state_label = '只同步/自然释放'
            plan_state = 'sync_only'
            plan_state_label = '只同步/自然释放'
            should_execute = False
            blocked_reason = '该云厂商不执行本地删机计划，仅同步状态，资源按云端自然释放'
        elif queue_status == 'scheduled_future':
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'
            plan_state = 'scheduled'
            plan_state_label = '已排期'
        elif item.get('is_history'):
            resource_state = 'history'
            resource_state_label = '历史记录'
        elif ip_retained:
            resource_state = 'fixed_ip_unattached'
            resource_state_label = '固定IP未附加'
            plan_state = 'scheduled'
            plan_state_label = '等待回收'
        elif str(item.get('status') or '') in {CloudAsset.STATUS_RUNNING, CloudAsset.STATUS_PENDING, CloudAsset.STATUS_STARTING, CloudAsset.STATUS_STOPPED, CloudAsset.STATUS_SUSPENDED}:
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'

        delete_attempt_label = str(item.get('delete_attempt_label') or '')
        if delete_attempt_label and is_ip_delete_item and confirm_state['count'] <= 0:
            resource_state_label = f'{resource_state_label}（{delete_attempt_label}）'

        display_note_parts = [item.get('display_note') or note or (source_note if is_ip_delete_item else '')]
        if confirm_summary and not is_history:
            display_note_parts.append(confirm_summary)
        if delete_attempt_label and is_ip_delete_item and confirm_state['count'] <= 0:
            display_note_parts.append(f'删除次数：{delete_attempt_label}')
        item['display_note'] = _compact_dashboard_note('\n'.join(filter(None, display_note_parts)), max_chars=500)

        item['resource_state'] = resource_state
        item['resource_state_label'] = resource_state_label
        item['plan_state'] = plan_state
        item['plan_state_label'] = plan_state_label
        item['should_execute'] = should_execute
        item['blocked_reason'] = blocked_reason
        item['status_summary'] = f'真实状态：{resource_state_label}；计划状态：{plan_state_label}' + (f'；原因：{blocked_reason}' if blocked_reason else '')
        return item

    def dedupe_shutdown_active_items(items):
        passthrough = []
        buckets = {}
        for item in items:
            if str(item.get('item_type') or '') != 'orphan_asset':
                passthrough.append(item)
                continue
            key = str(item.get('ip') or item.get('public_ip') or item.get('asset_id') or item.get('id') or '').strip()
            if not key:
                passthrough.append(item)
                continue
            buckets.setdefault(key, []).append(item)
        deduped = list(passthrough)
        for bucket in buckets.values():
            bucket = sorted(
                bucket,
                key=lambda entry: (
                    parse_item_dt(entry.get('logged_at') or entry.get('next_run_at') or entry.get('delete_at'), datetime.min.replace(tzinfo=dt_timezone.utc)),
                    int(entry.get('asset_id') or 0),
                ),
                reverse=True,
            )
            keep = bucket[0]
            duplicate_count = len(bucket) - 1
            if duplicate_count > 0:
                labels = [str(keep.get('quality_label') or '').strip(), f'已覆盖 {duplicate_count} 条同 IP 旧服务器记录']
                keep['quality_label'] = '，'.join([label for label in labels if label])
                flags = list(keep.get('quality_flags') or [])
                if 'covered_duplicates' not in flags:
                    flags.append('covered_duplicates')
                keep['quality_flags'] = flags
            deduped.append(keep)
        return deduped

    def convert_completed_active_to_history(item):
        executed_at = item.get('logged_at') or item.get('delete_at') or item.get('next_run_at')
        return {
            **item,
            'executed_at': executed_at,
            'failure_reason': None,
            'is_success': True,
            'result_label': '已完成',
            'execution_status': item.get('resource_state_label') or item.get('execution_status') or '已完成',
        }

    compact = str(request.GET.get('compact') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    force_refresh = str(request.GET.get('refresh') or request.GET.get('sync') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    did_refresh = False
    if force_refresh or not _lifecycle_plan_table_has_rows():
        sync_limit = max(limit, 1000)
        _sync_lifecycle_plan_table(limit=sync_limit)
        did_refresh = True
    shutdown_active_items = _cloud_lifecycle_plan_items(plan_kind=CloudLifecyclePlan.PLAN_KIND_SHUTDOWN_ORDER, data_group='active')
    shutdown_active_items.extend(_cloud_lifecycle_plan_items(plan_kind=CloudLifecyclePlan.PLAN_KIND_ORPHAN_ASSET_DELETE, data_group='active'))
    shutdown_active_items = _refresh_plan_payload_from_assets(shutdown_active_items)
    shutdown_active_items = [decorate_plan_item(item) for item in shutdown_active_items]

    completed_shutdown_items = [
        item for item in shutdown_active_items
        if item.get('plan_state') == 'completed' and not item.get('should_execute')
    ]
    shutdown_active_items = [
        item for item in shutdown_active_items
        if not (item.get('plan_state') == 'completed' and not item.get('should_execute'))
    ]
    shutdown_active_items = dedupe_shutdown_active_items(shutdown_active_items)

    history_items = _cloud_lifecycle_plan_items(plan_kind=CloudLifecyclePlan.PLAN_KIND_SHUTDOWN_ORDER, data_group='history')
    history_items = [decorate_plan_item(item) for item in history_items]
    history_items.extend(convert_completed_active_to_history(item) for item in completed_shutdown_items)
    history_items.sort(
        key=lambda item: parse_item_dt(item.get('executed_at') or item.get('logged_at') or '', datetime.min.replace(tzinfo=dt_timezone.utc)),
        reverse=True,
    )
    history_items = history_items[:limit]

    ip_delete_items = _cloud_lifecycle_plan_items(plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE, data_group='active')
    ip_delete_items.extend(_cloud_lifecycle_plan_items(plan_kind=CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE, data_group='history'))
    ip_delete_items = _refresh_plan_payload_from_assets(ip_delete_items)
    ip_delete_items = [decorate_plan_item(item) for item in ip_delete_items]

    active_ip_delete_items, converted_ip_history_items, _deleted_ip_plan_count = _move_completed_ip_delete_rows_to_history(ip_delete_items)
    converted_ip_history_items = [decorate_plan_item(item) for item in converted_ip_history_items]
    ip_delete_items = [*active_ip_delete_items, *converted_ip_history_items]

    shutdown_items = list(shutdown_active_items)
    due_items = list(shutdown_items)
    future_plan_items = []
    ip_delete_pending_until = now + timezone.timedelta(days=7)

    def is_ip_delete_pending(item):
        if item.get('is_history'):
            return False
        if item.get('is_overdue'):
            return True
        delete_at = item.get('delete_at')
        parsed = parse_item_dt(delete_at)
        return bool(parsed and parsed <= ip_delete_pending_until)

    pending_ip_delete_items = [item for item in ip_delete_items if is_ip_delete_pending(item)]
    ip_delete_history_items = [item for item in ip_delete_items if item.get('is_history')]
    shutdown_items = _sort_lifecycle_active_items(shutdown_items)
    due_items = _sort_lifecycle_active_items(due_items)
    future_plan_items = _sort_lifecycle_active_items(future_plan_items)
    ip_delete_items = _sort_lifecycle_active_items([item for item in ip_delete_items if not item.get('is_history')]) + _sort_lifecycle_history_items(ip_delete_history_items)
    pending_ip_delete_items = _sort_lifecycle_active_items(pending_ip_delete_items)
    ip_delete_history_items = _sort_lifecycle_history_items(ip_delete_history_items)
    recent_since = now - timezone.timedelta(days=1)
    recent_history = [
        item for item in history_items
        if parse_item_dt(item.get('executed_at')) and parse_item_dt(item.get('executed_at')) >= recent_since
    ]
    plan_stats = _cloud_asset_plan_stats()
    if compact:
        def compact_notes(items):
            for item in items:
                note = str(item.get('note') or '')
                if len(note) > 1200:
                    item['note'] = note[:1200] + '\n...（备注过长，已折叠预览）'
            return items
        compact_notes(shutdown_items)
        compact_notes(ip_delete_items)
        compact_notes(history_items)
        compact_notes(due_items)
        compact_notes(future_plan_items)
    last_refresh_at = _lifecycle_plan_last_refresh_at()
    return _ok({
        'task_key': 'server_delete_plans',
        'task_label': '删除计划',
        'status_label': '按代理列表资产生成',
        'interval_minutes': 1440,
        'last_run_at': history_items[0]['executed_at'] if history_items else None,
        'next_run_at': _iso(_next_runtime_time('cloud_delete_time', '15:00', now)),
        'last_refresh_at': _iso(last_refresh_at),
        'refreshed': did_refresh,
        'cache_mode': 'refreshed' if did_refresh else 'cached',
        'due_count': len(due_items),
        'recent_success_count': sum(1 for item in recent_history if item.get('is_success')),
        'recent_failure_count': sum(1 for item in recent_history if not item.get('is_success')),
        'pending_ip_delete_count': len(pending_ip_delete_items),
        'missing_expiry_count': plan_stats['missing_expiry_count'],
        'unattached_ip_count': plan_stats['unattached_ip_count'],
        'source_asset_count': plan_stats['source_asset_count'],
        'server_asset_count': plan_stats['server_asset_count'],
        'server_delete_history_count': len(history_items),
        'ip_delete_history_count': len(ip_delete_history_items),
        'shutdown_count': len(due_items) + len(future_plan_items),
        'shutdown_due_count': len(due_items),
        'ip_delete_count': len(pending_ip_delete_items),
        'ip_delete_due_count': len(pending_ip_delete_items),
        'due_items': due_items,
        'future_plan_items': future_plan_items,
        'history_items': history_items,
        'shutdown_items': shutdown_items,
        'ip_delete_items': ip_delete_items,
    })


@csrf_exempt
@dashboard_login_required
@require_POST
def refresh_lifecycle_plan_table(request):
    payload = _json_payload(request)
    try:
        limit = int(payload.get('limit') or request.POST.get('limit') or request.GET.get('limit') or 1000)
    except (TypeError, ValueError):
        limit = 1000
    limit = max(1, min(limit, 1000))
    bundle = _sync_lifecycle_plan_table(limit=limit)
    last_refresh_at = _lifecycle_plan_last_refresh_at()
    plan_stats = _cloud_asset_plan_stats(bundle.get('source_assets') or None)
    return _ok({
        'refreshed': True,
        'last_refresh_at': _iso(last_refresh_at),
        'due_count': len(bundle.get('due_items') or []),
        'future_count': len(bundle.get('future_plan_items') or []),
        'shutdown_count': len(bundle.get('shutdown_items') or []),
        'missing_expiry_count': plan_stats['missing_expiry_count'],
        'unattached_ip_count': plan_stats['unattached_ip_count'],
        'source_asset_count': plan_stats['source_asset_count'],
        'server_asset_count': plan_stats['server_asset_count'],
        'history_count': len(bundle.get('history_items') or []),
        'ip_delete_count': len(bundle.get('ip_delete_items') or []),
    })


@csrf_exempt
@dashboard_login_required
@require_POST
def update_lifecycle_plan_note(request):
    payload = _json_payload(request)
    item_type = str(payload.get('item_type') or '').strip()
    note = str(payload.get('note') or '').strip()
    order_id = payload.get('order_id')
    asset_id = payload.get('asset_id') or payload.get('id')
    actor = request.user if getattr(request, 'user', None) and getattr(request.user, 'is_authenticated', False) else None
    if item_type == 'order' or order_id:
        try:
            order_id = int(order_id or 0)
        except (TypeError, ValueError):
            order_id = 0
        order = CloudServerOrder.objects.filter(id=order_id).first()
        if not order:
            return _error('订单不存在', status=404)
        plan_note = _save_lifecycle_plan_note(item_type='order', note=note, order=order, actor=actor)
        note_text = _lifecycle_plan_note_text(plan_note)
        CloudLifecyclePlan.objects.filter(plan_kind=CloudLifecyclePlan.PLAN_KIND_SHUTDOWN_ORDER, order=order).update(note=note_text, display_note=_compact_dashboard_note(note_text, max_chars=500))
        return _ok({'item_type': 'order', 'order_id': order.id, 'note': note_text, 'display_note': _compact_dashboard_note(note_text, max_chars=500)})
    try:
        asset_id = int(asset_id or 0)
    except (TypeError, ValueError):
        asset_id = 0
    asset = CloudAsset.objects.filter(id=asset_id).first()
    if not asset:
        return _error('资产不存在', status=404)
    effective_item_type = item_type or 'asset'
    note_text = note
    asset.note = note_text or None
    asset.save(update_fields=['note', 'updated_at'])
    _sync_asset_note_to_server(asset)
    plan_kind = CloudLifecyclePlan.PLAN_KIND_ORPHAN_ASSET_DELETE if effective_item_type == 'orphan_asset' else CloudLifecyclePlan.PLAN_KIND_UNATTACHED_IP_DELETE
    display_note = _asset_display_note(asset, max_chars=500)
    CloudLifecyclePlan.objects.filter(plan_kind=plan_kind, asset=asset).update(note=note_text, display_note=display_note)
    return _ok({'item_type': effective_item_type, 'asset_id': asset.id, 'note': note_text, 'display_note': display_note})


def _run_shutdown_order_sync(order_id: int, queue_status='manual_single', enforce_schedule: bool = True):
    return run_shutdown_order_delete(order_id, queue_status=queue_status, enforce_schedule=enforce_schedule)


@csrf_exempt
@dashboard_superuser_required
@require_POST
def run_shutdown_plan_order(request, order_id):
    result = _run_shutdown_order_sync(order_id, 'manual_single', enforce_schedule=True)
    _sync_lifecycle_plan_table(limit=1000)
    return _ok({
        'batch_id': secrets.token_hex(8),
        'items': [result],
        'total': 1,
        'success_count': 1 if result['ok'] else 0,
        'failure_count': 0 if result['ok'] else 1,
        'message': '服务器删除任务已执行' if result['ok'] else result.get('error') or '服务器删除任务执行失败',
    })


def _run_orphan_asset_delete_sync(asset_id: int, enforce_schedule: bool = True):
    return run_orphan_asset_delete(asset_id, enforce_schedule=enforce_schedule)


@csrf_exempt
@dashboard_superuser_required
@require_POST
def run_orphan_asset_delete_plan(request, asset_id):
    result = _run_orphan_asset_delete_sync(asset_id, enforce_schedule=True)
    _sync_lifecycle_plan_table(limit=1000)
    return _ok({
        'batch_id': secrets.token_hex(8),
        'items': [result],
        'total': 1,
        'success_count': 1 if result['ok'] else 0,
        'failure_count': 0 if result['ok'] else 1,
        'message': '服务器删除任务已执行' if result['ok'] else result.get('error') or '服务器删除任务执行失败',
    })


def _run_unattached_ip_delete_sync(asset_id: int, enforce_schedule: bool = True):
    return run_unattached_ip_release(asset_id, enforce_schedule=enforce_schedule)


@csrf_exempt
@dashboard_superuser_required
@require_POST
def run_unattached_ip_delete_plan(request, asset_id):
    result = _run_unattached_ip_delete_sync(asset_id, enforce_schedule=True)
    _sync_lifecycle_plan_table(limit=1000)
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
    codes = ['dashboard', 'users', 'cloud:read', 'finance:read', 'monitoring:read', 'settings:read']
    if getattr(request.user, 'is_superuser', False):
        codes.extend([
            'superuser',
            'users:write',
            'cloud:write',
            'cloud:danger',
            'finance:write',
            'settings:write',
        ])
    return _ok(codes)


@csrf_exempt
@dashboard_superuser_required
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
@dashboard_superuser_required
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
    is_superuser = bool(getattr(request.user, 'is_superuser', False))
    return _ok({
        'userId': str(request.user.pk),
        'username': username,
        'realName': request.user.get_full_name() or username,
        'avatar': '',
        'desc': 'Shop 管理后台管理员',
        'homePath': '/admin/analytics',
        'token': _session_token_for_request(request),
        'is_superuser': is_superuser,
        'is_staff': bool(getattr(request.user, 'is_staff', False)),
        'roles': ['superuser' if is_superuser else 'staff'],
        'permissions': ['superuser', 'cloud:danger'] if is_superuser else [],
    })


@dashboard_login_required
@require_GET
def me(request):
    return _ok({
        'id': request.user.id,
        'username': request.user.get_username(),
        'is_superuser': request.user.is_superuser,
        'is_staff': request.user.is_staff,
    })


from bot.api_site_configs import (  # noqa: E402
    _masked_sensitive_preview,
    _site_config_group_map,
    _site_config_payload,
    button_config_detail,
    init_button_config_view,
    init_site_configs,
    init_text_site_configs,
    site_config_groups,
    site_configs_list,
    test_daily_expiry_summary_notification,
    update_button_config,
    update_site_config,
)


@dashboard_login_required
@require_GET
def cloud_accounts_list(request):
    queryset = CloudAccountConfig.objects.order_by('provider', 'name', 'id')
    return _ok([_cloud_account_payload(item) for item in queryset])


@csrf_exempt
@dashboard_superuser_required
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
    duplicate = _find_duplicate_cloud_account(provider=provider, external_account_id=external_account_id)
    if duplicate:
        return _error(_cloud_account_duplicate_error(duplicate), status=400)
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
    if not getattr(request.user, 'is_superuser', False):
        return _error('需要超级管理员权限', status=403)
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
    normalized_external_account_id = str(
        item.external_account_id if external_account_id is None else external_account_id or ''
    ).strip()
    duplicate = _find_duplicate_cloud_account(
        provider=provider,
        external_account_id=normalized_external_account_id,
        exclude_id=item.id,
    )
    if duplicate:
        return _error(_cloud_account_duplicate_error(duplicate), status=400)
    item.provider = provider
    item.name = name
    if external_account_id is not None:
        item.external_account_id = normalized_external_account_id or None
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
@dashboard_superuser_required
@require_POST
def delete_cloud_account(request, account_id: int):
    item = CloudAccountConfig.objects.filter(id=account_id).first()
    if not item:
        return _error('云账号不存在', status=404)
    linked_asset_count = CloudAsset.objects.filter(cloud_account=item).count()
    linked_order_count = CloudServerOrder.objects.filter(cloud_account=item).count()
    linked_log_count = item.sync_logs.count()
    if linked_asset_count or linked_order_count or linked_log_count:
        return _error(
            (
                '该云账号已有业务数据，不能物理删除；请改为停用账号。'
                f'关联资产 {linked_asset_count} 条，关联订单 {linked_order_count} 条，同步日志 {linked_log_count} 条。'
            ),
            status=400,
        )
    item.delete()
    return _ok(True)


@csrf_exempt
@dashboard_superuser_required
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
                duplicate = _find_duplicate_cloud_account(
                    provider=item.provider,
                    external_account_id=account_id,
                    exclude_id=item.id,
                )
                if duplicate:
                    raise ValueError(_cloud_account_duplicate_error(duplicate))
                item.external_account_id = account_id
                item.save(update_fields=['external_account_id', 'updated_at'])
            item.mark_status(CloudAccountConfig.STATUS_OK, f'验证成功，账号ID {account_id or "-"}，实例数 {count}，地区 {region or "ap-southeast-1"}')
            return _ok({'valid': True, 'provider': item.provider, 'region': region or 'ap-southeast-1', 'instance_count': count, 'account': _cloud_account_payload(item)})
        if item.provider == CloudAccountConfig.PROVIDER_ALIYUN:
            from alibabacloud_swas_open20200601 import models as swas_models
            from cloud.aliyun_simple import _build_client as _default_build_client
            from cloud.aliyun_simple import _region_endpoint, _runtime_options

            client = _default_build_client(_region_endpoint(region or 'cn-hongkong'), account=item)
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
                duplicate = _find_duplicate_cloud_account(
                    provider=item.provider,
                    external_account_id=account_id,
                    exclude_id=item.id,
                )
                if duplicate:
                    raise ValueError(_cloud_account_duplicate_error(duplicate))
                item.external_account_id = account_id
                item.save(update_fields=['external_account_id', 'updated_at'])
            item.mark_status(CloudAccountConfig.STATUS_OK, f'验证成功，账号ID {account_id or "-"}，实例数 {count}，地区 {region or "cn-hongkong"}')
            return _ok({'valid': True, 'provider': item.provider, 'region': region or 'cn-hongkong', 'instance_count': count, 'account': _cloud_account_payload(item)})
        item.mark_status(CloudAccountConfig.STATUS_UNSUPPORTED, '暂不支持该云平台验证')
        return _error('暂不支持该云平台', status=400)
    except Exception as exc:
        item.mark_status(CloudAccountConfig.STATUS_ERROR, str(exc))
        return _error(f'验证失败: {exc}', status=400)


from bot.api_admin_users import (  # noqa: E402
    admin_users_list,
    change_my_password,
    create_admin_user,
    delete_admin_user,
    update_admin_user,
)


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
@dashboard_superuser_required
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
@dashboard_superuser_required
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
    scope = (request.GET.get('scope') or '').strip().lower()
    include_archived = (request.GET.get('archived') or '').strip() in {'1', 'true', 'yes'}
    accounts = TelegramLoginAccount.objects.order_by('-updated_at', '-id')
    if keyword:
        accounts = accounts.filter(Q(label__icontains=keyword) | Q(phone__icontains=keyword) | Q(username__icontains=keyword))
    if scope == 'accounts':
        return _ok({
            'accounts': [_telegram_login_account_payload(item) for item in accounts[:50]],
            'chats': [],
            'users': [],
            'messages': [],
        })

    users = TelegramUser.objects.order_by('-updated_at', '-id')
    if keyword:
        user_filter = Q(username__icontains=keyword) | Q(first_name__icontains=keyword)
        if keyword.isdigit():
            user_filter |= Q(tg_user_id=int(keyword))
        users = users.filter(user_filter)

    user_items = list(users[:100])
    user_ids = [item.tg_user_id for item in user_items]
    user_messages = TelegramChatMessage.objects.filter(tg_user_id__in=user_ids)
    counts = dict(
        user_messages.values('tg_user_id')
        .annotate(total=Count('id'))
        .values_list('tg_user_id', 'total')
    )
    latest_by_user = {}
    for msg in (
        user_messages.select_related('login_account')
        .order_by('-created_at', '-id')
        .iterator(chunk_size=200)
    ):
        latest_by_user.setdefault(msg.tg_user_id, msg)
        if len(latest_by_user) >= len(user_ids):
            break
    if scope in {'', 'users'}:
        return _ok({
            'accounts': [_telegram_login_account_payload(item) for item in accounts[:50]],
            'chats': [],
            'users': [_telegram_chat_user_payload(user, latest_by_user.get(user.tg_user_id), counts.get(user.tg_user_id, 0)) for user in user_items],
            'messages': [],
        })

    archived_ids = set(TelegramChatArchive.objects.values_list('chat_id', flat=True))
    messages = TelegramChatMessage.objects.select_related('user', 'login_account').order_by('-created_at', '-id')
    if keyword:
        message_filter = Q(text__icontains=keyword) | Q(username_snapshot__icontains=keyword) | Q(first_name_snapshot__icontains=keyword)
        if keyword.isdigit():
            message_filter |= Q(tg_user_id=int(keyword))
        messages = messages.filter(message_filter)
    if not include_archived and archived_ids:
        messages = messages.exclude(chat_id__in=archived_ids)
    chat_counts = dict(messages.values('chat_id').annotate(total=Count('id')).values_list('chat_id', 'total'))
    latest_by_chat = {}
    for msg in messages.select_related('login_account').iterator(chunk_size=500):
        latest_by_chat.setdefault(msg.chat_id, msg)
        if len(latest_by_chat) >= 100:
            break
    return _ok({
        'accounts': [_telegram_login_account_payload(item) for item in accounts[:50]],
        'chats': [_telegram_chat_payload(chat_id, latest, chat_counts.get(chat_id, 0), archived_ids) for chat_id, latest in list(latest_by_chat.items())[:100]],
        'users': [_telegram_chat_user_payload(user, latest_by_user.get(user.tg_user_id), counts.get(user.tg_user_id, 0)) for user in user_items],
        'messages': [],
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
    item.tg_user_id = getattr(me, 'id', None) or item.tg_user_id
    item.username = _merge_login_account_usernames(item.username, getattr(me, 'username', None)) or item.username
    item.label = getattr(me, 'first_name', None) or item.username or item.phone or item.label
    if item.tg_user_id:
        _get_or_create_user_sync(item.tg_user_id, getattr(me, 'username', None), getattr(me, 'first_name', None))
    item.note = '登录成功'
    item.last_synced_at = timezone.now()
    item.save(update_fields=['status', 'tg_user_id', 'username', 'label', 'note', 'last_synced_at', 'updated_at'])
    return item


@csrf_exempt
@dashboard_superuser_required
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
@dashboard_superuser_required
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
@dashboard_superuser_required
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
@dashboard_superuser_required
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
@dashboard_superuser_required
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
        queryset = queryset.filter(collapsed=False, archived=False)
    if keyword:
        query = Q(title__icontains=keyword) | Q(username__icontains=keyword)
        try:
            query |= Q(chat_id=int(keyword))
        except ValueError:
            pass
        queryset = queryset.filter(query)
    return _ok([_telegram_group_filter_payload(item) for item in queryset[:300]])


@csrf_exempt
@dashboard_superuser_required
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
        archived=_payload_bool(payload, 'archived'),
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
@dashboard_superuser_required
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
    if 'archived' in payload:
        archived = _payload_bool(payload, 'archived')
        if getattr(item, 'archived', False) != archived:
            item.archived = archived
            changed.append('archived')
    if changed:
        changed.append('updated_at')
        item.save(update_fields=changed)
    return _ok(_telegram_group_filter_payload(item))


@csrf_exempt
@dashboard_superuser_required
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
@dashboard_superuser_required
@require_POST
def archive_telegram_chat(request):
    payload = _read_payload(request)
    raw_chat_id = payload.get('chat_id')
    archived = _payload_bool(payload, 'archived', True)
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
@dashboard_superuser_required
@require_POST
def create_telegram_login_account(request):
    payload = _read_payload(request)
    label = str(payload.get('label') or '').strip()
    phone = str(payload.get('phone') or '').strip()
    username = _limited_username_string(payload.get('username'))
    note = str(payload.get('note') or '').strip()
    tg_user_id = None
    raw_tg_user_id = str(payload.get('tg_user_id') or '').strip()
    if raw_tg_user_id:
        try:
            tg_user_id = int(raw_tg_user_id)
        except (TypeError, ValueError):
            return _error('Telegram 用户ID必须是数字', status=400)
    if not label:
        return _error('账号备注不能为空', status=400)
    item = TelegramLoginAccount.objects.create(
        label=label,
        phone=phone or None,
        tg_user_id=tg_user_id,
        username=username,
        note=note or '已登记。自动采集仅限 bot 会话内收到的用户资料和聊天记录；不会后台登录个人 Telegram 账号抓取私聊。',
        status='registered',
    )
    if tg_user_id:
        _get_or_create_user_sync(tg_user_id, username, label)
    return _ok(_telegram_login_account_payload(item))


@dashboard_login_required
@require_GET
def telegram_chat_messages(request):
    keyword = (request.GET.get('keyword') or '').strip().lstrip('@')
    user_id = request.GET.get('user_id')
    tg_user_id = request.GET.get('tg_user_id')
    chat_id = request.GET.get('chat_id')
    if not any([user_id, tg_user_id, chat_id]):
        return _ok([])
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


from bot.api_products import (  # noqa: E402
    create_product,
    products_list,
    update_product,
)


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
