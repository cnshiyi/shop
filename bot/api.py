"""bot 域后台 API。"""

import base64
import binascii
import hashlib
import hmac
import json
import os
import secrets
import struct
import time
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from urllib.parse import quote

from asgiref.sync import async_to_sync

from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.contrib.sessions.models import Session
from django.db import ProgrammingError, transaction
from django.db.models import Q, CharField, Count, Sum
from django.db.models.functions import Cast
from django.http import JsonResponse
from django.contrib.auth.password_validation import validate_password
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from bot.models import BotOperationLog, TelegramChatArchive, TelegramChatMessage, TelegramLoginAccount, TelegramUser
from bot.services import _get_or_create_user_sync
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


def _staff_required(user):
    return user.is_active and (user.is_staff or user.is_superuser)


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
    prefix = 'session-'
    if not token.startswith(prefix):
        return None
    session_key = token[len(prefix):].strip()
    if not session_key or session_key.isdigit():
        return None
    session = Session.objects.filter(session_key=session_key, expire_date__gt=timezone.now()).first()
    if not session:
        return None
    data = session.get_decoded()
    raw_user_id = data.get('_auth_user_id')
    if not raw_user_id:
        return None
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(pk=raw_user_id, is_active=True).first()


def _authenticate_dashboard_request(request):
    user = getattr(request, 'user', None)
    if user and user.is_authenticated:
        return user if _staff_required(user) else None
    auth_header = request.headers.get('Authorization') or ''
    prefix = 'Bearer '
    if not auth_header.startswith(prefix):
        return None
    user = _user_from_bearer_session(auth_header[len(prefix):].strip())
    if user and _staff_required(user):
        request.user = user
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


def _cloud_account_labels(item):
    account = getattr(item, 'cloud_account', None)
    account_name = getattr(account, 'name', '') or ''
    external_account_id = getattr(account, 'external_account_id', '') or getattr(item, 'account_label', '') or ''
    return account_name, external_account_id


def _shutdown_log_items(limit=100):
    cutoff = timezone.now() - timezone.timedelta(days=7)
    suspend_days = _runtime_int('cloud_suspend_after_days', 3)
    delete_days = _runtime_int('cloud_delete_after_days', 0)

    orders = list(
        CloudServerOrder.objects.select_related('user', 'cloud_account').filter(suspend_at__isnull=False)
        .exclude(status__in=['cancelled', 'deleted'])
        .order_by('suspend_at', '-updated_at')[:300]
    )
    order_ids = [item.id for item in orders]
    logs = {}
    for item in CloudIpLog.objects.filter(order_id__in=order_ids, event_type__in=['suspended', 'suspend_failed']).order_by('order_id', '-created_at', '-id'):
        logs.setdefault(item.order_id, item)

    items = []
    included_order_ids = set()
    for order in orders:
        included_order_ids.add(order.id)
        log = logs.get(order.id)
        user_display_name, username_label = _telegram_user_labels(order.user)
        account_name, external_account_id = _cloud_account_labels(order)
        is_aliyun = order.provider == 'aliyun_simple'
        items.append({
            'id': f'order-{order.id}',
            'order_id': order.id,
            'asset_id': None,
            'order_no': order.order_no,
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': order.public_ip or order.previous_public_ip or '',
            'provider': order.provider or '',
            'provider_label': _provider_label(order.provider),
            'cloud_account_id': order.cloud_account_id,
            'cloud_account_name': account_name,
            'external_account_id': external_account_id,
            'account_label': order.account_label or '',
            'status': order.status,
            'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
            'service_expires_at': order.service_expires_at,
            'suspend_at': None if is_aliyun else order.suspend_at,
            'delete_at': None if is_aliyun else order.delete_at,
            'note': (log.note if log else '') or '',
            'logged_at': log.created_at if log else None,
        })

    active_statuses = [
        CloudAsset.STATUS_RUNNING,
        CloudAsset.STATUS_PENDING,
        CloudAsset.STATUS_STARTING,
        CloudAsset.STATUS_STOPPED,
        CloudAsset.STATUS_SUSPENDED,
        CloudAsset.STATUS_EXPIRED_GRACE,
        CloudAsset.STATUS_UNKNOWN,
    ]
    aliyun_statuses = active_statuses + [
        CloudAsset.STATUS_EXPIRED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_DELETED,
    ]
    assets = list(
        CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER, actual_expires_at__isnull=False)
        .filter(Q(is_active=True, status__in=active_statuses) | Q(provider='aliyun_simple', status__in=aliyun_statuses))
        .exclude(order_id__in=included_order_ids)
        .order_by('actual_expires_at', '-updated_at')[:500]
    )
    for asset in assets:
        expires_at = asset.actual_expires_at
        user_display_name, username_label = _telegram_user_labels(asset.user or (asset.order.user if asset.order_id and asset.order else None))
        account_name, external_account_id = _cloud_account_labels(asset)
        if not external_account_id and asset.order_id and asset.order:
            order_account_name, order_external_account_id = _cloud_account_labels(asset.order)
            account_name = account_name or order_account_name
            external_account_id = order_external_account_id
        if asset.provider == 'aliyun_simple':
            suspend_at = None
            delete_at = None
        else:
            suspend_at = _with_runtime_time(expires_at + timezone.timedelta(days=suspend_days), 'cloud_suspend_time')
            delete_at = _with_runtime_time(suspend_at + timezone.timedelta(days=delete_days), 'cloud_delete_time')
            if delete_at and suspend_at and delete_at < suspend_at:
                delete_at = suspend_at
        items.append({
            'id': f'asset-{asset.id}',
            'order_id': asset.order_id,
            'asset_id': asset.id,
            'order_no': asset.order.order_no if asset.order_id and asset.order else asset.asset_name or asset.instance_id or f'asset-{asset.id}',
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': asset.public_ip or asset.previous_public_ip or '',
            'provider': asset.provider or '',
            'provider_label': _provider_label(asset.provider),
            'cloud_account_id': asset.cloud_account_id or (asset.order.cloud_account_id if asset.order_id and asset.order else None),
            'cloud_account_name': account_name,
            'external_account_id': external_account_id,
            'account_label': asset.account_label or (asset.order.account_label if asset.order_id and asset.order else '') or '',
            'status': asset.status,
            'status_label': _status_label(asset.status, CloudAsset.STATUS_CHOICES),
            'service_expires_at': expires_at,
            'suspend_at': suspend_at,
            'delete_at': delete_at,
            'note': asset.note or '',
            'logged_at': None,
        })

    def sort_key(item):
        suspend_at = item['suspend_at']
        sort_at = suspend_at or item['service_expires_at']
        is_old_shutdown = bool(suspend_at and suspend_at < cutoff)
        timestamp = sort_at.timestamp() if sort_at else float('inf')
        return (1 if is_old_shutdown else 0, -timestamp if is_old_shutdown else timestamp, str(item['id']))

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


def _unattached_ip_delete_items(limit=50):
    now = timezone.now()
    delete_days = _runtime_int('cloud_unattached_ip_delete_after_days', 15)
    assets = list(
        CloudAsset.objects.select_related('user').filter(kind=CloudAsset.KIND_SERVER)
        .filter(Q(provider_status__icontains='未附加') | Q(note__icontains='未附加IP') | Q(note__icontains='未附加固定IP'))
        .exclude(status__in=[CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_TERMINATING])
        .order_by('actual_expires_at', 'created_at', '-updated_at')[:300]
    )
    items = []
    for asset in assets:
        base_at = asset.actual_expires_at or asset.created_at or asset.updated_at or now
        user_display_name, username_label = _telegram_user_labels(asset.user)
        delete_at = _with_runtime_time(base_at + timezone.timedelta(days=delete_days), 'cloud_unattached_ip_delete_time')
        items.append({
            'id': asset.id,
            'asset_name': asset.asset_name or asset.instance_id or f'asset-{asset.id}',
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': asset.public_ip or asset.previous_public_ip or '',
            'provider_status': asset.provider_status or '',
            'delete_at': _iso(delete_at),
            'note': asset.note or '',
            'is_overdue': bool(delete_at and delete_at <= now),
        })
    return sorted(items, key=lambda item: (0 if item['is_overdue'] else 1, item['delete_at'] or '', item['id']))[:limit]


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
        'status': item.status,
        'status_label': item.status_label,
        'status_note': item.status_note,
        'last_checked_at': _iso(item.last_checked_at),
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
        'source_label': '个人号' if latest.source == 'account' else '机器人',
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
        'source_label': '个人号' if item.source == 'account' else '机器人',
        'created_at': _iso(item.created_at),
    }


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
    due_today = CloudAsset.objects.filter(
        kind=CloudAsset.KIND_SERVER,
        status__in=[CloudAsset.STATUS_RUNNING, CloudAsset.STATUS_UNKNOWN],
        actual_expires_at__gte=today_start,
        actual_expires_at__lt=today_end,
    ).exclude(actual_expires_at__isnull=True).count()
    renew_due = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, status__in=[CloudAsset.STATUS_RUNNING, CloudAsset.STATUS_UNKNOWN], actual_expires_at__lte=renew_before).exclude(actual_expires_at__isnull=True).count()
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
    for expires_at in CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, actual_expires_at__gte=trend_start, actual_expires_at__lt=trend_end).values_list('actual_expires_at', flat=True):
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
def shutdown_logs(request):
    try:
        limit = int(request.GET.get('limit') or 100)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 300))
    return _ok(_shutdown_log_items(limit=limit))


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
    request.session.set_expiry(2 * 60 * 60)
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
    request.session.set_expiry(2 * 60 * 60)
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
            'receive_address',
            'bot_token',
            'telegram_api_id',
            'telegram_api_hash',
            'trongrid_api_key',
            'scanner_block_log_enabled',
            'scanner_verbose',
            'cloud_suspend_after_days',
            'cloud_suspend_time',
            'cloud_delete_after_days',
            'cloud_delete_time',
            'cloud_unattached_ip_delete_after_days',
            'cloud_unattached_ip_delete_time',
            'bot_admin_chat_id',
            'dashboard_totp_secret',
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
    )
    return _ok(_cloud_account_payload(item))


@csrf_exempt
@dashboard_login_required
@require_POST
def update_cloud_account(request, account_id: int):
    item = CloudAccountConfig.objects.filter(id=account_id).first()
    if not item:
        return _error('云账号不存在', status=404)
    payload = _read_payload(request)
    provider = (payload.get('provider') or item.provider).strip()
    name = (payload.get('name') or item.name).strip()
    external_account_id = payload.get('external_account_id')
    access_key = payload.get('access_key')
    secret_key = payload.get('secret_key')
    region_hint = payload.get('region_hint')
    is_active = payload.get('is_active')
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
    for msg in TelegramChatMessage.objects.select_related('login_account').order_by('-created_at', '-id')[:500]:
        latest_by_user.setdefault(msg.tg_user_id, msg)
    for msg in messages.select_related('login_account')[:500]:
        latest_by_chat.setdefault(msg.chat_id, msg)
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
    item.username = getattr(me, 'username', None) or item.username
    item.label = getattr(me, 'first_name', None) or item.username or item.phone or item.label
    _get_or_create_user_sync(getattr(me, 'id', 0), getattr(me, 'username', None), getattr(me, 'first_name', None))
    item.note = '登录成功'
    item.last_synced_at = timezone.now()
    item.save(update_fields=['status', 'username', 'label', 'note', 'last_synced_at', 'updated_at'])
    return item


@csrf_exempt
@dashboard_login_required
@require_POST
def update_telegram_account_notify(request, account_id: int):
    payload = _read_payload(request)
    item = TelegramLoginAccount.objects.filter(id=account_id).first()
    if not item:
        return _error('账号不存在', status=404)
    item.notify_enabled = bool(payload.get('notify_enabled'))
    item.save(update_fields=['notify_enabled', 'updated_at'])
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
    username = str(payload.get('username') or '').strip().lstrip('@')
    note = str(payload.get('note') or '').strip()
    if not label:
        return _error('账号备注不能为空', status=400)
    item = TelegramLoginAccount.objects.create(
        label=label,
        phone=phone or None,
        username=username or None,
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
    'me',
    'overview',
    'products_list',
    'site_config_groups',
    'site_configs_list',
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
