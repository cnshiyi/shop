"""后台任务中心聚合 API。"""

from django.db.models import Count
from django.utils import timezone
from django.views.decorators.http import require_GET

from cloud.models import (
    CloudAssetSyncJob,
    CloudAutoRenewPlan,
    CloudLifecyclePlan,
    CloudNoticePlan,
    CloudServerOrder,
)
from cloud.sync_jobs import _cloud_asset_sync_job_payload, _cloud_asset_sync_jobs_metrics_payload
from core.dashboard_api import _iso, _ok, _provider_label, _status_label, dashboard_login_required


def _status_counts(queryset, field='status') -> dict:
    return {
        str(row[field] or ''): int(row['count'] or 0)
        for row in queryset.values(field).annotate(count=Count('id'))
    }


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
        'note': getattr(row, 'last_failure_reason', '') or getattr(row, 'execution_status', '') or getattr(row, 'notice_status_label', '') or '',
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
    queryset = CloudLifecyclePlan.objects.filter(data_group='active')
    failed_count = queryset.exclude(last_failure_reason__isnull=True).exclude(last_failure_reason='').count()
    warning_count = queryset.filter(queue_status__in=['overdue', 'due_now', 'blocked']).count()
    items = [
        _plan_item(row, task_type='lifecycle', task_label='生命周期计划')
        for row in queryset.order_by('next_run_at', 'delete_at', '-updated_at')[:8]
    ]
    return _task_section_payload(
        key='lifecycle',
        title='生命周期计划',
        path='/admin/tasks/plans',
        total=queryset.count(),
        active=queryset.filter(queue_status__in=['due_now', 'scheduled_future', 'overdue']).count(),
        failed=failed_count,
        warning=warning_count,
        status_counts=_status_counts(queryset, 'queue_status'),
        items=items,
        generated_at=now,
    )


def _notice_section(now) -> dict:
    queryset = CloudNoticePlan.objects.filter(data_group='active')
    failed_count = queryset.filter(notice_status__in=['failed', 'partial_failed']).count()
    warning_count = queryset.filter(queue_status__in=['due_now', 'overdue']).count()
    items = [
        _plan_item(row, task_type='notice', task_label='通知计划')
        for row in queryset.order_by('next_run_at', 'notice_at', '-updated_at')[:8]
    ]
    return _task_section_payload(
        key='notices',
        title='通知计划',
        path='/admin/tasks/notices',
        total=queryset.count(),
        active=queryset.filter(queue_status__in=['due_now', 'scheduled_future', 'overdue']).count(),
        failed=failed_count,
        warning=warning_count,
        status_counts=_status_counts(queryset, 'queue_status'),
        items=items,
        generated_at=now,
    )


def _auto_renew_section(now) -> dict:
    queryset = CloudAutoRenewPlan.objects.filter(data_group='active')
    failed_count = queryset.exclude(last_failure_reason__isnull=True).exclude(last_failure_reason='').count()
    warning_count = queryset.filter(queue_status__in=['due_now', 'overdue', 'balance_insufficient']).count()
    items = [
        _plan_item(row, task_type='auto_renew', task_label='自动续费')
        for row in queryset.order_by('auto_renew_at', 'next_run_at', '-updated_at')[:8]
    ]
    return _task_section_payload(
        key='auto_renew',
        title='自动续费',
        path='/admin/tasks/auto-renew',
        total=queryset.count(),
        active=queryset.filter(queue_status__in=['due_now', 'scheduled_future', 'overdue']).count(),
        failed=failed_count,
        warning=warning_count,
        status_counts=_status_counts(queryset, 'queue_status'),
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
