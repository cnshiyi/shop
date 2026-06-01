"""cloud 域后台 API。"""

import io
import logging
import uuid

from asgiref.sync import async_to_sync
from django.db.models import Case, CharField, Count, Value, When
from django.db.models.functions import Cast
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST, require_http_methods

from cloud.api_assets import (
    CloudAssetPayloadContext,
    _asset_payload,
    _build_cloud_asset_payload_context,
    _cloud_asset_payloads,
    _display_cloud_asset_note,
    _ensure_cloud_asset_dashboard_snapshots,
    _ensure_unattached_ip_expiry,
    _infer_asset_order,
    _parse_iso_datetime,
    _resolve_telegram_user,
    _sync_telegram_username,
    cloud_assets_list,
    cloud_assets_risk_summary,
    refresh_cloud_asset_dashboard_snapshots,
    toggle_cloud_asset_auto_renew,
    update_cloud_asset,
)
from cloud.api_monitors import (
    _fetch_address_chain_balances,
    cloud_ip_logs_list,
    monitors_list,
)
from cloud.api_orders import (
    _apply_cloud_order_status,
    _cloud_order_detail_payload,
    _cloud_order_source_tags,
    cloud_order_detail,
    cloud_orders_list,
    delete_cloud_order,
    update_cloud_order_status,
)
from cloud.api_tasks import (
    _get_due_orders,
    _run_auto_renew,
    _sync_auto_renew_plan_table,
    _sync_notice_plan_table,
    auto_renew_task_detail,
    delete_notice_history,
    notice_task_detail,
    refresh_notice_plan_table,
    run_auto_renew_order,
    run_auto_renew_tasks,
    tasks_overview,
    update_notice_plan_text,
    update_notice_switches,
)
from cloud.dashboard_snapshots import _refresh_dashboard_plan_snapshots, _refresh_dashboard_plan_snapshots_deferred
from cloud.lifecycle import _delete_instance, _mark_replaced_order_deleted
from cloud.models import CloudAsset, CloudIpLog, CloudServerPlan, ServerPrice
from cloud.note_utils import append_note
from cloud.provisioning import provision_cloud_server
from cloud.services import ensure_cloud_server_pricing, record_cloud_ip_log
from cloud.sync_jobs import (
    _active_sync_accounts,
    _asset_retained_static_ip_sync_scope,
    _call_command_capture,
    _cloud_asset_sync_job_payload,
    _execute_cloud_asset_sync_job,
    _heartbeat_sync_job,
    _log_sync_command_output,
    _record_dashboard_sync_log,
    _record_sync_job_event,
    _resolve_sync_account_for_asset,
    _sync_account_payload,
    _sync_log_tail,
    _sync_log_text,
    _sync_provider_for_asset,
    cancel_cloud_asset_sync_job,
    cloud_asset_sync_jobs_metrics,
    cloud_asset_sync_job_detail,
    cloud_asset_sync_jobs_list,
    cloud_assets_sync_status,
    retry_cloud_asset_sync_job,
    sync_cloud_assets,
)
from cloud.task_center import task_center_overview
from core.cloud_accounts import cloud_account_label_variants
from core.dashboard_api import _error, _ok, _read_payload, dashboard_superuser_required
from core.models import CloudAccountConfig

logger = logging.getLogger(__name__)

# 功能：删除或标记删除相关业务对象；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_superuser_required
@require_http_methods(['POST', 'DELETE'])
def delete_cloud_asset(request, asset_id: int):
    asset = CloudAsset.objects.select_related('order').filter(id=asset_id).first()
    if not asset:
        return _error('代理记录不存在', status=404)
    now = timezone.now()
    before_status = asset.status
    note = f'后台手动删除代理列表记录；时间: {now.isoformat()}'
    previous_public_ip = asset.public_ip or asset.previous_public_ip
    order = asset.order

    # 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
    def _clear_order_cloud_binding(target_order):
        if not target_order:
            return False
        target_order.server_name = ''
        target_order.instance_id = ''
        target_order.provider_resource_id = ''
        target_order.public_ip = None
        target_order.previous_public_ip = None
        target_order.static_ip_name = ''
        target_order.mtproxy_host = ''
        target_order.mtproxy_port = 0
        target_order.mtproxy_secret = ''
        target_order.mtproxy_link = ''
        target_order.proxy_links = []
        target_order.login_user = ''
        target_order.login_password = ''
        target_order.provision_note = append_note(
            target_order.provision_note,
            f'后台代理列表删除已清除云资源绑定；原IP={previous_public_ip or "-"}；后续云同步按全新资源处理，不再继承本订单状态；时间: {now.isoformat()}。',
        )
        target_order.save(update_fields=[
            'server_name', 'instance_id', 'provider_resource_id', 'public_ip', 'previous_public_ip',
            'static_ip_name', 'mtproxy_host', 'mtproxy_port', 'mtproxy_secret', 'mtproxy_link',
            'proxy_links', 'login_user', 'login_password', 'provision_note', 'updated_at',
        ])
        return True

    if not CloudIpLog.objects.filter(asset_id=asset.id, event_type=CloudIpLog.EVENT_DELETED, note__contains='后台手动删除代理列表记录').exists():
        record_cloud_ip_log(event_type=CloudIpLog.EVENT_DELETED, order=order, asset=asset, previous_public_ip=previous_public_ip, public_ip=None, note=note)
    order_status_changed = _clear_order_cloud_binding(order)
    asset.delete()
    logger.info(
        'DASHBOARD_CLOUD_ASSET_DELETED asset_id=%s order_id=%s before_status=%s previous_public_ip=%s order_binding_cleared=%s actor_id=%s',
        asset_id,
        getattr(order, 'id', None),
        before_status,
        previous_public_ip,
        order_status_changed,
        getattr(request.user, 'id', None),
    )
    return _ok({
        'target_type': 'cloud_asset',
        'target_id': asset_id,
        'before_status': before_status,
        'after_status': None,
        'hard_deleted': True,
        'exists_after': CloudAsset.objects.filter(id=asset_id).exists(),
        'removed_servers': 0,
        'removed_server_ids': [],
        'order_status_changed': order_status_changed,
    })


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _apply_server_missing_state(provider, region, existing_instance_ids, account=None):
    now = timezone.now()
    queryset = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, provider=provider, region_code=region).exclude(instance_id__isnull=True).exclude(instance_id='')
    if account:
        queryset = queryset.filter(account_label__in=cloud_account_label_variants(account))
    legacy_queryset = queryset.filter(provider_status='missing')
    legacy_updated = legacy_queryset.update(
        status=CloudAsset.STATUS_DELETED,
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
    logger.info(
        'DASHBOARD_SYNC_SERVERS_MISSING_STATE_SKIPPED provider=%s region=%s account_id=%s synced_instance_count=%s reason=managed_by_provider_sync_confirmation',
        provider,
        region,
        getattr(account, 'id', None),
        len([item for item in existing_instance_ids or [] if item]),
    )
    return legacy_updated


# 功能：同步外部或派生数据；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_superuser_required
@require_POST
def sync_servers(request):
    payload = _read_payload(request)
    aliyun_region = (payload.get('region') or request.POST.get('region') or request.GET.get('region') or 'cn-hongkong').strip() or 'cn-hongkong'
    aws_region = (payload.get('aws_region') or request.POST.get('aws_region') or request.GET.get('aws_region') or '').strip()
    if aws_region.lower() == 'all':
        aws_region = ''
    cancelled = False
    errors = []
    synced = {'aliyun': False, 'aws': False}
    missing = {'aliyun': 0, 'aws': 0}
    aws_regions = []
    command_output = io.StringIO()
    aliyun_accounts = _active_sync_accounts(CloudAccountConfig.PROVIDER_ALIYUN)
    aws_accounts = _active_sync_accounts(CloudAccountConfig.PROVIDER_AWS)
    aws_command = None
    warnings = []
    logger.info(
        'DASHBOARD_SYNC_SERVERS_START aliyun_region=%s aws_region=%s aliyun_account_count=%s aws_account_count=%s actor_id=%s',
        aliyun_region,
        aws_region or 'all',
        len(aliyun_accounts),
        len(aws_accounts),
        getattr(request.user, 'id', None),
    )
    for aliyun_account in aliyun_accounts:
        try:
            aliyun_command, _ = _call_command_capture('sync_aliyun_assets', region=aliyun_region, account_id=str(aliyun_account.id), stdout=command_output)
            synced['aliyun'] = True
            missing['aliyun'] += _apply_server_missing_state('aliyun_simple', aliyun_region, getattr(aliyun_command, 'synced_instance_ids', None) or [], aliyun_account)
        except Exception as exc:
            message = f'阿里云账号#{getattr(aliyun_account, "id", "-")}同步失败: {exc}'
            errors.append(message)
            logger.exception('DASHBOARD_SYNC_SERVERS_ALIYUN_FAILED account_id=%s region=%s', getattr(aliyun_account, 'id', None), aliyun_region)
    for aws_account in aws_accounts:
        try:
            if aws_region:
                aws_command, _ = _call_command_capture('sync_aws_assets', region=aws_region, account_id=str(aws_account.id), stdout=command_output)
                account_regions = [aws_region]
            else:
                aws_command, _ = _call_command_capture('sync_aws_assets', account_id=str(aws_account.id), stdout=command_output)
                account_regions = getattr(aws_command, 'synced_regions', None) or []
            aws_regions.extend(region for region in account_regions if region not in aws_regions)
            synced['aws'] = True
            warnings.extend(getattr(aws_command, 'sync_errors', []) or [])
            synced_map = getattr(aws_command, 'synced_instance_ids_by_region', None) or {}
            missing['aws'] += sum(
                _apply_server_missing_state('aws_lightsail', region, synced_map.get(region, []), aws_account)
                for region in account_regions
            )
        except Exception as exc:
            message = f'AWS账号#{getattr(aws_account, "id", "-")}同步失败: {exc}'
            errors.append(message)
            logger.exception('DASHBOARD_SYNC_SERVERS_AWS_FAILED account_id=%s region=%s', getattr(aws_account, 'id', None), aws_region or 'all')
    ok = (not cancelled) and (not errors or synced['aliyun'] or synced['aws'])
    response_payload = {'ok': ok, 'synced': synced, 'missing': missing, 'aliyun_region': aliyun_region, 'aws_region': aws_region or 'all', 'aws_regions': aws_regions, 'errors': errors, 'warnings': warnings[:50], 'logs': _sync_log_tail(command_output), 'accounts': {'aliyun': [_sync_account_payload(account) for account in aliyun_accounts], 'aws': [_sync_account_payload(account) for account in aws_accounts]}}
    _record_dashboard_sync_log(
        action='sync_servers',
        target=f'aliyun:{aliyun_region};aws:{aws_region or "all"}',
        request_payload={'aliyun_region': aliyun_region, 'aws_region': aws_region or 'all'},
        response_payload={**response_payload, 'log_text': _sync_log_text(command_output)},
        is_success=ok,
        error_message='; '.join(errors[:10]),
    )
    logger.info(
        'DASHBOARD_SYNC_SERVERS_DONE ok=%s aliyun_synced=%s aws_synced=%s aliyun_missing=%s aws_missing=%s aws_regions=%s error_count=%s warning_count=%s',
        ok,
        synced['aliyun'],
        synced['aws'],
        missing['aliyun'],
        missing['aws'],
        aws_regions,
        len(errors),
        len(warnings),
    )
    return _ok(response_payload)


# 功能：同步外部或派生数据；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_superuser_required
@require_POST
def sync_cloud_asset_status(request, asset_id):
    sync_run_id = uuid.uuid4().hex
    asset = CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'telegram_group').filter(pk=asset_id).first()
    if not asset:
        return _error('云资产不存在', status=404)
    provider = _sync_provider_for_asset(asset)
    if not provider:
        return _error('当前资产暂不支持单条状态更新', status=400)
    account = _resolve_sync_account_for_asset(asset)
    if not account:
        return _error('未找到可用的云账号配置，请先检查该代理绑定的云账号是否启用', status=400)

    region_code = str(getattr(asset, 'region_code', '') or getattr(account, 'region_hint', '') or '').strip()
    retained_scope = _asset_retained_static_ip_sync_scope(asset) if provider == CloudAccountConfig.PROVIDER_AWS else None
    scope_instance_id = (
        (retained_scope or {}).get('instance_id')
        if retained_scope is not None
        else (asset.instance_id or asset.provider_resource_id or asset.asset_name or '')
    )
    scope_public_ip = (retained_scope or {}).get('public_ip') or asset.public_ip or asset.previous_public_ip or ''
    command_output = io.StringIO()
    errors = []
    command_name = 'sync_aws_assets' if provider == CloudAccountConfig.PROVIDER_AWS else 'sync_aliyun_assets'
    request_payload = {
        'asset_id': asset.id,
        'provider': provider,
        'region_code': region_code or 'all',
        'account_id': account.id,
        'instance_id': scope_instance_id,
        'public_ip': scope_public_ip,
    }
    logger.info('CLOUD_SYNC_SINGLE_REQUEST_START run_id=%s payload=%s', sync_run_id, request_payload)
    try:
        command_kwargs = {'account_id': str(account.id), 'stdout': command_output}
        if region_code:
            command_kwargs['region'] = region_code
        command_kwargs.update({
            'asset_id': str(asset.id),
            'instance_id': scope_instance_id,
            'public_ip': scope_public_ip,
        })
        _call_command_capture(command_name, **command_kwargs)
        logger.info('CLOUD_SYNC_SINGLE_REQUEST_DONE run_id=%s asset_id=%s command=%s kwargs=%s', sync_run_id, asset.id, command_name, {key: value for key, value in command_kwargs.items() if key != 'stdout'})
    except Exception as exc:
        errors.append(str(exc))
        logger.exception(
            'DASHBOARD_SYNC_SINGLE_ASSET_FAILED run_id=%s asset_id=%s provider=%s region=%s account_id=%s kwargs=%s',
            sync_run_id,
            asset.id,
            provider,
            region_code or 'all',
            getattr(account, 'id', None),
            {key: value for key, value in command_kwargs.items() if key != 'stdout'},
        )
        _log_sync_command_output(f'CLOUD_SYNC_SINGLE_FAILED_LOG run_id={sync_run_id} command={command_name}', _sync_log_text(command_output), level=logging.ERROR)

    refreshed = CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'telegram_group').filter(pk=asset_id).first()
    response_payload = {
        'ok': not errors,
        'asset': _asset_payload(refreshed) if refreshed else None,
        'provider': provider,
        'region_code': region_code or 'all',
        'account': _sync_account_payload(account),
        'errors': errors,
        'logs': _sync_log_tail(command_output),
        'scope': {
            'asset_id': asset.id,
            'instance_id': scope_instance_id,
            'public_ip': scope_public_ip,
        },
    }
    if not errors:
        _refresh_dashboard_plan_snapshots_deferred(f'cloud_asset_sync:{asset.id}', cloud_asset_ids=[asset.id])
    _log_sync_command_output(f'CLOUD_SYNC_SINGLE_REQUEST_LOG run_id={sync_run_id} command={command_name}', _sync_log_text(command_output))
    _record_dashboard_sync_log(
        action='sync_cloud_asset_status',
        target=f'asset:{asset.id}',
        request_payload=request_payload,
        response_payload={**response_payload, 'log_text': _sync_log_text(command_output)},
        is_success=not errors,
        error_message='; '.join(errors[:10]),
    )
    return _ok(response_payload)


# 功能：同步外部或派生数据；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_superuser_required
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
        async_to_sync(ensure_cloud_server_pricing)()
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
            'before_plan_count': CloudServerPlan.objects.filter(is_active=True).count(),
            'after_plan_count': CloudServerPlan.objects.filter(is_active=True).count(),
            'before_pricing_count': before_pricing_count,
            'after_pricing_count': after_pricing_count,
            'region_count': len(after_regions),
        },
        'regions': after_regions,
        'before_regions': before_regions,
        'provider_region_summary': provider_region_summary,
    })


from cloud.api_servers import (  # noqa: E402
    delete_server,
    rebuild_server_preserve_link,
    servers_list,
    servers_statistics,
)
from cloud.api_plans import (  # noqa: E402
    _cloud_plan_payload,
    _resolve_cloud_plan_config_id,
    _server_price_payload,
    cloud_plans_list,
    cloud_pricing_list,
    create_cloud_plan,
    delete_cloud_plan,
    update_cloud_plan,
)


__all__ = [
    'cloud_assets_list',
    'cloud_assets_risk_summary',
    'cloud_asset_sync_jobs_list',
    'cloud_asset_sync_jobs_metrics',
    'cloud_asset_sync_job_detail',
    'cancel_cloud_asset_sync_job',
    'retry_cloud_asset_sync_job',
    'cloud_assets_sync_status',
    'sync_cloud_asset_status',
    'cloud_ip_logs_list',
    'cloud_order_detail',
    'cloud_orders_list',
    'delete_cloud_order',
    'notice_task_detail',
    'update_notice_plan_text',
    'delete_notice_history',
    'update_notice_switches',
    'auto_renew_task_detail',
    'run_auto_renew_order',
    'run_auto_renew_tasks',
    'tasks_overview',
    'task_center_overview',
    'cloud_plans_list',
    'cloud_pricing_list',
    'create_cloud_plan',
    'delete_cloud_plan',
    'delete_server',
    'monitors_list',
    'servers_list',
    'servers_statistics',
    'sync_cloud_assets',
    'sync_cloud_plans',
    'sync_servers',
    'update_cloud_asset',
    'update_cloud_order_status',
    'update_cloud_plan',
]
