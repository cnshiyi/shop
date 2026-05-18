import base64
import binascii
import hmac
import io
import json
import secrets
import struct
import time
from decimal import Decimal, InvalidOperation
from hashlib import sha1
from urllib.parse import quote

from asgiref.sync import async_to_sync
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.core import signing
from django.contrib.auth.models import AnonymousUser
from django.core.management import call_command
from django.db import IntegrityError, ProgrammingError, transaction
from django.db.models import BooleanField, Case, CharField, Count, Q, Value, When
from django.db.models.functions import Cast
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.runtime_config import CONFIG_HELP, SENSITIVE_CONFIG_KEYS
from core.models import CloudAccountConfig, ExternalSyncLog, SiteConfig
from core.persistence import record_external_sync_log
from accounts.models import BalanceLedger, TelegramUser, TelegramUsername
from finance.models import Recharge
from mall.models import CloudAsset, CloudServerOrder, Order, Product, Server, CloudServerPlan, ServerPrice
from monitoring.models import AddressMonitor
from cloud.lifecycle import (
    _delete_instance,
    _mark_deleted,
    _mark_recycled,
    _release_static_ip,
    mark_static_ip_asset_released,
    release_aws_static_ip_asset,
)
from cloud.provisioning import provision_cloud_server
from biz.services.cloud_servers import mark_cloud_server_ip_change_requested
from biz.services.custom import ensure_cloud_server_plans, refresh_custom_plan_cache


DASHBOARD_TEXT_CONFIG_DEFAULTS = {
    'bot_custom_quantity_title': '请选择购买数量',
    'bot_custom_quantity_hint': '可输入自定义数量。',
    'bot_custom_payment_title': '请选择支付方式',
    'bot_custom_payment_hint': '请按页面提示完成支付。',
    'bot_custom_wallet_title': '充值钱包',
    'bot_custom_pending_order': '你有未完成订单，请继续处理。',
    'bot_custom_pending_wallet': '请先完成充值后再继续。',
    'bot_custom_order_notice': '订单已创建，请留意状态变化。',
    'bot_custom_port_hint': '默认端口可在订单详情中查看。',
    'bot_custom_balance_insufficient': '余额不足，请先充值。',
}

DASHBOARD_CONFIG_DEFAULTS = {
    **DASHBOARD_TEXT_CONFIG_DEFAULTS,
}

DASHBOARD_CONFIG_LABELS = {
    'admin_password_notice': '后台密码提醒文案',
    'alibaba_cloud_account_id': '阿里云主账号 ID',
    'aliyun_account_id': '阿里云账号 ID',
    'aliyun_region': '阿里云默认地域',
    'aws_access_key_id': 'AWS 访问密钥 ID',
    'aws_region': 'AWS 默认地域',
    'aws_secret_access_key': 'AWS 私密访问密钥',
    'bot_custom_balance_insufficient': '余额不足提示',
    'bot_custom_order_notice': '订单创建提示',
    'bot_custom_payment_hint': '支付方式提示',
    'bot_custom_payment_title': '支付方式标题',
    'bot_custom_pending_order': '未完成订单提示',
    'bot_custom_pending_wallet': '待充值提示',
    'bot_custom_port_hint': '端口选择提示',
    'bot_custom_quantity_hint': '购买数量提示',
    'bot_custom_quantity_title': '购买数量标题',
    'bot_custom_wallet_title': '钱包充值标题',
    'bot_token': 'Telegram 机器人访问令牌',
    'cleanup_retention_days': '清理日志保留天数',
    'cloud_asset_sync_interval_seconds': '云资产自动同步间隔（秒）',
    'cloud_auto_renew_execution_notify_enabled': '自动续费结果通知',
    'cloud_auto_renew_execution_notify_events': '自动续费通知事件',
    'cloud_daily_expiry_summary_enabled': '每日到期汇总通知',
    'cloud_delete_after_days': '关机后删机等待天数',
    'cloud_delete_time': '删机任务执行时间',
    'cloud_renew_notice_days': '续费提醒提前天数',
    'cloud_renew_notice_debug_repeat': '续费提醒调试重复发送',
    'cloud_suspend_after_days': '到期后关机宽限天数',
    'cloud_suspend_time': '关机任务执行时间',
    'cloud_unattached_ip_delete_after_days': '未附加固定 IP 保留天数',
    'cloud_unattached_ip_delete_time': '固定 IP 回收执行时间',
    'dashboard_totp_secret': '后台 Google Authenticator 二级验证',
    'database_url': '数据库连接地址（优先）',
    'db_host': '数据库主机',
    'db_name': '数据库名称',
    'db_password': '数据库密码',
    'db_port': '数据库端口',
    'db_user': '数据库用户名',
    'fsm_data_ttl': '机器人临时数据保留时间（秒）',
    'fsm_state_ttl': '机器人会话状态保留时间（秒）',
    'receive_address': '收款地址（USDT/TRX 共用）',
    'redis_db': 'Redis 数据库编号',
    'redis_port': 'Redis 端口',
    'redis_url': 'Redis 连接地址',
    'scanner_block_log_enabled': '区块扫描日志',
    'scanner_verbose': '扫描详细日志',
    'telegram_listener_push_enabled': 'Telegram 操作通知',
    'telegram_listener_push_private_enabled': 'Telegram 私聊通知',
    'telegram_api_hash': 'Telegram 登录应用密钥',
    'telegram_api_id': 'Telegram 登录应用 ID',
    'telegram_webhook_url': 'Telegram 回调地址',
    'text_init_enabled': '启用默认文案初始化',
    'trongrid_api_key': 'TRONGrid 接口密钥',
}

DASHBOARD_SENSITIVE_CONFIG_KEYS = SENSITIVE_CONFIG_KEYS | {
    'aws_secret_access_key',
    'dashboard_totp_secret',
    'db_password',
    'telegram_api_hash',
}

NOTICE_SWITCH_CONFIG_KEYS = {
    'renew_notice': 'cloud_notice_renew_enabled',
    'auto_renew_notice': 'cloud_notice_auto_renew_enabled',
    'delete_notice': 'cloud_notice_delete_enabled',
    'recycle_notice': 'cloud_notice_recycle_enabled',
}

NOTICE_SENT_FIELDS = {
    'renew_notice': 'renew_notice_sent_at',
    'delete_notice': 'delete_notice_sent_at',
    'recycle_notice': 'recycle_notice_sent_at',
}

NOTICE_TEXT_CONFIG_PREFIX = 'cloud_notice_text_override'
AUTO_RENEW_ELIGIBLE_STATUSES = {'completed', 'expiring', 'suspended'}
RENEWABLE_CLOUD_ORDER_STATUSES = {'completed', 'expiring', 'suspended'}


def _dashboard_config_label(key):
    return DASHBOARD_CONFIG_LABELS.get(key) or CONFIG_HELP.get(key, '')


def _site_config_payload(item):
    is_sensitive = item.is_sensitive or item.key in DASHBOARD_SENSITIVE_CONFIG_KEYS
    plain_value = SiteConfig.get(item.key, '')
    return {
        'id': item.id,
        'key': item.key,
        'value': '' if is_sensitive else plain_value,
        'value_preview': item.masked_value() if is_sensitive else plain_value,
        'is_sensitive': is_sensitive,
        'description': _dashboard_config_label(item.key),
        'default_value': DASHBOARD_CONFIG_DEFAULTS.get(item.key, ''),
    }


def _cloud_account_payload(item):
    default_region = 'ap-southeast-1' if item.provider == CloudAccountConfig.PROVIDER_AWS else 'cn-hongkong'
    return {
        'id': item.id,
        'provider': item.provider,
        'provider_label': item.get_provider_display(),
        'name': item.name,
        'external_account_id': '',
        'access_key': '',
        'secret_key': '',
        'access_key_preview': item.access_key_preview,
        'secret_key_preview': item.secret_key_preview,
        'region_hint': item.region_hint,
        'effective_region': item.region_hint or default_region,
        'is_active': item.is_active,
        'shutdown_enabled': item.provider != CloudAccountConfig.PROVIDER_ALIYUN,
        'status': item.status,
        'status_label': item.status_label,
        'status_note': item.status_note,
        'last_checked_at': _iso(item.last_checked_at),
        'created_at': _iso(item.created_at),
        'updated_at': _iso(item.updated_at),
    }


def _mask_secret_value(value: str) -> str:
    value = str(value or '')
    if not value:
        return ''
    if len(value) <= 8:
        return '*' * len(value)
    return f'{value[:4]}***{value[-4:]}'


def _admin_user_payload(user):
    return {
        'id': user.id,
        'username': user.get_username(),
        'email': user.email or '',
        'is_active': bool(user.is_active),
        'is_staff': bool(user.is_staff),
        'is_superuser': bool(user.is_superuser),
        'last_login': _iso(user.last_login),
        'date_joined': _iso(user.date_joined),
    }


def _require_superuser(request):
    if not getattr(request, 'user', None) or not request.user.is_superuser:
        return _error('需要超级管理员权限', status=403)
    return None


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


def _decimal_to_str(value, places=None):
    if value is None:
        value = Decimal('0')
    elif not isinstance(value, Decimal):
        value = Decimal(str(value))
    if places is not None:
        quantizer = Decimal('1').scaleb(-places)
        value = value.quantize(quantizer)
    return format(value, 'f')


def _staff_required(user):
    return user.is_active and (user.is_staff or user.is_superuser)


def _make_session_token(user_pk: int) -> str:
    """Issue an HMAC-signed token that encodes the user PK.

    Format returned to the client: ``session-<signed_value>``
    The signed value is produced by Django's ``signing`` module (uses SECRET_KEY
    + salt + timestamp), so it cannot be forged or tampered with.
    """
    return 'session-' + signing.dumps(user_pk, salt='dashboard-session', compress=True)


def _verify_session_token(token: str):
    """Return the user PK encoded in *token*, or None if invalid/expired.

    Tokens are valid for 30 days (2_592_000 seconds).  Tampering or using a
    token after SECRET_KEY rotation causes BadSignature → returns None.
    """
    prefix = 'session-'
    if not token.startswith(prefix):
        return None
    signed = token[len(prefix):]
    try:
        return signing.loads(signed, salt='dashboard-session', max_age=2_592_000)
    except signing.SignatureExpired:
        return None
    except signing.BadSignature:
        return None


def _normalise_totp_secret(secret: str) -> str:
    return str(secret or '').strip().replace(' ', '').upper()


def _totp_code(secret: str, counter: int) -> str:
    normalized = _normalise_totp_secret(secret)
    padding = '=' * ((8 - len(normalized) % 8) % 8)
    key = base64.b32decode((normalized + padding).encode('ascii'), casefold=True)
    msg = struct.pack('>Q', int(counter))
    digest = hmac.new(key, msg, sha1).digest()
    offset = digest[-1] & 0x0F
    code_int = struct.unpack('>I', digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return f'{code_int % 1_000_000:06d}'


def _verify_totp(secret: str, token: str, *, valid_window: int = 1) -> bool:
    token = str(token or '').strip()
    if len(token) != 6 or not token.isdigit():
        return False
    try:
        counter = int(time.time() // 30)
        for offset in range(-valid_window, valid_window + 1):
            if hmac.compare_digest(_totp_code(secret, counter + offset), token):
                return True
    except (binascii.Error, ValueError, TypeError):
        return False
    return False


def _authenticate_dashboard_request(request):
    if getattr(request, 'user', None) and request.user.is_authenticated:
        user = request.user
    else:
        auth_header = request.headers.get('Authorization') or ''
        if not auth_header.startswith('Bearer '):
            return None
        token = auth_header[len('Bearer '):].strip()
        user_pk = _verify_session_token(token)
        if user_pk is None:
            return None
        user = get_user_model().objects.filter(pk=user_pk, is_active=True).first()
    if user:
        request.user = user
    return user if user and _staff_required(user) else None


def dashboard_login_required(view_func):
    def wrapped(request, *args, **kwargs):
        if not _authenticate_dashboard_request(request):
            return _error('请先登录', status=401)
        return view_func(request, *args, **kwargs)
    return wrapped


def _ok(data):
    return JsonResponse({'code': 0, 'data': data, 'message': 'ok'})


def _error(message, code=1, status=400):
    return JsonResponse({'code': code, 'message': message, 'data': None}, status=status)


def _iso(value):
    if not value:
        return None
    return timezone.localtime(value).isoformat() if timezone.is_aware(value) else value.isoformat()


def _days_left(value):
    if not value:
        return None
    now = timezone.now()
    delta = value - now
    return delta.days if delta.days >= 0 else 0


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


def _provider_label(provider):
    mapping = {
        'aliyun_simple': '阿里云',
        'aws_lightsail': 'AWS Lightsail',
    }
    return mapping.get(provider, provider or '-')


def _server_source_label(source):
    mapping = {
        'aliyun': '阿里云自动同步',
        'aws_manual': 'AWS 手工录入',
        'aws_sync': 'AWS 自动同步',
        'order': '订单创建',
    }
    return mapping.get(source, source or '-')


def _region_label(region_code, region_name=None):
    mapping = {
        # Aliyun 中国大陆
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
        # Aliyun 海外
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
        # AWS 常用区域
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


def _normalize_server_status(provider, raw_status):
    value = str(raw_status or '').strip().lower()
    if provider == 'aws_lightsail':
        mapping = {
            'running': CloudAsset.STATUS_RUNNING,
            'pending': CloudAsset.STATUS_PENDING,
            'starting': CloudAsset.STATUS_STARTING,
            'stopping': CloudAsset.STATUS_STOPPING,
            'stopped': CloudAsset.STATUS_STOPPED,
            'shutting-down': CloudAsset.STATUS_TERMINATING,
            'terminated': CloudAsset.STATUS_TERMINATED,
            'terminating': CloudAsset.STATUS_TERMINATING,
        }
        return mapping.get(value, CloudAsset.STATUS_UNKNOWN)
    if provider == 'aliyun_simple':
        mapping = {
            'running': CloudAsset.STATUS_RUNNING,
            'starting': CloudAsset.STATUS_STARTING,
            'pending': CloudAsset.STATUS_PENDING,
            'stopping': CloudAsset.STATUS_STOPPING,
            'stopped': CloudAsset.STATUS_STOPPED,
            'expired': CloudAsset.STATUS_EXPIRED,
            'deleting': CloudAsset.STATUS_DELETING,
            'deleted': CloudAsset.STATUS_DELETED,
        }
        return mapping.get(value, CloudAsset.STATUS_UNKNOWN)
    return CloudAsset.STATUS_UNKNOWN


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


def _merge_usernames(existing, incoming):
    result = []
    seen = set()
    for item in [*incoming, *existing]:
        key = str(item).lower()
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


def _parse_decimal(value, field_label):
    raw = str(value or '').strip()
    if raw == '':
        raise ValueError(f'{field_label}不能为空')
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'{field_label}格式不正确')


def _read_payload(request):
    try:
        import json
        payload = json.loads(request.body.decode('utf-8') or '{}') if request.body else {}
    except Exception:
        payload = {}
    if payload:
        return payload
    return request.POST.dict() if hasattr(request.POST, 'dict') else request.POST


def _normalize_cloud_provider(value):
    provider = str(value or '').strip().lower()
    if provider in {'aliyun', 'aliyun_simple', 'alibaba', 'alibaba_cloud'}:
        return 'aliyun'
    if provider in {'aws', 'aws_lightsail', 'lightsail'}:
        return 'aws'
    return ''


def _cloud_sync_task(provider, region, *, reason='', asset_ids=None):
    command = 'sync_aliyun_assets' if provider == 'aliyun' else 'sync_aws_assets'
    return {
        'provider': provider,
        'region': region,
        'command': command,
        'reason': reason,
        'summary': {
            'asset_ids': asset_ids or [],
        },
    }


def _run_cloud_sync_task(task):
    stdout = io.StringIO()
    started_at = time.monotonic()
    call_command(task['command'], region=task['region'], stdout=stdout)
    duration = round(time.monotonic() - started_at, 3)
    output = [line.strip() for line in stdout.getvalue().splitlines() if line.strip()]
    return {
        **task,
        'duration_seconds': duration,
        'logs': output[-20:],
    }


def _selected_cloud_sync_tasks(asset_ids):
    if not asset_ids:
        return [], []
    assets = CloudAsset.objects.filter(pk__in=asset_ids).only('id', 'provider', 'region_code')
    tasks = {}
    skipped = []
    for asset in assets:
        provider = _normalize_cloud_provider(asset.provider)
        region = (asset.region_code or '').strip()
        if not provider or not region:
            skipped.append({
                'provider': provider or asset.provider or '',
                'region': region,
                'reason': f'资产 #{asset.id} 缺少云厂商或地域，无法定位同步任务',
                'summary': {'asset_ids': [asset.id]},
            })
            continue
        key = (provider, region)
        if key not in tasks:
            tasks[key] = _cloud_sync_task(
                provider,
                region,
                reason='按选中资产涉及的云厂商和地域同步',
                asset_ids=[],
            )
        tasks[key]['summary']['asset_ids'].append(asset.id)

    missing_ids = sorted(set(asset_ids) - {asset.id for asset in assets})
    for asset_id in missing_ids:
        skipped.append({
            'provider': '',
            'region': '',
            'reason': f'资产 #{asset_id} 不存在',
            'summary': {'asset_ids': [asset_id]},
        })
    return list(tasks.values()), skipped


def _dashboard_sync_log_payload(tasks, skipped_tasks, errors, synced):
    return {
        'tasks': tasks,
        'skipped_tasks': skipped_tasks,
        'errors': errors,
        'synced': synced,
    }


def _get_keyword(request):
    return (request.GET.get('keyword') or request.GET.get('q') or request.GET.get('search') or '').strip()


def _json_config(key, default):
    raw = SiteConfig.get(key, '')
    if not raw:
        return json.loads(json.dumps(default, ensure_ascii=False))
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return json.loads(json.dumps(default, ensure_ascii=False))


def _set_json_config(key, value):
    SiteConfig.set(key, json.dumps(value, ensure_ascii=False), sensitive=False)


def _telegram_session_config_key(account_id):
    return f'telegram_login_session_{account_id}'


def _get_telegram_session(account_id):
    return SiteConfig.get(_telegram_session_config_key(account_id), '')


def _set_telegram_session(account_id, session_string):
    SiteConfig.set(
        _telegram_session_config_key(account_id),
        session_string or '',
        sensitive=True,
    )


def _telegram_api_config():
    api_id_raw = SiteConfig.get('telegram_api_id', '').strip()
    api_hash = SiteConfig.get('telegram_api_hash', '').strip()
    if not api_id_raw or not api_hash:
        raise ValueError('请先在“设置 / Telegram 登录设置”填写登录应用 ID 和登录应用密钥。')
    try:
        api_id = int(api_id_raw)
    except (TypeError, ValueError) as exc:
        raise ValueError('Telegram 登录应用 ID 必须是数字。') from exc
    return api_id, api_hash


def _telegram_error_message(exc):
    name = exc.__class__.__name__
    if name == 'PhoneNumberInvalidError':
        return '手机号不正确，请检查国家区号和号码。'
    if name == 'PhoneCodeInvalidError':
        return '验证码不正确，请重新输入。'
    if name == 'PhoneCodeExpiredError':
        return '验证码已过期，请重新发送。'
    if name in {'PasswordHashInvalidError', 'PasswordHashInvalidError'}:
        return '二级密码不正确。'
    if name == 'SessionPasswordNeededError':
        return '需要输入二级密码。'
    if name == 'FloodWaitError':
        seconds = getattr(exc, 'seconds', None)
        if seconds:
            return f'Telegram 限制请求过快，请等待 {seconds} 秒后再试。'
    return str(exc) or 'Telegram 请求失败。'


def _telethon_user_payload(me):
    usernames = []
    username = getattr(me, 'username', None)
    if username:
        usernames.append(str(username).lstrip('@'))
    for item in getattr(me, 'usernames', None) or []:
        value = getattr(item, 'username', None) or str(item)
        if value:
            usernames.append(str(value).lstrip('@'))
    usernames = _split_usernames(','.join(usernames))
    return {
        'first_name': getattr(me, 'first_name', '') or '',
        'phone': getattr(me, 'phone', '') or '',
        'tg_user_id': int(getattr(me, 'id', 0) or 0),
        'username': usernames[0] if usernames else '',
        'usernames': usernames,
    }


def _save_telegram_identity(tg_user_id, usernames=None, first_name=''):
    if not tg_user_id:
        return None
    username_text = ','.join(_split_usernames(','.join(usernames or [])))
    user, _ = TelegramUser.objects.get_or_create(
        tg_user_id=int(tg_user_id),
        defaults={'username': username_text, 'first_name': first_name or ''},
    )
    changed_fields = []
    if first_name and user.first_name != first_name:
        user.first_name = first_name
        changed_fields.append('first_name')
    if changed_fields:
        changed_fields.append('updated_at')
        user.save(update_fields=changed_fields)
    _sync_telegram_username(user, username_text)
    return user


def _normalize_telegram_account(item):
    now = _iso(timezone.now())
    normalized = {
        'created_at': item.get('created_at') or now,
        'first_name': item.get('first_name') or '',
        'has_session': bool(item.get('has_session')),
        'id': int(item.get('id') or 0),
        'label': item.get('label') or item.get('phone') or 'Telegram 账号',
        'last_synced_at': item.get('last_synced_at'),
        'listener_push_enabled': bool(item.get('listener_push_enabled', False)),
        'note': item.get('note') or '',
        'notify_enabled': bool(item.get('notify_enabled', True)),
        'phone': item.get('phone') or '',
        'phone_code_hash': item.get('phone_code_hash') or '',
        'status': item.get('status') or 'registered',
        'tg_user_id': item.get('tg_user_id'),
        'updated_at': item.get('updated_at') or now,
        'username': item.get('username') or '',
        'usernames': _split_usernames(item.get('username')) or item.get('usernames') or [],
    }
    normalized['has_session'] = bool(_get_telegram_session(normalized['id']))
    return normalized


def _normalize_telegram_accounts_overview(overview):
    accounts = [
        _normalize_telegram_account(item)
        for item in overview.get('accounts', [])
        if isinstance(item, dict)
    ]
    overview['accounts'] = accounts
    return overview


def _find_telegram_account(overview, account_id):
    for item in overview.setdefault('accounts', []):
        if int(item.get('id') or 0) == int(account_id or 0):
            return item
    return None


def _next_telegram_account_id(accounts):
    return max([int(item.get('id') or 0) for item in accounts] or [0]) + 1


def _ensure_telegram_login_account(overview, phone):
    accounts = overview.setdefault('accounts', [])
    for item in accounts:
        if str(item.get('phone') or '').strip() == phone:
            return item
    next_id = _next_telegram_account_id(accounts)
    now = _iso(timezone.now())
    item = {
        'id': next_id,
        'label': phone or f'Telegram 账号 {next_id}',
        'phone': phone,
        'username': '',
        'usernames': [],
        'tg_user_id': None,
        'first_name': '',
        'note': '',
        'status': 'pending',
        'notify_enabled': True,
        'listener_push_enabled': False,
        'has_session': False,
        'created_at': now,
        'updated_at': now,
        'last_synced_at': None,
        'phone_code_hash': '',
    }
    accounts.append(item)
    return item


def _finalize_telegram_account(account, profile, session_string):
    now = _iso(timezone.now())
    usernames = _split_usernames(','.join(profile.get('usernames') or []))
    username_text = ','.join(usernames)
    account.update({
        'first_name': profile.get('first_name') or account.get('first_name') or '',
        'has_session': True,
        'last_synced_at': now,
        'phone': account.get('phone') or profile.get('phone') or '',
        'phone_code_hash': '',
        'status': 'logged_in',
        'tg_user_id': profile.get('tg_user_id') or account.get('tg_user_id'),
        'updated_at': now,
        'username': username_text,
        'usernames': usernames,
    })
    if not account.get('label') or account.get('label') == account.get('phone'):
        account['label'] = (
            profile.get('first_name')
            or (f"@{usernames[0]}" if usernames else '')
            or account.get('phone')
            or f"Telegram 账号 {account.get('id')}"
        )
    _set_telegram_session(account['id'], session_string)
    _save_telegram_identity(account.get('tg_user_id'), usernames, account.get('first_name') or '')
    return account


def _telegram_users_from_db(messages=None):
    messages = messages or []
    stats = {}
    for message in messages:
        tg_user_id = message.get('tg_user_id')
        if not tg_user_id:
            continue
        item = stats.setdefault(int(tg_user_id), {'count': 0, 'latest_at': None, 'latest_message': ''})
        item['count'] += 1
        created_at = message.get('created_at')
        if created_at and (not item['latest_at'] or created_at > item['latest_at']):
            item['latest_at'] = created_at
            item['latest_message'] = message.get('text') or ''
    users = []
    for user in TelegramUser.objects.prefetch_related('telegramusernames').order_by('-updated_at', '-id')[:500]:
        usernames = user.usernames
        payload = _user_payload({
            'id': user.id,
            'tg_user_id': user.tg_user_id,
            'username': user.username,
            'first_name': user.first_name,
            'usernames': usernames,
            'primary_username': usernames[0] if usernames else '',
        })
        stat = stats.get(int(user.tg_user_id), {})
        users.append({
            **payload,
            'latest_at': stat.get('latest_at'),
            'latest_message': stat.get('latest_message') or '',
            'message_count': stat.get('count') or 0,
        })
    return users


async def _telegram_send_code_async(phone, api_id, api_hash):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(''), api_id, api_hash)
    try:
        await client.connect()
        sent = await client.send_code_request(phone)
        return {
            'phone_code_hash': sent.phone_code_hash,
            'session_string': client.session.save(),
        }
    finally:
        await client.disconnect()


async def _telegram_sign_in_code_async(session_string, phone, code, phone_code_hash, api_id, api_hash):
    from telethon import TelegramClient
    from telethon.errors import SessionPasswordNeededError
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_string or ''), api_id, api_hash)
    try:
        await client.connect()
        try:
            await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        except SessionPasswordNeededError:
            return {
                'requires_password': True,
                'session_string': client.session.save(),
            }
        me = await client.get_me()
        return {
            'profile': _telethon_user_payload(me),
            'requires_password': False,
            'session_string': client.session.save(),
        }
    finally:
        await client.disconnect()


async def _telegram_sign_in_password_async(session_string, password, api_id, api_hash):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_string or ''), api_id, api_hash)
    try:
        await client.connect()
        await client.sign_in(password=password or '')
        me = await client.get_me()
        return {
            'profile': _telethon_user_payload(me),
            'session_string': client.session.save(),
        }
    finally:
        await client.disconnect()


async def _telegram_check_session_async(session_string, api_id, api_hash):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_string or ''), api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            return {'authorized': False, 'session_string': client.session.save()}
        me = await client.get_me()
        return {
            'authorized': True,
            'profile': _telethon_user_payload(me),
            'session_string': client.session.save(),
        }
    finally:
        await client.disconnect()


async def _telegram_send_message_async(session_string, chat_id, text, api_id, api_hash):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    client = TelegramClient(StringSession(session_string or ''), api_id, api_hash)
    try:
        await client.connect()
        if not await client.is_user_authorized():
            raise ValueError('Telegram 会话已失效，请重新登录账号。')
        sent = await client.send_message(chat_id, text)
        return {
            'message_id': getattr(sent, 'id', None),
            'created_at': _iso(getattr(sent, 'date', None) or timezone.now()),
            'session_string': client.session.save(),
        }
    finally:
        await client.disconnect()


def _apply_keyword_filter(queryset, keyword, fields):
    if not keyword:
        return queryset
    condition = Q()
    for field in fields:
        condition |= Q(**{f'{field}__icontains': keyword})
    return queryset.filter(condition)


def _server_price_payload(item):
    return {
        'id': item.id,
        'provider': item.provider,
        'region_code': item.region_code,
        'region_name': item.region_name,
        'bundle_code': item.bundle_code,
        'plan_name': item.server_name,
        'server_name': item.server_name,
        'plan_description': item.server_description or '',
        'server_description': item.server_description or '',
        'cpu': item.cpu,
        'memory': item.memory,
        'storage': item.storage,
        'bandwidth': item.bandwidth,
        'cost_price': _decimal_to_str(getattr(item, 'cost_price', 0)),
        'price': _decimal_to_str(item.price),
        'currency': item.currency,
        'sort_order': item.sort_order,
        'is_active': item.is_active,
        'updated_at': _iso(item.updated_at),
    }


@ensure_csrf_cookie
@require_GET
def csrf(request):
    return _ok({'csrf': True})


@csrf_exempt
@require_POST
def auth_login(request):
    username = request.POST.get('username') or request.headers.get('x-username')
    password = request.POST.get('password') or request.headers.get('x-password')

    if not username or not password:
        try:
            payload = json.loads(request.body.decode('utf-8') or '{}')
            username = username or payload.get('username')
            password = password or payload.get('password')
        except Exception:
            pass

    user = authenticate(request, username=username, password=password)
    if not user:
        return _error('用户名或密码错误', status=401)
    if not user.is_active:
        return _error('用户已禁用', status=403)
    if not _staff_required(user):
        return _error('账号没有后台访问权限', status=403)
    totp_secret = SiteConfig.get('dashboard_totp_secret', '')
    if totp_secret:
        otp_token = str((request.POST.get('otp_token') if request.POST else '') or '').strip()
        if not otp_token:
            try:
                payload = json.loads(request.body.decode('utf-8') or '{}') if request.body else {}
                otp_token = str(payload.get('otp_token') or '').strip()
            except Exception:
                otp_token = ''
        if not _verify_totp(totp_secret, otp_token):
            return _error('Google 动态验证码不正确', status=401)

    login(request, user)
    return _ok({'accessToken': _make_session_token(user.pk)})


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
    return _ok({'accessToken': _make_session_token(request.user.pk)})


@dashboard_login_required
@require_GET
def auth_codes(request):
    codes = ['dashboard', 'users', 'cloud', 'finance', 'monitoring', 'settings']
    if request.user.is_superuser:
        codes.append('superuser')
    return _ok(codes)


@dashboard_login_required
@require_GET
def user_info(request):
    username = request.user.get_username() or 'admin'
    is_superuser = bool(request.user.is_superuser)
    roles = ['superuser'] if is_superuser else ['staff']
    permissions = ['superuser'] if is_superuser else []
    return _ok({
        'userId': str(request.user.pk),
        'username': username,
        'realName': request.user.get_full_name() or username,
        'avatar': '',
        'desc': 'Shop 管理后台管理员',
        'homePath': '/admin/cloud-assets',
        'is_superuser': is_superuser,
        'permissions': permissions,
        'roles': roles,
        'token': _make_session_token(request.user.pk),
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


@dashboard_login_required
@require_GET
def site_configs_list(request):
    queryset = SiteConfig.objects.order_by('key')
    return _ok([_site_config_payload(item) for item in queryset])


def _dashboard_site_config_groups():
    return {
        'system': ['dashboard_totp_secret', 'text_init_enabled'],
        'payment': ['receive_address', 'trongrid_api_key'],
        'logs': [
            'scanner_block_log_enabled', 'scanner_verbose', 'cloud_renew_notice_debug_repeat',
            'cleanup_retention_days',
        ],
        'notifications': [
            'bot_token', 'telegram_webhook_url', 'telegram_listener_push_enabled',
            'telegram_listener_push_private_enabled', 'cloud_auto_renew_execution_notify_enabled',
            'cloud_auto_renew_execution_notify_events', 'cloud_daily_expiry_summary_enabled',
            'admin_password_notice',
        ],
        'telegram_login': ['telegram_api_id', 'telegram_api_hash'],
        'lifecycle': [
            'cloud_asset_sync_interval_seconds', 'cloud_renew_notice_days',
            'cloud_suspend_after_days', 'cloud_suspend_time', 'cloud_delete_after_days',
            'cloud_delete_time', 'cloud_unattached_ip_delete_after_days',
            'cloud_unattached_ip_delete_time', 'fsm_state_ttl', 'fsm_data_ttl',
        ],
        'database': [
            'database_url', 'db_host', 'db_port', 'db_name', 'db_user', 'db_password',
            'redis_url', 'redis_port', 'redis_db',
        ],
        'custom_text': [
            'bot_custom_quantity_title', 'bot_custom_quantity_hint', 'bot_custom_payment_title',
            'bot_custom_payment_hint', 'bot_custom_wallet_title', 'bot_custom_pending_order',
            'bot_custom_pending_wallet', 'bot_custom_order_notice', 'bot_custom_port_hint',
            'bot_custom_balance_insufficient',
        ],
        'aws': ['aws_access_key_id', 'aws_secret_access_key', 'aws_region'],
        'aliyun': ['alibaba_cloud_account_id', 'aliyun_account_id', 'aliyun_region'],
    }


@csrf_exempt
@dashboard_login_required
@require_POST
def init_site_configs(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    created = 0
    keys = set(CONFIG_HELP)
    for group_keys in _dashboard_site_config_groups().values():
        keys.update(group_keys)
    for key in sorted(keys):
        _, was_created = SiteConfig.objects.get_or_create(
            key=key,
            defaults={
                'value': DASHBOARD_CONFIG_DEFAULTS.get(key, ''),
                'is_sensitive': key in DASHBOARD_SENSITIVE_CONFIG_KEYS,
            },
        )
        created += int(was_created)
    return _ok({'created': created})


@csrf_exempt
@dashboard_login_required
@require_POST
def update_site_config(request, config_id: int):
    denied = _require_superuser(request)
    if denied:
        return denied
    item = SiteConfig.objects.filter(id=config_id).first()
    if not item:
        return _error('配置不存在', status=404)
    data = _read_payload(request) or request.GET
    item.is_sensitive = (
        str(data.get('is_sensitive', item.is_sensitive)).lower() in {'1', 'true', 'yes', 'on'}
        or item.key in DASHBOARD_SENSITIVE_CONFIG_KEYS
    )
    preserve_existing = str(data.get('preserve_existing', '')).lower() in {'1', 'true', 'yes', 'on'}
    if preserve_existing and item.is_sensitive:
        item.save(update_fields=['is_sensitive'])
        return _ok(_site_config_payload(item))
    value = data.get('value')
    SiteConfig.set(item.key, '' if value is None else str(value), sensitive=item.is_sensitive)
    item = SiteConfig.objects.get(id=item.id)
    return _ok(_site_config_payload(item))


@csrf_exempt
@dashboard_login_required
@require_POST
def init_text_configs(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    mode = payload.get('mode') or 'missing_only'
    created = 0
    updated = 0
    for key, default_value in DASHBOARD_TEXT_CONFIG_DEFAULTS.items():
        obj, was_created = SiteConfig.objects.get_or_create(
            key=key,
            defaults={'value': default_value, 'is_sensitive': False},
        )
        if was_created:
            created += 1
        elif mode == 'reset_defaults':
            obj.value = default_value
            obj.is_sensitive = False
            obj.save(update_fields=['value', 'is_sensitive'])
            updated += 1
    return _ok({'created': created, 'updated': updated, 'mode': mode})


@csrf_exempt
@dashboard_login_required
@require_POST
def test_daily_expiry_summary(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    now = timezone.now()
    local_now = timezone.localtime(now)
    today_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_end = today_start + timezone.timedelta(days=1)
    active_statuses = ['completed', 'expiring', 'renew_pending', 'suspended', 'deleting']
    today = CloudServerOrder.objects.filter(
        status__in=active_statuses,
        service_expires_at__gte=today_start,
        service_expires_at__lt=today_end,
    ).count()
    expired = CloudServerOrder.objects.filter(
        status__in=active_statuses,
        service_expires_at__lt=now,
    ).count()
    delete_due = CloudServerOrder.objects.filter(
        status__in=['suspended', 'deleting'],
        delete_at__lte=now,
    ).count()
    ip_due = CloudServerOrder.objects.filter(
        status='deleted',
        ip_recycle_at__lte=now,
    ).filter(Q(public_ip__isnull=False) | Q(static_ip_name__isnull=False)).count()

    text = (
        '每日云服务器到期汇总测试\n'
        f'日期：{local_now:%Y-%m-%d}\n'
        f'今日到期：{today} 台\n'
        f'已过期未完成处理：{expired} 台\n'
        f'待删机：{delete_due} 台\n'
        f'待释放固定 IP：{ip_due} 个'
    )
    target_groups = [
        item for item in _telegram_groups()
        if item.get('enabled') and item.get('push_enabled') and item.get('chat_id')
    ]
    if not target_groups:
        return _error('没有启用的 Telegram 推送群组，无法发送测试汇总。', status=400)

    sent = 0
    errors = []
    for group in target_groups:
        try:
            _send_dashboard_telegram_text(
                int(group.get('chat_id')),
                text,
                chat_title=group.get('title') or str(group.get('chat_id')),
            )
            sent += 1
        except Exception as exc:
            errors.append(f"{group.get('title') or group.get('chat_id')}: {_telegram_error_message(exc)}")
    if sent == 0 and errors:
        return _error('；'.join(errors), status=400)
    return _ok({'expired': expired, 'sent': sent, 'today': today, 'delete_due': delete_due, 'ip_due': ip_due, 'errors': errors})


@csrf_exempt
@dashboard_login_required
@require_POST
def start_totp_bind(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    current_secret = SiteConfig.get('dashboard_totp_secret', '')
    if current_secret and not _verify_totp(current_secret, payload.get('old_otp_token')):
        return _error('当前 Google 动态验证码不正确', status=400)
    secret = base64.b32encode(secrets.token_bytes(10)).decode('ascii').rstrip('=')
    label = quote(f"Shop Admin:{request.user.get_username() or 'admin'}")
    issuer = quote('Shop Admin')
    otpauth_url = f'otpauth://totp/{label}?secret={secret}&issuer={issuer}'
    SiteConfig.set('dashboard_totp_pending_secret', secret, sensitive=True)
    return _ok({'enabled': bool(SiteConfig.get('dashboard_totp_secret', '')), 'secret': secret, 'otpauthUrl': otpauth_url})


@csrf_exempt
@dashboard_login_required
@require_POST
def bind_totp(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    token = str(payload.get('otp_token') or '').strip()
    if len(token) != 6 or not token.isdigit():
        return _error('请输入 6 位动态码', status=400)
    pending_secret = SiteConfig.get('dashboard_totp_pending_secret', '')
    if not pending_secret:
        return _error('请先生成绑定二维码', status=400)
    if not _verify_totp(pending_secret, token):
        return _error('Google 动态验证码不正确', status=400)
    SiteConfig.set('dashboard_totp_secret', pending_secret, sensitive=True)
    SiteConfig.set('dashboard_totp_pending_secret', '', sensitive=True)
    return _ok({'enabled': True})


@dashboard_login_required
@require_GET
def admin_users_list(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    User = get_user_model()
    queryset = User.objects.order_by('-is_superuser', '-is_staff', 'username', 'id')
    return _ok([_admin_user_payload(item) for item in queryset])


@csrf_exempt
@dashboard_login_required
@require_POST
def create_admin_user(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    username = str(payload.get('username') or '').strip()
    password = str(payload.get('password') or '').strip()
    if not username:
        return _error('用户名不能为空')
    if not password:
        return _error('新管理员密码不能为空')
    User = get_user_model()
    user = User(
        username=username,
        email=str(payload.get('email') or '').strip(),
        is_active=bool(payload.get('is_active', True)),
        is_staff=True,
        is_superuser=bool(payload.get('is_superuser', False)),
    )
    user.set_password(password)
    try:
        user.save()
    except IntegrityError:
        return _error('用户名已存在')
    return _ok(_admin_user_payload(user))


@csrf_exempt
@dashboard_login_required
@require_POST
def update_admin_user(request, user_id):
    denied = _require_superuser(request)
    if denied:
        return denied
    User = get_user_model()
    user = User.objects.filter(pk=user_id).first()
    if not user:
        return _error('管理员不存在', status=404)
    payload = _read_payload(request)
    username = str(payload.get('username') or user.get_username()).strip()
    if not username:
        return _error('用户名不能为空')
    is_active = bool(payload.get('is_active', user.is_active))
    is_superuser = bool(payload.get('is_superuser', user.is_superuser))
    if user.id == request.user.id and (not is_active or not is_superuser):
        return _error('不能停用当前账号或取消自己的超级管理员权限')
    user.username = username
    user.email = str(payload.get('email') or '').strip()
    user.is_active = is_active
    user.is_staff = True
    user.is_superuser = is_superuser
    password = str(payload.get('password') or '').strip()
    if password:
        user.set_password(password)
    try:
        user.save()
    except IntegrityError:
        return _error('用户名已存在')
    return _ok(_admin_user_payload(user))


@csrf_exempt
@dashboard_login_required
@require_POST
def delete_admin_user(request, user_id):
    denied = _require_superuser(request)
    if denied:
        return denied
    User = get_user_model()
    user = User.objects.filter(pk=user_id).first()
    if not user:
        return _error('管理员不存在', status=404)
    if user.id == request.user.id:
        return _error('不能删除当前登录账号')
    if user.is_superuser and User.objects.filter(is_active=True, is_superuser=True).exclude(pk=user.id).count() == 0:
        return _error('不能删除最后一个超级管理员')
    user.delete()
    return _ok(True)


@csrf_exempt
@dashboard_login_required
@require_POST
def change_admin_password(request):
    payload = _read_payload(request)
    old_password = str(payload.get('old_password') or '')
    new_password = str(payload.get('new_password') or '')
    confirm_password = str(payload.get('confirm_password') or '')
    if not request.user.check_password(old_password):
        return _error('旧密码不正确')
    if not new_password:
        return _error('新密码不能为空')
    if new_password != confirm_password:
        return _error('两次输入的新密码不一致')
    request.user.set_password(new_password)
    request.user.save(update_fields=['password'])
    return _ok(True)


@dashboard_login_required
@require_GET
def cloud_accounts_list(request):
    queryset = CloudAccountConfig.objects.order_by('provider', 'name', 'id')
    return _ok([_cloud_account_payload(item) for item in queryset])


def _cloud_account_detail_payload(item):
    payload = _cloud_account_payload(item)
    logs = list(item.sync_logs.order_by('-created_at', '-id')[:50])
    recent_logs = []
    latest_success_log_at = None
    latest_failed_log_at = None
    for log in logs:
        if log.is_success and latest_success_log_at is None:
            latest_success_log_at = log.created_at
        if not log.is_success and latest_failed_log_at is None:
            latest_failed_log_at = log.created_at
        recent_logs.append({
            'id': log.id,
            'source': log.source,
            'source_label': dict(ExternalSyncLog.SOURCE_CHOICES).get(log.source, log.source),
            'action': log.action,
            'target': log.target or '',
            'is_success': log.is_success,
            'error_message': log.error_message or '',
            'request_payload': log.request_payload or '',
            'response_payload': log.response_payload or '',
            'created_at': _iso(log.created_at),
        })

    provider = 'aws_lightsail' if item.provider == CloudAccountConfig.PROVIDER_AWS else 'aliyun_simple'
    region_filter = Q(provider=provider)
    if item.region_hint:
        region_filter &= Q(region_code=item.region_hint)
    active_statuses = ['completed', 'expiring', 'renew_pending', 'paid', 'provisioning']
    payload.update({
        'cloud_asset_count': CloudAsset.objects.filter(region_filter).count(),
        'active_cloud_asset_count': CloudAsset.objects.filter(region_filter, is_active=True).count(),
        'cloud_order_count': CloudServerOrder.objects.filter(region_filter).count(),
        'running_cloud_order_count': CloudServerOrder.objects.filter(region_filter, status__in=active_statuses).count(),
        'sync_log_count': item.sync_logs.count(),
        'latest_success_log_at': _iso(latest_success_log_at),
        'latest_failed_log_at': _iso(latest_failed_log_at),
        'recent_logs': recent_logs,
    })
    return payload


@csrf_exempt
@dashboard_login_required
@require_POST
def create_cloud_account(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    provider = (payload.get('provider') or '').strip()
    name = (payload.get('name') or '').strip()
    access_key = (payload.get('access_key') or '').strip()
    secret_key = (payload.get('secret_key') or '').strip()
    region_hint = (payload.get('region_hint') or '').strip()
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
        access_key=access_key,
        secret_key=secret_key,
        region_hint=region_hint or None,
        is_active=is_active,
    )
    return _ok(_cloud_account_payload(item))


@csrf_exempt
@dashboard_login_required
@require_http_methods(['GET', 'POST', 'PUT', 'PATCH'])
def update_cloud_account(request, account_id: int):
    item = CloudAccountConfig.objects.filter(id=account_id).first()
    if not item:
        return _error('云账号不存在', status=404)
    if request.method == 'GET':
        return _ok(_cloud_account_detail_payload(item))

    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    provider = (payload.get('provider') or item.provider).strip()
    name = (payload.get('name') or item.name).strip()
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
    if access_key not in (None, ''):
        item.access_key = str(access_key).strip()
    if secret_key not in (None, ''):
        item.secret_key = str(secret_key).strip()
    if region_hint is not None:
        item.region_hint = str(region_hint).strip() or None
    if is_active is not None:
        item.is_active = str(is_active).lower() in {'1', 'true', 'yes', 'on'}
    item.save()
    return _ok(_cloud_account_payload(item))


@csrf_exempt
@dashboard_login_required
@require_POST
def delete_cloud_account(request, account_id: int):
    denied = _require_superuser(request)
    if denied:
        return denied
    item = CloudAccountConfig.objects.filter(id=account_id).first()
    if not item:
        return _error('云账号不存在', status=404)
    item.delete()
    return _ok(True)


@csrf_exempt
@dashboard_login_required
@require_POST
def verify_cloud_account(request, account_id: int):
    denied = _require_superuser(request)
    if denied:
        return denied
    item = CloudAccountConfig.objects.filter(id=account_id).first()
    if not item:
        return _error('云账号不存在', status=404)
    region = (request.POST.get('region') or request.GET.get('region') or item.region_hint or '').strip()
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
            item.mark_status(CloudAccountConfig.STATUS_OK, f'验证成功，实例数 {count}，地区 {region or "ap-southeast-1"}')
            return _ok({'valid': True, 'provider': item.provider, 'region': region or 'ap-southeast-1', 'instance_count': count, 'account': _cloud_account_payload(item)})
        if item.provider == CloudAccountConfig.PROVIDER_ALIYUN:
            from alibabacloud_swas_open20200601 import models as swas_models
            from cloud.aliyun_simple import _region_endpoint, _runtime_options
            from cloud.aliyun_simple import _build_client as _default_build_client
            import os

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
                count = len(response.body.to_map().get('Instances', []) or [])
                item.mark_status(CloudAccountConfig.STATUS_OK, f'验证成功，实例数 {count}，地区 {region or "cn-hongkong"}')
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


@csrf_exempt
@dashboard_login_required
@require_POST
def delete_server(request, server_id: int):
    denied = _require_superuser(request)
    if denied:
        return denied
    server = Server.objects.select_related('order').filter(id=server_id).first()
    if not server:
        return _error('服务器不存在', status=404)
    now = timezone.now()
    note = f'后台手动删除服务器记录；时间: {now.isoformat()}'
    previous_public_ip = server.public_ip or server.previous_public_ip
    order = server.order
    current_instance_id = server.instance_id
    current_provider_resource_id = server.provider_resource_id
    server.status = Server.STATUS_DELETED
    server.provider_status = '已删除'
    server.is_active = False
    server.previous_public_ip = previous_public_ip
    server.public_ip = None
    server.instance_id = None
    server.provider_resource_id = None
    server.note = '\n'.join(filter(None, [server.note, note]))
    server.save(update_fields=['status', 'provider_status', 'is_active', 'previous_public_ip', 'public_ip', 'instance_id', 'provider_resource_id', 'note', 'updated_at'])
    asset_filter = Q()
    if order:
        asset_filter |= Q(order=order)
    if current_instance_id:
        asset_filter |= Q(instance_id=current_instance_id)
    if current_provider_resource_id:
        asset_filter |= Q(provider_resource_id=current_provider_resource_id)
    if asset_filter:
        CloudAsset.objects.filter(asset_filter).update(
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
            previous_public_ip=previous_public_ip,
            public_ip=None,
            instance_id=None,
            provider_resource_id=None,
            note=note,
            updated_at=now,
        )
    if order:
        order.status = 'deleted'
        order.previous_public_ip = previous_public_ip
        order.public_ip = ''
        order.instance_id = ''
        order.provider_resource_id = ''
        order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
        order.save(update_fields=['status', 'previous_public_ip', 'public_ip', 'instance_id', 'provider_resource_id', 'provision_note', 'updated_at'])
    return _ok(True)


@csrf_exempt
@dashboard_login_required
@require_POST
def rebuild_server_preserve_link(request, server_id: int):
    denied = _require_superuser(request)
    if denied:
        return denied
    server = Server.objects.select_related('order').filter(id=server_id).first()
    if not server:
        return _error('服务器不存在', status=404)
    order = server.order
    if not order:
        return _error('服务器未关联云订单，无法创建迁移单')
    existing = order.replacement_orders.order_by('-created_at').first()
    if existing:
        return _ok({
            'accepted': True,
            'message': '已存在保留链接迁移单',
            'order_id': existing.id,
            'order_no': existing.order_no,
            'replacement_for_id': order.id,
        })
    try:
        new_order = async_to_sync(mark_cloud_server_ip_change_requested)(
            order.id,
            order.user_id,
            order.region_code,
            order.mtproxy_port or 9528,
        )
    except IntegrityError:
        existing = order.replacement_orders.order_by('-created_at').first()
        if existing:
            return _ok({
                'accepted': True,
                'message': '已存在保留链接迁移单',
                'order_id': existing.id,
                'order_no': existing.order_no,
                'replacement_for_id': order.id,
            })
        return _error('创建迁移单失败：订单号已存在')
    except Exception as exc:
        return _error(f'创建迁移单失败: {exc}', status=500)
    if new_order is False:
        return _error('当前订单状态不可创建保留链接迁移单')
    if not new_order:
        return _error('未找到可用于迁移的订单或套餐')
    async_to_sync(provision_cloud_server)(new_order.id)
    return _ok({
        'accepted': True,
        'message': '保留链接迁移单已创建',
        'order_id': new_order.id,
        'order_no': new_order.order_no,
        'replacement_for_id': order.id,
    })


@dashboard_login_required
@require_GET
def overview(request):
    users_total = TelegramUser.objects.count()
    products_total = Product.objects.count()
    cloud_orders_total = CloudServerOrder.objects.count()
    recharges_total = Recharge.objects.count()
    monitors_total = AddressMonitor.objects.count()
    orders_total = Order.objects.count()

    cloud_pending = CloudServerOrder.objects.filter(status='pending').count()
    recharge_pending = Recharge.objects.filter(status='pending').count()

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

    return _ok({
        'summary': {
            'users_total': users_total,
            'products_total': products_total,
            'cloud_orders_total': cloud_orders_total,
            'recharges_total': recharges_total,
            'monitors_total': monitors_total,
            'orders_total': orders_total,
            'cloud_pending': cloud_pending,
            'recharge_pending': recharge_pending,
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
        'latest_recharges': [
            {
                **item,
                'status_label': _status_label(item['status'], Recharge.STATUS_CHOICES),
                'amount': _decimal_to_str(item['amount']),
                'created_at': _iso(item['created_at']),
            }
            for item in latest_recharges
        ],
    })


@dashboard_login_required
@require_GET
def users_list(request):
    keyword = _get_keyword(request)
    try:
        queryset = TelegramUser.objects.prefetch_related('telegramusernames').order_by('-id')
        if keyword and keyword.isdigit():
            queryset = queryset.annotate(tg_user_id_text=Cast('tg_user_id', output_field=CharField()))
            queryset = queryset.filter(
                Q(id=int(keyword))
                | Q(tg_user_id=int(keyword))
                | Q(tg_user_id_text__icontains=keyword)
                | Q(username__icontains=keyword)
                | Q(first_name__icontains=keyword)
                | Q(telegramusernames__username__icontains=keyword)
            )
        else:
            queryset = _apply_keyword_filter(
                queryset,
                keyword,
                ['username', 'first_name', 'telegramusernames__username'],
            )
        users = list(queryset.distinct()[:50])
    except ProgrammingError:
        queryset = TelegramUser.objects.order_by('-id')
        if keyword and keyword.isdigit():
            queryset = queryset.annotate(tg_user_id_text=Cast('tg_user_id', output_field=CharField()))
            queryset = queryset.filter(
                Q(id=int(keyword)) | Q(tg_user_id=int(keyword)) | Q(tg_user_id_text__icontains=keyword)
            )
        else:
            queryset = _apply_keyword_filter(queryset, keyword, ['username', 'first_name'])
        users = list(queryset.distinct()[:50])
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
        }
        for user in users
    ])


@csrf_exempt
@dashboard_login_required
@require_POST
def update_user_balance(request, user_id):
    denied = _require_superuser(request)
    if denied:
        return denied
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
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    try:
        discount = _parse_decimal(payload.get('cloud_discount_rate'), '云服务器折扣')
    except ValueError as exc:
        return _error(str(exc), status=400)
    if discount <= 0 or discount > 100:
        return _error('云服务器折扣必须大于 0 且小于等于 100', status=400)
    try:
        with transaction.atomic():
            user = TelegramUser.objects.select_for_update().get(pk=user_id)
            user.cloud_discount_rate = discount
            user.save(update_fields=['cloud_discount_rate', 'updated_at'])
    except TelegramUser.DoesNotExist:
        return _error('用户不存在', status=404)
    return _ok({
        'id': user.id,
        'cloud_discount_rate': _decimal_to_str(user.cloud_discount_rate),
    })


@dashboard_login_required
@require_GET
def user_balance_details(request, user_id):
    user = TelegramUser.objects.prefetch_related('telegramusernames').filter(pk=user_id).first()
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
    denied = _require_superuser(request)
    if denied:
        return denied
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
    denied = _require_superuser(request)
    if denied:
        return denied
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


@dashboard_login_required
@require_GET
def tasks_overview(request):
    orders = CloudServerOrder.objects.order_by('-updated_at')[:50]
    items = []
    for order in orders:
        if order.status not in {'paid', 'provisioning', 'renew_pending', 'expiring', 'suspended', 'deleting', 'failed'}:
            continue
        items.append({
            'id': order.id,
            'order_no': order.order_no,
            'task_type': 'cloud_order',
            'task_label': '云服务器任务',
            'status': order.status,
            'status_label': dict(CloudServerOrder.STATUS_CHOICES).get(order.status, order.status),
            'provider': order.provider,
            'provider_label': _provider_label(order.provider),
            'plan_name': order.plan_name,
            'public_ip': order.public_ip,
            'note': order.provision_note,
            'created_at': _iso(order.created_at),
            'updated_at': _iso(order.updated_at),
            'related_path': f'/admin/cloud-orders/{order.id}',
        })
    return _ok(items)


def _notice_switches():
    labels = {
        'renew_notice': '续费提醒',
        'auto_renew_notice': '自动续费提醒',
        'delete_notice': '删除提醒',
        'recycle_notice': 'IP 回收提醒',
    }
    switches = []
    for key, config_key in NOTICE_SWITCH_CONFIG_KEYS.items():
        enabled = str(SiteConfig.get(config_key, '1')).strip().lower() in {'1', 'true', 'yes', 'on'}
        switches.append({
            'key': key,
            'label': labels[key],
            'notice_type': key,
            'enabled': enabled,
        })
    return switches


def _notice_type_enabled(notice_type):
    config_key = NOTICE_SWITCH_CONFIG_KEYS.get(notice_type)
    if not config_key:
        return True
    return str(SiteConfig.get(config_key, '1')).strip().lower() in {'1', 'true', 'yes', 'on'}


def _empty_run_result(message='本地开发环境暂无待执行任务'):
    return {
        'batch_id': '',
        'success_count': 0,
        'failure_count': 0,
        'total': 0,
        'items': [],
        'message': message,
    }


def _cloud_task_user_payload(user):
    if not user:
        return {
            'user_id': None,
            'tg_user_id': None,
            'user_display_name': '未绑定用户',
            'username_label': '-',
        }
    usernames = user.usernames
    payload = _user_payload({
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'username': user.username,
        'first_name': user.first_name,
        'usernames': usernames,
        'primary_username': usernames[0] if usernames else '',
    })
    return {
        'user_id': user.id,
        'tg_user_id': user.tg_user_id,
        'user_display_name': payload['display_name'],
        'username_label': payload['username_label'],
    }


def _task_time_state(target):
    if not target:
        return ('scheduled', '未设置时间')
    return ('pending', '待执行') if target <= timezone.now() else ('scheduled', '已计划')


def _shutdown_plan_item(order, *, item_type='order', asset=None, is_history=False):
    time_state, time_label = _task_time_state(order.delete_at)
    if order.status == 'deleted':
        plan_state, plan_state_label = ('completed', '已删除')
        resource_state, resource_state_label = ('instance_deleted_ip_retained', '实例已删，固定 IP 保留')
    else:
        plan_state, plan_state_label = time_state, time_label
        resource_state, resource_state_label = ('instance_present', '实例仍存在')
    public_ip = order.public_ip or order.previous_public_ip or getattr(asset, 'public_ip', '') or ''
    note = order.provision_note or getattr(asset, 'note', '') or ''
    return {
        **_cloud_task_user_payload(order.user),
        'id': f'{item_type}-{order.id}',
        'asset_id': getattr(asset, 'id', None),
        'asset_name': getattr(asset, 'asset_name', None) or order.server_name or order.instance_id or public_ip,
        'order_id': order.id,
        'order_no': order.order_no,
        'item_type': item_type,
        'ip': public_ip,
        'service_expires_at': _iso(order.service_expires_at),
        'suspend_at': _iso(order.suspend_at),
        'delete_at': _iso(order.delete_at),
        'ip_recycle_at': _iso(order.ip_recycle_at),
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'queue_status': 'history' if is_history else ('due_now' if order.delete_at and order.delete_at <= timezone.now() else 'scheduled'),
        'queue_status_label': '历史记录' if is_history else ('到期待删' if order.delete_at and order.delete_at <= timezone.now() else '计划中'),
        'resource_state': resource_state,
        'resource_state_label': resource_state_label,
        'plan_state': plan_state,
        'plan_state_label': plan_state_label,
        'should_execute': bool(order.status in {'suspended', 'deleting'} and order.delete_at and order.delete_at <= timezone.now()),
        'execution_status': note or plan_state_label,
        'note': note,
        'display_note': note,
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'related_path': f'/admin/cloud-orders/{order.id}',
        'next_run_at': _iso(order.delete_at),
    }


def _shutdown_history_item(order):
    item = _shutdown_plan_item(order, is_history=True)
    return {
        **item,
        'id': f'order-history-{order.id}',
        'executed_at': _iso(order.updated_at),
        'is_success': order.status == 'deleted',
        'result_label': '已删除' if order.status == 'deleted' else '未完成',
        'failure_reason': '' if order.status == 'deleted' else (order.provision_note or ''),
        'deletion_source_label': '订单生命周期',
    }


def _ip_delete_item_from_order(order, *, is_history=False):
    time_state, time_label = _task_time_state(order.ip_recycle_at)
    active_ip = order.public_ip or ''
    previous_ip = order.previous_public_ip or active_ip
    released = order.status == 'deleted' and not order.public_ip and not order.static_ip_name
    if released:
        plan_state, plan_state_label = ('completed', '已释放')
        resource_state, resource_state_label = ('cloud_missing', '固定 IP 已释放')
    else:
        plan_state, plan_state_label = time_state, time_label
        resource_state, resource_state_label = ('fixed_ip_unattached', '固定 IP 保留中')
    note = order.provision_note or ''
    return {
        **_cloud_task_user_payload(order.user),
        'id': order.id,
        'asset_id': order.id,
        'asset_name': order.static_ip_name or order.server_name or previous_ip or order.order_no,
        'public_ip': active_ip or previous_ip,
        'provider_status': '固定IP保留' if not released else '已释放',
        'delete_at': _iso(order.ip_recycle_at),
        'service_expires_at': _iso(order.service_expires_at),
        'note': note,
        'display_note': note,
        'logged_at': _iso(order.updated_at),
        'is_history': is_history or released,
        'is_overdue': bool(order.ip_recycle_at and order.ip_recycle_at <= timezone.now()),
        'deletion_source_label': '订单固定 IP',
        'execution_status': note or plan_state_label,
        'plan_state': plan_state,
        'plan_state_label': plan_state_label,
        'resource_state': resource_state,
        'resource_state_label': resource_state_label,
        'should_execute': bool(order.status == 'deleted' and order.ip_recycle_at and order.ip_recycle_at <= timezone.now() and (order.public_ip or order.static_ip_name)),
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'asset_detail_path': f'/admin/cloud-orders/{order.id}',
    }


def _site_int_config(key, default):
    raw = SiteConfig.get(key, str(default))
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _static_ip_asset_delete_at(asset):
    days = max(_site_int_config('cloud_unattached_ip_delete_after_days', 15), 0)
    return asset.created_at + timezone.timedelta(days=days)


def _ip_delete_item_from_asset(asset, *, is_history=False):
    delete_at = _static_ip_asset_delete_at(asset)
    released = asset.status == CloudAsset.STATUS_DELETED or str(asset.provider_status or '').lower() == 'released'
    time_state, time_label = _task_time_state(delete_at)
    if released:
        plan_state, plan_state_label = ('completed', '已释放')
        resource_state, resource_state_label = ('cloud_missing', '固定 IP 已释放')
    else:
        plan_state, plan_state_label = time_state, time_label
        resource_state, resource_state_label = ('fixed_ip_unattached', '固定 IP 未附加')
    return {
        **_cloud_task_user_payload(asset.user),
        'id': asset.id,
        'asset_id': asset.id,
        'asset_name': asset.asset_name or asset.public_ip or f'asset-{asset.id}',
        'public_ip': asset.public_ip or asset.previous_public_ip or '',
        'provider_status': asset.provider_status or 'unattached',
        'delete_at': _iso(delete_at),
        'service_expires_at': _iso(asset.actual_expires_at),
        'note': asset.note or '',
        'display_note': asset.note or '',
        'logged_at': _iso(asset.updated_at),
        'is_history': is_history or released,
        'is_overdue': bool(delete_at and delete_at <= timezone.now()),
        'deletion_source_label': '未附加固定 IP',
        'execution_status': asset.note or plan_state_label,
        'plan_state': plan_state,
        'plan_state_label': plan_state_label,
        'resource_state': resource_state,
        'resource_state_label': resource_state_label,
        'should_execute': bool(not released and delete_at <= timezone.now()),
        'detail_path': f'/admin/cloud-assets/{asset.id}',
        'asset_detail_path': f'/admin/cloud-assets/{asset.id}',
    }


def _lifecycle_plan_payload(refreshed=False):
    now = timezone.now()
    orders = list(
        CloudServerOrder.objects.select_related('user').filter(
            status__in=['suspended', 'deleting', 'deleted'],
        ).order_by('delete_at', 'ip_recycle_at', '-updated_at')[:300]
    )
    static_ip_assets = list(
        CloudAsset.objects.select_related('user').filter(
            kind=CloudAsset.KIND_MTPROXY,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
        ).filter(
            Q(provider_status__icontains='unattached')
            | Q(provider_status__icontains='固定IP')
            | Q(provider_status='released')
        ).order_by('created_at', '-updated_at')[:300]
    )
    due_items = [
        _shutdown_plan_item(order)
        for order in orders
        if order.status in {'suspended', 'deleting'} and order.delete_at and order.delete_at <= now
    ]
    future_items = [
        _shutdown_plan_item(order)
        for order in orders
        if order.status in {'suspended', 'deleting'} and (not order.delete_at or order.delete_at > now)
    ]
    history_items = [_shutdown_history_item(order) for order in orders if order.status == 'deleted']
    ip_delete_items = [
        _ip_delete_item_from_order(order, is_history=not (order.public_ip or order.static_ip_name))
        for order in orders
        if order.status == 'deleted'
    ]
    ip_delete_items.extend(
        _ip_delete_item_from_asset(asset, is_history=asset.status == CloudAsset.STATUS_DELETED)
        for asset in static_ip_assets
    )
    pending_ip_delete_items = [
        item for item in ip_delete_items
        if not item['is_history']
        and (
            item.get('is_overdue')
            or (
                item.get('delete_at')
                and parse_datetime(item['delete_at'])
                and parse_datetime(item['delete_at']) <= now + timezone.timedelta(days=7)
            )
        )
    ]
    ip_due_count = len([item for item in pending_ip_delete_items if item.get('is_overdue')])
    return {
        'task_key': 'lifecycle_plans',
        'task_label': '删除计划',
        'status_label': f'待删机 {len(due_items)} / 待释放 IP {ip_due_count}',
        'interval_minutes': 60,
        'last_run_at': None,
        'last_refresh_at': _iso(now),
        'next_run_at': _iso(min([o.delete_at for o in orders if o.delete_at and o.delete_at > now] + [o.ip_recycle_at for o in orders if o.ip_recycle_at and o.ip_recycle_at > now], default=None)),
        'due_count': len(due_items),
        'due_items': due_items,
        'future_plan_items': future_items,
        'history_items': history_items,
        'ip_delete_count': len(ip_delete_items),
        'ip_delete_due_count': ip_due_count,
        'ip_delete_history_count': len([item for item in ip_delete_items if item['is_history']]),
        'ip_delete_items': ip_delete_items,
        'pending_ip_delete_count': len(pending_ip_delete_items),
        'recent_failure_count': 0,
        'recent_success_count': len(history_items),
        'server_delete_history_count': len(history_items),
        'shutdown_count': len(due_items) + len(future_items),
        'shutdown_due_count': len(due_items),
        'shutdown_items': due_items,
        'cache_mode': 'refreshed' if refreshed else 'live-db',
        'refreshed': refreshed,
    }


def _notice_type_label(notice_type):
    return {
        'renew_notice': '续费提醒',
        'delete_notice': '删除提醒',
        'recycle_notice': 'IP 回收提醒',
    }.get(notice_type, notice_type or '-')


def _notice_override_config_key(notice_type, user_id):
    return f"{NOTICE_TEXT_CONFIG_PREFIX}_{notice_type}_{user_id or 'unbound'}"


class _NoticeFormatDict(dict):
    def __missing__(self, key):
        return '{' + key + '}'


def _format_notice_override(template, order, default_text):
    if not template:
        return default_text
    values = _NoticeFormatDict({
        'delete_at': order.delete_at or '',
        'ip': order.public_ip or order.previous_public_ip or '',
        'ip_recycle_at': order.ip_recycle_at or '',
        'order_no': order.order_no or '',
        'provider': _provider_label(order.provider),
        'service_expires_at': order.service_expires_at or '',
        'status': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
    })
    try:
        return str(template).format_map(values)
    except (KeyError, ValueError):
        return str(template)


def _notice_channel_payload(user):
    if not user:
        return ('unbound', '未绑定用户', [{'channel': 'unbound', 'label': '未绑定用户', 'status': 'failed', 'status_label': '不可发送', 'error': '未绑定用户'}])
    return ('telegram_user', 'Telegram 用户', [{'channel': 'telegram_user', 'label': 'Telegram 用户', 'status': 'pending', 'status_label': '待发送', 'error': ''}])


def _notice_text(order, notice_type):
    if notice_type == 'renew_notice':
        default = f'云服务器到期提醒：订单 {order.order_no} 将于 {order.service_expires_at} 到期，请及时续费。'
    elif notice_type == 'delete_notice':
        default = f'云服务器删机提醒：订单 {order.order_no} 计划于 {order.delete_at} 删除实例。'
    elif notice_type == 'recycle_notice':
        default = f'固定 IP 删除提醒：订单 {order.order_no} 计划于 {order.ip_recycle_at} 释放固定 IP。'
    else:
        default = f'云服务器通知：订单 {order.order_no}'
    override = SiteConfig.get(_notice_override_config_key(notice_type, order.user_id), '')
    return _format_notice_override(override, order, default)


def _notice_item(order, notice_type, notice_at, *, is_history=False):
    channel, channel_label, attempts = _notice_channel_payload(order.user)
    text = _notice_text(order, notice_type)
    sent_field = {
        'renew_notice': order.renew_notice_sent_at,
        'delete_notice': order.delete_notice_sent_at,
        'recycle_notice': order.recycle_notice_sent_at,
    }.get(notice_type)
    status = 'sent' if sent_field else ('pending' if notice_at and notice_at <= timezone.now() else 'scheduled_soon')
    return {
        **_cloud_task_user_payload(order.user),
        'id': f'{notice_type}-{order.id}',
        'order_id': order.id,
        'order_no': order.order_no,
        'ip': order.public_ip or order.previous_public_ip or '',
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'service_expires_at': _iso(order.service_expires_at),
        'suspend_at': _iso(order.suspend_at),
        'delete_at': _iso(order.delete_at),
        'ip_recycle_at': _iso(order.ip_recycle_at),
        'notice_at': _iso(notice_at),
        'next_notice_at': _iso(notice_at),
        'auto_renew_at': None,
        'notice_type': notice_type,
        'notice_type_label': _notice_type_label(notice_type),
        'notice_channel': channel,
        'notice_channel_label': channel_label,
        'notice_channel_attempts': attempts,
        'notice_status': status,
        'notice_status_label': {'sent': '已发送', 'pending': '待发送', 'scheduled_soon': '计划中'}.get(status, status),
        'notice_text_preview': text,
        'retry_label': '失败后下次任务重试',
        'queue_status': 'history' if is_history else ('due_now' if notice_at and notice_at <= timezone.now() else 'scheduled'),
        'queue_status_label': '历史记录' if is_history else ('待发送' if notice_at and notice_at <= timezone.now() else '计划中'),
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'related_path': f'/admin/cloud-orders/{order.id}',
    }


def _notice_summary_item(item):
    manual_text = SiteConfig.get(_notice_override_config_key(item['notice_type'], item['user_id']), '')
    return {
        'id': f"{item['notice_type']}-{item['user_id'] or 'unbound'}",
        'user_id': item['user_id'],
        'tg_user_id': item['tg_user_id'],
        'user_display_name': item['user_display_name'],
        'username_label': item['username_label'],
        'notice_type': item['notice_type'],
        'notice_type_label': item['notice_type_label'],
        'notice_event': item['notice_type'],
        'notice_channel': item['notice_channel'],
        'notice_channel_label': item['notice_channel_label'],
        'notice_channel_attempts': item['notice_channel_attempts'],
        'notice_count': 1,
        'pending_count': 1 if item['notice_status'] != 'sent' else 0,
        'failed_retry_count': 0,
        'ip_count': 1 if item['ip'] else 0,
        'ips': [item['ip']] if item['ip'] else [],
        'order_ids': [item['order_id']] if item['order_id'] else [],
        'next_notice_at': item['next_notice_at'],
        'notice_text_preview': item['notice_text_preview'],
        'notice_has_manual_text': bool(manual_text),
        'notice_manual_text': manual_text,
        'notice_override_key': f"{item['notice_type']}:{item['user_id'] or 'unbound'}",
        'retry_label': item['retry_label'],
        'related_path': item['related_path'],
    }


def _notice_plan_payload(refreshed=False):
    now = timezone.now()
    renew_window = now + timezone.timedelta(days=5)
    delete_window = now + timezone.timedelta(days=1)
    recycle_window = now + timezone.timedelta(days=1)
    items = []
    if _notice_type_enabled('renew_notice'):
        for order in CloudServerOrder.objects.select_related('user').filter(
            status__in=['completed', 'expiring', 'renew_pending'],
            service_expires_at__isnull=False,
            service_expires_at__lte=renew_window,
            service_expires_at__gt=now,
            renew_notice_sent_at__isnull=True,
        )[:100]:
            items.append(_notice_item(order, 'renew_notice', order.service_expires_at))
    if _notice_type_enabled('delete_notice'):
        for order in CloudServerOrder.objects.select_related('user').filter(
            status__in=['suspended', 'deleting'],
            delete_at__isnull=False,
            delete_at__lte=delete_window,
            delete_at__gt=now,
            delete_notice_sent_at__isnull=True,
        )[:100]:
            items.append(_notice_item(order, 'delete_notice', order.delete_at))
    if _notice_type_enabled('recycle_notice'):
        for order in CloudServerOrder.objects.select_related('user').filter(
            status='deleted',
            ip_recycle_at__isnull=False,
            ip_recycle_at__lte=recycle_window,
            ip_recycle_at__gt=now,
            recycle_notice_sent_at__isnull=True,
        )[:100]:
            items.append(_notice_item(order, 'recycle_notice', order.ip_recycle_at))
    history_items = []
    sent_filters = (
        ('renew_notice', 'renew_notice_sent_at'),
        ('delete_notice', 'delete_notice_sent_at'),
        ('recycle_notice', 'recycle_notice_sent_at'),
    )
    for notice_type, field in sent_filters:
        for order in CloudServerOrder.objects.select_related('user').filter(**{f'{field}__isnull': False}).order_by(f'-{field}')[:30]:
            item = _notice_item(order, notice_type, getattr(order, field), is_history=True)
            item.update({
                'batch_id': '',
                'created_at': _iso(getattr(order, field)),
                'delivered': True,
                'result_label': '已标记发送',
                'target_chat_id': order.user.tg_user_id if order.user else None,
                'text_preview': item['notice_text_preview'],
                'ip_count': 1 if item['ip'] else 0,
                'ips': [item['ip']] if item['ip'] else [],
            })
            history_items.append(item)
    history_items.sort(key=lambda item: item.get('created_at') or '', reverse=True)
    due_items = [item for item in items if item['notice_at'] and parse_datetime(item['notice_at']) and parse_datetime(item['notice_at']) <= now]
    future_items = [item for item in items if item not in due_items]
    return {
        'task_key': 'notice_plans',
        'task_label': '通知计划',
        'status_label': f'待发送 {len(due_items)} / 计划中 {len(future_items)}',
        'interval_minutes': 60,
        'last_run_at': None,
        'last_refresh_at': _iso(now),
        'next_run_at': _iso(min([parse_datetime(item['notice_at']) for item in future_items if item.get('notice_at') and parse_datetime(item['notice_at'])], default=None)),
        'due_count': len(due_items),
        'due_items': due_items,
        'due_user_count': len({item['user_id'] for item in due_items}),
        'due_user_summary_items': [_notice_summary_item(item) for item in due_items],
        'future_count': len(future_items),
        'future_plan_items': future_items,
        'future_user_count': len({item['user_id'] for item in future_items}),
        'future_user_summary_items': [_notice_summary_item(item) for item in future_items],
        'history_count': len(history_items),
        'history_items': history_items[:100],
        'notice_switches': _notice_switches(),
        'recent_failure_count': 0,
        'recent_failure_user_count': 0,
        'recent_success_count': len(history_items),
        'recent_success_user_count': len({item['user_id'] for item in history_items}),
        'retry_policy_label': '失败后按任务配置重试',
    }


@dashboard_login_required
@require_GET
def lifecycle_plans(request):
    return _ok(_lifecycle_plan_payload())


@csrf_exempt
@dashboard_login_required
@require_POST
def refresh_lifecycle_plans(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _lifecycle_plan_payload(refreshed=True)
    return _ok({
        **payload,
        'future_count': len(payload['future_plan_items']),
        'history_count': len(payload['history_items']),
    })


@csrf_exempt
@dashboard_login_required
@require_POST
def update_lifecycle_plan_note(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    note = payload.get('note') or ''
    item_type = str(payload.get('item_type') or '')
    order_id = payload.get('order_id')
    asset_id = payload.get('asset_id')
    target_id = str(payload.get('id') or '').split('-')[-1]
    if order_id or (target_id.isdigit() and item_type != 'static_ip_asset' and not asset_id):
        resolved_id = int(order_id or target_id)
        order = CloudServerOrder.objects.filter(id=resolved_id).first()
        if order:
            order.provision_note = note
            order.save(update_fields=['provision_note', 'updated_at'])
            CloudAsset.objects.filter(order=order).update(note=note, updated_at=timezone.now())
            Server.objects.filter(order=order).update(note=note, updated_at=timezone.now())
    elif asset_id or target_id.isdigit():
        resolved_id = int(asset_id or target_id)
        asset = CloudAsset.objects.filter(id=resolved_id).first()
        if asset:
            asset.note = note
            asset.save(update_fields=['note', 'updated_at'])
    return _ok({'note': note, 'display_note': note})


def _run_static_ip_asset_release(asset_id):
    asset = CloudAsset.objects.filter(id=asset_id).first()
    if not asset:
        return None, _empty_run_result('未附加 IP 计划不存在')
    result = async_to_sync(release_aws_static_ip_asset)(asset)
    ok = result.ok
    updated = async_to_sync(mark_static_ip_asset_released)(asset.id, result.note) if ok else asset
    return updated, {
        'batch_id': f'manual-{timezone.now().strftime("%Y%m%d%H%M%S")}',
        'success_count': 1 if ok else 0,
        'failure_count': 0 if ok else 1,
        'total': 1,
        'items': [{
            'id': updated.id,
            'order_id': None,
            'ok': ok,
            'ip': updated.public_ip or updated.previous_public_ip or '',
            'order_no': '',
            'queue_status': 'done' if ok else 'failed',
            'error': '' if ok else result.note,
            'message': result.note,
        }],
        'message': 'IP 删除完成' if ok else 'IP 删除失败',
    }


@csrf_exempt
@dashboard_login_required
@require_POST
def run_lifecycle_plan_item(request, *args, **kwargs):
    denied = _require_superuser(request)
    if denied:
        return denied
    order_id = kwargs.get('order_id') or kwargs.get('asset_id')
    is_ip_release = 'unattached-ips' in request.path
    if is_ip_release and 'unattached-ips' in request.path:
        asset = CloudAsset.objects.filter(
            id=order_id,
            kind=CloudAsset.KIND_MTPROXY,
            source=CloudAsset.SOURCE_AWS_SYNC,
            provider='aws_lightsail',
        ).first()
        if asset:
            _, result = _run_static_ip_asset_release(order_id)
            return _ok(result)
    order = CloudServerOrder.objects.filter(id=order_id).first()
    if not order:
        return _error('计划项不存在', status=404)
    if is_ip_release:
        result = async_to_sync(_release_static_ip)(order)
        ok = result.ok
        updated = async_to_sync(_mark_recycled)(order.id, result.note) if ok else order
        label = 'IP 删除完成' if ok else 'IP 删除失败'
    else:
        result = async_to_sync(_delete_instance)(order)
        ok = result.ok
        updated = async_to_sync(_mark_deleted)(order.id, result.note) if ok else order
        label = '服务器删除完成' if ok else '服务器删除失败'
    return _ok({
        'batch_id': f'manual-{timezone.now().strftime("%Y%m%d%H%M%S")}',
        'success_count': 1 if ok else 0,
        'failure_count': 0 if ok else 1,
        'total': 1,
        'items': [{
            'id': updated.id,
            'order_id': updated.id,
            'ok': ok,
            'ip': updated.public_ip or updated.previous_public_ip or '',
            'order_no': updated.order_no,
            'queue_status': 'done' if ok else 'failed',
            'error': '' if ok else result.note,
            'message': result.note,
        }],
        'message': label,
    })


@dashboard_login_required
@require_GET
def notice_plans(request):
    return _ok(_notice_plan_payload())


@csrf_exempt
@dashboard_login_required
@require_POST
def refresh_notice_plans(request):
    return _ok({**_notice_plan_payload(refreshed=True), 'refreshed': True})


@csrf_exempt
@dashboard_login_required
@require_POST
def delete_notice_history(request, log_id):
    denied = _require_superuser(request)
    if denied:
        return denied
    raw_id = str(log_id or '').strip()
    notice_type = ''
    order_id = None
    if '-' in raw_id:
        notice_type, raw_order_id = raw_id.rsplit('-', 1)
        if str(raw_order_id).isdigit():
            order_id = int(raw_order_id)
    elif raw_id.isdigit():
        order_id = int(raw_id)
    if not order_id:
        return _error('通知历史 ID 不正确', status=400)

    fields = [NOTICE_SENT_FIELDS[notice_type]] if notice_type in NOTICE_SENT_FIELDS else list(NOTICE_SENT_FIELDS.values())
    update_payload = {field: None for field in fields}
    update_payload['updated_at'] = timezone.now()
    reset_count = CloudServerOrder.objects.filter(id=order_id).update(**update_payload)
    if not reset_count:
        return _error('通知历史不存在', status=404)
    return _ok({'deleted': True, 'reset_count': reset_count})


@csrf_exempt
@dashboard_login_required
@require_POST
def update_notice_switches(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    switches = payload.get('switches') or []
    for item in switches:
        key = str((item or {}).get('key') or '').strip()
        if key not in NOTICE_SWITCH_CONFIG_KEYS:
            return _error(f'未知通知开关：{key}', status=400)
        enabled = bool((item or {}).get('enabled'))
        SiteConfig.set(NOTICE_SWITCH_CONFIG_KEYS[key], '1' if enabled else '0', sensitive=False)
    return _ok({'notice_switches': _notice_switches()})


@csrf_exempt
@dashboard_login_required
@require_POST
def update_notice_text(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    notice_type = payload.get('event') or payload.get('notice_type') or 'notice'
    user_id = payload.get('user_id') or payload.get('tg_user_id') or 'all'
    if notice_type not in NOTICE_SWITCH_CONFIG_KEYS:
        return _error(f'未知通知类型：{notice_type}', status=400)
    config_user_id = None if user_id in {'all', 'unbound', ''} else user_id
    notice_text = str(payload.get('notice_text') or '').strip()
    SiteConfig.set(
        _notice_override_config_key(notice_type, config_user_id),
        notice_text,
        sensitive=False,
    )
    return _ok({
        'notice_has_manual_text': bool(notice_text),
        'notice_manual_text': notice_text,
        'notice_override_key': f"{notice_type}:{user_id}",
    })


def _auto_renew_before_days():
    return max(_site_int_config('cloud_auto_renew_before_days', 1), 0)


def _auto_renew_at(order):
    if not order.service_expires_at:
        return None
    return order.service_expires_at - timezone.timedelta(days=_auto_renew_before_days())


def _auto_renew_amount(order):
    user = order.user
    discount_rate = Decimal(str(getattr(user, 'cloud_discount_rate', 100) or 100))
    if discount_rate <= 0:
        discount_rate = Decimal('100')
    return (Decimal(str(order.total_amount or 0)) * discount_rate / Decimal('100')).quantize(Decimal('0.01'))


def _auto_renew_order_queryset():
    return CloudServerOrder.objects.select_related('user').prefetch_related('user__telegramusernames').filter(
        auto_renew_enabled=True,
        service_expires_at__isnull=False,
        status__in=AUTO_RENEW_ELIGIBLE_STATUSES,
    ).order_by('service_expires_at', 'id')


def _auto_renew_due_item(order):
    now = timezone.now()
    renew_at = _auto_renew_at(order)
    amount = _auto_renew_amount(order)
    balance = Decimal(str(getattr(order.user, 'balance', 0) or 0))
    is_due = bool(renew_at and renew_at <= now)
    blocked_reason = ''
    if not order.public_ip:
        blocked_reason = '订单没有公网 IP，不能自动续费。'
    elif balance < amount:
        blocked_reason = f'USDT 余额不足，需要 {amount}，当前 {balance}。'
    if blocked_reason and is_due:
        queue_status, queue_status_label = 'retry_failed', '待处理'
    elif is_due:
        queue_status, queue_status_label = 'due_now', '待执行'
    else:
        queue_status, queue_status_label = 'scheduled', '计划中'
    return {
        **_cloud_task_user_payload(order.user),
        'id': order.id,
        'order_id': order.id,
        'order_no': order.order_no,
        'ip': order.public_ip or order.previous_public_ip or '',
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'service_expires_at': _iso(order.service_expires_at),
        'auto_renew_at': _iso(renew_at),
        'next_run_at': _iso(renew_at),
        'suspend_at': _iso(order.suspend_at),
        'delete_at': _iso(order.delete_at),
        'ip_recycle_at': _iso(order.ip_recycle_at),
        'balance': _decimal_to_str(balance, 2),
        'queue_status': queue_status,
        'queue_status_label': queue_status_label,
        'last_failure_reason': blocked_reason,
        'related_path': f'/admin/cloud-orders/{order.id}',
    }


def _auto_renew_history_item(ledger):
    order = CloudServerOrder.objects.select_related('user').filter(id=ledger.related_id).first()
    user = order.user if order else ledger.user
    return {
        **_cloud_task_user_payload(user),
        'id': ledger.id,
        'order_id': ledger.related_id,
        'order_no': order.order_no if order else '',
        'ip': (order.public_ip or order.previous_public_ip) if order else '',
        'provider': order.provider if order else None,
        'provider_label': _provider_label(order.provider) if order else '-',
        'service_expires_at': _iso(order.service_expires_at) if order else None,
        'executed_at': _iso(ledger.created_at),
        'batch_id': '',
        'is_success': True,
        'result_label': '自动续费成功',
        'failure_reason': '',
        'currency': ledger.currency,
        'balance_before': _decimal_to_str(ledger.before_balance, 2),
        'balance_after': _decimal_to_str(ledger.after_balance, 2),
        'balance_change': _decimal_to_str(ledger.amount, 2),
        'related_path': f'/admin/cloud-orders/{ledger.related_id}' if ledger.related_id else '',
    }


def _auto_renew_task_payload(refreshed=False):
    now = timezone.now()
    orders = list(_auto_renew_order_queryset()[:300])
    due_items = []
    future_items = []
    for order in orders:
        item = _auto_renew_due_item(order)
        renew_at = _auto_renew_at(order)
        if renew_at and renew_at <= now:
            due_items.append(item)
        else:
            future_items.append(item)
    history_ledgers = BalanceLedger.objects.select_related('user').filter(
        type=BalanceLedger.TYPE_CLOUD_ORDER_BALANCE_PAY,
        related_type='cloud_order',
        description__icontains='自动续费',
    ).order_by('-created_at', '-id')[:100]
    history_items = [_auto_renew_history_item(ledger) for ledger in history_ledgers]
    latest_batch_id = f"auto-renew-{timezone.localtime(now):%Y%m%d}" if history_items else ''
    return {
        'task_key': 'auto_renew',
        'task_label': '续费列表',
        'status_label': f'待续费 {len(due_items)} / 计划中 {len(future_items)}',
        'interval_minutes': 60,
        'last_run_at': history_items[0]['executed_at'] if history_items else None,
        'last_refresh_at': _iso(now),
        'next_run_at': _iso(min([parse_datetime(item['auto_renew_at']) for item in future_items if item.get('auto_renew_at') and parse_datetime(item['auto_renew_at'])], default=None)),
        'due_count': len(due_items),
        'due_items': due_items,
        'future_plan_items': future_items,
        'history_items': history_items,
        'latest_batch_count': len(history_items),
        'latest_batch_failure_count': 0,
        'latest_batch_id': latest_batch_id,
        'latest_batch_success_count': len(history_items),
        'latest_failed_ips': [item['ip'] for item in due_items if item.get('last_failure_reason')],
        'notice_switches': _notice_switches(),
        'recent_failure_count': len([item for item in due_items if item.get('last_failure_reason')]),
        'recent_success_count': len(history_items),
        'cache_mode': 'refreshed' if refreshed else 'live-db',
        'refreshed': refreshed,
    }


def _run_auto_renew_order(order_id, *, operator=None, require_due=True):
    now = timezone.now()
    with transaction.atomic():
        order = CloudServerOrder.objects.select_for_update().select_related('user').filter(id=order_id).first()
        if not order:
            return None, '订单不存在'
        if not order.auto_renew_enabled:
            return order, '订单未开启自动续费'
        if order.status not in AUTO_RENEW_ELIGIBLE_STATUSES:
            return order, '当前订单状态不可自动续费'
        if not order.public_ip:
            return order, '订单没有公网 IP，不能自动续费'
        renew_at = _auto_renew_at(order)
        if require_due and renew_at and renew_at > now:
            return order, f'未到自动续费时间：{renew_at}'
        user = TelegramUser.objects.select_for_update().get(id=order.user_id)
        amount = _auto_renew_amount(order)
        old_balance = Decimal(str(user.balance or 0))
        if old_balance < amount:
            return order, f'USDT 余额不足，需要 {amount}，当前 {old_balance}'
        user.balance = old_balance - amount
        user.save(update_fields=['balance', 'updated_at'])
        base = order.service_expires_at or now
        if base < now:
            base = now
        days = max(int(order.lifecycle_days or 31), 1)
        order.service_expires_at = base + timezone.timedelta(days=days)
        order.last_renewed_at = now
        order.status = 'completed'
        order.currency = 'USDT'
        order.pay_method = 'balance'
        order.pay_amount = amount
        order.paid_at = now
        order.expired_at = None
        order.renew_notice_sent_at = None
        order.delete_notice_sent_at = None
        order.recycle_notice_sent_at = None
        order.save(update_fields=[
            'service_expires_at', 'last_renewed_at', 'status', 'currency',
            'pay_method', 'pay_amount', 'paid_at', 'expired_at',
            'renew_notice_sent_at', 'delete_notice_sent_at', 'recycle_notice_sent_at',
            'updated_at',
        ])
        CloudAsset.objects.filter(order=order).update(actual_expires_at=order.service_expires_at, updated_at=now)
        Server.objects.filter(order=order).update(expires_at=order.service_expires_at, updated_at=now)
        _record_balance_ledger(
            user,
            currency='USDT',
            old_balance=old_balance,
            new_balance=user.balance,
            ledger_type=BalanceLedger.TYPE_CLOUD_ORDER_BALANCE_PAY,
            related_type='cloud_order',
            related_id=order.id,
            description=f'云服务器自动续费订单 #{order.order_no}',
            operator=operator,
        )
        return order, None


def _auto_renew_run_result(orders, *, operator=None):
    batch_id = f"auto-renew-{timezone.localtime(timezone.now()):%Y%m%d%H%M%S}"
    items = []
    success_count = 0
    failure_count = 0
    for order in orders:
        updated, error = _run_auto_renew_order(order.id, operator=operator)
        target = updated or order
        ok = not error
        success_count += 1 if ok else 0
        failure_count += 0 if ok else 1
        items.append({
            'order_id': target.id,
            'renewed_order_id': target.id if ok else 0,
            'order_no': target.order_no,
            'ip': target.public_ip or target.previous_public_ip or '',
            'ok': ok,
            'queue_status': 'done' if ok else 'failed',
            'error': error or '',
        })
    return {
        'batch_id': batch_id,
        'success_count': success_count,
        'failure_count': failure_count,
        'total': len(items),
        'items': items,
        'message': '自动续费执行完成' if items else '当前没有可执行的续费任务',
    }


@dashboard_login_required
@require_GET
def auto_renew_tasks(request):
    return _ok(_auto_renew_task_payload(refreshed=str(request.GET.get('refresh') or '') == '1'))


@csrf_exempt
@dashboard_login_required
@require_POST
def run_auto_renew_tasks(request, *args, **kwargs):
    denied = _require_superuser(request)
    if denied:
        return denied
    order_id = kwargs.get('order_id')
    operator = getattr(request.user, 'username', '') or 'dashboard'
    if order_id:
        order = CloudServerOrder.objects.filter(id=order_id).first()
        if not order:
            return _error('自动续费订单不存在', status=404)
        return _ok(_auto_renew_run_result([order], operator=operator))
    now = timezone.now()
    due_orders = [
        order for order in _auto_renew_order_queryset()[:100]
        if _auto_renew_at(order) and _auto_renew_at(order) <= now
    ]
    return _ok(_auto_renew_run_result(due_orders, operator=operator))


@dashboard_login_required
@require_GET
def orders_list(request):
    keyword = _get_keyword(request)
    queryset = Order.objects.select_related('user', 'product').prefetch_related('user__telegramusernames').order_by('-created_at')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['order_no', 'product_name', 'status', 'tx_hash', 'user__tg_user_id', 'user__username', 'product__name'],
    )
    items = [_order_payload(item) for item in queryset[:100]]
    return _ok(items)


@dashboard_login_required
@require_GET
def cloud_orders_list(request):
    keyword = _get_keyword(request)
    queryset = CloudServerOrder.objects.select_related('user', 'plan').prefetch_related('user__telegramusernames').order_by('-created_at')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['order_no', 'provider', 'region_name', 'plan_name', 'status', 'public_ip', 'user__tg_user_id', 'user__username'],
    )
    items = [_cloud_order_detail_payload(item) for item in queryset[:100]]
    now = timezone.now()
    for item in items:
        status = item.get('status')
        service_expires_at = item.get('service_expires_at')
        renew_grace_expires_at = item.get('renew_grace_expires_at')
        delete_at = item.get('delete_at')
        auto_renew_enabled = bool(item.get('auto_renew_enabled'))

        service_expires_dt = parse_datetime(service_expires_at) if isinstance(service_expires_at, str) and service_expires_at else None
        renew_grace_dt = parse_datetime(renew_grace_expires_at) if isinstance(renew_grace_expires_at, str) and renew_grace_expires_at else None
        delete_dt = parse_datetime(delete_at) if isinstance(delete_at, str) and delete_at else None
        if service_expires_dt is not None and timezone.is_naive(service_expires_dt):
            service_expires_dt = timezone.make_aware(service_expires_dt, timezone.get_current_timezone())
        if renew_grace_dt is not None and timezone.is_naive(renew_grace_dt):
            renew_grace_dt = timezone.make_aware(renew_grace_dt, timezone.get_current_timezone())
        if delete_dt is not None and timezone.is_naive(delete_dt):
            delete_dt = timezone.make_aware(delete_dt, timezone.get_current_timezone())

        if status in {'pending', 'cancelled', 'failed'}:
            item['renew_status'] = 'unpaid'
            item['renew_status_label'] = '未付款'
        elif status in {'paid', 'provisioning'}:
            item['renew_status'] = 'paid'
            item['renew_status_label'] = '已付款'
        elif status in {'completed', 'renew_pending', 'expiring', 'suspended', 'deleting', 'deleted', 'expired'}:
            item['renew_status'] = 'completed'
            item['renew_status_label'] = '已完成'
        elif service_expires_dt and service_expires_dt <= now:
            item['renew_status'] = 'completed'
            item['renew_status_label'] = '已完成'
        else:
            item['renew_status'] = 'unpaid'
            item['renew_status_label'] = '未付款'

        item['can_renew'] = bool(item.get('public_ip')) and status in RENEWABLE_CLOUD_ORDER_STATUSES
        item['auto_renew_enabled'] = auto_renew_enabled
        item['expired_by_time'] = bool(service_expires_dt and service_expires_dt <= now)
        item['grace_expired'] = bool(renew_grace_dt and renew_grace_dt <= now)
        item['delete_scheduled'] = bool(delete_dt and delete_dt > now)
        item['is_expired'] = status in {'deleted', 'expired'} or item['grace_expired']
        item['expires_in_days'] = _days_left(service_expires_dt) if service_expires_dt else None
        item['grace_expires_in_days'] = _days_left(renew_grace_dt) if renew_grace_dt else None
    return _ok(items)



def _asset_payload(asset):
    user = asset.user
    order = asset.order
    user_payload = None
    if user:
        usernames = user.usernames
        user_payload = _user_payload({
            'id': user.id,
            'tg_user_id': user.tg_user_id,
            'username': user.username,
            'first_name': user.first_name,
            'usernames': usernames,
            'primary_username': usernames[0] if usernames else '',
        })
    expires_at = asset.actual_expires_at or getattr(order, 'service_expires_at', None)
    return {
        'id': asset.id,
        'kind': asset.kind,
        'source': asset.source,
        'source_label': _server_source_label(asset.source),
        'provider': asset.provider,
        'provider_label': _provider_label(asset.provider),
        'region_label': _region_label(getattr(asset, 'region_code', None), asset.region_name),
        'region_name': asset.region_name,
        'asset_name': asset.asset_name,
        'instance_id': asset.instance_id,
        'provider_resource_id': asset.provider_resource_id,
        'public_ip': asset.public_ip,
        'mtproxy_link': asset.mtproxy_link,
        'mtproxy_port': asset.mtproxy_port,
        'mtproxy_secret': asset.mtproxy_secret,
        'mtproxy_host': asset.mtproxy_host,
        'note': asset.note,
        'actual_expires_at': _iso(expires_at),
        'days_left': _days_left(expires_at),
        'status_countdown': f"剩余 {_days_left(expires_at)} 天" if _days_left(expires_at) is not None else '-',
        'price': _decimal_to_str(asset.price if asset.price is not None else (order.total_amount if order and order.total_amount is not None else None), 2),
        'currency': asset.currency or (order.currency if order else ''),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'order_id': order.id if order else None,
        'order_no': order.order_no if order else '',
        'status': asset.status,
        'status_label': _status_label(asset.status, CloudAsset.STATUS_CHOICES),
        'provider_status': '已删除' if asset.status == CloudAsset.STATUS_DELETED else asset.provider_status,
        'is_active': asset.is_active,
        'updated_at': _iso(asset.updated_at),
    }


def _cloud_order_summary_payload(order):
    if not order:
        return None
    return {
        'id': order.id,
        'order_id': order.id,
        'order_no': order.order_no,
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'plan_name': order.plan_name,
        'public_ip': order.public_ip,
        'previous_public_ip': order.previous_public_ip,
        'service_expires_at': _iso(order.service_expires_at),
        'suspend_at': _iso(order.suspend_at),
        'delete_at': _iso(order.delete_at),
        'ip_recycle_at': _iso(order.ip_recycle_at),
        'created_at': _iso(order.created_at),
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
        'order_source_tags': [],
        'order_source_tag_labels': [],
    }


def _asset_detail_payload(asset):
    order = asset.order
    payload = _asset_payload(asset)
    payload.update({
        'created_at': _iso(asset.created_at),
        'service_started_at': _iso(getattr(order, 'service_started_at', None)),
        'service_expires_at': _iso(getattr(order, 'service_expires_at', None) or asset.actual_expires_at),
        'renew_grace_expires_at': _iso(getattr(order, 'renew_grace_expires_at', None)),
        'suspend_at': _iso(getattr(order, 'suspend_at', None)),
        'delete_at': _iso(getattr(order, 'delete_at', None)),
        'ip_recycle_at': _iso(getattr(order, 'ip_recycle_at', None)),
        'last_renewed_at': _iso(getattr(order, 'last_renewed_at', None)),
        'order_status': getattr(order, 'status', None),
        'order_status_label': _status_label(getattr(order, 'status', None), CloudServerOrder.STATUS_CHOICES),
        'provision_note': getattr(order, 'provision_note', None),
        'related_order': _cloud_order_summary_payload(order),
        'history_orders': [_cloud_order_summary_payload(order)] if order else [],
        'ip_logs': [],
        'lifecycle_order_links': {},
    })
    return payload


def _resolve_telegram_user(value):
    raw = str(value or '').strip().lstrip('@')
    if not raw:
        return None
    queryset = TelegramUser.objects.prefetch_related('telegramusernames')
    if raw.isdigit():
        return queryset.filter(Q(id=int(raw)) | Q(tg_user_id=int(raw))).first()
    return queryset.filter(Q(username__icontains=raw) | Q(telegramusernames__username__iexact=raw)).distinct().first()


def _parse_iso_datetime(value, field_label='时间'):
    raw = str(value or '').strip()
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        raise ValueError(f'{field_label}格式不正确，请使用 ISO 时间')
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _active_cloud_asset_filter():
    return Q(
        is_active=True,
        status__in=[
            CloudAsset.STATUS_RUNNING,
            CloudAsset.STATUS_PENDING,
            CloudAsset.STATUS_STARTING,
        ],
    )


def _expired_cloud_asset_filter(now=None):
    now = now or timezone.now()
    return Q(status__in=[CloudAsset.STATUS_EXPIRED, CloudAsset.STATUS_EXPIRED_GRACE, CloudAsset.STATUS_DELETED]) | Q(actual_expires_at__lte=now)


def _unattached_ip_cloud_asset_filter():
    return Q(public_ip__isnull=True) | Q(public_ip='') | Q(provider_status__icontains='未附加') | Q(provider_status__icontains='固定IP保留')


def _apply_cloud_asset_risk_filter(queryset, risk_status):
    risk_status = str(risk_status or 'all').strip()
    if risk_status in {'', 'all'}:
        return queryset
    now = timezone.now()
    expired_filter = _expired_cloud_asset_filter(now)
    active_filter = _active_cloud_asset_filter()
    if risk_status == 'normal':
        return queryset.filter(active_filter)
    if risk_status == 'due_soon':
        return queryset.filter(actual_expires_at__gt=now, actual_expires_at__lte=now + timezone.timedelta(days=7))
    if risk_status == 'expired':
        return queryset.filter(expired_filter)
    if risk_status == 'unattached_ip':
        return queryset.filter(_unattached_ip_cloud_asset_filter())
    if risk_status == 'abnormal':
        return queryset.exclude(active_filter).exclude(expired_filter)
    if risk_status == 'unbound_user':
        return queryset.filter(user__isnull=True)
    if risk_status == 'auto_renew_off':
        return queryset.filter(Q(order__isnull=True) | Q(order__auto_renew_enabled=False))
    return queryset


def _cloud_asset_risk_counts(queryset):
    now = timezone.now()
    active_filter = _active_cloud_asset_filter()
    expired_filter = _expired_cloud_asset_filter(now)
    all_count = queryset.count()
    return {
        'all': all_count,
        'normal': queryset.filter(active_filter).count(),
        'due_soon': queryset.filter(actual_expires_at__gt=now, actual_expires_at__lte=now + timezone.timedelta(days=7)).count(),
        'expired': queryset.filter(expired_filter).count(),
        'unattached_ip': queryset.filter(_unattached_ip_cloud_asset_filter()).count(),
        'abnormal': queryset.exclude(active_filter).exclude(expired_filter).count(),
        'shutdown_disabled': 0,
        'unbound_user': queryset.filter(user__isnull=True).count(),
        'unbound_group': 0,
        'auto_renew_off': queryset.filter(Q(order__isnull=True) | Q(order__auto_renew_enabled=False)).count(),
    }


def _sync_telegram_username(user, username=None):
    incoming = _split_usernames(username)
    if not incoming:
        return

    merged = _merge_usernames(user.usernames, incoming)
    user.username = ','.join(merged)
    user.save(update_fields=['username', 'updated_at'])

    current_names = {item.username.lower(): item for item in user.telegramusernames.all() if item.username}
    primary_key = incoming[0].lower() if incoming else None
    for raw in merged:
        key = raw.lower()
        existing = current_names.pop(key, None)
        should_be_primary = key == primary_key
        if existing:
            changed_fields = []
            if existing.username != raw:
                existing.username = raw
                changed_fields.append('username')
            if existing.is_primary != should_be_primary:
                existing.is_primary = should_be_primary
                changed_fields.append('is_primary')
            if changed_fields:
                changed_fields.append('updated_at')
                existing.save(update_fields=changed_fields)
        else:
            TelegramUsername.objects.create(user=user, username=raw, is_primary=should_be_primary)

    for item in current_names.values():
        if item.is_primary and primary_key:
            item.is_primary = False
            item.save(update_fields=['is_primary', 'updated_at'])


@dashboard_login_required
@require_GET
def cloud_assets_list(request):
    keyword = _get_keyword(request)
    grouped = (request.GET.get('grouped') or '').lower() in {'1', 'true', 'yes'}
    try:
        queryset = CloudAsset.objects.select_related('user', 'order').prefetch_related('user__telegramusernames')
        queryset = _apply_keyword_filter(
            queryset,
            keyword,
            [
                'asset_name', 'public_ip', 'mtproxy_link', 'user__tg_user_id',
                'user__username', 'user__telegramusernames__username', 'order__order_no',
            ],
        ).distinct()
        risk_counts = _cloud_asset_risk_counts(queryset)
        queryset = _apply_cloud_asset_risk_filter(queryset, request.GET.get('risk_status')).order_by('actual_expires_at', '-updated_at', '-id')
        items = [_asset_payload(asset) for asset in queryset[:200]]
    except ProgrammingError:
        return _ok({'groups': [], 'items': []} if grouped else [])

    if not grouped:
        return _ok({'items': items, 'total': len(items), 'page': 1, 'page_size': 200, 'risk_counts': risk_counts} if (request.GET.get('paginated') or '').lower() in {'1', 'true', 'yes'} else items)

    groups = {}
    for item in items:
        key = str(item['tg_user_id'] or 'unbound')
        group = groups.setdefault(key, {
            'user_key': key,
            'tg_user_id': item['tg_user_id'],
            'user_display_name': item['user_display_name'],
            'username_label': item['username_label'],
            'default_expanded': True,
            'items': [],
        })
        group['items'].append(item)
    ordered_groups = list(groups.values())
    ordered_groups.sort(key=lambda group: (
        min((row['actual_expires_at'] or '9999-12-31T23:59:59') for row in group['items']),
        str(group['tg_user_id'] or 'zzzz'),
    ))
    return _ok({'groups': ordered_groups, 'items': items, 'total': len(items), 'page': 1, 'page_size': 200, 'risk_counts': risk_counts})


@csrf_exempt
@dashboard_login_required
@require_http_methods(['GET', 'POST', 'PUT', 'PATCH'])
def update_cloud_asset(request, asset_id):
    if request.method == 'GET':
        asset = CloudAsset.objects.select_related('order', 'user').prefetch_related('user__telegramusernames').filter(pk=asset_id).first()
        if not asset:
            return _error('云资产不存在', status=404)
        return _ok(_asset_detail_payload(asset))

    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    try:
        with transaction.atomic():
            asset = CloudAsset.objects.select_for_update().select_related('order', 'user').prefetch_related('user__telegramusernames').get(pk=asset_id)

            server = None
            server_lookup = Q()
            if asset.instance_id:
                server_lookup |= Q(instance_id=asset.instance_id)
            if asset.provider_resource_id:
                server_lookup |= Q(provider_resource_id=asset.provider_resource_id)
            if asset.order_id:
                server_lookup |= Q(order_id=asset.order_id)
            if server_lookup:
                server = Server.objects.select_for_update().filter(server_lookup).first()

            user_lookup = payload.get('user_query') or payload.get('user_id') or payload.get('tg_user_id') or payload.get('username')
            username_raw = payload.get('user_query') or payload.get('username')
            clear_user = str(payload.get('clear_user') or '').lower() in {'1', 'true', 'yes', 'on'}
            if clear_user:
                asset.user = None
                if server:
                    server.user = None
                if asset.order_id:
                    asset.order.last_user_id = None
                    asset.order.save(update_fields=['last_user_id', 'updated_at'])
            elif user_lookup not in (None, ''):
                user = _resolve_telegram_user(user_lookup)
                if not user:
                    return _error('未找到匹配的 Telegram 用户', status=404)
                asset.user = user
                _sync_telegram_username(user, username_raw)
                if server:
                    server.user = user
                if asset.order_id:
                    asset.order.user = user
                    asset.order.last_user_id = user.tg_user_id
                    asset.order.save(update_fields=['user', 'last_user_id', 'updated_at'])

            if 'price' in payload:
                try:
                    price = _parse_decimal(payload.get('price'), '价格').quantize(Decimal('0.01'))
                except ValueError as exc:
                    return _error(str(exc), status=400)
                asset.price = price
                if asset.order_id:
                    asset.order.total_amount = price
                    asset.order.save(update_fields=['total_amount', 'updated_at'])

            if 'currency' in payload:
                asset.currency = (payload.get('currency') or 'USDT').strip() or 'USDT'
                if asset.order_id and asset.order.currency != asset.currency:
                    asset.order.currency = asset.currency
                    asset.order.save(update_fields=['currency', 'updated_at'])

            if server and 'account_label' in payload:
                server.account_label = payload.get('account_label') or None

            if 'actual_expires_at' in payload:
                try:
                    asset.actual_expires_at = _parse_iso_datetime(payload.get('actual_expires_at'), '到期时间')
                except ValueError as exc:
                    return _error(str(exc), status=400)
                if asset.order_id:
                    asset.order.service_expires_at = asset.actual_expires_at
                    asset.order.save(update_fields=['service_expires_at', 'updated_at'])
                if server:
                    server.expires_at = asset.actual_expires_at

            if 'mtproxy_link' in payload and asset.order_id:
                asset.order.mtproxy_link = payload.get('mtproxy_link') or None
                asset.order.save(update_fields=['mtproxy_link', 'updated_at'])

            if 'provider_resource_id' in payload and asset.order_id:
                asset.order.provider_resource_id = payload.get('provider_resource_id') or None
                asset.order.save(update_fields=['provider_resource_id', 'updated_at'])

            if 'public_ip' in payload and asset.order_id:
                asset.order.public_ip = payload.get('public_ip') or None
                asset.order.save(update_fields=['public_ip', 'updated_at'])

            if 'public_ip' in payload:
                old_public_ip = asset.public_ip
                new_public_ip = payload.get('public_ip') or None
                if old_public_ip and old_public_ip != new_public_ip:
                    asset.previous_public_ip = old_public_ip

            for field in ('asset_name', 'public_ip', 'provider_resource_id', 'mtproxy_link', 'mtproxy_secret', 'mtproxy_host', 'note'):
                if field in payload:
                    setattr(asset, field, payload.get(field) or None)
            if 'mtproxy_port' in payload:
                mtproxy_port = payload.get('mtproxy_port')
                asset.mtproxy_port = int(mtproxy_port) if mtproxy_port not in (None, '') else None
            for field in ('provider', 'region_name', 'region_code'):
                if field in payload:
                    setattr(asset, field, payload.get(field) or None)
            if server:
                if 'asset_name' in payload:
                    server.server_name = payload.get('asset_name') or None
                if 'public_ip' in payload:
                    old_public_ip = server.public_ip
                    server.public_ip = payload.get('public_ip') or None
                    if old_public_ip and old_public_ip != server.public_ip:
                        server.previous_public_ip = old_public_ip
                if 'provider_resource_id' in payload:
                    server.provider_resource_id = payload.get('provider_resource_id') or None
                if 'provider' in payload:
                    server.provider = payload.get('provider') or None
                if 'region_name' in payload:
                    server.region_name = payload.get('region_name') or None
                if 'region_code' in payload:
                    server.region_code = payload.get('region_code') or None
                if 'note' in payload:
                    server.note = payload.get('note') or None
            if 'is_active' in payload:
                asset.is_active = str(payload.get('is_active')).lower() in {'1', 'true', 'yes', 'on'}
                if server:
                    server.is_active = asset.is_active

            if user_lookup not in (None, '') and server:
                server.user = asset.user
            if asset.kind == CloudAsset.KIND_SERVER and not server:
                server = Server(
                    source=(asset.source or Server.SOURCE_ORDER) if asset.source in {choice[0] for choice in Server.SOURCE_CHOICES} else Server.SOURCE_ORDER,
                    instance_id=asset.instance_id or asset.provider_resource_id or asset.public_ip,
                )
            if server:
                if asset.order_id:
                    server.order = asset.order
                if asset.instance_id:
                    server.instance_id = asset.instance_id
                elif not server.instance_id:
                    server.instance_id = asset.provider_resource_id or asset.public_ip
                server.user = asset.user
                server.source = server.source or Server.SOURCE_ORDER
                server.provider = asset.provider
                server.region_name = asset.region_name
                server.region_code = asset.region_code
                server.provider_resource_id = asset.provider_resource_id
                server.public_ip = asset.public_ip
                server.note = asset.note
                server.expires_at = asset.actual_expires_at
                server.is_active = asset.is_active
                if asset.asset_name:
                    server.server_name = asset.asset_name
                if server.account_label in (None, ''):
                    server.account_label = asset.provider
                server.save()

            asset.save()
    except CloudAsset.DoesNotExist:
        return _error('云资产不存在', status=404)

    asset = CloudAsset.objects.select_related('user', 'order').prefetch_related('user__telegramusernames').get(pk=asset_id)
    return _ok(_asset_payload(asset))


@dashboard_login_required
@require_GET
def cloud_assets_sync_status(request):
    recent_syncs = []
    for item in ExternalSyncLog.objects.filter(source__in=[
        ExternalSyncLog.SOURCE_AWS,
        ExternalSyncLog.SOURCE_ALIYUN,
        ExternalSyncLog.SOURCE_DASHBOARD,
    ]).order_by('-created_at', '-id')[:10]:
        response_payload = {}
        try:
            response_payload = json.loads(item.response_payload or '{}')
        except (TypeError, ValueError):
            response_payload = {}
        recent_syncs.append({
            'id': item.id,
            'target': item.target or item.action,
            'providers': [item.source] if item.source else [],
            'is_success': item.is_success,
            'error_message': item.error_message or '',
            'created_at': _iso(item.created_at),
            'tasks': response_payload.get('tasks') or [],
            'skipped_tasks': response_payload.get('skipped_tasks') or [],
        })
    last_synced_at = recent_syncs[0]['created_at'] if recent_syncs else None
    auto_sync_every_seconds = max(_site_int_config('cloud_asset_sync_interval_seconds', 5 * 60 * 60), 60)
    unattached_ip_count = CloudAsset.objects.filter(_unattached_ip_cloud_asset_filter()).count()
    return _ok({
        'accounts': {
            'aliyun': [_cloud_account_payload(item) for item in CloudAccountConfig.objects.filter(provider=CloudAccountConfig.PROVIDER_ALIYUN, is_active=True)],
            'aws': [_cloud_account_payload(item) for item in CloudAccountConfig.objects.filter(provider=CloudAccountConfig.PROVIDER_AWS, is_active=True)],
        },
        'aliyun_existing_count': CloudAsset.objects.filter(provider__in=['aliyun', 'aliyun_simple']).count(),
        'aws_existing_count': CloudAsset.objects.filter(provider__in=['aws', 'aws_lightsail']).count(),
        'auto_sync_every_seconds': auto_sync_every_seconds,
        'last_synced_at': last_synced_at,
        'recent_syncs': recent_syncs,
        'unattached_ip_count': unattached_ip_count,
    })


@dashboard_login_required
@require_GET
def cloud_asset_risk_summary(request):
    keyword = _get_keyword(request)
    queryset = CloudAsset.objects.select_related('user', 'order').prefetch_related('user__telegramusernames')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        [
            'asset_name', 'public_ip', 'mtproxy_link', 'user__tg_user_id',
            'user__username', 'user__telegramusernames__username', 'order__order_no',
        ],
    ).distinct()
    risk_counts = _cloud_asset_risk_counts(queryset)
    return _ok({'risk_counts': risk_counts, 'total': risk_counts['all']})


@dashboard_login_required
@require_GET
def cloud_asset_ip_logs(request):
    items = []
    keyword = _get_keyword(request)
    logs = ExternalSyncLog.objects.order_by('-created_at', '-id')
    if keyword:
        logs = logs.filter(Q(target__icontains=keyword) | Q(action__icontains=keyword) | Q(error_message__icontains=keyword))
    for item in logs[:200]:
        items.append({
            'id': item.id,
            'event_type': item.action or item.source,
            'event_label': dict(ExternalSyncLog.SOURCE_CHOICES).get(item.source, item.source),
            'provider': item.source,
            'provider_label': dict(ExternalSyncLog.SOURCE_CHOICES).get(item.source, item.source),
            'provider_resource_id': item.target,
            'instance_id': None,
            'public_ip': None,
            'previous_public_ip': None,
            'region_code': None,
            'region_name': None,
            'region_label': None,
            'asset_id': None,
            'asset_name': None,
            'server_id': None,
            'order_id': None,
            'order_no': None,
            'user_id': None,
            'tg_user_id': None,
            'user_display_name': '-',
            'username_label': '-',
            'note': item.error_message or item.response_payload or item.request_payload or '',
            'created_at': _iso(item.created_at),
        })
    return _ok(items)


@dashboard_login_required
@require_GET
def dashboard_ip_delete_logs(request):
    payload = _lifecycle_plan_payload()
    items = payload.get('ip_delete_items') or []
    limit_raw = request.GET.get('limit')
    try:
        limit = max(min(int(limit_raw or 300), 1000), 1)
    except (TypeError, ValueError):
        limit = 300
    return _ok(items[:limit])


@dashboard_login_required
@require_GET
def bot_operation_logs(request):
    keyword = _get_keyword(request).lower()
    overview = _telegram_accounts_overview()
    user_map = {
        user.tg_user_id: user
        for user in TelegramUser.objects.prefetch_related('telegramusernames').filter(
            tg_user_id__in=[
                int(item.get('tg_user_id') or 0)
                for item in overview.get('messages', [])
                if str(item.get('tg_user_id') or '').isdigit()
            ]
        )
    }
    items = []
    for raw in overview.get('messages', [])[:500]:
        tg_user_id = int(raw.get('tg_user_id') or 0) if str(raw.get('tg_user_id') or '').isdigit() else None
        user = user_map.get(tg_user_id)
        user_payload = _cloud_task_user_payload(user)
        action_type = 'message' if raw.get('direction') == 'in' else 'callback' if raw.get('content_type') == 'callback' else 'message'
        item = {
            **user_payload,
            'id': raw.get('id') or len(items) + 1,
            'action_type': action_type,
            'action_label': '消息' if action_type == 'message' else '按钮',
            'chat_id': raw.get('chat_id'),
            'message_id': raw.get('message_id'),
            'payload': raw.get('text') or raw.get('payload') or raw.get('latest_message') or '',
            'created_at': raw.get('created_at') or raw.get('latest_at'),
        }
        haystack = ' '.join(str(item.get(key) or '') for key in ['payload', 'user_display_name', 'username_label', 'tg_user_id', 'chat_id'])
        if keyword and keyword not in haystack.lower():
            continue
        items.append(item)
    return _ok(items)


@csrf_exempt
@dashboard_login_required
@require_POST
def toggle_cloud_asset_auto_renew(request, asset_id):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    asset = CloudAsset.objects.select_related('order', 'user').prefetch_related('user__telegramusernames').filter(pk=asset_id).first()
    if not asset:
        return _error('云资产不存在', status=404)
    enabled = str(payload.get('enabled')).lower() in {'1', 'true', 'yes', 'on'}
    if asset.order:
        asset.order.auto_renew_enabled = enabled
        asset.order.save(update_fields=['auto_renew_enabled', 'updated_at'])
    return _ok(_asset_payload(asset))


@csrf_exempt
@dashboard_login_required
@require_POST
def sync_cloud_asset_status(request, asset_id):
    denied = _require_superuser(request)
    if denied:
        return denied
    asset = CloudAsset.objects.select_related('order', 'user').prefetch_related('user__telegramusernames').filter(pk=asset_id).first()
    if not asset:
        return _error('云资产不存在', status=404)
    original_updated_at = asset.updated_at
    provider = _normalize_cloud_provider(asset.provider)
    region = (asset.region_code or '').strip()
    errors = []
    logs = []
    task = None
    if not provider or not region:
        errors.append('资产缺少云厂商或地域，无法同步云上状态。')
    else:
        task = _cloud_sync_task(
            provider,
            region,
            reason=f'更新资产 #{asset.id} 的云上状态',
            asset_ids=[asset.id],
        )
        try:
            result = _run_cloud_sync_task(task)
            logs = result.get('logs') or []
        except Exception as exc:
            errors.append(f'{provider.upper()} {region} 同步失败: {exc}')

    asset = CloudAsset.objects.select_related('order', 'user').prefetch_related('user__telegramusernames').filter(pk=asset_id).first()
    if not errors and asset and provider and region:
        if asset.updated_at <= original_updated_at:
            errors.append('已完成地域同步，但未在云上同步结果中找到该实例，请确认实例是否已被删除或实例 ID 是否正确。')
    return _ok({
        'ok': len(errors) == 0,
        'asset': _asset_payload(asset) if asset else None,
        'provider': provider or asset.provider if asset else provider,
        'region_code': region,
        'task': task,
        'logs': logs,
        'errors': errors,
    })


@csrf_exempt
@dashboard_login_required
@require_POST
def delete_cloud_asset(request, asset_id):
    denied = _require_superuser(request)
    if denied:
        return denied
    asset = CloudAsset.objects.select_related('order', 'user').prefetch_related('user__telegramusernames').filter(pk=asset_id).first()
    if not asset:
        return _error('云资产不存在', status=404)
    asset.status = CloudAsset.STATUS_DELETED
    asset.is_active = False
    asset.save(update_fields=['status', 'is_active', 'updated_at'])
    return _ok(True)


def _server_payload(server):
    user = server.user
    user_payload = None
    if user:
        usernames = user.usernames
        user_payload = _user_payload({
            'id': user.id,
            'tg_user_id': user.tg_user_id,
            'username': user.username,
            'first_name': user.first_name,
            'usernames': usernames,
            'primary_username': usernames[0] if usernames else '',
        })
    order = server.order
    return {
        'id': server.id,
        'status': server.status,
        'status_label': _status_label(server.status, Server.STATUS_CHOICES),
        'source': server.source,
        'source_label': _server_source_label(server.source),
        'provider': server.provider,
        'provider_label': _provider_label(server.provider),
        'account_label': server.account_label,
        'region_label': _region_label(server.region_code, server.region_name),
        'region_name': server.region_name,
        'server_name': server.server_name,
        'instance_id': server.instance_id,
        'provider_resource_id': server.provider_resource_id,
        'public_ip': server.public_ip,
        'login_user': server.login_user,
        'expires_at': _iso(server.expires_at),
        'days_left': _days_left(server.expires_at),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'order_id': order.id if order else None,
        'order_no': order.order_no if order else '',
        'order_detail_path': f'/admin/cloud-orders/{order.id}' if order else '',
        'provider_status': '已删除' if server.status == Server.STATUS_DELETED else server.provider_status,
        'is_active': server.is_active,
        'updated_at': _iso(server.updated_at),
    }


def _cloud_order_detail_payload(order, *, include_secrets=False):
    user = order.user
    usernames = user.usernames if user else []
    user_payload = _user_payload({
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'username': user.username,
        'first_name': user.first_name,
        'usernames': usernames,
        'primary_username': usernames[0] if usernames else '',
    }) if user else None
    login_password = order.login_password_plain
    return {
        'id': order.id,
        'order_no': order.order_no,
        'provider': order.provider,
        'region_code': order.region_code,
        'region_label': _region_label(order.region_code, order.region_name),
        'region_name': order.region_name,
        'plan_name': order.plan_name,
        'quantity': order.quantity,
        'currency': order.currency,
        'total_amount': _decimal_to_str(order.total_amount),
        'pay_amount': _decimal_to_str(order.pay_amount) if order.pay_amount is not None else None,
        'pay_method': order.pay_method,
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'tx_hash': order.tx_hash,
        'image_name': order.image_name,
        'server_name': order.server_name,
        'lifecycle_days': order.lifecycle_days,
        'service_started_at': _iso(order.service_started_at),
        'service_expires_at': _iso(order.service_expires_at),
        'renew_grace_expires_at': _iso(order.renew_grace_expires_at),
        'suspend_at': _iso(order.suspend_at),
        'delete_at': _iso(order.delete_at),
        'ip_recycle_at': _iso(order.ip_recycle_at),
        'last_renewed_at': _iso(order.last_renewed_at),
        'last_user_id': order.last_user_id,
        'mtproxy_port': order.mtproxy_port,
        'mtproxy_link': order.mtproxy_link,
        'mtproxy_secret': order.mtproxy_secret,
        'mtproxy_host': order.mtproxy_host,
        'instance_id': order.instance_id,
        'provider_resource_id': order.provider_resource_id,
        'static_ip_name': order.static_ip_name,
        'public_ip': order.public_ip,
        'previous_public_ip': order.previous_public_ip,
        'login_user': order.login_user,
        'login_password': login_password if include_secrets else '',
        'login_password_preview': _mask_secret_value(login_password),
        'provision_note': order.provision_note,
        'created_at': _iso(order.created_at),
        'paid_at': _iso(order.paid_at),
        'expired_at': _iso(order.expired_at),
        'completed_at': _iso(order.completed_at),
        'updated_at': _iso(order.updated_at),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'plan_id': order.plan_id,
    }


def _order_payload(order):
    user = order.user
    usernames = user.usernames if user else []
    user_payload = _user_payload({
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'username': user.username,
        'first_name': user.first_name,
        'usernames': usernames,
        'primary_username': usernames[0] if usernames else '',
    }) if user else None
    product = getattr(order, 'product', None)
    return {
        'id': order.id,
        'order_no': order.order_no,
        'product_id': order.product_id,
        'product_name': order.product_name,
        'product_label': product.name if product else order.product_name,
        'product_description': product.description if product else None,
        'quantity': order.quantity,
        'currency': order.currency,
        'total_amount': _decimal_to_str(order.total_amount),
        'pay_amount': _decimal_to_str(order.pay_amount) if order.pay_amount is not None else None,
        'pay_method': order.pay_method,
        'status': order.status,
        'status_label': _status_label(order.status, Order.STATUS_CHOICES),
        'tx_hash': order.tx_hash,
        'created_at': _iso(order.created_at),
        'paid_at': _iso(order.paid_at),
        'expired_at': _iso(order.expired_at),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
    }


def _recharge_detail_payload(recharge):
    user = recharge.user
    usernames = user.usernames if user else []
    user_payload = _user_payload({
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'username': user.username,
        'first_name': user.first_name,
        'usernames': usernames,
        'primary_username': usernames[0] if usernames else '',
    }) if user else None
    return {
        'id': recharge.id,
        'amount': _decimal_to_str(recharge.amount),
        'currency': recharge.currency,
        'status': recharge.status,
        'status_label': _status_label(recharge.status, Recharge.STATUS_CHOICES),
        'tx_hash': recharge.tx_hash,
        'pay_amount': _decimal_to_str(recharge.pay_amount) if getattr(recharge, 'pay_amount', None) is not None else None,
        'receive_address': getattr(recharge, 'receive_address', None),
        'created_at': _iso(recharge.created_at),
        'completed_at': _iso(recharge.completed_at),
        'updated_at': _iso(getattr(recharge, 'updated_at', None)),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
    }


def _append_provision_note(order, note):
    if not note:
        return order.provision_note
    return '\n'.join(filter(None, [order.provision_note, note]))


@transaction.atomic
def _apply_recharge_status(recharge, new_status, *, operator=None):
    now = timezone.now()
    old_status = recharge.status
    allowed_statuses = {choice[0] for choice in Recharge.STATUS_CHOICES}
    if new_status not in allowed_statuses:
        raise ValueError('充值订单状态不正确')
    if new_status == old_status:
        return recharge

    user = TelegramUser.objects.select_for_update().get(pk=recharge.user_id)
    balance_field = 'balance_trx' if recharge.currency == 'TRX' else 'balance'

    if old_status == 'completed' and new_status != 'completed':
        current_balance = getattr(user, balance_field)
        if current_balance < recharge.amount:
            raise ValueError('用户余额不足，无法从已完成回退状态')
        new_balance = current_balance - recharge.amount
        setattr(user, balance_field, new_balance)
        user.save(update_fields=[balance_field, 'updated_at'])
        _record_balance_ledger(
            user,
            currency=recharge.currency,
            old_balance=current_balance,
            new_balance=new_balance,
            ledger_type=BalanceLedger.TYPE_RECHARGE,
            related_type='recharge',
            related_id=recharge.id,
            description='Dashboard 手动回退充值入账',
            operator=operator,
        )
        recharge.completed_at = None

    if new_status == 'completed' and old_status != 'completed':
        current_balance = getattr(user, balance_field)
        new_balance = current_balance + recharge.amount
        setattr(user, balance_field, new_balance)
        user.save(update_fields=[balance_field, 'updated_at'])
        _record_balance_ledger(
            user,
            currency=recharge.currency,
            old_balance=current_balance,
            new_balance=new_balance,
            ledger_type=BalanceLedger.TYPE_RECHARGE,
            related_type='recharge',
            related_id=recharge.id,
            description='Dashboard 手动确认充值入账',
            operator=operator,
        )
        recharge.completed_at = recharge.completed_at or now
    elif new_status in {'pending', 'expired'}:
        recharge.completed_at = None

    recharge.status = new_status
    recharge.save(update_fields=['status', 'completed_at'])
    return recharge


@dashboard_login_required
@require_GET
def site_config_groups(request):
    groups = _dashboard_site_config_groups()
    existing = {item.key: item for item in SiteConfig.objects.all()}
    payload = []
    for group_key, keys in groups.items():
        items = []
        for index, key in enumerate(keys):
            obj = existing.get(key)
            is_sensitive = bool(
                (obj and obj.is_sensitive) or key in DASHBOARD_SENSITIVE_CONFIG_KEYS,
            )
            plain_value = SiteConfig.get(key, '') if obj else ''
            items.append({
                'key': key,
                'id': obj.id if obj else None,
                'value': '' if is_sensitive else plain_value,
                'value_preview': obj.masked_value() if obj and is_sensitive else plain_value,
                'is_sensitive': is_sensitive,
                'description': _dashboard_config_label(key),
                'default_value': DASHBOARD_CONFIG_DEFAULTS.get(key, ''),
                'sort_order': index,
            })
        payload.append({'group': group_key, 'items': items})
    return _ok(payload)


DEFAULT_BUTTON_CONFIG = {
    'row_size': 2,
    'items': [
        {'key': 'buy_proxy', 'label': '购买代理', 'type': 'business', 'sort_order': 10, 'enabled': True, 'locked': True},
        {'key': 'my_orders', 'label': '我的订单', 'type': 'business', 'sort_order': 20, 'enabled': True, 'locked': True},
        {'key': 'recharge', 'label': '充值余额', 'type': 'business', 'sort_order': 30, 'enabled': True, 'locked': True},
        {'key': 'support', 'label': '联系客服', 'type': 'business', 'sort_order': 40, 'enabled': True, 'locked': True},
    ],
}


def _button_config_payload():
    config = _json_config('dashboard_button_config', DEFAULT_BUTTON_CONFIG)
    items = config.get('items') if isinstance(config, dict) else []
    normalized_items = []
    for index, item in enumerate(items or []):
        normalized_items.append({
            'key': str(item.get('key') or f'button_{index}'),
            'label': str(item.get('label') or ''),
            'button_label': item.get('button_label') or '',
            'url': item.get('url') or '',
            'message': item.get('message') or '',
            'type': item.get('type') if item.get('type') in {'business', 'link'} else 'link',
            'sort_order': int(item.get('sort_order') or 0),
            'enabled': bool(item.get('enabled', True)),
            'locked': bool(item.get('locked', False)),
        })
    return {'row_size': int(config.get('row_size') or 2), 'items': normalized_items}


@dashboard_login_required
@require_GET
def button_config(request):
    return _ok(_button_config_payload())


@csrf_exempt
@dashboard_login_required
@require_POST
def update_button_config(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    config = {
        'row_size': int(payload.get('row_size') or 2),
        'items': payload.get('items') if isinstance(payload.get('items'), list) else [],
    }
    _set_json_config('dashboard_button_config', config)
    return _ok(_button_config_payload())


@csrf_exempt
@dashboard_login_required
@require_POST
def init_button_config(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    _set_json_config('dashboard_button_config', DEFAULT_BUTTON_CONFIG)
    return _ok(_button_config_payload())


def _telegram_groups():
    return _json_config('dashboard_telegram_groups', [])


def _set_telegram_groups(groups):
    _set_json_config('dashboard_telegram_groups', groups)


def _telegram_accounts_overview():
    overview = _json_config('dashboard_telegram_accounts_overview', {
        'accounts': [],
        'chats': [],
        'messages': [],
        'users': [],
    })
    return _normalize_telegram_accounts_overview(overview)


@dashboard_login_required
@require_GET
def telegram_accounts(request):
    overview = _telegram_accounts_overview()
    keyword = _get_keyword(request).lower()
    archived = str(request.GET.get('archived') or '').lower()
    messages = overview.get('messages', [])
    users = _telegram_users_from_db(messages)
    if keyword:
        overview['chats'] = [
            item for item in overview.get('chats', [])
            if keyword in str(item.get('title') or '').lower() or keyword in str(item.get('latest_message') or '').lower()
        ]
        messages = [
            item for item in overview.get('messages', [])
            if keyword in str(item.get('text') or '').lower() or keyword in str(item.get('chat_title') or '').lower()
        ]
        users = [
            item for item in users
            if keyword in str(item.get('display_name') or '').lower()
            or keyword in str(item.get('username_label') or '').lower()
            or keyword in str(item.get('tg_user_id') or '').lower()
        ]
    if archived in {'0', 'false', 'no'}:
        overview['chats'] = [item for item in overview.get('chats', []) if not item.get('archived')]
    return _ok({
        'accounts': overview.get('accounts', []),
        'chats': overview.get('chats', []),
        'messages': messages,
        'users': users,
    })


@csrf_exempt
@dashboard_login_required
@require_POST
def create_telegram_account(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    overview = _telegram_accounts_overview()
    accounts = overview.setdefault('accounts', [])
    next_id = _next_telegram_account_id(accounts)
    usernames = _split_usernames(payload.get('username') or '')
    tg_user_id = int(payload.get('tg_user_id')) if str(payload.get('tg_user_id') or '').isdigit() else None
    item = {
        'id': next_id,
        'label': payload.get('label') or payload.get('phone') or f'Telegram 账号 {next_id}',
        'phone': payload.get('phone') or '',
        'username': ','.join(usernames),
        'usernames': usernames,
        'tg_user_id': tg_user_id,
        'first_name': payload.get('first_name') or '',
        'note': payload.get('note') or '',
        'status': 'registered',
        'notify_enabled': True,
        'listener_push_enabled': False,
        'has_session': False,
        'created_at': _iso(timezone.now()),
        'updated_at': _iso(timezone.now()),
        'last_synced_at': None,
    }
    accounts.append(item)
    if tg_user_id:
        _save_telegram_identity(tg_user_id, usernames, item.get('first_name') or '')
    _set_json_config('dashboard_telegram_accounts_overview', overview)
    return _ok(item)


@csrf_exempt
@dashboard_login_required
@require_POST
def telegram_account_status(request, account_id):
    denied = _require_superuser(request)
    if denied:
        return denied
    overview = _telegram_accounts_overview()
    item = _find_telegram_account(overview, account_id)
    if not item:
        return _error('Telegram 账号不存在', status=404)
    session_string = _get_telegram_session(account_id)
    if not session_string:
        if item.get('status') == 'logged_in':
            item['status'] = 'session_expired'
            item['updated_at'] = _iso(timezone.now())
            _set_json_config('dashboard_telegram_accounts_overview', overview)
        return _ok(_normalize_telegram_account(item))
    try:
        api_id, api_hash = _telegram_api_config()
        result = async_to_sync(_telegram_check_session_async)(session_string, api_id, api_hash)
    except Exception as exc:
        item['status'] = 'listener_error'
        item['note'] = _telegram_error_message(exc)
        item['updated_at'] = _iso(timezone.now())
        _set_json_config('dashboard_telegram_accounts_overview', overview)
        return _ok(_normalize_telegram_account(item))
    if result.get('authorized'):
        _finalize_telegram_account(item, result.get('profile') or {}, result.get('session_string') or session_string)
    else:
        item['status'] = 'session_expired'
        item['has_session'] = False
        item['updated_at'] = _iso(timezone.now())
    _set_json_config('dashboard_telegram_accounts_overview', overview)
    return _ok(_normalize_telegram_account(item))


@csrf_exempt
@dashboard_login_required
@require_POST
def telegram_account_notify(request, account_id):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    overview = _telegram_accounts_overview()
    for item in overview.get('accounts', []):
        if int(item.get('id') or 0) == account_id:
            if payload.get('listener_push_enabled') and not _get_telegram_session(account_id):
                return _error('请先完成 Telegram 账号登录，再开启监听推送。')
            if 'notify_enabled' in payload:
                item['notify_enabled'] = bool(payload.get('notify_enabled'))
            if 'listener_push_enabled' in payload:
                item['listener_push_enabled'] = bool(payload.get('listener_push_enabled'))
            item['updated_at'] = _iso(timezone.now())
            _set_json_config('dashboard_telegram_accounts_overview', overview)
            return _ok(item)
    return _error('Telegram 账号不存在', status=404)


@csrf_exempt
@dashboard_login_required
@require_POST
def start_telegram_login(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    phone = str(payload.get('phone') or '').strip()
    if not phone:
        return _error('请输入 Telegram 手机号。')
    try:
        api_id, api_hash = _telegram_api_config()
    except ValueError as exc:
        return _error(str(exc))
    overview = _telegram_accounts_overview()
    account = _ensure_telegram_login_account(overview, phone)
    account['status'] = 'pending'
    account['updated_at'] = _iso(timezone.now())
    try:
        result = async_to_sync(_telegram_send_code_async)(phone, api_id, api_hash)
    except Exception as exc:
        account['status'] = 'error'
        account['note'] = _telegram_error_message(exc)
        _set_json_config('dashboard_telegram_accounts_overview', overview)
        return _error(account['note'])
    account['phone_code_hash'] = result.get('phone_code_hash') or ''
    account['status'] = 'code_sent'
    account['has_session'] = True
    account['note'] = ''
    account['updated_at'] = _iso(timezone.now())
    _set_telegram_session(account['id'], result.get('session_string') or '')
    _set_json_config('dashboard_telegram_accounts_overview', overview)
    return _ok({
        'account': _normalize_telegram_account(account),
        'account_id': account['id'],
        'next_step': 'code',
    })


@csrf_exempt
@dashboard_login_required
@require_POST
def submit_telegram_login_code(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    account_id = payload.get('account_id')
    code = str(payload.get('code') or '').strip().replace(' ', '')
    if not account_id:
        return _error('登录会话不存在，请重新输入手机号。')
    if not code:
        return _error('请输入 Telegram 验证码。')
    overview = _telegram_accounts_overview()
    account = _find_telegram_account(overview, account_id)
    if not account:
        return _error('Telegram 账号不存在', status=404)
    session_string = _get_telegram_session(account_id)
    if not session_string or not account.get('phone_code_hash'):
        account['status'] = 'session_expired'
        account['updated_at'] = _iso(timezone.now())
        _set_json_config('dashboard_telegram_accounts_overview', overview)
        return _error('登录会话已过期，请重新发送验证码。')
    try:
        api_id, api_hash = _telegram_api_config()
        result = async_to_sync(_telegram_sign_in_code_async)(
            session_string,
            account.get('phone') or '',
            code,
            account.get('phone_code_hash') or '',
            api_id,
            api_hash,
        )
    except Exception as exc:
        account['status'] = 'error'
        account['note'] = _telegram_error_message(exc)
        account['updated_at'] = _iso(timezone.now())
        _set_json_config('dashboard_telegram_accounts_overview', overview)
        return _error(account['note'])
    if result.get('requires_password'):
        account['status'] = 'password_required'
        account['has_session'] = True
        account['updated_at'] = _iso(timezone.now())
        _set_telegram_session(account['id'], result.get('session_string') or session_string)
        _set_json_config('dashboard_telegram_accounts_overview', overview)
        return _ok({
            'account': _normalize_telegram_account(account),
            'account_id': account['id'],
            'next_step': 'password',
            'requires_password': True,
        })
    _finalize_telegram_account(account, result.get('profile') or {}, result.get('session_string') or session_string)
    account['note'] = ''
    _set_json_config('dashboard_telegram_accounts_overview', overview)
    return _ok({
        'account': _normalize_telegram_account(account),
        'account_id': account['id'],
        'next_step': 'done',
        'requires_password': False,
    })


@csrf_exempt
@dashboard_login_required
@require_POST
def submit_telegram_login_password(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    account_id = payload.get('account_id')
    if not account_id:
        return _error('登录会话不存在，请重新输入手机号。')
    overview = _telegram_accounts_overview()
    account = _find_telegram_account(overview, account_id)
    if not account:
        return _error('Telegram 账号不存在', status=404)
    session_string = _get_telegram_session(account_id)
    if not session_string:
        account['status'] = 'session_expired'
        account['updated_at'] = _iso(timezone.now())
        _set_json_config('dashboard_telegram_accounts_overview', overview)
        return _error('登录会话已过期，请重新发送验证码。')
    try:
        api_id, api_hash = _telegram_api_config()
        result = async_to_sync(_telegram_sign_in_password_async)(
            session_string,
            payload.get('password') or '',
            api_id,
            api_hash,
        )
    except Exception as exc:
        account['status'] = 'error'
        account['note'] = _telegram_error_message(exc)
        account['updated_at'] = _iso(timezone.now())
        _set_json_config('dashboard_telegram_accounts_overview', overview)
        return _error(account['note'])
    _finalize_telegram_account(account, result.get('profile') or {}, result.get('session_string') or session_string)
    account['note'] = ''
    _set_json_config('dashboard_telegram_accounts_overview', overview)
    return _ok({
        'account': _normalize_telegram_account(account),
        'account_id': account['id'],
        'next_step': 'done',
    })


@dashboard_login_required
@require_GET
def telegram_groups(request):
    keyword = _get_keyword(request).lower()
    groups = _telegram_groups()
    if keyword:
        groups = [
            item for item in groups
            if keyword in str(item.get('title') or '').lower() or keyword in str(item.get('username') or '').lower()
        ]
    return _ok(groups)


@csrf_exempt
@dashboard_login_required
@require_POST
def create_telegram_group(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    groups = _telegram_groups()
    next_id = max([int(item.get('id') or 0) for item in groups] or [0]) + 1
    now = _iso(timezone.now())
    item = {
        'id': next_id,
        'chat_id': int(payload.get('chat_id')) if str(payload.get('chat_id') or '').lstrip('-').isdigit() else next_id,
        'title': payload.get('title') or f'群组 {next_id}',
        'username': payload.get('username') or '',
        'enabled': bool(payload.get('enabled', False)),
        'push_enabled': bool(payload.get('push_enabled', False)),
        'collapsed': bool(payload.get('collapsed', False)),
        'archived': bool(payload.get('archived', False)),
        'created_at': now,
        'updated_at': now,
    }
    groups.append(item)
    _set_telegram_groups(groups)
    return _ok(item)


@csrf_exempt
@dashboard_login_required
@require_POST
def update_telegram_group(request, group_id):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    groups = _telegram_groups()
    for item in groups:
        if int(item.get('id') or 0) == group_id:
            for field in ('title', 'username'):
                if field in payload:
                    item[field] = payload.get(field) or ''
            if 'chat_id' in payload and str(payload.get('chat_id') or '').lstrip('-').isdigit():
                item['chat_id'] = int(payload.get('chat_id'))
            for field in ('enabled', 'push_enabled', 'collapsed', 'archived'):
                if field in payload:
                    item[field] = bool(payload.get(field))
            item['updated_at'] = _iso(timezone.now())
            _set_telegram_groups(groups)
            return _ok(item)
    return _error('Telegram 群组不存在', status=404)


@dashboard_login_required
@require_GET
def telegram_group_detail(request, group_id):
    for item in _telegram_groups():
        if int(item.get('id') or 0) == group_id:
            chat_id = int(item.get('chat_id') or 0)
            overview = _telegram_accounts_overview()
            messages = [
                message for message in overview.get('messages', [])
                if int(message.get('chat_id') or 0) == chat_id
            ]
            member_stats = {}
            for message in messages:
                tg_user_id = int(message.get('tg_user_id') or 0)
                if not tg_user_id:
                    continue
                stat = member_stats.setdefault(tg_user_id, {
                    'first_name': message.get('first_name_snapshot') or '',
                    'last_seen_at': None,
                    'message_count': 0,
                    'tg_user_id': tg_user_id,
                    'username': message.get('username_snapshot') or '',
                })
                stat['message_count'] += 1
                if message.get('first_name_snapshot'):
                    stat['first_name'] = message.get('first_name_snapshot')
                if message.get('username_snapshot'):
                    stat['username'] = message.get('username_snapshot')
                if message.get('created_at') and (
                    not stat['last_seen_at'] or message.get('created_at') > stat['last_seen_at']
                ):
                    stat['last_seen_at'] = message.get('created_at')

            users = {
                user.tg_user_id: user
                for user in TelegramUser.objects.filter(tg_user_id__in=member_stats).prefetch_related('telegramusernames')
            }
            members = []
            for tg_user_id, stat in member_stats.items():
                user = users.get(tg_user_id)
                usernames = user.usernames if user else _split_usernames(stat.get('username') or '')
                username = usernames[0] if usernames else stat.get('username') or ''
                first_name = (user.first_name if user else stat.get('first_name')) or ''
                members.append({
                    'display_label': first_name or (f'@{username}' if username else f'ID {tg_user_id}'),
                    'display_name': first_name or (f'@{username}' if username else f'ID {tg_user_id}'),
                    'first_name': first_name,
                    'last_seen_at': stat.get('last_seen_at'),
                    'message_count': stat.get('message_count') or 0,
                    'tg_user_id': tg_user_id,
                    'username': username,
                })
            members.sort(key=lambda member: (member.get('last_seen_at') or '', member.get('message_count') or 0), reverse=True)
            return _ok({'group': item, 'members': members, 'messages': messages[:200]})
    return _error('Telegram 群组不存在', status=404)


@dashboard_login_required
@require_GET
def telegram_messages(request):
    overview = _telegram_accounts_overview()
    messages = overview.get('messages', [])
    chat_id = request.GET.get('chat_id')
    tg_user_id = request.GET.get('tg_user_id')
    if str(chat_id or '').lstrip('-').isdigit():
        chat_id_value = int(chat_id)
        messages = [
            item for item in messages
            if int(item.get('chat_id') or 0) == chat_id_value
        ]
    if str(tg_user_id or '').isdigit():
        tg_user_id_value = int(tg_user_id)
        messages = [
            item for item in messages
            if int(item.get('tg_user_id') or 0) == tg_user_id_value
        ]
    return _ok(messages)


def _send_dashboard_telegram_text(chat_id: int, text: str, *, login_account_id=None, chat_title: str = ''):
    overview = _telegram_accounts_overview()
    account = _find_telegram_account(overview, login_account_id) if login_account_id else None
    if not account:
        for item in overview.get('accounts', []):
            if item.get('status') == 'logged_in' and _get_telegram_session(item.get('id')):
                account = item
                break
    if not account:
        raise ValueError('没有可用的 Telegram 登录账号，请先完成账号登录。')
    if not account.get('notify_enabled', True):
        raise ValueError('该账号已关闭通知发送，请先开启账号通知。')
    session_string = _get_telegram_session(account.get('id'))
    if not session_string:
        account['status'] = 'session_expired'
        account['updated_at'] = _iso(timezone.now())
        _set_json_config('dashboard_telegram_accounts_overview', overview)
        raise ValueError('Telegram 会话已失效，请重新登录账号。')

    api_id, api_hash = _telegram_api_config()
    result = async_to_sync(_telegram_send_message_async)(session_string, chat_id, text, api_id, api_hash)
    _set_telegram_session(account['id'], result.get('session_string') or session_string)
    messages = overview.setdefault('messages', [])
    next_id = max([int(item.get('id') or 0) for item in messages] or [0]) + 1
    resolved_chat_title = str(chat_title or '')
    for chat in overview.get('chats', []):
        if int(chat.get('chat_id') or 0) == chat_id:
            resolved_chat_title = resolved_chat_title or chat.get('title') or str(chat_id)
            break
    resolved_chat_title = resolved_chat_title or str(chat_id)
    item = {
        'chat_id': chat_id,
        'chat_title': resolved_chat_title,
        'content_type': 'text',
        'created_at': result.get('created_at') or _iso(timezone.now()),
        'direction': 'out',
        'direction_label': '发出',
        'first_name_snapshot': account.get('first_name') or '',
        'id': next_id,
        'login_account_id': account.get('id'),
        'login_account_label': account.get('label') or '',
        'message_id': result.get('message_id'),
        'source': 'telegram_login',
        'source_label': 'Telegram 登录账号',
        'text': text,
        'tg_user_id': account.get('tg_user_id') or 0,
        'username_snapshot': (account.get('username') or '').split(',', 1)[0],
    }
    messages.insert(0, item)
    chats = overview.setdefault('chats', [])
    chat = None
    for existing in chats:
        if int(existing.get('chat_id') or 0) == chat_id:
            chat = existing
            break
    if not chat:
        chat = {
            'archived': False,
            'chat_id': chat_id,
            'is_group': chat_id < 0,
            'login_account_id': account.get('id'),
            'login_account_label': account.get('label') or '',
            'message_count': 0,
            'source': 'telegram_login',
            'source_label': 'Telegram 登录账号',
            'subtitle': account.get('label') or '',
            'title': resolved_chat_title,
        }
        chats.insert(0, chat)
    chat.update({
        'latest_at': item['created_at'],
        'latest_message': text,
        'message_count': int(chat.get('message_count') or 0) + 1,
    })
    _set_json_config('dashboard_telegram_accounts_overview', overview)
    return item


@csrf_exempt
@dashboard_login_required
@require_POST
def send_telegram_message(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    text = str(payload.get('text') or '').strip()
    if not text:
        return _error('请输入要发送的消息。')
    chat_id_raw = payload.get('chat_id')
    try:
        chat_id = int(chat_id_raw)
    except (TypeError, ValueError):
        return _error('聊天对象 ID 不正确。')

    try:
        item = _send_dashboard_telegram_text(
            chat_id,
            text,
            login_account_id=payload.get('login_account_id'),
            chat_title=str(payload.get('chat_title') or ''),
        )
    except Exception as exc:
        return _error(_telegram_error_message(exc))
    return _ok(item)


@csrf_exempt
@dashboard_login_required
@require_POST
def archive_telegram_chat(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    chat_id_raw = payload.get('chat_id')
    if not str(chat_id_raw or '').lstrip('-').isdigit():
        return _error('会话 ID 不正确。')
    chat_id = int(chat_id_raw)
    login_account_id = payload.get('login_account_id')
    archived = bool(payload.get('archived'))
    overview = _telegram_accounts_overview()
    for item in overview.setdefault('chats', []):
        same_chat = int(item.get('chat_id') or 0) == chat_id
        same_account = (
            not login_account_id
            or int(item.get('login_account_id') or 0) == int(login_account_id)
        )
        if same_chat and same_account:
            item['archived'] = archived
            item['updated_at'] = _iso(timezone.now())
            _set_json_config('dashboard_telegram_accounts_overview', overview)
            return _ok(item)
    return _error('Telegram 会话不存在', status=404)


def _apply_cloud_order_status(order, new_status):
    now = timezone.now()
    old_status = order.status
    allowed_statuses = {choice[0] for choice in CloudServerOrder.STATUS_CHOICES}
    if new_status not in allowed_statuses:
        raise ValueError('订单状态不正确')
    if new_status == old_status:
        return order

    note = None
    trigger_provision = False
    status_to_save = new_status
    active_statuses = {'completed', 'renew_pending', 'expiring'}
    inactive_statuses = {'failed', 'cancelled', 'expired', 'deleted', 'suspended', 'deleting', 'pending'}

    if new_status in {'paid', 'provisioning', 'completed'} and not order.paid_at:
        order.paid_at = now

    if new_status == 'completed':
        if not order.completed_at:
            order.completed_at = now
        if not order.last_renewed_at:
            order.last_renewed_at = now
        note = '后台手动改状态为已完成。'
    elif new_status == 'paid':
        order.completed_at = None
        note = '后台手动改状态为已支付。'
        if not (order.instance_id or order.provider_resource_id or order.public_ip):
            trigger_provision = True
    elif new_status == 'provisioning':
        order.completed_at = None
        note = '后台手动改状态为创建中。'
        if not (order.instance_id or order.provider_resource_id or order.public_ip):
            trigger_provision = True
            status_to_save = 'paid'
    elif new_status == 'renew_pending':
        order.completed_at = None
        if order.service_expires_at and order.service_expires_at > now:
            order.last_renewed_at = order.last_renewed_at or now
        note = '后台手动改状态为待续费。'
    elif new_status == 'expiring':
        order.completed_at = None
        note = '后台手动改状态为即将到期。'
    elif new_status in inactive_statuses:
        if new_status == 'pending':
            order.paid_at = None
        order.completed_at = None
        note = f"后台手动改状态为{dict(CloudServerOrder.STATUS_CHOICES).get(new_status, new_status)}。"

    order.status = status_to_save
    order.provision_note = _append_provision_note(order, note)
    order.save()

    if new_status in active_statuses:
        CloudAsset.objects.filter(order=order).update(
            actual_expires_at=order.service_expires_at,
            is_active=True,
            note=order.provision_note,
            updated_at=now,
        )
        Server.objects.filter(order=order).update(
            expires_at=order.service_expires_at,
            is_active=True,
            status=Server.STATUS_RUNNING if new_status == 'completed' else Server.STATUS_PENDING,
            note=order.provision_note,
            updated_at=now,
        )
    elif new_status in inactive_statuses:
        CloudAsset.objects.filter(order=order).update(
            is_active=False,
            note=order.provision_note,
            updated_at=now,
        )
        Server.objects.filter(order=order).update(
            is_active=False,
            status=Server.STATUS_DELETED if new_status == 'deleted' else Server.STATUS_STOPPED,
            note=order.provision_note,
            updated_at=now,
        )

    order._trigger_provision = trigger_provision

    return order


@csrf_exempt
@dashboard_login_required
@require_http_methods(['GET', 'POST', 'PUT', 'PATCH'])
def cloud_order_detail(request, order_id):
    order = CloudServerOrder.objects.select_related('user', 'plan').prefetch_related('user__telegramusernames').filter(pk=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    if request.method == 'GET':
        return _ok(_cloud_order_detail_payload(order, include_secrets=request.user.is_superuser))

    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    trigger_provision = False
    try:
        with transaction.atomic():
            order = CloudServerOrder.objects.select_for_update().select_related('user', 'plan').get(pk=order_id)
            user_lookup = payload.get('user_query') or payload.get('user_id') or payload.get('tg_user_id') or payload.get('username')
            username_raw = payload.get('user_query') or payload.get('username')
            if user_lookup not in (None, ''):
                user = _resolve_telegram_user(user_lookup)
                if not user:
                    return _error('未找到匹配的 Telegram 用户', status=404)
                order.user = user
                order.last_user_id = user.tg_user_id
                _sync_telegram_username(user, username_raw)
                CloudAsset.objects.filter(order=order).update(user=user, updated_at=timezone.now())
                Server.objects.filter(order=order).update(user=user, updated_at=timezone.now())

            new_status = str(payload.get('status') or '').strip()
            if new_status and new_status != order.status:
                order = _apply_cloud_order_status(order, new_status)
                trigger_provision = bool(getattr(order, '_trigger_provision', False))

            for field in ('server_name', 'public_ip', 'provision_note'):
                if field in payload:
                    setattr(order, field, payload.get(field) or None)
            if 'total_amount' in payload and payload.get('total_amount') not in (None, ''):
                order.total_amount = _parse_decimal(payload.get('total_amount'), '总金额')
            if 'pay_amount' in payload:
                order.pay_amount = _parse_decimal(payload.get('pay_amount'), '应付金额') if payload.get('pay_amount') not in (None, '') else None
            datetime_updates = {}
            for field, label in (
                ('service_expires_at', '服务到期时间'),
                ('suspend_at', '计划关机时间'),
                ('delete_at', '计划删机时间'),
                ('ip_recycle_at', 'IP 保留到期时间'),
            ):
                if field in payload:
                    datetime_updates[field] = _parse_iso_datetime(payload.get(field), label)
                    setattr(order, field, datetime_updates[field])
            order.save()
            manual_datetime_updates = {
                field: value for field, value in datetime_updates.items()
                if field in {'suspend_at', 'delete_at', 'ip_recycle_at'}
            }
            if manual_datetime_updates:
                manual_datetime_updates['updated_at'] = timezone.now()
                CloudServerOrder.objects.filter(pk=order.pk).update(**manual_datetime_updates)
                order.refresh_from_db()
            asset_updates = {'updated_at': timezone.now()}
            server_updates = {'updated_at': timezone.now()}
            if 'server_name' in payload:
                asset_updates['asset_name'] = order.server_name
                server_updates['server_name'] = order.server_name
            if 'public_ip' in payload:
                asset_updates['public_ip'] = order.public_ip
                server_updates['public_ip'] = order.public_ip
            if 'service_expires_at' in payload:
                asset_updates['actual_expires_at'] = order.service_expires_at
                server_updates['expires_at'] = order.service_expires_at
            if len(asset_updates) > 1:
                CloudAsset.objects.filter(order=order).update(**asset_updates)
            if len(server_updates) > 1:
                Server.objects.filter(order=order).update(**server_updates)
    except ValueError as exc:
        return _error(str(exc), status=400)
    except Exception as exc:
        return _error(f'保存订单失败: {exc}', status=500)
    if trigger_provision:
        order = async_to_sync(provision_cloud_server)(order.id) or order
        order.refresh_from_db()
    return _ok(_cloud_order_detail_payload(order, include_secrets=True))


@csrf_exempt
@dashboard_login_required
@require_POST
def update_cloud_order_status(request, order_id):
    denied = _require_superuser(request)
    if denied:
        return denied
    order = CloudServerOrder.objects.select_related('user', 'plan').filter(pk=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    payload = _read_payload(request)
    new_status = str(payload.get('status') or '').strip()
    if not new_status:
        return _error('订单状态不能为空')
    try:
        with transaction.atomic():
            order = _apply_cloud_order_status(order, new_status)
            trigger_provision = bool(getattr(order, '_trigger_provision', False))
        if trigger_provision:
            order = async_to_sync(provision_cloud_server)(order.id) or order
            order.refresh_from_db()
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f'更新订单状态失败: {exc}', status=500)
    return _ok(_cloud_order_detail_payload(order, include_secrets=True))


@csrf_exempt
@dashboard_login_required
@require_POST
def delete_cloud_order(request, order_id):
    denied = _require_superuser(request)
    if denied:
        return denied
    order = CloudServerOrder.objects.filter(pk=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    CloudAsset.objects.filter(order=order).update(order=None, updated_at=timezone.now())
    Server.objects.filter(order=order).update(order=None, updated_at=timezone.now())
    order.delete()
    return _ok(True)


@dashboard_login_required
@require_GET
def recharge_detail(request, recharge_id):
    recharge = Recharge.objects.select_related('user').prefetch_related('user__telegramusernames').filter(pk=recharge_id).first()
    if not recharge:
        return _error('充值订单不存在', status=404)
    return _ok(_recharge_detail_payload(recharge))


@csrf_exempt
@dashboard_login_required
@require_POST
def update_recharge_status(request, recharge_id):
    denied = _require_superuser(request)
    if denied:
        return denied
    recharge = Recharge.objects.select_related('user').prefetch_related('user__telegramusernames').filter(pk=recharge_id).first()
    if not recharge:
        return _error('充值订单不存在', status=404)
    payload = _read_payload(request)
    new_status = str(payload.get('status') or '').strip()
    if not new_status:
        return _error('充值订单状态不能为空')
    try:
        operator = getattr(request.user, 'username', '') or str(getattr(request.user, 'id', '') or '')
        recharge = _apply_recharge_status(recharge, new_status, operator=operator)
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f'更新充值订单状态失败: {exc}', status=500)
    return _ok(_recharge_detail_payload(recharge))


@dashboard_login_required
@require_GET
def servers_statistics(request):
    keyword = _get_keyword(request)
    queryset = Server.objects.all()
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['region_code', 'region_name', 'provider', 'account_label', 'server_name', 'instance_id', 'public_ip'],
    )
    rows = list(
        queryset
        .values('provider', 'region_code', 'region_name', 'account_label')
        .annotate(total_count=Count('id'))
        .order_by('account_label', 'provider', 'region_name')
    )

    region_pairs = []
    region_seen = set()
    for row in rows:
        region_code = row['region_code'] or ''
        region_label = _region_label(region_code, row['region_name'])
        key = (region_code, region_label)
        if key not in region_seen:
            region_seen.add(key)
            region_pairs.append({'region_code': region_code, 'region_label': region_label})
    region_pairs.sort(key=lambda item: (item['region_label'], item['region_code']))

    account_map = {}
    for row in rows:
        account_id = row['account_label'] or '-'
        entry = account_map.setdefault(
            account_id,
            {
                'account_id': account_id,
                'account_label': account_id,
                'provider_label': _provider_label(row['provider']),
                'regions': {},
                'total_count': 0,
            },
        )
        region_code = row['region_code'] or ''
        region_label = _region_label(region_code, row['region_name'])
        region_key = region_code or region_label
        count = row['total_count']
        entry['regions'][region_key] = entry['regions'].get(region_key, 0) + count
        entry['total_count'] += count

    items = []
    totals = {'account_id': '合计', 'account_label': '合计', 'provider_label': '-', 'regions': {}, 'total_count': 0}
    for account_id in sorted(account_map.keys()):
        entry = account_map[account_id]
        row_payload = {
            'account_id': entry['account_id'],
            'account_label': entry['account_label'],
            'provider_label': entry['provider_label'],
            'total_count': entry['total_count'],
        }
        for region in region_pairs:
            region_key = region['region_code'] or region['region_label']
            value = entry['regions'].get(region_key, 0)
            row_payload[region_key] = value
            totals['regions'][region_key] = totals['regions'].get(region_key, 0) + value
        totals['total_count'] += entry['total_count']
        items.append(row_payload)

    total_row = {
        'account_id': totals['account_id'],
        'account_label': totals['account_label'],
        'provider_label': totals['provider_label'],
        'total_count': totals['total_count'],
    }
    for region in region_pairs:
        region_key = region['region_code'] or region['region_label']
        total_row[region_key] = totals['regions'].get(region_key, 0)

    return _ok({
        'regions': region_pairs,
        'items': items,
        'summary': total_row,
    })


@csrf_exempt
@dashboard_login_required
@require_POST
def sync_cloud_assets(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    payload = _read_payload(request)
    aliyun_region = (str(payload.get('region') or request.GET.get('region') or 'cn-hongkong')).strip() or 'cn-hongkong'
    aws_region = (str(payload.get('aws_region') or request.GET.get('aws_region') or 'ap-southeast-1')).strip() or 'ap-southeast-1'
    if aws_region.lower() == 'all':
        aws_region = 'ap-southeast-1'
    raw_providers = payload.get('providers')
    if isinstance(raw_providers, str):
        raw_providers = [part.strip() for part in raw_providers.split(',') if part.strip()]
    providers = [_normalize_cloud_provider(item) for item in (raw_providers or [])]
    providers = [item for item in providers if item]
    try:
        asset_ids = [int(item) for item in (payload.get('asset_ids') or []) if str(item).strip()]
    except (TypeError, ValueError):
        return _error('asset_ids 参数格式不正确')

    requested_providers = providers or ['aliyun', 'aws']
    tasks = []
    skipped_tasks = []
    if asset_ids:
        selected_tasks, selected_skipped = _selected_cloud_sync_tasks(asset_ids)
        if providers:
            selected_tasks = [task for task in selected_tasks if task['provider'] in providers]
        tasks.extend(selected_tasks)
        skipped_tasks.extend(selected_skipped)
    else:
        if 'aliyun' in requested_providers:
            tasks.append(_cloud_sync_task('aliyun', aliyun_region, reason='后台手动同步'))
        if 'aws' in requested_providers:
            tasks.append(_cloud_sync_task('aws', aws_region, reason='后台手动同步'))

    errors = []
    synced = {'aliyun': False, 'aws': False}
    completed_tasks = []
    for task in tasks:
        provider = task['provider']
        try:
            completed = _run_cloud_sync_task(task)
            completed_tasks.append(completed)
            synced[provider] = True
        except Exception as exc:
            task_error = f"{'阿里云' if provider == 'aliyun' else 'AWS'} {task['region']} 代理同步失败: {exc}"
            errors.append(task_error)
            skipped_tasks.append({**task, 'reason': task_error})

    response_payload = {
        'ok': len(errors) == 0,
        'synced': synced,
        'providers': requested_providers,
        'aliyun_region': aliyun_region,
        'aws_region': aws_region,
        'tasks': completed_tasks,
        'skipped_tasks': skipped_tasks,
        'errors': errors,
    }
    record_external_sync_log(
        source=ExternalSyncLog.SOURCE_DASHBOARD,
        action='sync_cloud_assets',
        target=','.join(requested_providers),
        request_payload=payload,
        response_payload=_dashboard_sync_log_payload(completed_tasks, skipped_tasks, errors, synced),
        is_success=not errors,
        error_message='；'.join(errors),
    )
    if errors and not any(synced.values()):
        return _error('；'.join(errors), status=500)
    return _ok(response_payload)


@dashboard_login_required
@require_GET
def servers_list(request):
    keyword = _get_keyword(request)
    dedup_raw = (request.GET.get('dedup') or '').lower()
    dedup = dedup_raw not in {'0', 'false', 'no', 'off'}
    queryset = Server.objects.select_related('user', 'order').prefetch_related('user__telegramusernames').order_by('expires_at', '-updated_at', '-id')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['server_name', 'instance_id', 'public_ip', 'account_label', 'provider', 'region_name', 'user__tg_user_id', 'user__username', 'order__order_no'],
    )
    provider = (request.GET.get('provider') or '').strip()
    region_code = (request.GET.get('region_code') or '').strip()
    if provider:
        queryset = queryset.filter(provider=provider)
    if region_code:
        queryset = queryset.filter(region_code=region_code)
    items = [_server_payload(server) for server in queryset[:500]]
    if dedup:
        seen = set()
        deduped = []
        for item in items:
            dedup_key = (item.get('provider') or '', item.get('instance_id') or '', item.get('public_ip') or '', item.get('server_name') or '')
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            deduped.append(item)
        items = deduped
    return _ok(items)


def _apply_server_missing_state(provider, region, existing_instance_ids):
    now = timezone.now()
    existing_instance_ids = {str(item) for item in existing_instance_ids if item}
    queryset = Server.objects.filter(provider=provider, region_code=region).exclude(instance_id__isnull=True).exclude(instance_id='')
    legacy_queryset = queryset.filter(provider_status='missing')
    legacy_updated = legacy_queryset.update(
        status=Server.STATUS_DELETED,
        provider_status='已删除',
        is_active=False,
        note=Case(
            When(note__isnull=True, then=Value(f'历史状态修正：服务器不存在，已统一标记为已删除；检查时间: {now.isoformat()}')),
            When(note='', then=Value(f'历史状态修正：服务器不存在，已统一标记为已删除；检查时间: {now.isoformat()}')),
            default=Cast('note', output_field=CharField()),
            output_field=CharField(),
        ),
        updated_at=now,
    )
    queryset = queryset.filter(is_active=True)
    if existing_instance_ids:
        queryset = queryset.exclude(instance_id__in=existing_instance_ids)
    missing_servers = list(queryset.select_related('order'))
    missing_note = f'云平台同步未发现该服务器，已标记为已删除；检查时间: {now.isoformat()}'
    updated = queryset.update(
        status=Server.STATUS_DELETED,
        provider_status='已删除',
        is_active=False,
        note=missing_note,
        updated_at=now,
    )
    order_ids = [item.order_id for item in missing_servers if item.order_id]
    instance_ids = [item.instance_id for item in missing_servers if item.instance_id]
    if order_ids:
        CloudServerOrder.objects.filter(id__in=order_ids).exclude(status='deleted').update(
            status='deleted',
            provision_note=missing_note,
            updated_at=now,
        )
        CloudAsset.objects.filter(order_id__in=order_ids).update(
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
            note=missing_note,
            updated_at=now,
        )
    if instance_ids:
        CloudAsset.objects.filter(instance_id__in=instance_ids).update(
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
            note=missing_note,
            updated_at=now,
        )
    return legacy_updated + updated


def _cloud_plan_payload(plan):
    return {
        'id': plan.id,
        'provider': plan.provider,
        'provider_label': _provider_label(plan.provider),
        'region_code': plan.region_code,
        'region_name': plan.region_name,
        'region_label': _region_label(plan.region_code, plan.region_name),
        'plan_name': plan.plan_name,
        'plan_description': plan.plan_description,
        'cpu': plan.cpu,
        'memory': plan.memory,
        'storage': plan.storage,
        'bandwidth': plan.bandwidth,
        'cost_price': _decimal_to_str(getattr(plan, 'cost_price', 0)),
        'price': _decimal_to_str(plan.price),
        'currency': plan.currency,
        'sort_order': plan.sort_order,
        'is_active': plan.is_active,
        'updated_at': _iso(plan.updated_at),
    }


@dashboard_login_required
@require_GET
def cloud_pricing_list(request):
    keyword = _get_keyword(request)
    queryset = ServerPrice.objects.filter(is_active=True).order_by('provider', 'region_code', '-sort_order', 'id')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['provider', 'region_code', 'region_name', 'bundle_code', 'server_name', 'server_description', 'cpu', 'memory', 'storage', 'bandwidth'],
    )
    provider = (request.GET.get('provider') or '').strip()
    region_code = (request.GET.get('region_code') or '').strip()
    if provider:
        queryset = queryset.filter(provider=provider)
    if region_code:
        queryset = queryset.filter(region_code=region_code)
    return _ok([_server_price_payload(item) for item in queryset])


@dashboard_login_required
@require_GET
def cloud_plans_list(request):
    keyword = _get_keyword(request)
    queryset = CloudServerPlan.objects.filter(is_active=True).order_by('provider', 'region_code', '-sort_order', 'id')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['provider', 'region_code', 'region_name', 'plan_name', 'plan_description', 'cpu', 'memory', 'storage', 'bandwidth'],
    )
    provider = (request.GET.get('provider') or '').strip()
    region_code = (request.GET.get('region_code') or '').strip()
    if provider:
        queryset = queryset.filter(provider=provider)
    if region_code:
        queryset = queryset.filter(region_code=region_code)
    return _ok([_cloud_plan_payload(item) for item in queryset])


@csrf_exempt
@dashboard_login_required
@require_POST
def sync_servers(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    aliyun_region = (request.POST.get('region') or request.GET.get('region') or 'cn-hongkong').strip() or 'cn-hongkong'
    aws_region = (request.POST.get('aws_region') or request.GET.get('aws_region') or 'ap-southeast-1').strip() or 'ap-southeast-1'
    errors = []
    synced = {'aliyun': False, 'aws': False}
    missing = {'aliyun': 0, 'aws': 0}
    try:
        aliyun_command = call_command('sync_aliyun_assets', region=aliyun_region)
        synced['aliyun'] = True
        missing['aliyun'] = _apply_server_missing_state('aliyun_simple', aliyun_region, getattr(aliyun_command, 'synced_instance_ids', None) or [])
    except Exception as exc:
        errors.append(f'阿里云同步失败: {exc}')
    try:
        aws_command = call_command('sync_aws_assets', region=aws_region)
        synced['aws'] = True
        missing['aws'] = _apply_server_missing_state('aws_lightsail', aws_region, getattr(aws_command, 'synced_instance_ids', None) or [])
    except Exception as exc:
        errors.append(f'AWS 同步失败: {exc}')
    if errors and not any(synced.values()):
        return _error('；'.join(errors), status=500)
    return _ok({'synced': synced, 'missing': missing, 'aliyun_region': aliyun_region, 'aws_region': aws_region, 'errors': errors})


@csrf_exempt
@dashboard_login_required
@require_POST
def sync_cloud_plans(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    before_pricing_count = ServerPrice.objects.filter(is_active=True).count()
    before_regions = list(
        ServerPrice.objects.filter(is_active=True)
        .values('provider', 'region_code', 'region_name')
        .distinct()
        .order_by('provider', 'region_code')
    )
    try:
        async_to_sync(ensure_cloud_server_plans)()
    except Exception as exc:
        return _error(f'同步价格配置失败: {exc}', status=500)
    active_pricing_queryset = ServerPrice.objects.filter(is_active=True)
    after_pricing_count = active_pricing_queryset.count()
    after_regions = list(
        active_pricing_queryset
        .values('provider', 'region_code', 'region_name')
        .distinct()
        .order_by('provider', 'region_code')
    )
    provider_region_summary = list(
        active_pricing_queryset
        .values('provider', 'region_code', 'region_name')
        .annotate(pricing_count=Count('id'))
        .order_by('provider', 'region_code')
    )
    return _ok({
        'synced': True,
        'refreshed_regions': len(after_regions),
        'summary': {
            'before_plan_count': 0,
            'after_plan_count': 0,
            'before_pricing_count': before_pricing_count,
            'after_pricing_count': after_pricing_count,
            'region_count': len(after_regions),
        },
        'regions': after_regions,
        'before_regions': before_regions,
        'provider_region_summary': provider_region_summary,
    })


@csrf_exempt
@dashboard_login_required
@require_http_methods(['POST'])
def create_cloud_plan(request):
    denied = _require_superuser(request)
    if denied:
        return denied
    data = _read_payload(request)
    provider = (data.get('provider') or '').strip()
    region_code = (data.get('region_code') or '').strip()
    region_name = (data.get('region_name') or '').strip()
    plan_name = (data.get('plan_name') or '').strip()
    if not provider or not region_code or not region_name or not plan_name:
        return _error('云厂商、地区代码、地区名称、套餐名不能为空')
    try:
        plan = CloudServerPlan.objects.create(
            provider=provider,
            region_code=region_code,
            region_name=region_name,
            plan_name=plan_name,
            plan_description=(data.get('plan_description') or '').strip(),
            cpu=(data.get('cpu') or '').strip(),
            memory=(data.get('memory') or '').strip(),
            storage=(data.get('storage') or '').strip(),
            bandwidth=(data.get('bandwidth') or '').strip(),
            cost_price=_parse_decimal(data.get('cost_price') or 0, '进货价').quantize(Decimal('0.01')),
            price=_parse_decimal(data.get('price') or 0, '出售价').quantize(Decimal('0.01')),
            currency=(data.get('currency') or 'USDT').strip() or 'USDT',
            sort_order=int(data.get('sort_order') or 0),
            is_active=str(data.get('is_active', True)).lower() in {'1', 'true', 'yes', 'on'},
        )
    except IntegrityError:
        return _error('同地区下已存在同名套餐', status=400)
    except (InvalidOperation, TypeError, ValueError):
        return _error('提交的套餐数据格式不正确', status=400)
    async_to_sync(refresh_custom_plan_cache)()
    return _ok(_cloud_plan_payload(plan))


@csrf_exempt
@dashboard_login_required
@require_http_methods(['POST'])
def delete_cloud_plan(request, plan_id: int):
    denied = _require_superuser(request)
    if denied:
        return denied
    plan = CloudServerPlan.objects.filter(id=plan_id).first()
    if not plan:
        return _error('套餐不存在', status=404)
    if CloudServerOrder.objects.filter(plan_id=plan_id).exists():
        return _error('该套餐已有订单引用，无法删除，请改为停用', status=400)
    plan.delete()
    async_to_sync(refresh_custom_plan_cache)()
    return _ok({'id': plan_id, 'deleted': True})


@csrf_exempt
@dashboard_login_required
@require_http_methods(['POST'])
def update_cloud_plan(request, plan_id: int):
    denied = _require_superuser(request)
    if denied:
        return denied
    plan = CloudServerPlan.objects.filter(id=plan_id).first()
    if not plan:
        return _error('套餐不存在', status=404)
    data = request.POST or request.GET
    plan_name = (data.get('plan_name') or '').strip()
    plan_description = (data.get('plan_description') or '').strip()
    price = data.get('price')
    cost_price = data.get('cost_price')
    sort_order = data.get('sort_order')
    is_active = data.get('is_active')
    try:
        if plan_name:
            plan.plan_name = plan_name
        if 'provider' in data:
            plan.provider = (data.get('provider') or '').strip() or plan.provider
        if 'region_code' in data:
            plan.region_code = (data.get('region_code') or '').strip() or plan.region_code
        if 'region_name' in data:
            plan.region_name = (data.get('region_name') or '').strip() or plan.region_name
        if 'cpu' in data:
            plan.cpu = (data.get('cpu') or '').strip()
        if 'memory' in data:
            plan.memory = (data.get('memory') or '').strip()
        if 'storage' in data:
            plan.storage = (data.get('storage') or '').strip()
        if 'bandwidth' in data:
            plan.bandwidth = (data.get('bandwidth') or '').strip()
        if 'currency' in data:
            plan.currency = (data.get('currency') or 'USDT').strip() or 'USDT'
        plan.plan_description = plan_description
        if price not in (None, ''):
            plan.price = Decimal(str(price))
        if cost_price not in (None, ''):
            plan.cost_price = Decimal(str(cost_price))
        if sort_order not in (None, ''):
            plan.sort_order = int(sort_order)
        if is_active not in (None, ''):
            plan.is_active = str(is_active).lower() in {'1', 'true', 'yes', 'on'}
        plan.save()
    except IntegrityError:
        return _error('同地区下已存在同名套餐', status=400)
    except (InvalidOperation, ValueError):
        return _error('提交的套餐数据格式不正确')
    async_to_sync(refresh_custom_plan_cache)()
    return _ok(_cloud_plan_payload(plan))
@dashboard_login_required
@require_GET
def recharges_list(request):
    keyword = _get_keyword(request)
    queryset = Recharge.objects.select_related('user').prefetch_related('user__telegramusernames').order_by('-created_at')
    queryset = _apply_keyword_filter(queryset, keyword, ['id', 'currency', 'status', 'tx_hash', 'user__tg_user_id', 'user__username'])
    items = [
        _recharge_detail_payload(item)
        for item in queryset[:50]
    ]
    return _ok(items)


@dashboard_login_required
@require_GET
def monitors_list(request):
    keyword = _get_keyword(request)
    queryset = AddressMonitor.objects.select_related('user').order_by('-created_at')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['address', 'remark', 'daily_income_currency', 'daily_expense_currency', 'user__tg_user_id', 'user__username'],
    )
    items = list(
        queryset[:100].values(
            'id', 'address', 'remark', 'monitor_transfers', 'monitor_resources',
            'daily_income', 'daily_expense', 'daily_income_currency', 'daily_expense_currency',
            'stats_date', 'is_active', 'created_at', 'resource_checked_at', 'user__tg_user_id', 'user__username'
        )
    )
    return _ok([
        {
            **item,
            'daily_income': _decimal_to_str(item['daily_income']),
            'daily_expense': _decimal_to_str(item['daily_expense']),
            'created_at': _iso(item['created_at']),
            'resource_checked_at': _iso(item['resource_checked_at']),
            'tg_user_id': item.pop('user__tg_user_id', None),
            'username': item.pop('user__username', None),
        }
        for item in items
    ])
