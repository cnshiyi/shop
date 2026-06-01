"""Dashboard API views for cloud account management."""

from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from core.dashboard_api import (
    _error,
    _iso,
    _ok,
    _read_payload,
    dashboard_login_required,
    dashboard_superuser_required,
)
from cloud.models import CloudAsset, CloudServerOrder
from core.models import CloudAccountConfig


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
