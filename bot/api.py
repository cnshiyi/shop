"""bot 域后台 API。"""

import os

from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from bot.models import TelegramUser
from cloud.models import AddressMonitor, CloudServerOrder
from core.models import CloudAccountConfig, SiteConfig
from dashboard_api.views import (
    CONFIG_HELP,
    SENSITIVE_CONFIG_KEYS,
    _cloud_account_payload,
    _decimal_to_str,
    _error,
    _iso,
    _ok,
    _read_payload,
    _region_label,
    _site_config_payload,
    _status_label,
    create_product,
    csrf,
    dashboard_login_required,
    products_list,
    update_product,
    update_user_balance,
    update_user_discount,
    user_balance_details,
    users_list,
)
from orders.models import Order, Product, Recharge


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
