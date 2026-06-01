"""云资产同步任务运行时与后台 API。"""

import io
import json
import logging
import re
import uuid
from datetime import timedelta

from django.core.cache import cache
from django.core.management import get_commands, load_command_class
from django.db import close_old_connections
from django.db.models import Count, F, Q
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from cloud.dashboard_snapshots import _refresh_dashboard_plan_snapshots_deferred
from cloud.models import CloudAsset, CloudAssetSyncJob, CloudAssetSyncJobEvent
from core.cloud_accounts import cloud_account_label, list_cloud_account_labels
from core.dashboard_api import _error, _iso, _ok, _read_payload, dashboard_login_required, dashboard_superuser_required
from core.models import CloudAccountConfig, ExternalSyncLog
from core.persistence import record_external_sync_log
from core.runtime_config import get_cloud_asset_sync_interval_seconds

logger = logging.getLogger(__name__)
_SYNC_CONSOLE_LOG_MAX_CHARS = 50000


# 类型说明：封装 云资产、云订单和生命周期 中 CapturedCommandError 相关的数据和行为。
class CapturedCommandError(RuntimeError):
    # 功能：初始化对象状态和依赖。
    def __init__(self, command_name: str, log_text: str, original_error: Exception):
        super().__init__(str(original_error))
        self.command_name = command_name
        self.log_text = log_text
        self.original_error = original_error


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _trim_sync_console_log(log_text: str, *, limit: int = _SYNC_CONSOLE_LOG_MAX_CHARS) -> str:
    text = str(log_text or '').strip()
    if len(text) <= limit:
        return text
    return f'{text[-limit:]}\n... 同步日志过长，控制台仅显示最后 {limit} 字符'


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _log_sync_command_output(tag: str, log_text: str, *, level: int = logging.INFO):
    text = _trim_sync_console_log(log_text)
    if text:
        logger.log(level, '%s\n%s', tag, text)

# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _active_sync_accounts(provider: str):
    return list(CloudAccountConfig.objects.filter(provider=provider, is_active=True).order_by('id'))


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _sync_account_payload(account):
    if not account:
        return None
    return {
        'id': account.id,
        'provider': account.provider,
        'name': account.name,
        'label': cloud_account_label(account),
    }


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _sync_log_tail(output: io.StringIO, limit: int = 80) -> list[str]:
    lines = [line.strip() for line in output.getvalue().splitlines() if line.strip()]
    return lines[-limit:]


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _sync_log_text(output: io.StringIO, limit: int = 200) -> str:
    return '\n'.join(_sync_log_tail(output, limit=limit))


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _record_dashboard_sync_log(*, action: str, target: str, request_payload: dict, response_payload: dict, is_success: bool, error_message: str = ''):
    try:
        record_external_sync_log(
            source=ExternalSyncLog.SOURCE_DASHBOARD,
            action=action,
            target=target,
            request_payload=request_payload,
            response_payload=response_payload,
            is_success=is_success,
            error_message=error_message,
        )
    except Exception:
        logger.exception('DASHBOARD_SYNC_LOG_RECORD_FAILED action=%s target=%s', action, target)


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _sync_job_event_payload(event: CloudAssetSyncJobEvent) -> dict:
    return {
        'id': event.id,
        'event_type': event.event_type,
        'event_type_label': dict(CloudAssetSyncJobEvent.TYPE_CHOICES).get(event.event_type, event.event_type),
        'status_from': event.status_from or '',
        'status_to': event.status_to or '',
        'message': event.message or '',
        'payload': event.payload or {},
        'worker_id': event.worker_id or '',
        'actor_id': event.actor_id,
        'created_at': _iso(event.created_at),
    }


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _record_sync_job_event(
    job_or_id,
    event_type: str,
    message: str = '',
    *,
    payload: dict | None = None,
    status_from: str = '',
    status_to: str = '',
    worker_id: str = '',
    actor=None,
    log_level: int = logging.INFO,
) -> CloudAssetSyncJobEvent | None:
    job_id = job_or_id.pk if isinstance(job_or_id, CloudAssetSyncJob) else int(job_or_id)
    payload = dict(payload or {})
    try:
        event = CloudAssetSyncJobEvent.objects.create(
            job_id=job_id,
            event_type=event_type,
            status_from=status_from or '',
            status_to=status_to or '',
            message=str(message or '')[:255],
            payload=payload,
            worker_id=str(worker_id or '')[:64],
            actor=actor if getattr(actor, 'is_authenticated', False) else None,
        )
        logger.log(
            log_level,
            'CLOUD_SYNC_JOB_EVENT job_id=%s event_type=%s status_from=%s status_to=%s worker_id=%s message=%s payload=%s',
            job_id,
            event_type,
            status_from or '',
            status_to or '',
            worker_id or '',
            message,
            payload,
        )
        return event
    except Exception:
        logger.exception('CLOUD_SYNC_JOB_EVENT_RECORD_FAILED job_id=%s event_type=%s message=%s', job_id, event_type, message)
        return None


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _update_sync_job_status(job: CloudAssetSyncJob, status: str, current_task: str, *, event_type: str = CloudAssetSyncJobEvent.TYPE_STATUS, payload: dict | None = None, worker_id: str = '', actor=None, **updates):
    previous_status = job.status
    now = timezone.now()
    update_payload = {
        'status': status,
        'current_task': current_task,
        'updated_at': now,
        **updates,
    }
    CloudAssetSyncJob.objects.filter(pk=job.pk).update(**update_payload)
    _record_sync_job_event(
        job,
        event_type,
        current_task,
        payload=payload,
        status_from=previous_status,
        status_to=status,
        worker_id=worker_id or getattr(job, 'worker_id', ''),
        actor=actor,
    )
    job.status = status
    job.current_task = current_task


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _heartbeat_sync_job(job_or_id, *, worker_id: str = '', current_task: str = '', payload: dict | None = None, record_event: bool = False):
    job_id = job_or_id.pk if isinstance(job_or_id, CloudAssetSyncJob) else int(job_or_id)
    now = timezone.now()
    updates = {
        'worker_heartbeat_at': now,
        'updated_at': now,
    }
    if worker_id:
        updates['worker_id'] = str(worker_id)[:64]
    if current_task:
        updates['current_task'] = current_task[:255]
    CloudAssetSyncJob.objects.filter(pk=job_id).update(**updates)
    if record_event:
        _record_sync_job_event(
            job_id,
            CloudAssetSyncJobEvent.TYPE_HEARTBEAT,
            current_task or 'worker heartbeat',
            payload=payload or {},
            worker_id=worker_id,
        )


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _sync_job_cancel_requested(job_or_id) -> bool:
    job_id = job_or_id.pk if isinstance(job_or_id, CloudAssetSyncJob) else int(job_or_id)
    return CloudAssetSyncJob.objects.filter(pk=job_id, cancel_requested_at__isnull=False).exists()


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _call_command_capture(command_name: str, *args, **options):
    output = options.pop('stdout', None) or io.StringIO()
    command = load_command_class(get_commands()[command_name], command_name)
    defaults = {
        'force_color': False,
        'no_color': False,
        'pythonpath': None,
        'settings': None,
        'skip_checks': True,
        'stderr': io.StringIO(),
        'traceback': False,
        'verbosity': 1,
    }
    defaults.update(options)
    command.execute(*args, stdout=output, **defaults)
    return command, output.getvalue()


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _call_command_capture_threaded(command_name: str, **options):
    output = io.StringIO()
    close_old_connections()
    try:
        command, log_text = _call_command_capture(command_name, stdout=output, **options)
        return command, log_text
    except Exception as exc:
        log_text = output.getvalue()
        _log_sync_command_output(
            f'CLOUD_SYNC_COMMAND_FAILED command={command_name} options={{{", ".join(f"{key}={value}" for key, value in sorted(options.items()))}}}',
            log_text,
            level=logging.ERROR,
        )
        raise CapturedCommandError(command_name, log_text, exc) from exc
    finally:
        close_old_connections()


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _sync_provider_for_asset(asset) -> str:
    provider = str(getattr(asset, 'provider', '') or '').strip().lower()
    if provider == 'aws_lightsail':
        return CloudAccountConfig.PROVIDER_AWS
    if provider == 'aliyun_simple':
        return CloudAccountConfig.PROVIDER_ALIYUN
    return ''


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _resolve_sync_account_for_asset(asset):
    provider = _sync_provider_for_asset(asset)
    if not provider:
        return None
    account = getattr(asset, 'cloud_account', None)
    if account and account.provider == provider and account.is_active:
        return account
    order_account = getattr(getattr(asset, 'order', None), 'cloud_account', None)
    if order_account and order_account.provider == provider and order_account.is_active:
        return order_account
    account_label = str(
        getattr(asset, 'account_label', '')
        or cloud_account_label(account)
        or getattr(getattr(asset, 'order', None), 'account_label', '')
        or ''
    ).strip()
    queryset = CloudAccountConfig.objects.filter(provider=provider, is_active=True).order_by('id')
    if getattr(asset, 'cloud_account_id', None):
        matched = queryset.filter(id=asset.cloud_account_id).first()
        if matched:
            return matched
    if account_label:
        for candidate in queryset:
            if cloud_account_label(candidate) == account_label:
                return candidate
    return None


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _asset_retained_static_ip_sync_scope(asset):
    public_ip = str(getattr(asset, 'public_ip', '') or getattr(asset, 'previous_public_ip', '') or '').strip()
    order = getattr(asset, 'order', None)
    provider_status = str(getattr(asset, 'provider_status', '') or '')
    note = str(getattr(asset, 'note', '') or '')
    is_retained = bool(
        public_ip
        and not str(getattr(asset, 'instance_id', '') or '').strip()
        and (
            '固定IP保留中' in provider_status
            or '固定 IP 保留中' in provider_status
            or '固定IP保留中' in note
            or '固定 IP 保留中' in note
            or (
                order
                and getattr(order, 'status', '') == 'deleted'
                and getattr(order, 'ip_recycle_at', None)
            )
        )
    )
    if not is_retained:
        return None
    static_name = str(getattr(order, 'static_ip_name', '') if order else '').strip()
    provider_resource_id = str(getattr(asset, 'provider_resource_id', '') or '').strip()
    if not static_name and 'StaticIp' in provider_resource_id:
        static_name = provider_resource_id.rsplit('/', 1)[-1]
    return {'instance_id': static_name, 'public_ip': public_ip}


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _sync_task_for_asset(asset, *, account=None, default_aliyun_region='cn-hongkong', default_aws_region=''):
    provider = _sync_provider_for_asset(asset)
    if not provider:
        return None, '当前资产暂不支持同步'
    account = account or _resolve_sync_account_for_asset(asset)
    if not account:
        return None, '未找到可用云账号'
    region_code = str(getattr(asset, 'region_code', '') or getattr(account, 'region_hint', '') or '').strip()
    if provider == CloudAccountConfig.PROVIDER_ALIYUN:
        region_code = region_code or default_aliyun_region or 'cn-hongkong'
        command_name = 'sync_aliyun_assets'
        scope_instance_id = asset.instance_id or asset.provider_resource_id or ''
        scope_public_ip = asset.public_ip or asset.previous_public_ip or ''
        provider_key = 'aliyun'
    else:
        region_code = region_code or default_aws_region or ''
        command_name = 'sync_aws_assets'
        retained_scope = _asset_retained_static_ip_sync_scope(asset)
        scope_instance_id = (
            (retained_scope or {}).get('instance_id')
            if retained_scope is not None
            else (asset.instance_id or asset.provider_resource_id or asset.asset_name or '')
        )
        scope_public_ip = (retained_scope or {}).get('public_ip') or asset.public_ip or asset.previous_public_ip or ''
        provider_key = 'aws'
    kwargs = {
        'region': region_code,
        'account_id': str(account.id),
        'asset_id': str(asset.id),
        'instance_id': scope_instance_id,
        'public_ip': scope_public_ip,
    }
    return {
        'provider': provider_key,
        'account': account,
        'command': command_name,
        'kwargs': kwargs,
        'asset': asset,
    }, ''


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _provider_sync_source(provider: str) -> str:
    return ExternalSyncLog.SOURCE_AWS if provider == 'aws' else ExternalSyncLog.SOURCE_ALIYUN


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _sync_task_lock_key(task: dict) -> str:
    account = task.get('account')
    account_id = getattr(account, 'id', 'none')
    region = str((task.get('kwargs') or {}).get('region') or 'all')
    kwargs = task.get('kwargs') or {}
    scope = str(kwargs.get('asset_id') or kwargs.get('instance_id') or kwargs.get('public_ip') or 'all').replace(':', '_')
    return f"cloud_asset_sync:{task.get('provider')}:{account_id}:{region}:{scope}"


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _sync_task_payload(task: dict) -> dict:
    account = task.get('account')
    kwargs = task.get('kwargs') or {}
    return {
        'provider': task.get('provider'),
        'account': _sync_account_payload(account) if account else None,
        'region': kwargs.get('region') or 'all',
        'command': task.get('command'),
        'asset_id': kwargs.get('asset_id') or None,
    }


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _run_sync_task_with_lock(task: dict, *, ttl_seconds: int = 900) -> dict:
    lock_key = _sync_task_lock_key(task)
    started_at = timezone.now()
    state_payload = {**_sync_task_payload(task), 'started_at': _iso(started_at)}
    if not cache.add(lock_key, json.dumps(state_payload, ensure_ascii=False), timeout=ttl_seconds):
        logger.warning('CLOUD_SYNC_TASK_SKIPPED run_id=%s lock_key=%s payload=%s', task.get('run_id') or '-', lock_key, state_payload)
        return {'task': task, 'skipped': True, 'reason': '同账号/地区已有同步正在运行', 'duration_seconds': 0, 'log_text': ''}
    try:
        logger.info('CLOUD_SYNC_TASK_START run_id=%s lock_key=%s payload=%s kwargs=%s', task.get('run_id') or '-', lock_key, state_payload, task.get('kwargs') or {})
        command, log_text = _call_command_capture_threaded(task['command'], **task['kwargs'])
        duration = max((timezone.now() - started_at).total_seconds(), 0)
        summary = getattr(command, 'summary', {}) if command else {}
        logger.info('CLOUD_SYNC_TASK_DONE run_id=%s lock_key=%s duration_seconds=%.3f summary=%s', task.get('run_id') or '-', lock_key, duration, summary)
        _log_sync_command_output(f'CLOUD_SYNC_TASK_LOG run_id={task.get("run_id") or "-"} command={task.get("command")}', log_text)
        return {'task': task, 'command': command, 'log_text': log_text, 'skipped': False, 'duration_seconds': round(duration, 3)}
    finally:
        cache.delete(lock_key)


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _parse_int_list(value) -> set[int]:
    if value is None:
        return set()
    if isinstance(value, str):
        raw_items = re.split(r'[,，\s]+', value.strip())
    elif isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = [value]
    result = set()
    for item in raw_items:
        try:
            parsed = int(item)
            if parsed > 0:
                result.add(parsed)
        except (TypeError, ValueError):
            continue
    return result


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _parse_provider_scope(value) -> set[str]:
    if value is None or value == '':
        return {'aliyun', 'aws'}
    if isinstance(value, str):
        raw_items = re.split(r'[,，\s]+', value.strip())
    elif isinstance(value, (list, tuple, set)):
        raw_items = value
    else:
        raw_items = [value]
    providers = {str(item or '').strip().lower() for item in raw_items}
    normalized = set()
    if 'all' in providers:
        return {'aliyun', 'aws'}
    if providers & {'aliyun', CloudAccountConfig.PROVIDER_ALIYUN}:
        normalized.add('aliyun')
    if providers & {'aws', CloudAccountConfig.PROVIDER_AWS, 'aws_lightsail'}:
        normalized.add('aws')
    return normalized or {'aliyun', 'aws'}


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _parse_dashboard_page(request, *, default_size=20, min_size=1, max_size=200):
    try:
        page = max(int(request.GET.get('page') or '1'), 1)
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.GET.get('page_size') or str(default_size))
    except (TypeError, ValueError):
        page_size = default_size
    page_size = min(max(page_size, min_size), max_size)
    return page, page_size


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _run_cloud_assets_sync(payload, *, sync_run_id: str | None = None, job: CloudAssetSyncJob | None = None):
    sync_run_id = sync_run_id or uuid.uuid4().hex[:8]
    payload = dict(payload or {})
    aliyun_region = (payload.get('region') or 'cn-hongkong').strip() or 'cn-hongkong'
    aws_region = (payload.get('aws_region') or '').strip()
    if aws_region.lower() == 'all':
        aws_region = ''
    providers = _parse_provider_scope(payload.get('providers') or payload.get('provider'))
    requested_account_ids = _parse_int_list(payload.get('account_ids'))
    requested_asset_ids = _parse_int_list(payload.get('asset_ids'))
    errors = []
    synced = {'aliyun': False, 'aws': False, 'reconcile': False}
    aws_regions = []
    command_output = io.StringIO()
    aliyun_accounts = _active_sync_accounts(CloudAccountConfig.PROVIDER_ALIYUN)
    aws_accounts = _active_sync_accounts(CloudAccountConfig.PROVIDER_AWS)
    logger.info(
        'CLOUD_SYNC_REQUEST_START run_id=%s providers=%s aliyun_region=%s aws_region=%s account_ids=%s asset_ids=%s payload=%s',
        sync_run_id,
        sorted(providers),
        aliyun_region,
        aws_region or 'all',
        sorted(requested_account_ids),
        sorted(requested_asset_ids),
        payload,
    )
    if job:
        _record_sync_job_event(
            job,
            CloudAssetSyncJobEvent.TYPE_STATUS,
            '同步请求开始',
            payload={
                'providers': sorted(providers),
                'aliyun_region': aliyun_region,
                'aws_region': aws_region or 'all',
                'account_ids': sorted(requested_account_ids),
                'asset_ids': sorted(requested_asset_ids),
                'request_payload': payload,
            },
            status_from=job.status,
            status_to=CloudAssetSyncJob.STATUS_RUNNING,
            worker_id=job.worker_id,
        )
    if requested_account_ids:
        aliyun_accounts = [account for account in aliyun_accounts if account.id in requested_account_ids]
        aws_accounts = [account for account in aws_accounts if account.id in requested_account_ids]
    warnings = []
    selected_assets = []
    selected_account_keys = set()
    selected_asset_tasks = []
    if requested_asset_ids:
        selected_assets = list(CloudAsset.objects.select_related('cloud_account', 'order').filter(id__in=requested_asset_ids))
        found_asset_ids = {asset.id for asset in selected_assets}
        missing_asset_ids = sorted(requested_asset_ids - found_asset_ids)
        if missing_asset_ids:
            warnings.append(f'部分选中资产不存在，已跳过: {missing_asset_ids[:20]}')
        for asset in selected_assets:
            task, warning = _sync_task_for_asset(
                asset,
                default_aliyun_region=aliyun_region,
                default_aws_region=aws_region,
            )
            if warning:
                warnings.append(f'资产#{asset.id}: {warning}')
                continue
            if task['provider'] not in providers:
                warnings.append(f'资产#{asset.id}: 不在本次同步厂商范围内，已跳过')
                continue
            if requested_account_ids and getattr(task['account'], 'id', None) not in requested_account_ids:
                warnings.append(f'资产#{asset.id}: 不在本次同步账号范围内，已跳过')
                continue
            task['run_id'] = sync_run_id
            selected_asset_tasks.append(task)
            selected_account_keys.add((task['provider'], getattr(task['account'], 'id', None)))
        if selected_asset_tasks:
            providers = {task['provider'] for task in selected_asset_tasks}
            aliyun_accounts = [account for account in aliyun_accounts if ('aliyun', account.id) in selected_account_keys]
            aws_accounts = [account for account in aws_accounts if ('aws', account.id) in selected_account_keys]
        else:
            errors.append('选中资产没有可同步任务，已避免退化为全账号同步')
    sync_tasks = []
    if selected_asset_tasks:
        sync_tasks = selected_asset_tasks
    elif 'aliyun' in providers:
        for aliyun_account in aliyun_accounts:
            kwargs = {'region': aliyun_region, 'account_id': str(aliyun_account.id)}
            sync_tasks.append({
                'run_id': sync_run_id,
                'provider': 'aliyun',
                'account': aliyun_account,
                'command': 'sync_aliyun_assets',
                'kwargs': kwargs,
            })
    if not selected_asset_tasks and 'aws' in providers:
        for aws_account in aws_accounts:
            kwargs = {'region': aws_region, 'account_id': str(aws_account.id)}
            sync_tasks.append({
                'run_id': sync_run_id,
                'provider': 'aws',
                'account': aws_account,
                'command': 'sync_aws_assets',
                'kwargs': kwargs,
            })
    logger.info(
        'CLOUD_SYNC_TASKS_BUILT run_id=%s task_count=%s tasks=%s',
        sync_run_id,
        len(sync_tasks),
        [_sync_task_payload(task) for task in sync_tasks],
    )
    if job:
        CloudAssetSyncJob.objects.filter(pk=job.pk).update(
            progress_current=0,
            progress_total=len(sync_tasks),
            current_task='同步任务已生成' if sync_tasks else '没有可执行同步任务',
            warnings=warnings[:50],
            errors=errors[:50],
            updated_at=timezone.now(),
        )
        _record_sync_job_event(
            job,
            CloudAssetSyncJobEvent.TYPE_TASK,
            '同步任务已生成' if sync_tasks else '没有可执行同步任务',
            payload={
                'task_count': len(sync_tasks),
                'tasks': [_sync_task_payload(task) for task in sync_tasks],
                'warnings': warnings[:50],
                'errors': errors[:50],
            },
            worker_id=job.worker_id,
        )

    cancelled = False
    if job and _sync_job_cancel_requested(job):
        cancelled = True
        warnings.append('同步任务已取消，未执行任何云同步任务')
        _record_sync_job_event(
            job,
            CloudAssetSyncJobEvent.TYPE_CANCEL,
            '同步任务在执行前被取消',
            payload={'progress_current': 0, 'progress_total': len(sync_tasks)},
            worker_id=job.worker_id,
        )
        sync_tasks = []

    task_results = []
    skipped_tasks = []
    completed_tasks = 0
    if sync_tasks:
        logger.info('CLOUD_SYNC_TASKS_EXECUTION_MODE run_id=%s mode=serial task_count=%s', sync_run_id, len(sync_tasks))
        for task in sync_tasks:
            if job and _sync_job_cancel_requested(job):
                cancelled = True
                warnings.append('同步任务收到取消请求，已停止调度后续任务')
                _record_sync_job_event(
                    job,
                    CloudAssetSyncJobEvent.TYPE_CANCEL,
                    '同步任务收到取消请求，停止调度后续任务',
                    payload={
                        'progress_current': completed_tasks,
                        'progress_total': len(sync_tasks),
                        'next_task': _sync_task_payload(task),
                    },
                    worker_id=job.worker_id,
                )
                break

            account = task['account']
            task_payload = _sync_task_payload(task)
            if job:
                _record_sync_job_event(
                    job,
                    CloudAssetSyncJobEvent.TYPE_TASK,
                    '同步子任务开始',
                    payload=task_payload,
                    worker_id=job.worker_id,
                )
                _heartbeat_sync_job(
                    job,
                    worker_id=job.worker_id,
                    current_task=f'{task.get("provider") or "-"}:{getattr(account, "id", "-")} 开始同步',
                    payload={'progress_current': completed_tasks, 'progress_total': len(sync_tasks), **task_payload},
                )
            try:
                result = _run_sync_task_with_lock(task)
                if result.get('skipped'):
                    skipped_payload = {**task_payload, 'reason': result.get('reason') or '同步已在运行'}
                    skipped_tasks.append(skipped_payload)
                    if job:
                        _record_sync_job_event(
                            job,
                            CloudAssetSyncJobEvent.TYPE_WARNING,
                            '同步子任务跳过',
                            payload=skipped_payload,
                            worker_id=job.worker_id,
                            log_level=logging.WARNING,
                        )
                else:
                    command = result.get('command')
                    result_payload = {
                        **task_payload,
                        'duration_seconds': result.get('duration_seconds'),
                        'summary': getattr(command, 'summary', {}) if command else {},
                    }
                    task_results.append(result_payload)
                    log_text = result.get('log_text') or ''
                    command_output.write(log_text)
                    if job:
                        _record_sync_job_event(
                            job,
                            CloudAssetSyncJobEvent.TYPE_TASK,
                            '同步子任务完成',
                            payload=result_payload,
                            worker_id=job.worker_id,
                        )
                        if log_text:
                            _record_sync_job_event(
                                job,
                                CloudAssetSyncJobEvent.TYPE_LOG,
                                '同步子任务日志',
                                payload={**task_payload, 'log_tail': [line for line in log_text.splitlines() if line.strip()][-80:]},
                                worker_id=job.worker_id,
                            )
                    if task['provider'] == 'aliyun':
                        synced['aliyun'] = True
                    else:
                        task_region = (task.get('kwargs') or {}).get('region') or aws_region or 'all'
                        account_regions = getattr(command, 'synced_regions', None) or [task_region]
                        aws_regions.extend(region for region in account_regions if region not in aws_regions)
                        warnings.extend(getattr(command, 'sync_errors', []) or [])
                        synced['aws'] = True
            except Exception as exc:
                captured_log = getattr(exc, 'log_text', '') or ''
                if captured_log:
                    command_output.write(captured_log)
                if task['provider'] == 'aliyun':
                    message = f'阿里云账号#{getattr(account, "id", "-")}代理同步失败: {exc}'
                    logger.exception('DASHBOARD_SYNC_ASSETS_ALIYUN_FAILED run_id=%s account_id=%s region=%s kwargs=%s', sync_run_id, getattr(account, 'id', None), aliyun_region, task.get('kwargs') or {})
                else:
                    message = f'AWS账号#{getattr(account, "id", "-")}代理同步失败: {exc}'
                    logger.exception('DASHBOARD_SYNC_ASSETS_AWS_FAILED run_id=%s account_id=%s region=%s kwargs=%s', sync_run_id, getattr(account, 'id', None), aws_region or 'all', task.get('kwargs') or {})
                _log_sync_command_output(f'CLOUD_SYNC_TASK_FAILED_LOG run_id={sync_run_id} command={task.get("command")}', captured_log, level=logging.ERROR)
                errors.append(message)
                if job:
                    _record_sync_job_event(
                        job,
                        CloudAssetSyncJobEvent.TYPE_ERROR,
                        '同步子任务失败',
                        payload={**task_payload, 'error': str(exc), 'log_tail': [line for line in captured_log.splitlines() if line.strip()][-80:]},
                        worker_id=job.worker_id,
                        log_level=logging.ERROR,
                    )
            finally:
                completed_tasks += 1
                if job:
                    CloudAssetSyncJob.objects.filter(pk=job.pk).update(
                        progress_current=completed_tasks,
                        progress_total=len(sync_tasks),
                        current_task=f'{task.get("provider") or "-"}:{getattr(account, "id", "-")} 已处理',
                        warnings=warnings[:50],
                        errors=errors[:50],
                        updated_at=timezone.now(),
                    )
                    _record_sync_job_event(
                        job,
                        CloudAssetSyncJobEvent.TYPE_PROGRESS,
                        f'{completed_tasks}/{len(sync_tasks)} 已处理',
                        payload={'progress_current': completed_tasks, 'progress_total': len(sync_tasks), 'task': task_payload},
                        worker_id=job.worker_id,
                    )
                    _heartbeat_sync_job(
                        job,
                        worker_id=job.worker_id,
                        current_task=f'{task.get("provider") or "-"}:{getattr(account, "id", "-")} 已处理',
                        payload={'progress_current': completed_tasks, 'progress_total': len(sync_tasks), **task_payload},
                    )
            if job and _sync_job_cancel_requested(job):
                cancelled = True
                warnings.append('同步任务收到取消请求，已停止调度后续任务')
                _record_sync_job_event(
                    job,
                    CloudAssetSyncJobEvent.TYPE_CANCEL,
                    '同步任务收到取消请求',
                    payload={'progress_current': completed_tasks, 'progress_total': len(sync_tasks)},
                    worker_id=job.worker_id,
                )
                break
    synced['reconcile'] = True
    logger.info('CLOUD_SYNC_RECONCILE_SKIPPED run_id=%s reason=cloud_asset_is_canonical', sync_run_id)
    ok = (not cancelled) and (not errors or synced['aliyun'] or synced['aws'])
    response_payload = {
        'ok': ok,
        'synced': synced,
        'aliyun_region': aliyun_region,
        'aws_region': aws_region or 'all',
        'aws_regions': aws_regions,
        'providers': sorted(providers),
        'asset_ids': sorted(requested_asset_ids),
        'errors': errors,
        'warnings': warnings[:50],
        'logs': _sync_log_tail(command_output),
        'tasks': task_results,
        'skipped_tasks': skipped_tasks,
        'cancelled': cancelled,
        'accounts': {'aliyun': [_sync_account_payload(account) for account in aliyun_accounts], 'aws': [_sync_account_payload(account) for account in aws_accounts]},
    }
    logger.info(
        'CLOUD_SYNC_REQUEST_DONE run_id=%s ok=%s synced=%s errors=%s warnings=%s tasks=%s skipped=%s',
        sync_run_id,
        ok,
        synced,
        errors,
        warnings[:20],
        task_results,
        skipped_tasks,
    )
    _log_sync_command_output(f'CLOUD_SYNC_REQUEST_LOG run_id={sync_run_id}', _sync_log_text(command_output))
    if ok:
        _refresh_dashboard_plan_snapshots_deferred(
            f'cloud_assets_sync:{sync_run_id}',
            cloud_asset_ids=sorted(requested_asset_ids) or None,
            full_cloud_assets=not bool(requested_asset_ids),
        )
    _record_dashboard_sync_log(
        action='sync_cloud_assets',
        target=f'providers:{",".join(sorted(providers))};aliyun:{aliyun_region};aws:{aws_region or "all"}',
        request_payload={'aliyun_region': aliyun_region, 'aws_region': aws_region or 'all', 'providers': sorted(providers), 'account_ids': sorted(requested_account_ids), 'asset_ids': sorted(requested_asset_ids)},
        response_payload={**response_payload, 'log_text': _sync_log_text(command_output)},
        is_success=ok,
        error_message='; '.join(errors[:10]),
    )
    return response_payload


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _cloud_assets_sync_request_payload(request) -> dict:
    payload = dict(_read_payload(request) or {})
    fallbacks = {
        'region': request.POST.get('region') or request.GET.get('region'),
        'aws_region': request.POST.get('aws_region') or request.GET.get('aws_region'),
        'provider': request.POST.get('provider') or request.GET.get('provider'),
        'providers': request.POST.get('providers') or request.GET.get('providers'),
        'account_ids': request.POST.get('account_ids') or request.GET.get('account_ids'),
        'asset_ids': request.POST.get('asset_ids') or request.GET.get('asset_ids'),
    }
    for key, value in fallbacks.items():
        if key not in payload and value not in (None, ''):
            payload[key] = value
    return payload


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _cloud_assets_sync_scope(payload: dict) -> dict:
    providers = sorted(_parse_provider_scope(payload.get('providers') or payload.get('provider')))
    account_ids = sorted(_parse_int_list(payload.get('account_ids')))
    asset_ids = sorted(_parse_int_list(payload.get('asset_ids')))
    aliyun_region = str(payload.get('region') or 'cn-hongkong').strip() or 'cn-hongkong'
    aws_region = str(payload.get('aws_region') or '').strip()
    if aws_region.lower() == 'all':
        aws_region = ''
    return {
        'providers': providers,
        'account_ids': account_ids,
        'asset_ids': asset_ids,
        'aliyun_region': aliyun_region,
        'aws_region': aws_region or 'all',
    }


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_sync_job_payload(job: CloudAssetSyncJob) -> dict:
    result_payload = dict(job.result_payload or {})
    progress_total = int(job.progress_total or 0)
    progress_current = int(job.progress_current or 0)
    progress_percent = 100 if job.is_terminal else (round(progress_current * 100 / progress_total) if progress_total else 0)
    events = [
        _sync_job_event_payload(event)
        for event in CloudAssetSyncJobEvent.objects.filter(job_id=job.id).order_by('-created_at', '-id')[:80]
    ]
    events.reverse()
    payload = {
        'id': job.id,
        'job_id': job.id,
        'run_id': job.run_id,
        'status': job.status,
        'status_label': dict(CloudAssetSyncJob.STATUS_CHOICES).get(job.status, job.status),
        'is_terminal': job.is_terminal,
        'progress_current': progress_current,
        'progress_total': progress_total,
        'progress_percent': progress_percent,
        'current_task': job.current_task or '',
        'providers': job.providers or [],
        'account_ids': job.account_ids or [],
        'asset_ids': job.asset_ids or [],
        'scope': job.scope or {},
        'errors': job.errors or result_payload.get('errors') or [],
        'warnings': job.warnings or result_payload.get('warnings') or [],
        'logs': job.logs or result_payload.get('logs') or [],
        'events': events,
        'tasks': result_payload.get('tasks') or [],
        'skipped_tasks': result_payload.get('skipped_tasks') or [],
        'result': result_payload,
        'cancelled': bool(job.cancel_requested_at) or job.status == CloudAssetSyncJob.STATUS_CANCELLED or bool(result_payload.get('cancelled')),
        'can_cancel': job.status in {CloudAssetSyncJob.STATUS_QUEUED, CloudAssetSyncJob.STATUS_RUNNING} and not job.cancel_requested_at,
        'worker_id': job.worker_id or '',
        'worker_heartbeat_at': _iso(job.worker_heartbeat_at),
        'cancel_requested_at': _iso(job.cancel_requested_at),
        'cancel_requested_by_id': job.cancel_requested_by_id,
        'ok': result_payload.get('ok') if result_payload else job.status not in {CloudAssetSyncJob.STATUS_FAILED, CloudAssetSyncJob.STATUS_CANCELLED},
        'created_at': _iso(job.created_at),
        'started_at': _iso(job.started_at),
        'finished_at': _iso(job.finished_at),
        'updated_at': _iso(job.updated_at),
        'requested_by_id': job.requested_by_id,
    }
    return payload


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_sync_jobs_metrics_payload(*, window_hours: int = 24, duration_sample_size: int = 200) -> dict:
    window_hours = min(max(int(window_hours or 24), 1), 24 * 30)
    duration_sample_size = min(max(int(duration_sample_size or 200), 20), 1000)
    now = timezone.now()
    cutoff = now - timedelta(hours=window_hours)
    status_counts = {
        row['status']: row['count']
        for row in CloudAssetSyncJob.objects.values('status').annotate(count=Count('id'))
    }
    recent_status_counts = {
        row['status']: row['count']
        for row in CloudAssetSyncJob.objects.filter(created_at__gte=cutoff).values('status').annotate(count=Count('id'))
    }
    active_statuses = [CloudAssetSyncJob.STATUS_QUEUED, CloudAssetSyncJob.STATUS_RUNNING]
    failed_statuses = [CloudAssetSyncJob.STATUS_FAILED, CloudAssetSyncJob.STATUS_PARTIAL]
    terminal_statuses = list(CloudAssetSyncJob.TERMINAL_STATUSES)
    recent_total = sum(recent_status_counts.values())
    recent_failed = sum(recent_status_counts.get(status, 0) for status in failed_statuses)
    stale_cutoff = now - timedelta(minutes=15)
    stale_running_count = CloudAssetSyncJob.objects.filter(
        status=CloudAssetSyncJob.STATUS_RUNNING,
        finished_at__isnull=True,
    ).filter(Q(worker_heartbeat_at__lt=stale_cutoff) | Q(worker_heartbeat_at__isnull=True, started_at__lt=stale_cutoff)).count()
    terminal_jobs = list(
        CloudAssetSyncJob.objects
        .filter(status__in=terminal_statuses, started_at__isnull=False, finished_at__isnull=False)
        .order_by('-finished_at', '-id')[:duration_sample_size]
    )
    durations = sorted(
        max((job.finished_at - job.started_at).total_seconds(), 0)
        for job in terminal_jobs
        if job.started_at and job.finished_at
    )
    if durations:
        avg_duration = round(sum(durations) / len(durations), 3)
        p95_index = min(max(int(len(durations) * 0.95) - 1, 0), len(durations) - 1)
        p95_duration = round(durations[p95_index], 3)
        max_duration = round(durations[-1], 3)
    else:
        avg_duration = p95_duration = max_duration = 0
    latest_failed_job = (
        CloudAssetSyncJob.objects
        .filter(status__in=failed_statuses)
        .order_by('-finished_at', '-updated_at', '-id')
        .first()
    )
    event_counts = {
        row['event_type']: row['count']
        for row in CloudAssetSyncJobEvent.objects.filter(created_at__gte=cutoff).values('event_type').annotate(count=Count('id'))
    }
    return {
        'window_hours': window_hours,
        'generated_at': _iso(now),
        'status_counts': status_counts,
        'recent_status_counts': recent_status_counts,
        'active_count': sum(status_counts.get(status, 0) for status in active_statuses),
        'queued_count': status_counts.get(CloudAssetSyncJob.STATUS_QUEUED, 0),
        'running_count': status_counts.get(CloudAssetSyncJob.STATUS_RUNNING, 0),
        'succeeded_count': status_counts.get(CloudAssetSyncJob.STATUS_SUCCEEDED, 0),
        'partial_count': status_counts.get(CloudAssetSyncJob.STATUS_PARTIAL, 0),
        'failed_count': status_counts.get(CloudAssetSyncJob.STATUS_FAILED, 0),
        'cancelled_count': status_counts.get(CloudAssetSyncJob.STATUS_CANCELLED, 0),
        'recent_total': recent_total,
        'recent_failed': recent_failed,
        'recent_failure_rate': round(recent_failed / recent_total, 4) if recent_total else 0,
        'stale_running_count': stale_running_count,
        'event_counts': event_counts,
        'duration_sample_size': len(durations),
        'avg_duration_seconds': avg_duration,
        'p95_duration_seconds': p95_duration,
        'max_duration_seconds': max_duration,
        'latest_failed_job': _cloud_asset_sync_job_payload(latest_failed_job) if latest_failed_job else None,
    }


# 功能：处理 云资产、云订单和生命周期 中的 cloud asset sync jobs metrics 业务流程。
@dashboard_login_required
@require_GET
def cloud_asset_sync_jobs_metrics(request):
    try:
        window_hours = int(request.GET.get('window_hours') or '24')
    except (TypeError, ValueError):
        window_hours = 24
    return _ok(_cloud_asset_sync_jobs_metrics_payload(window_hours=window_hours))


# 功能：提供 云资产、云订单和生命周期 的内部辅助逻辑，供同模块流程复用。
def _execute_cloud_asset_sync_job(job_or_id):
    job = job_or_id if isinstance(job_or_id, CloudAssetSyncJob) else CloudAssetSyncJob.objects.get(pk=job_or_id)
    job.refresh_from_db()
    if job.status == CloudAssetSyncJob.STATUS_CANCELLED:
        _record_sync_job_event(
            job,
            CloudAssetSyncJobEvent.TYPE_CANCEL,
            '任务已取消，worker 跳过执行',
            payload={'run_id': job.run_id},
            status_from=job.status,
            status_to=job.status,
            worker_id=job.worker_id,
        )
        return job
    started_at = timezone.now()
    previous_status = job.status
    CloudAssetSyncJob.objects.filter(pk=job.pk).update(
        status=CloudAssetSyncJob.STATUS_RUNNING,
        started_at=started_at,
        worker_heartbeat_at=started_at,
        current_task='开始同步云资产',
        updated_at=started_at,
    )
    _record_sync_job_event(
        job,
        CloudAssetSyncJobEvent.TYPE_STATUS,
        '开始同步云资产',
        payload={'run_id': job.run_id, 'request_payload': job.request_payload or {}},
        status_from=previous_status,
        status_to=CloudAssetSyncJob.STATUS_RUNNING,
        worker_id=job.worker_id,
    )
    job.refresh_from_db()
    try:
        result_payload = _run_cloud_assets_sync(job.request_payload or {}, sync_run_id=job.run_id, job=job)
        errors = result_payload.get('errors') or []
        warnings = result_payload.get('warnings') or []
        if result_payload.get('cancelled') or _sync_job_cancel_requested(job):
            status = CloudAssetSyncJob.STATUS_CANCELLED
        elif result_payload.get('ok') and not errors:
            status = CloudAssetSyncJob.STATUS_SUCCEEDED
        elif result_payload.get('ok'):
            status = CloudAssetSyncJob.STATUS_PARTIAL
        else:
            status = CloudAssetSyncJob.STATUS_FAILED
        finished_at = timezone.now()
        current_task = (
            '同步已取消'
            if status == CloudAssetSyncJob.STATUS_CANCELLED
            else ('同步完成' if status != CloudAssetSyncJob.STATUS_FAILED else '同步失败')
        )
        CloudAssetSyncJob.objects.filter(pk=job.pk).update(
            status=status,
            progress_current=F('progress_total'),
            current_task=current_task,
            errors=errors[:50],
            warnings=warnings[:50],
            logs=result_payload.get('logs') or [],
            result_payload=result_payload,
            finished_at=finished_at,
            updated_at=finished_at,
        )
        _record_sync_job_event(
            job,
            CloudAssetSyncJobEvent.TYPE_STATUS,
            current_task,
            payload={
                'status': status,
                'errors': errors[:50],
                'warnings': warnings[:50],
                'tasks': result_payload.get('tasks') or [],
                'skipped_tasks': result_payload.get('skipped_tasks') or [],
                'cancelled': bool(result_payload.get('cancelled')),
            },
            status_from=CloudAssetSyncJob.STATUS_RUNNING,
            status_to=status,
            worker_id=job.worker_id,
            log_level=logging.WARNING if status in {CloudAssetSyncJob.STATUS_FAILED, CloudAssetSyncJob.STATUS_CANCELLED} else logging.INFO,
        )
    except Exception as exc:
        finished_at = timezone.now()
        logger.exception('CLOUD_SYNC_JOB_FAILED job_id=%s run_id=%s', job.id, job.run_id)
        CloudAssetSyncJob.objects.filter(pk=job.pk).update(
            status=CloudAssetSyncJob.STATUS_FAILED,
            current_task='同步异常退出',
            errors=[str(exc)],
            finished_at=finished_at,
            updated_at=finished_at,
        )
        _record_sync_job_event(
            job,
            CloudAssetSyncJobEvent.TYPE_ERROR,
            '同步异常退出',
            payload={'error': str(exc)},
            status_from=CloudAssetSyncJob.STATUS_RUNNING,
            status_to=CloudAssetSyncJob.STATUS_FAILED,
            worker_id=job.worker_id,
            log_level=logging.ERROR,
        )
    job.refresh_from_db()
    return job


# 功能：同步外部或派生数据；当前函数属于 云资产、云订单和生命周期。
@csrf_exempt
@dashboard_superuser_required
@require_POST
def sync_cloud_assets(request):
    payload = _cloud_assets_sync_request_payload(request)
    scope = _cloud_assets_sync_scope(payload)
    sync_run_id = uuid.uuid4().hex
    requested_by = request.user if getattr(request.user, 'is_authenticated', False) else None
    job = CloudAssetSyncJob.objects.create(
        run_id=sync_run_id,
        requested_by=requested_by,
        request_payload=payload,
        providers=scope['providers'],
        account_ids=scope['account_ids'],
        asset_ids=scope['asset_ids'],
        scope=scope,
        current_task='已加入同步队列',
    )
    logger.info('CLOUD_SYNC_JOB_QUEUED job_id=%s run_id=%s scope=%s payload=%s', job.id, job.run_id, scope, payload)
    _record_sync_job_event(
        job,
        CloudAssetSyncJobEvent.TYPE_QUEUED,
        '同步任务已入队',
        payload={'scope': scope, 'request_payload': payload},
        status_from='',
        status_to=CloudAssetSyncJob.STATUS_QUEUED,
        actor=requested_by,
    )
    return _ok({
        'ok': True,
        'queued': True,
        'job_id': job.id,
        'run_id': job.run_id,
        'status': job.status,
        'message': '云资产同步已加入后台队列，等待同步 worker 执行',
        'job': _cloud_asset_sync_job_payload(job),
        'tasks': [],
        'skipped_tasks': [],
        'errors': [],
        'warnings': [],
    })


# 功能：处理 云资产、云订单和生命周期 中的 cloud asset sync jobs list 业务流程。
@dashboard_login_required
@require_GET
def cloud_asset_sync_jobs_list(request):
    queryset = CloudAssetSyncJob.objects.order_by('-created_at', '-id')
    status = str(request.GET.get('status') or '').strip()
    failed_only = str(request.GET.get('failed_only') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    if failed_only:
        queryset = queryset.filter(status__in=[CloudAssetSyncJob.STATUS_FAILED, CloudAssetSyncJob.STATUS_PARTIAL])
    elif status == 'active':
        queryset = queryset.filter(status__in=[CloudAssetSyncJob.STATUS_QUEUED, CloudAssetSyncJob.STATUS_RUNNING])
    elif status == 'terminal':
        queryset = queryset.filter(status__in=CloudAssetSyncJob.TERMINAL_STATUSES)
    if status:
        if status not in {'active', 'terminal'}:
            queryset = queryset.filter(status=status)
    page, page_size = _parse_dashboard_page(request, default_size=20, min_size=1, max_size=100)
    total = queryset.count()
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    jobs = list(queryset[start:start + page_size])
    return _ok({
        'items': [_cloud_asset_sync_job_payload(job) for job in jobs],
        'total': total,
        'page': page,
        'page_size': page_size,
        'total_pages': total_pages,
    })


# 功能：处理 云资产、云订单和生命周期 中的 cloud asset sync job detail 业务流程。
@dashboard_login_required
@require_GET
def cloud_asset_sync_job_detail(request, job_id: int):
    job = CloudAssetSyncJob.objects.filter(pk=job_id).first()
    if not job:
        return _error('同步任务不存在', status=404)
    return _ok(_cloud_asset_sync_job_payload(job))


# 功能：处理 云资产、云订单和生命周期 中的 retry cloud asset sync job 业务流程。
@csrf_exempt
@dashboard_superuser_required
@require_POST
def retry_cloud_asset_sync_job(request, job_id: int):
    source_job = CloudAssetSyncJob.objects.filter(pk=job_id).first()
    if not source_job:
        return _error('同步任务不存在', status=404)
    if source_job.status in {CloudAssetSyncJob.STATUS_QUEUED, CloudAssetSyncJob.STATUS_RUNNING}:
        return _error('任务仍在队列或执行中，不能重复重试', status=400)
    scope = _cloud_assets_sync_scope(source_job.request_payload or {})
    requested_by = request.user if getattr(request.user, 'is_authenticated', False) else None
    job = CloudAssetSyncJob.objects.create(
        run_id=uuid.uuid4().hex,
        requested_by=requested_by,
        request_payload=source_job.request_payload or {},
        providers=scope['providers'],
        account_ids=scope['account_ids'],
        asset_ids=scope['asset_ids'],
        scope={**scope, 'retry_of_job_id': source_job.id},
        current_task='重试任务已加入同步队列',
    )
    logger.info('CLOUD_SYNC_JOB_RETRY_QUEUED source_job_id=%s job_id=%s run_id=%s scope=%s', source_job.id, job.id, job.run_id, job.scope)
    _record_sync_job_event(
        source_job,
        CloudAssetSyncJobEvent.TYPE_RETRY,
        '已创建重试任务',
        payload={'retry_job_id': job.id, 'retry_run_id': job.run_id},
        actor=requested_by,
    )
    _record_sync_job_event(
        job,
        CloudAssetSyncJobEvent.TYPE_QUEUED,
        '重试任务已入队',
        payload={'retry_of_job_id': source_job.id, 'scope': job.scope},
        status_from='',
        status_to=CloudAssetSyncJob.STATUS_QUEUED,
        actor=requested_by,
    )
    return _ok({
        'ok': True,
        'queued': True,
        'job_id': job.id,
        'run_id': job.run_id,
        'status': job.status,
        'message': '同步重试已加入后台队列',
        'job': _cloud_asset_sync_job_payload(job),
    })


# 功能：处理 云资产、云订单和生命周期 中的 cancel cloud asset sync job 业务流程。
@csrf_exempt
@dashboard_superuser_required
@require_POST
def cancel_cloud_asset_sync_job(request, job_id: int):
    job = CloudAssetSyncJob.objects.filter(pk=job_id).first()
    if not job:
        return _error('同步任务不存在', status=404)
    if job.is_terminal:
        return _error('任务已结束，不能取消', status=400)
    actor = request.user if getattr(request.user, 'is_authenticated', False) else None
    now = timezone.now()
    if job.status == CloudAssetSyncJob.STATUS_QUEUED:
        CloudAssetSyncJob.objects.filter(pk=job.pk).update(
            status=CloudAssetSyncJob.STATUS_CANCELLED,
            cancel_requested_at=now,
            cancel_requested_by=actor,
            current_task='任务已取消',
            finished_at=now,
            updated_at=now,
        )
        _record_sync_job_event(
            job,
            CloudAssetSyncJobEvent.TYPE_CANCEL,
            '排队任务已取消',
            payload={'previous_status': job.status},
            status_from=job.status,
            status_to=CloudAssetSyncJob.STATUS_CANCELLED,
            actor=actor,
            log_level=logging.WARNING,
        )
    else:
        CloudAssetSyncJob.objects.filter(pk=job.pk).update(
            cancel_requested_at=now,
            cancel_requested_by=actor,
            current_task='取消请求已提交，等待当前子任务结束',
            updated_at=now,
        )
        _record_sync_job_event(
            job,
            CloudAssetSyncJobEvent.TYPE_CANCEL,
            '运行中任务收到取消请求',
            payload={'previous_status': job.status, 'progress_current': job.progress_current, 'progress_total': job.progress_total},
            status_from=job.status,
            status_to=job.status,
            worker_id=job.worker_id,
            actor=actor,
            log_level=logging.WARNING,
        )
    job.refresh_from_db()
    logger.warning('CLOUD_SYNC_JOB_CANCEL_REQUESTED job_id=%s run_id=%s status=%s actor_id=%s', job.id, job.run_id, job.status, getattr(actor, 'id', None))
    return _ok({
        'ok': True,
        'cancelled': job.status == CloudAssetSyncJob.STATUS_CANCELLED,
        'job': _cloud_asset_sync_job_payload(job),
        'message': '同步任务已取消' if job.status == CloudAssetSyncJob.STATUS_CANCELLED else '取消请求已提交',
    })


# 功能：处理 云资产、云订单和生命周期 中的 cloud assets sync status 业务流程。
@dashboard_login_required
@require_GET
def cloud_assets_sync_status(request):
    latest_log = ExternalSyncLog.objects.filter(
        source__in=[ExternalSyncLog.SOURCE_AWS, ExternalSyncLog.SOURCE_ALIYUN],
        is_success=True,
    ).order_by('-created_at', '-id').first()
    latest_asset = CloudAsset.objects.filter(
        source__in=[CloudAsset.SOURCE_AWS_SYNC, CloudAsset.SOURCE_ALIYUN],
    ).order_by('-updated_at', '-id').first()
    last_synced_at = None
    if latest_log and latest_asset:
        last_synced_at = max(latest_log.created_at, latest_asset.updated_at)
    elif latest_log:
        last_synced_at = latest_log.created_at
    elif latest_asset:
        last_synced_at = latest_asset.updated_at

    since = last_synced_at
    active_account_labels = list_cloud_account_labels(True)
    active_account_filter = (
        Q(cloud_account__is_active=True)
        | Q(cloud_account__isnull=True, account_label__in=active_account_labels)
    )
    aws_existing_count = CloudAsset.objects.filter(
        active_account_filter,
        kind=CloudAsset.KIND_SERVER,
        provider='aws_lightsail',
    ).exclude(status=CloudAsset.STATUS_DELETED).count()
    aliyun_existing_count = CloudAsset.objects.filter(
        active_account_filter,
        kind=CloudAsset.KIND_SERVER,
        provider='aliyun_simple',
    ).exclude(status=CloudAsset.STATUS_DELETED).count()
    unattached_ip_count = CloudAsset.objects.filter(
        active_account_filter,
        kind=CloudAsset.KIND_SERVER,
    ).filter(
        Q(provider_status__icontains='未附加') | Q(note__icontains='未附加IP') | Q(note__icontains='未附加固定IP')
    ).exclude(status__in=[
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_TERMINATED,
        CloudAsset.STATUS_TERMINATING,
    ]).count()
    recent_syncs = []
    for log in ExternalSyncLog.objects.filter(source=ExternalSyncLog.SOURCE_DASHBOARD, action='sync_cloud_assets').order_by('-created_at', '-id')[:5]:
        response_payload = {}
        try:
            response_payload = json.loads(log.response_payload or '{}')
        except Exception:
            response_payload = {}
        recent_syncs.append({
            'id': log.id,
            'created_at': _iso(log.created_at),
            'is_success': log.is_success,
            'target': log.target or '',
            'error_message': log.error_message or '',
            'providers': response_payload.get('providers') or [],
            'tasks': response_payload.get('tasks') or [],
            'skipped_tasks': response_payload.get('skipped_tasks') or [],
        })
    recent_jobs = [
        _cloud_asset_sync_job_payload(job)
        for job in CloudAssetSyncJob.objects.order_by('-created_at', '-id')[:5]
    ]
    active_jobs = [
        _cloud_asset_sync_job_payload(job)
        for job in CloudAssetSyncJob.objects
        .filter(status__in=[CloudAssetSyncJob.STATUS_QUEUED, CloudAssetSyncJob.STATUS_RUNNING])
        .order_by('created_at', 'id')[:5]
    ]
    return _ok({
        'auto_sync_every_seconds': get_cloud_asset_sync_interval_seconds(),
        'last_synced_at': _iso(last_synced_at),
        'aws_existing_count': aws_existing_count,
        'aliyun_existing_count': aliyun_existing_count,
        'unattached_ip_count': unattached_ip_count,
        'accounts': {
            'aliyun': [_sync_account_payload(account) for account in _active_sync_accounts(CloudAccountConfig.PROVIDER_ALIYUN)],
            'aws': [_sync_account_payload(account) for account in _active_sync_accounts(CloudAccountConfig.PROVIDER_AWS)],
        },
        'recent_syncs': recent_syncs,
        'recent_jobs': recent_jobs,
        'active_jobs': active_jobs,
        'metrics': _cloud_asset_sync_jobs_metrics_payload(window_hours=24),
    })
