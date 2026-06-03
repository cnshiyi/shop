"""后台任务中心聚合 API。"""

from django.db.models import Count
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.http import require_GET

from cloud.models import (
    CloudAssetSyncJob,
    CloudServerOrder,
)
from cloud.sync_jobs import _cloud_asset_sync_job_payload, _cloud_asset_sync_jobs_metrics_payload
from core.dashboard_api import _iso, _ok, _provider_label, _status_label, dashboard_login_required

_NOTICE_FAILED_STATUSES = {'failed', 'partial_failed', 'failed_retry'}
_NOTICE_WARNING_QUEUE_STATUSES = {'due_now', 'overdue', 'fallback_notice', 'within_window'}
_AUTO_RENEW_FAILED_QUEUE_STATUSES = {'retry_failed'}
_AUTO_RENEW_WARNING_QUEUE_STATUSES = {'due_now', 'overdue', 'balance_insufficient', 'retry_failed', 'fallback_retry'}


def _status_counts(queryset, field='status') -> dict:
    return {
        str(row[field] or ''): int(row['count'] or 0)
        for row in queryset.values(field).annotate(count=Count('id'))
    }


def _status_counts_from_items(items, field='queue_status') -> dict:
    counts = {}
    for item in items:
        value = str(item.get(field) or item.get('status') or '')
        counts[value] = counts.get(value, 0) + 1
    return counts


def _first_nonempty(*values) -> str:
    for value in values:
        text = str(value or '').strip()
        if text:
            return text
    return ''


def _parse_task_time(value):
    if not value:
        return None
    if hasattr(value, 'tzinfo'):
        return value
    if isinstance(value, str):
        return parse_datetime(value)
    return None


def _task_identity(item: dict) -> tuple:
    return (
        item.get('notice_type') or item.get('task_type') or '',
        item.get('order_id') or '',
        item.get('asset_id') or '',
        item.get('ip') or item.get('public_ip') or '',
    )


def _recent_failed_history_items(items, *, since, exclude_keys=None) -> list[dict]:
    exclude_keys = set(exclude_keys or [])
    failed_items = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        if _task_identity(item) in exclude_keys:
            continue
        item_time = _parse_task_time(
            item.get('executed_at')
            or item.get('created_at')
            or item.get('logged_at')
            or item.get('updated_at')
        )
        if item_time and item_time < since:
            continue
        is_failed = (
            item.get('delivered') is False
            or item.get('is_success') is False
            or item.get('notice_status') in _NOTICE_FAILED_STATUSES
            or bool(item.get('failure_reason'))
        )
        if is_failed:
            failed_items.append(item)
    return failed_items


def _auto_renew_recent_failure_count(history_qs, *, since, exclude_order_ids=None) -> int:
    if not history_qs:
        return 0
    exclude_order_ids = {item for item in (exclude_order_ids or set()) if item}
    if hasattr(history_qs, 'filter'):
        qs = history_qs.filter(executed_at__gte=since, is_success=False)
        if exclude_order_ids:
            qs = qs.exclude(order_id__in=exclude_order_ids)
        return qs.count()
    filtered_items = [
        item for item in history_qs
        if not isinstance(item, dict) or not item.get('order_id') or item.get('order_id') not in exclude_order_ids
    ]
    return len(_recent_failed_history_items(filtered_items, since=since))


def _task_section_payload(
    *,
    key: str,
    title: str,
    path: str,
    total: int,
    active: int = 0,
    failed: int = 0,
    warning: int = 0,
    status_counts: dict | None = None,
    items: list | None = None,
    generated_at=None,
    extra: dict | None = None,
) -> dict:
    health = 'ok'
    if failed:
        health = 'error'
    elif warning or active:
        health = 'warning'
    return {
        'key': key,
        'title': title,
        'path': path,
        'health': health,
        'total': total,
        'active': active,
        'failed': failed,
        'warning': warning,
        'status_counts': status_counts or {},
        'items': items or [],
        'generated_at': _iso(generated_at or timezone.now()),
        **(extra or {}),
    }


def _cloud_order_task_item(order) -> dict:
    return {
        'id': f'cloud-order:{order.id}',
        'order_id': order.id,
        'order_no': order.order_no,
        'task_type': 'cloud_order',
        'task_label': '云服务器任务',
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'execution_status': order.status,
        'execution_status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'plan_name': order.plan_name,
        'public_ip': order.public_ip,
        'note': order.provision_note,
        'created_at': _iso(order.created_at),
        'updated_at': _iso(order.updated_at),
        'related_path': f'/admin/cloud-orders/{order.id}',
        'detail_path': f'/admin/cloud-orders/{order.id}',
    }


def _plan_item(row, *, task_type: str, task_label: str) -> dict:
    if isinstance(row, dict):
        order_id = row.get('order_id')
        asset_id = row.get('asset_id')
        row_id = row.get('id') or order_id or asset_id or ''
        status = (
            row.get('queue_status')
            or row.get('status')
            or row.get('notice_status')
            or ('failed' if row.get('is_success') is False or row.get('delivered') is False else '')
        )
        status_label = (
            row.get('queue_status_label')
            or row.get('status_label')
            or row.get('notice_status_label')
            or row.get('result_label')
            or status
        )
        return {
            'id': f'{task_type}:{row_id}',
            'task_type': task_type,
            'task_label': task_label,
            'status': status,
            'status_label': status_label,
            'execution_status': status,
            'execution_status_label': status_label,
            'provider': row.get('provider') or '',
            'provider_label': row.get('provider_label') or _provider_label(row.get('provider') or ''),
            'order_id': order_id,
            'order_no': row.get('order_no') or '',
            'asset_id': asset_id,
            'public_ip': row.get('ip') or row.get('public_ip') or '',
            'note': _first_nonempty(
                row.get('last_failure_reason'),
                row.get('failure_reason'),
                row.get('last_error'),
                row.get('error'),
                row.get('retry_label'),
                row.get('result_label') if row.get('is_success') is False or row.get('delivered') is False else '',
                row.get('execution_status'),
                row.get('notice_status_label'),
            ),
            'created_at': row.get('created_at') or row.get('executed_at') or row.get('logged_at'),
            'updated_at': row.get('updated_at') or row.get('executed_at') or row.get('logged_at'),
            'next_run_at': row.get('next_run_at'),
            'related_path': row.get('related_path') or row.get('detail_path') or row.get('order_detail_path') or '',
            'detail_path': row.get('detail_path') or row.get('related_path') or '',
        }
    order_id = getattr(row, 'order_id', None)
    asset_id = getattr(row, 'asset_id', None)
    return {
        'id': f'{task_type}:{row.id}',
        'task_type': task_type,
        'task_label': task_label,
        'status': row.queue_status or row.status or '',
        'status_label': row.queue_status_label or row.status_label or row.queue_status or row.status or '',
        'execution_status': row.queue_status or row.status or '',
        'execution_status_label': row.queue_status_label or row.status_label or '',
        'provider': row.provider or '',
        'provider_label': row.provider_label or _provider_label(row.provider or ''),
        'order_id': order_id,
        'order_no': getattr(row, 'order_no', '') or getattr(getattr(row, 'order', None), 'order_no', '') or '',
        'asset_id': asset_id,
        'public_ip': getattr(row, 'ip', '') or '',
        'note': _first_nonempty(
            getattr(row, 'last_failure_reason', ''),
            getattr(row, 'failure_reason', ''),
            getattr(row, 'last_error', ''),
            getattr(row, 'error', ''),
            getattr(row, 'execution_status', ''),
            getattr(row, 'notice_status_label', ''),
        ),
        'created_at': _iso(row.created_at),
        'updated_at': _iso(row.updated_at),
        'next_run_at': _iso(getattr(row, 'next_run_at', None)),
        'related_path': row.related_path or row.detail_path or row.order_detail_path or '',
        'detail_path': row.detail_path or row.related_path or '',
    }


def _sync_section(now) -> dict:
    metrics = _cloud_asset_sync_jobs_metrics_payload(window_hours=24)
    active_jobs = [
        _cloud_asset_sync_job_payload(job)
        for job in CloudAssetSyncJob.objects
        .filter(status__in=[CloudAssetSyncJob.STATUS_QUEUED, CloudAssetSyncJob.STATUS_RUNNING])
        .order_by('created_at', 'id')[:5]
    ]
    recent_jobs = [
        _cloud_asset_sync_job_payload(job)
        for job in CloudAssetSyncJob.objects.order_by('-created_at', '-id')[:5]
    ]
    return _task_section_payload(
        key='cloud_sync',
        title='云资产同步',
        path='/admin/cloud-assets',
        total=sum(metrics.get('status_counts', {}).values()),
        active=metrics.get('active_count', 0),
        failed=metrics.get('recent_failed', 0),
        warning=metrics.get('stale_running_count', 0),
        status_counts=metrics.get('status_counts') or {},
        items=active_jobs or recent_jobs,
        generated_at=now,
        extra={'metrics': metrics},
    )


def _cloud_orders_section(now) -> dict:
    task_statuses = ['paid', 'provisioning', 'renew_pending', 'expiring', 'suspended', 'deleting', 'failed']
    queryset = CloudServerOrder.objects.filter(status__in=task_statuses)
    items = [_cloud_order_task_item(order) for order in queryset.order_by('-updated_at', '-id')[:8]]
    return _task_section_payload(
        key='cloud_orders',
        title='云服务器任务',
        path='/admin/tasks',
        total=queryset.count(),
        active=queryset.exclude(status='failed').count(),
        failed=queryset.filter(status='failed').count(),
        status_counts=_status_counts(queryset),
        items=items,
        generated_at=now,
    )


def _lifecycle_section(now) -> dict:
    from bot.api import _build_lifecycle_plan_bundle

    bundle = _build_lifecycle_plan_bundle(limit=1000)
    items_source = [
        *bundle.get('due_items', []),
        *bundle.get('future_plan_items', []),
        *[item for item in bundle.get('ip_delete_items', []) if not item.get('is_history')],
    ]
    failed_count = sum(1 for item in items_source if item.get('last_failure_reason') or item.get('failure_reason'))
    active_failure_keys = {
        _task_identity(item)
        for item in items_source
        if item.get('last_failure_reason') or item.get('failure_reason')
    }
    recent_failed_history = _recent_failed_history_items(
        bundle.get('history_items', []),
        since=now - timezone.timedelta(days=1),
        exclude_keys=active_failure_keys,
    )
    warning_count = sum(1 for item in items_source if item.get('queue_status') in ['overdue', 'due_now', 'blocked'])
    items = [
        _plan_item(row, task_type='lifecycle', task_label='生命周期计划')
        for row in [*items_source, *recent_failed_history][:8]
    ]
    return _task_section_payload(
        key='lifecycle',
        title='生命周期计划',
        path='/admin/tasks/plans',
        total=len(items_source),
        active=sum(1 for item in items_source if item.get('queue_status') in ['due_now', 'scheduled_future', 'overdue', 'within_window']),
        failed=failed_count + len(recent_failed_history),
        warning=warning_count,
        status_counts=_status_counts_from_items(items_source, 'queue_status'),
        items=items,
        generated_at=now,
    )


def _notice_section(now) -> dict:
    from cloud.api_tasks import _build_notice_plan_bundle

    bundle = _build_notice_plan_bundle(limit=1000, future_limit=200, history_limit=1000)
    items_source = bundle.get('active_items') or []
    failed_count = sum(1 for item in items_source if item.get('notice_status') in _NOTICE_FAILED_STATUSES)
    active_failure_keys = {
        _task_identity(item)
        for item in items_source
        if item.get('notice_status') in _NOTICE_FAILED_STATUSES
    }
    recent_failed_history = _recent_failed_history_items(
        bundle.get('history_items', []),
        since=now - timezone.timedelta(days=1),
        exclude_keys=active_failure_keys,
    )
    warning_count = sum(1 for item in items_source if item.get('queue_status') in _NOTICE_WARNING_QUEUE_STATUSES)
    items = [
        _plan_item(row, task_type='notice', task_label='通知计划')
        for row in [*items_source, *recent_failed_history][:8]
    ]
    return _task_section_payload(
        key='notices',
        title='通知计划',
        path='/admin/tasks/notices',
        total=len(items_source),
        active=sum(1 for item in items_source if item.get('queue_status') in ['due_now', 'scheduled_future', 'overdue', 'fallback_notice', 'within_window']),
        failed=failed_count + len(recent_failed_history),
        warning=warning_count,
        status_counts=_status_counts_from_items(items_source, 'queue_status'),
        items=items,
        generated_at=now,
    )


def _auto_renew_section(now) -> dict:
    from cloud.api_tasks import _build_auto_renew_plan_items

    bundle = _build_auto_renew_plan_items(now=now)
    items_source = [*bundle.get('due_items', []), *bundle.get('future_plan_items', [])]
    failed_count = sum(
        1
        for item in items_source
        if item.get('last_failure_reason') or item.get('queue_status') in _AUTO_RENEW_FAILED_QUEUE_STATUSES
    )
    active_failure_order_ids = {
        item.get('order_id')
        for item in items_source
        if item.get('last_failure_reason') or item.get('queue_status') in _AUTO_RENEW_FAILED_QUEUE_STATUSES
    }
    recent_failed_count = _auto_renew_recent_failure_count(
        bundle.get('history_qs'),
        since=now - timezone.timedelta(days=1),
        exclude_order_ids=active_failure_order_ids,
    )
    warning_count = sum(1 for item in items_source if item.get('queue_status') in _AUTO_RENEW_WARNING_QUEUE_STATUSES)
    items = [
        _plan_item(row, task_type='auto_renew', task_label='自动续费')
        for row in items_source[:8]
    ]
    return _task_section_payload(
        key='auto_renew',
        title='自动续费',
        path='/admin/tasks/auto-renew',
        total=len(items_source),
        active=sum(1 for item in items_source if item.get('queue_status') in ['due_now', 'scheduled_future', 'overdue', 'within_window', 'retry_failed', 'fallback_retry']),
        failed=failed_count + recent_failed_count,
        warning=warning_count,
        status_counts=_status_counts_from_items(items_source, 'queue_status'),
        items=items,
        generated_at=now,
    )


def task_center_payload() -> dict:
    now = timezone.now()
    sections = [
        _sync_section(now),
        _cloud_orders_section(now),
        _lifecycle_section(now),
        _notice_section(now),
        _auto_renew_section(now),
    ]
    return {
        'generated_at': _iso(now),
        'sections': sections,
        'totals': {
            'sections': len(sections),
            'tasks': sum(section['total'] for section in sections),
            'active': sum(section['active'] for section in sections),
            'failed': sum(section['failed'] for section in sections),
            'warning': sum(section['warning'] for section in sections),
        },
    }


@dashboard_login_required
@require_GET
def task_center_overview(request):
    return _ok(task_center_payload())
