from decimal import Decimal, InvalidOperation

from asgiref.sync import async_to_sync
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import AnonymousUser
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.management import call_command
from django.db import ProgrammingError, transaction
from django.db.models import BooleanField, Case, CharField, Count, Q, Value, When
from django.db.models.functions import Cast
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from core.runtime_config import CONFIG_HELP, SENSITIVE_CONFIG_KEYS
from core.models import CloudAccountConfig, SiteConfig
from bot.models import TelegramUser
from cloud.models import AddressMonitor, CloudAsset, CloudServerOrder, CloudServerPlan, Server, ServerPrice
from cloud.provisioning import provision_cloud_server
from cloud.services import ensure_cloud_server_plans, refresh_custom_plan_cache
from orders.models import BalanceLedger, Order, Product, Recharge


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


def _get_keyword(request):
    return (request.GET.get('keyword') or request.GET.get('q') or request.GET.get('search') or '').strip()


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


@login_required
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


@login_required
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
            queryset = _apply_keyword_filter(
                queryset,
                keyword,
                ['username', 'first_name'],
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


@login_required
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


@login_required
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


@login_required
@require_GET
def orders_list(request):
    keyword = _get_keyword(request)
    queryset = Order.objects.select_related('user', 'product').order_by('-created_at')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['order_no', 'product_name', 'status', 'tx_hash', 'user__tg_user_id', 'user__username', 'product__name'],
    )
    items = [_order_payload(item) for item in queryset[:100]]
    return _ok(items)


@login_required
@require_GET
def cloud_orders_list(request):
    keyword = _get_keyword(request)
    queryset = CloudServerOrder.objects.select_related('user', 'plan').order_by('-created_at')
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

        item['can_renew'] = item['renew_status'] != 'unpaid' and status not in {'cancelled', 'failed'}
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


def _resolve_telegram_user(value):
    raw = str(value or '').strip().lstrip('@')
    if not raw:
        return None
    queryset = TelegramUser.objects.all()
    if raw.isdigit():
        return queryset.filter(Q(id=int(raw)) | Q(tg_user_id=int(raw))).first()
    return queryset.filter(username__icontains=raw).first()


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


def _sync_telegram_username(user, username=None):
    incoming = _split_usernames(username)
    if not incoming:
        return

    merged = _merge_usernames(user.usernames, incoming)
    user.username = ','.join(merged)
    user.save(update_fields=['username', 'updated_at'])


@login_required
@require_GET
def cloud_assets_list(request):
    keyword = _get_keyword(request)
    grouped = (request.GET.get('grouped') or '').lower() in {'1', 'true', 'yes'}
    try:
        queryset = CloudAsset.objects.select_related('user', 'order')
        queryset = _apply_keyword_filter(
            queryset,
            keyword,
            [
                'asset_name', 'public_ip', 'mtproxy_link', 'user__tg_user_id',
                'user__username', 'order__order_no',
            ],
        ).distinct().order_by('actual_expires_at', '-updated_at', '-id')
        items = [_asset_payload(asset) for asset in queryset[:200]]
    except ProgrammingError:
        return _ok({'groups': [], 'items': []} if grouped else [])

    if not grouped:
        return _ok(items)

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
    return _ok({'groups': ordered_groups, 'items': items})


@csrf_exempt
@dashboard_login_required
@require_http_methods(['POST', 'PUT', 'PATCH'])
def update_cloud_asset(request, asset_id):
    payload = _read_payload(request)
    try:
        with transaction.atomic():
            asset = CloudAsset.objects.select_for_update().select_related('order', 'user').get(pk=asset_id)

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
                    asset.order.user = None
                    asset.order.last_user_id = None
                    asset.order.save(update_fields=['user', 'last_user_id', 'updated_at'])
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

    asset = CloudAsset.objects.select_related('user', 'order').get(pk=asset_id)
    return _ok(_asset_payload(asset))


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


def _cloud_order_detail_payload(order):
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
        'login_password': order.login_password,
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
def _apply_recharge_status(recharge, new_status):
    now = timezone.now()
    old_status = recharge.status
    allowed_statuses = {choice[0] for choice in Recharge.STATUS_CHOICES}
    if new_status not in allowed_statuses:
        raise ValueError('充值订单状态不正确')
    if new_status == old_status:
        return recharge

    user = recharge.user
    balance_field = 'balance_trx' if recharge.currency == 'TRX' else 'balance'

    if old_status == 'completed' and new_status != 'completed':
        current_balance = getattr(user, balance_field)
        if current_balance < recharge.amount:
            raise ValueError('用户余额不足，无法从已完成回退状态')
        setattr(user, balance_field, current_balance - recharge.amount)
        user.save(update_fields=[balance_field, 'updated_at'])
        recharge.completed_at = None

    if new_status == 'completed' and old_status != 'completed':
        setattr(user, balance_field, getattr(user, balance_field) + recharge.amount)
        user.save(update_fields=[balance_field, 'updated_at'])
        recharge.completed_at = recharge.completed_at or now
    elif new_status in {'pending', 'expired'}:
        recharge.completed_at = None

    recharge.status = new_status
    recharge.save(update_fields=['status', 'completed_at'])
    return recharge


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


@transaction.atomic
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

    order.status = new_status
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

    if trigger_provision:
        async_to_sync(provision_cloud_server)(order.id)
        order.refresh_from_db()

    return order


@dashboard_login_required
@require_GET
def cloud_order_detail(request, order_id):
    order = CloudServerOrder.objects.select_related('user', 'plan').filter(pk=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    return _ok(_cloud_order_detail_payload(order))


@csrf_exempt
@dashboard_login_required
@require_POST
def update_cloud_order_status(request, order_id):
    order = CloudServerOrder.objects.select_related('user', 'plan').filter(pk=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    payload = _read_payload(request)
    new_status = str(payload.get('status') or '').strip()
    if not new_status:
        return _error('订单状态不能为空')
    try:
        order = _apply_cloud_order_status(order, new_status)
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f'更新订单状态失败: {exc}', status=500)
    return _ok(_cloud_order_detail_payload(order))


@dashboard_login_required
@require_GET
def recharge_detail(request, recharge_id):
    recharge = Recharge.objects.select_related('user').filter(pk=recharge_id).first()
    if not recharge:
        return _error('充值订单不存在', status=404)
    return _ok(_recharge_detail_payload(recharge))


@csrf_exempt
@dashboard_login_required
@require_POST
def update_recharge_status(request, recharge_id):
    recharge = Recharge.objects.select_related('user').filter(pk=recharge_id).first()
    if not recharge:
        return _error('充值订单不存在', status=404)
    payload = _read_payload(request)
    new_status = str(payload.get('status') or '').strip()
    if not new_status:
        return _error('充值订单状态不能为空')
    try:
        recharge = _apply_recharge_status(recharge, new_status)
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f'更新充值订单状态失败: {exc}', status=500)
    return _ok(_recharge_detail_payload(recharge))


@login_required
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
    aliyun_region = (request.POST.get('region') or request.GET.get('region') or 'cn-hongkong').strip() or 'cn-hongkong'
    aws_region = (request.POST.get('aws_region') or request.GET.get('aws_region') or 'ap-southeast-1').strip() or 'ap-southeast-1'
    errors = []
    synced = {'aliyun': False, 'aws': False}
    try:
        call_command('sync_aliyun_assets', region=aliyun_region)
        synced['aliyun'] = True
    except Exception as exc:
        errors.append(f'阿里云代理同步失败: {exc}')
    try:
        call_command('sync_aws_assets', region=aws_region)
        synced['aws'] = True
    except Exception as exc:
        errors.append(f'AWS 代理同步失败: {exc}')
    if errors and not any(synced.values()):
        return _error('；'.join(errors), status=500)
    return _ok({'synced': synced, 'aliyun_region': aliyun_region, 'aws_region': aws_region, 'errors': errors})


@login_required
@require_GET
def servers_list(request):
    keyword = _get_keyword(request)
    dedup_raw = (request.GET.get('dedup') or '').lower()
    dedup = dedup_raw not in {'0', 'false', 'no', 'off'}
    queryset = Server.objects.select_related('user', 'order').order_by('expires_at', '-updated_at', '-id')
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
@login_required
@require_GET
def recharges_list(request):
    keyword = _get_keyword(request)
    queryset = Recharge.objects.select_related('user').order_by('-created_at')
    queryset = _apply_keyword_filter(queryset, keyword, ['id', 'currency', 'status', 'tx_hash', 'user__tg_user_id', 'user__username'])
    items = [
        _recharge_detail_payload(item)
        for item in queryset[:50]
    ]
    return _ok(items)


@login_required
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
