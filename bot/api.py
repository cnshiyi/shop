"""bot 域后台 API。"""

import os
from decimal import Decimal, InvalidOperation

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.db import ProgrammingError, transaction
from django.db.models import Q, CharField
from django.db.models.functions import Cast
from django.http import JsonResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from bot.models import TelegramUser
from cloud.models import AddressMonitor, CloudAsset, CloudServerOrder
from core.models import CloudAccountConfig, SiteConfig
from core.runtime_config import CONFIG_HELP, SENSITIVE_CONFIG_KEYS
from orders.models import BalanceLedger, Order, Product, Recharge


@ensure_csrf_cookie
@require_GET
def csrf(request):
    return _ok({'csrf': True})


def _decimal_to_str(value, places=None):
    if value is None:
        value = Decimal('0')
    elif not isinstance(value, Decimal):
        value = Decimal(str(value))
    if places is not None:
        quantizer = Decimal('1').scaleb(-places)
        value = value.quantize(quantizer)
    return format(value, 'f')


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


def _authenticate_dashboard_request(request):
    if getattr(request, 'user', None) and request.user.is_authenticated:
        return request.user
    auth_header = request.headers.get('Authorization') or ''
    prefix = 'Bearer session-'
    if not auth_header.startswith(prefix):
        return None
    raw_user_id = auth_header[len(prefix):].strip()
    if not raw_user_id.isdigit():
        return None
    from django.contrib.auth import get_user_model

    user = get_user_model().objects.filter(pk=int(raw_user_id), is_active=True).first()
    if user:
        request.user = user
    return user


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


def _parse_decimal(value, field_label):
    raw = str(value or '').strip()
    if raw == '':
        raise ValueError(f'{field_label}不能为空')
    try:
        return Decimal(raw)
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError(f'{field_label}格式不正确')


def _site_config_payload(item):
    return {
        'id': item.id,
        'key': item.key,
        'value': SiteConfig.get(item.key, ''),
        'value_preview': item.masked_value() if item.is_sensitive else (item.value or ''),
        'is_sensitive': item.is_sensitive,
        'description': CONFIG_HELP.get(item.key, ''),
    }


def _cloud_account_payload(item):
    return {
        'id': item.id,
        'provider': item.provider,
        'provider_label': item.get_provider_display(),
        'name': item.name,
        'access_key': item.access_key_plain,
        'secret_key': item.secret_key_plain,
        'region_hint': item.region_hint,
        'is_active': item.is_active,
        'status': item.status,
        'status_label': item.status_label,
        'status_note': item.status_note,
        'last_checked_at': _iso(item.last_checked_at),
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


def _days_left(value):
    if not value:
        return None
    delta = value - timezone.now()
    return delta.days if delta.days >= 0 else 0


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


@csrf_exempt
@require_POST
def auth_login(request):
    username = request.POST.get('username') or request.headers.get('x-username')
    password = request.POST.get('password') or request.headers.get('x-password')

    if not username or not password:
        try:
            import json
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

    login(request, user)
    return _ok({'accessToken': f'session-{user.pk}'})


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
    return _ok(f'session-{request.user.pk}')


@dashboard_login_required
@require_GET
def auth_codes(request):
    return _ok(['dashboard', 'users', 'cloud', 'finance', 'monitoring', 'settings'])


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
        'homePath': '/admin/workspace',
        'token': f'session-{request.user.pk}',
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
    queryset = SiteConfig.objects.order_by('key')
    return _ok([_site_config_payload(item) for item in queryset])


@dashboard_login_required
@require_GET
def site_config_groups(request):
    groups = {
        'database': ['database_url', 'db_host', 'db_port', 'db_name', 'db_user', 'db_password'],
        'tron': ['receive_address', 'trongrid_api_key'],
        'aws': ['aws_access_key_id', 'aws_secret_access_key', 'aws_region'],
        'aliyun': ['alibaba_cloud_account_id', 'aliyun_account_id', 'aliyun_region'],
        'bot': ['bot_token', 'telegram_webhook_url'],
        'runtime': ['redis_url', 'm_account_token', 'admin_password_notice'],
        'custom_text': [
            'bot_custom_quantity_title', 'bot_custom_quantity_hint', 'bot_custom_payment_title',
            'bot_custom_payment_hint', 'bot_custom_wallet_title', 'bot_custom_pending_order',
            'bot_custom_pending_wallet', 'bot_custom_order_notice', 'bot_custom_port_hint',
            'bot_custom_balance_insufficient',
        ],
    }
    existing = {item.key: item for item in SiteConfig.objects.all()}
    payload = []
    for group_key, keys in groups.items():
        items = []
        for key in keys:
            obj = existing.get(key)
            items.append({
                'key': key,
                'id': obj.id if obj else None,
                'value': SiteConfig.get(key, ''),
                'is_sensitive': bool(getattr(obj, 'is_sensitive', key in SENSITIVE_CONFIG_KEYS)),
                'description': CONFIG_HELP.get(key, ''),
            })
        payload.append({'group': group_key, 'items': items})
    return _ok(payload)


@csrf_exempt
@dashboard_login_required
@require_POST
def init_site_configs(request):
    created = 0
    for key in CONFIG_HELP:
        _, was_created = SiteConfig.objects.get_or_create(
            key=key,
            defaults={'value': '', 'is_sensitive': key in SENSITIVE_CONFIG_KEYS},
        )
        created += int(was_created)
    return _ok({'created': created})


@csrf_exempt
@dashboard_login_required
@require_POST
def update_site_config(request, config_id: int):
    item = SiteConfig.objects.filter(id=config_id).first()
    if not item:
        return _error('配置不存在', status=404)
    data = request.POST or request.GET
    item.is_sensitive = str(data.get('is_sensitive', item.is_sensitive)).lower() in {'1', 'true', 'yes', 'on'}
    value = data.get('value')
    SiteConfig.set(item.key, '' if value is None else str(value), sensitive=item.is_sensitive)
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
@require_POST
def update_cloud_account(request, account_id: int):
    item = CloudAccountConfig.objects.filter(id=account_id).first()
    if not item:
        return _error('云账号不存在', status=404)
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
    'auth_logout',
    'auth_refresh',
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
