"""生命周期任务、通知计划与自动续费后台 API。"""

import inspect
import json
import logging
import uuid
from datetime import timezone as dt_timezone

from asgiref.sync import async_to_sync
from django.db.models import Count, Min, Q
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from bot.models import TelegramLoginAccount
from cloud.asset_expiry import order_asset_expiry
from cloud.dashboard_snapshots import _refresh_dashboard_plan_snapshots
from cloud.lifecycle import (
    AUTO_RENEW_BEFORE_EXPIRY_WINDOW,
    NOTICE_TYPE_SWITCH_CONFIG,
    _auto_renew_notice_batch_payload,
    _bulk_notice_payload_map,
    _get_notice_text_override,
    _lifecycle_notice_batch_payload,
    _notice_asset_is_unattached_static_ip,
    _notice_effective_delivered,
    _notice_override_key,
    _notice_payload_for_order,
    _notice_schedule,
    _record_auto_renew_patrol_log,
    _renew_notice_batch_payload,
    _run_auto_renew,
    _set_notice_text_override,
    cloud_notice_type_enabled,
)
from cloud.lifecycle_schedule import compute_order_lifecycle_fields
from cloud.models import CloudAsset, CloudAutoRenewPatrolLog, CloudAutoRenewRetryTask, CloudServerOrder, CloudUserNoticeLog
from cloud.services import RenewalPriceMissingError, _renewal_price
from core.dashboard_api import _decimal_to_str, _error, _iso, _ok, _provider_label, _read_payload, _status_label, _user_payload, dashboard_login_required, dashboard_superuser_required
from core.models import SiteConfig
from core.runtime_config import get_runtime_config

logger = logging.getLogger(__name__)

NOTICE_PLAN_MAX_OFFSET = 10_000_000


def _auto_renew_failure_was_price_missing(reason: str | None) -> bool:
    return '缺少续费价格' in str(reason or '') or '缺少价格' in str(reason or '')


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _order_has_renewal_price(order) -> bool:
    try:
        _renewal_price(order, getattr(order, 'user', None))
        return True
    except RenewalPriceMissingError:
        return False


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _auto_renew_task_status(order, now, *, latest_failure_reason: str | None = None, expires_at=None):
    if not getattr(order, 'auto_renew_enabled', False):
        return None
    last_renewed_at = getattr(order, 'last_renewed_at', None)
    if last_renewed_at and last_renewed_at >= now - timezone.timedelta(days=1):
        return 'auto_renew_success', '自动续费成功'
    if expires_at is None:
        expires_at = order_asset_expiry(order)
    suspend_at = getattr(order, 'suspend_at', None)
    in_renew_window = bool(expires_at and expires_at <= now + timezone.timedelta(days=1) and expires_at > now)
    in_shutdown_fallback = bool(expires_at and expires_at <= now and suspend_at and suspend_at > now)
    in_retry_window = bool(in_renew_window or in_shutdown_fallback or expires_at and expires_at <= now)
    price_missing_fixed = bool(
        order.status == 'renew_pending'
        and in_retry_window
        and _auto_renew_failure_was_price_missing(latest_failure_reason)
        and _order_has_renewal_price(order)
    )
    if price_missing_fixed:
        return 'auto_renew_pending', '自动续费待执行'
    if order.status == 'renew_pending' and in_retry_window:
        return 'auto_renew_failed', '自动续费失败/待补余额'
    if order.status in {'completed', 'expiring', 'renew_pending'} and (in_renew_window or in_shutdown_fallback):
        return 'auto_renew_pending', '自动续费待执行'
    return None


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _auto_renew_pinned_task(now):
    total_enabled = CloudServerOrder.objects.filter(auto_renew_enabled=True).count()
    retry_queryset = CloudAutoRenewRetryTask.objects.filter(
        status__in=[CloudAutoRenewRetryTask.STATUS_PENDING, CloudAutoRenewRetryTask.STATUS_FAILED],
    )
    pending_count = retry_queryset.filter(status=CloudAutoRenewRetryTask.STATUS_PENDING).count()
    failed_count = retry_queryset.filter(status=CloudAutoRenewRetryTask.STATUS_FAILED).count()
    success_count = CloudAutoRenewPatrolLog.objects.filter(is_success=True, executed_at__gte=now - timezone.timedelta(days=1)).count()
    latest_time = (
        CloudAutoRenewPatrolLog.objects.order_by('-executed_at').values_list('executed_at', flat=True).first()
        or now
    )
    if failed_count:
        execution_status, execution_status_label = 'auto_renew_failed', '自动续费失败/待补余额'
    elif pending_count:
        execution_status, execution_status_label = 'auto_renew_pending', '自动续费待执行'
    elif success_count:
        execution_status, execution_status_label = 'auto_renew_success', '自动续费成功'
    else:
        execution_status, execution_status_label = 'active', '自动续费巡检中'
    return {
        'id': -10001,
        'order_no': 'AUTO_RENEW_PATROL',
        'task_type': 'auto_renew',
        'task_label': '自动续费巡检',
        'status': 'active',
        'status_label': '置顶',
        'execution_status': execution_status,
        'execution_status_label': execution_status_label,
        'provider': 'system',
        'provider_label': '系统任务',
        'plan_name': '多IP自动续费',
        'public_ip': f'{total_enabled} 个IP',
        'note': f'固定置顶任务，不重复新建；每30分钟巡检一次。开启自动续费 {total_enabled} 个，待执行 {pending_count} 个，失败/待补余额 {failed_count} 个，近24小时成功 {success_count} 个。',
        'created_at': None,
        'updated_at': _iso(latest_time),
        'related_path': '/admin/tasks/auto-renew',
        'detail_path': '/admin/tasks/auto-renew',
        'order_detail_path': '/admin/tasks/auto-renew',
        'order_link_path': '/admin/tasks/auto-renew',
    }


# 功能：处理 后台 API 接口 中的 tasks overview 业务流程。
@dashboard_login_required
@require_GET
def tasks_overview(request):
    now = timezone.now()
    orders = CloudServerOrder.objects.order_by('-updated_at')[:100]
    items = []
    pinned_auto_renew = _auto_renew_pinned_task(now)
    if pinned_auto_renew:
        items.append(pinned_auto_renew)
    for order in orders:
        is_regular_task = order.status in {'paid', 'provisioning', 'renew_pending', 'expiring', 'suspended', 'deleting', 'failed'}
        if not is_regular_task:
            continue
        execution_status, execution_status_label = (
            order.status,
            dict(CloudServerOrder.STATUS_CHOICES).get(order.status, order.status),
        )
        items.append({
            'id': order.id,
            'order_id': order.id,
            'order_no': order.order_no,
            'task_type': 'cloud_order',
            'task_label': '云服务器任务',
            'status': order.status,
            'status_label': dict(CloudServerOrder.STATUS_CHOICES).get(order.status, order.status),
            'execution_status': execution_status,
            'execution_status_label': execution_status_label,
            'provider': order.provider,
            'provider_label': _provider_label(order.provider),
            'plan_name': order.plan_name,
            'public_ip': order.public_ip,
            'note': order.provision_note,
            'created_at': _iso(order.created_at),
            'updated_at': _iso(order.updated_at),
            'related_path': f'/admin/cloud-orders/{order.id}',
            'detail_path': f'/admin/cloud-orders/{order.id}',
            'order_detail_path': f'/admin/cloud-orders/{order.id}',
            'order_link_path': f'/admin/cloud-orders/{order.id}',
        })
    return _ok(items[:50])


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _auto_renew_due_item_payload(order, *, queue_status: str = 'due_now', queue_status_label: str = '本轮待执行', next_run_at=None, last_failure_reason: str | None = None, notice: dict | None = None):
    user = getattr(order, 'user', None)
    usernames = list(getattr(user, 'usernames', []) or []) if user else []
    user_payload = _user_payload({
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'username': user.username,
        'first_name': user.first_name,
        'usernames': usernames,
        'primary_username': usernames[0] if usernames else '',
    }) if user else None
    notice_type = 'auto_renew_notice'
    latest_log = None
    sent_at = getattr(order, 'auto_renew_notice_sent_at', None)
    notice_was_provided = notice is not None
    notice = notice if notice_was_provided else (_notice_payload_for_order(order) or {})
    expires_at = notice.get('expires_at')
    if expires_at is None and not notice_was_provided:
        expires_at = order_asset_expiry(order)
    auto_renew_at = expires_at - timezone.timedelta(days=1) if expires_at else None
    return {
        'id': order.id,
        'order_id': order.id,
        'order_no': order.order_no,
        'ip': notice.get('ip') or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '未分配',
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'queue_status': queue_status,
        'queue_status_label': queue_status_label,
        **_notice_status_payload(sent_at=sent_at, latest_log=latest_log, queue_status=queue_status),
        **_notice_channel_payload(user, latest_log),
        'notice_text_preview': _notice_task_text_preview(order, notice_type, notice),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'balance': _decimal_to_str(getattr(user, 'balance', None)) if user and getattr(user, 'balance', None) is not None else None,
        'actual_expires_at': _iso(expires_at),
        'auto_renew_at': _iso(auto_renew_at),
        'next_run_at': _iso(next_run_at),
        'last_failure_reason': last_failure_reason,
        'suspend_at': _iso(notice.get('suspend_at') or getattr(order, 'suspend_at', None)),
        'delete_at': _iso(notice.get('delete_at') or getattr(order, 'delete_at', None)),
        'ip_recycle_at': _iso(notice.get('ip_recycle_at') or getattr(order, 'ip_recycle_at', None)),
        'related_path': f'/admin/cloud-orders/{order.id}',
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
    }


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _auto_renew_order_has_active_notice(order) -> bool:
    if not order:
        return False
    if getattr(order, 'status', None) not in {'completed', 'expiring', 'renew_pending'}:
        return False
    notice = _notice_payload_for_order(order)
    ip = str(notice.get('ip') if notice else getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '').strip()
    if not ip:
        return False
    linked_assets = list(CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, order=order).only('id', 'status', 'is_active')[:20])
    if not linked_assets:
        return True
    excluded_statuses = {
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_TERMINATED,
        CloudAsset.STATUS_TERMINATING,
    }
    return any(asset.is_active and asset.status not in excluded_statuses for asset in linked_assets)


_AUTO_RENEW_ACTIVE_ORDER_STATUSES = {'completed', 'expiring', 'renew_pending'}
_AUTO_RENEW_EXCLUDED_ASSET_STATUSES = {
    CloudAsset.STATUS_DELETED,
    CloudAsset.STATUS_DELETING,
    CloudAsset.STATUS_TERMINATED,
    CloudAsset.STATUS_TERMINATING,
}


def _auto_renew_notice_context(orders: list[CloudServerOrder]) -> dict[int, dict]:
    if not orders:
        return {}
    order_ids = [order.id for order in orders if getattr(order, 'id', None)]
    payload_map = _bulk_notice_payload_map(orders)
    context = {
        order.id: {
            'notice': payload_map.get(order.id),
            'has_assets': False,
            'has_active_asset': False,
        }
        for order in orders
        if getattr(order, 'id', None)
    }
    asset_rows = CloudAsset.objects.filter(order_id__in=order_ids, kind=CloudAsset.KIND_SERVER).values('order_id', 'status', 'is_active')
    for row in asset_rows:
        order_context = context.setdefault(row['order_id'], {'notice': None, 'has_assets': False, 'has_active_asset': False})
        order_context['has_assets'] = True
        if row.get('is_active') and row.get('status') not in _AUTO_RENEW_EXCLUDED_ASSET_STATUSES:
            order_context['has_active_asset'] = True
    return context


def _auto_renew_notice_from_context(order, context: dict[int, dict]) -> dict | None:
    if not order:
        return None
    order_context = context.get(order.id) or {}
    notice = order_context.get('notice')
    if order_context.get('has_assets') and not order_context.get('has_active_asset'):
        return None
    if notice:
        return notice
    if order_context.get('has_assets'):
        return None
    ip = str(getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '').strip()
    if not ip or getattr(order, 'status', None) not in _AUTO_RENEW_ACTIVE_ORDER_STATUSES:
        return None
    return {
        'ip': ip,
        'expires_at': None,
        'suspend_at': getattr(order, 'suspend_at', None),
        'delete_at': getattr(order, 'delete_at', None),
        'ip_recycle_at': getattr(order, 'ip_recycle_at', None),
        'auto_renew_enabled': bool(getattr(order, 'auto_renew_enabled', False)),
        'asset_id': None,
    }


def _auto_renew_due_asset_rows(now):
    due_until = now + AUTO_RENEW_BEFORE_EXPIRY_WINDOW
    due_qs = (
        CloudAsset.objects.select_related('order', 'order__user', 'cloud_account', 'order__cloud_account')
        .filter(
            kind=CloudAsset.KIND_SERVER,
            actual_expires_at__isnull=False,
            order__auto_renew_enabled=True,
            order__status__in=list(_AUTO_RENEW_ACTIVE_ORDER_STATUSES),
        )
        .exclude(public_ip__isnull=True)
        .exclude(public_ip='')
        .exclude(status__in=list(_AUTO_RENEW_EXCLUDED_ASSET_STATUSES))
        .filter(
            Q(actual_expires_at__gt=now, actual_expires_at__lte=due_until)
            | Q(actual_expires_at__lte=now, order__suspend_at__gt=now)
        )
        .order_by('actual_expires_at', 'order_id', 'id')
    )
    return list(due_qs)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _auto_renew_future_plan_items(now, next_run_at, due_orders: list, *, notice_by_order: dict[int, dict] | None = None):
    plan_items = []
    seen = set()
    for order in due_orders:
        seen.add(order.id)
    future_assets = (
        CloudAsset.objects.select_related('order', 'order__user')
        .filter(
            kind=CloudAsset.KIND_SERVER,
            actual_expires_at__isnull=False,
            order__auto_renew_enabled=True,
            order__status__in=['completed', 'expiring', 'renew_pending'],
        )
        .exclude(public_ip__isnull=True)
        .exclude(public_ip='')
        .exclude(status__in=list(_AUTO_RENEW_EXCLUDED_ASSET_STATUSES))
        .exclude(order_id__in=list(seen))
        .order_by('actual_expires_at', 'order_id', 'id')[:100]
    )
    for asset in future_assets:
        order = asset.order
        if not order or order.id in seen:
            continue
        if _notice_asset_is_unattached_static_ip(asset):
            continue
        expires_at = asset.actual_expires_at
        if not expires_at:
            continue
        notice = _notice_schedule(order, asset)
        seen.add(order.id)
        if expires_at <= now:
            queue_status = 'fallback_retry'
            queue_status_label = '过期后兜底重试'
        elif expires_at <= now + timezone.timedelta(days=1):
            queue_status = 'within_window'
            queue_status_label = '24小时内进入执行窗口'
        else:
            queue_status = 'scheduled_future'
            queue_status_label = '未来计划'
        if notice_by_order is not None:
            notice_by_order[order.id] = notice
        plan_items.append(_auto_renew_due_item_payload(order, queue_status=queue_status, queue_status_label=queue_status_label, next_run_at=next_run_at, notice=notice))
    return plan_items


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _collect_auto_renew_due_orders(now):
    due_orders = []
    due_ids = set()
    notice_by_order = {}
    for asset in _auto_renew_due_asset_rows(now):
        if _notice_asset_is_unattached_static_ip(asset):
            continue
        order = asset.order
        if not order or order.id in due_ids:
            continue
        due_ids.add(order.id)
        due_orders.append(order)
        notice_by_order[order.id] = _notice_schedule(order, asset)
    history_qs = CloudAutoRenewPatrolLog.objects.select_related('order', 'order__user', 'user').order_by('-executed_at', '-id')

    retry_candidates = []
    recent_logs = history_qs.filter(executed_at__gte=now - timezone.timedelta(days=7))
    seen_history_order_ids = set()
    latest_failure_by_order = {}
    for log in recent_logs:
        order = getattr(log, 'order', None)
        if not order:
            continue
        if order.id in seen_history_order_ids:
            continue
        seen_history_order_ids.add(order.id)
        if log.is_success:
            continue
        if not getattr(order, 'auto_renew_enabled', False):
            continue
        if order.id in due_ids:
            latest_failure_by_order[order.id] = log.failure_reason
            continue
        retry_candidates.append((order, 'retry_failed', '失败待重试', log.failure_reason))

    retry_orders = []
    retry_context = _auto_renew_notice_context([item[0] for item in retry_candidates])
    for order, queue_status, queue_status_label, failure_reason in retry_candidates:
        notice = _auto_renew_notice_from_context(order, retry_context)
        if not notice:
            continue
        due_ids.add(order.id)
        notice_by_order[order.id] = notice
        latest_failure_by_order[order.id] = failure_reason
        retry_orders.append((order, queue_status, queue_status_label, failure_reason))

    fallback_orders = []
    fallback_assets = (
        CloudAsset.objects.select_related('order', 'order__user')
        .filter(
            kind=CloudAsset.KIND_SERVER,
            actual_expires_at__lte=now,
            order__auto_renew_enabled=True,
            order__status__in=['completed', 'expiring', 'renew_pending'],
        )
        .exclude(public_ip__isnull=True)
        .exclude(public_ip='')
        .exclude(status__in=list(_AUTO_RENEW_EXCLUDED_ASSET_STATUSES))
        .exclude(order_id__in=list(due_ids))
        .order_by('actual_expires_at', 'order_id', 'id')[:100]
    )
    for asset in fallback_assets:
        order = asset.order
        if not order or order.id in due_ids:
            continue
        if _notice_asset_is_unattached_static_ip(asset):
            continue
        due_ids.add(order.id)
        notice_by_order[order.id] = _notice_schedule(order, asset)
        fallback_orders.append((order, 'fallback_retry', '过期后兜底重试', None))

    return {
        'due_orders': due_orders,
        'retry_orders': retry_orders,
        'fallback_orders': fallback_orders,
        'history_qs': history_qs,
        'due_ids': due_ids,
        'notice_by_order': notice_by_order,
        'latest_failure_by_order': latest_failure_by_order,
    }


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
async def _await_result(awaitable):
    return await awaitable


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _run_auto_renew_sync(order_id: int):
    result = _run_auto_renew(order_id)
    if inspect.isawaitable(result):
        return async_to_sync(_await_result)(result)
    return result


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _manual_run_auto_renew_queue(orders: list[tuple[CloudServerOrder, str]], *, batch_id: str | None = None):
    batch_id = batch_id or uuid.uuid4().hex[:16]
    results = []
    for order, queue_status in orders:
        notice = _notice_payload_for_order(order) or {}
        renewed, err, balance_change = _run_auto_renew_sync(order.id)
        renewed_order_id = getattr(renewed, 'id', None) or order.id
        ip = notice.get('ip') or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '未分配'
        ok = not bool(err)
        async_to_sync(_record_auto_renew_patrol_log)(
            order.id,
            batch_id=batch_id,
            ip=ip,
            ok=ok,
            error=err,
            balance_change=balance_change,
            renewed_order_id=renewed_order_id,
        )
        results.append({
            'order_id': order.id,
            'renewed_order_id': renewed_order_id,
            'order_no': order.order_no,
            'ip': ip,
            'queue_status': queue_status,
            'ok': ok,
            'error': err,
        })
    return {
        'batch_id': batch_id,
        'items': results,
        'total': len(results),
        'success_count': sum(1 for item in results if item['ok']),
        'failure_count': sum(1 for item in results if not item['ok']),
    }


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _auto_renew_history_item_payload(log):
    order_id = getattr(log, 'completed_order_id', None) or getattr(log, 'order_id', None)
    return {
        'id': log.id,
        'batch_id': log.batch_id,
        'order_id': order_id,
        'order_no': log.order_no,
        'ip': log.ip,
        'provider': log.provider,
        'provider_label': _provider_label(log.provider),
        'user_id': log.user_id,
        'tg_user_id': log.tg_user_id,
        'user_display_name': log.user_display_name or '未绑定用户',
        'username_label': log.username_label or '-',
        'is_success': bool(log.is_success),
        'result_label': '成功' if log.is_success else '失败',
        'failure_reason': log.failure_reason,
        'currency': log.currency,
        'balance_before': _decimal_to_str(log.balance_before) if log.balance_before is not None else None,
        'balance_after': _decimal_to_str(log.balance_after) if log.balance_after is not None else None,
        'balance_change': _decimal_to_str(log.balance_change) if log.balance_change is not None else None,
        'actual_expires_at': _iso(order_asset_expiry(log.order)),
        'executed_at': _iso(log.executed_at),
        'related_path': f'/admin/cloud-orders/{order_id}' if order_id else '',
        'detail_path': f'/admin/cloud-orders/{order_id}' if order_id else '',
        'order_detail_path': f'/admin/cloud-orders/{order_id}' if order_id else '',
        'order_link_path': f'/admin/cloud-orders/{order_id}' if order_id else '',
    }


_AUTO_RENEW_PLAN_DUE_STATUSES = {'due_now', 'retry_failed', 'fallback_retry'}


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _build_auto_renew_plan_items(now=None):
    now = now or timezone.now()
    queue = _collect_auto_renew_due_orders(now)
    history_qs = queue['history_qs']
    latest_log = history_qs.first()
    last_run_at = getattr(latest_log, 'executed_at', None)
    next_run_at = (last_run_at + timezone.timedelta(minutes=30)) if last_run_at else (now + timezone.timedelta(minutes=30))
    due_orders = queue['due_orders']
    notice_by_order = queue.get('notice_by_order') or {}
    latest_failure_by_order = queue.get('latest_failure_by_order') or {}
    # 功能：处理 后台 API 接口 中的 due queue state 业务流程。
    def due_queue_state(order):
        notice = notice_by_order.get(order.id) or {}
        status = _auto_renew_task_status(order, now, latest_failure_reason=latest_failure_by_order.get(order.id), expires_at=notice.get('expires_at'))
        return status[0] if status else ''

    due_items = []
    for order in due_orders:
        queue_state = due_queue_state(order)
        due_items.append(
            _auto_renew_due_item_payload(
                order,
                queue_status='retry_failed' if queue_state == 'auto_renew_failed' else 'due_now',
                queue_status_label='失败待重试' if queue_state == 'auto_renew_failed' else '本轮待执行',
                next_run_at=next_run_at,
                last_failure_reason=latest_failure_by_order.get(order.id),
                notice=notice_by_order.get(order.id),
            )
        )
    # 功能：处理 后台 API 接口 中的 retry item payload 业务流程。
    def retry_item_payload(order, queue_status, queue_status_label, last_failure_reason):
        notice = notice_by_order.get(order.id) or {}
        status = _auto_renew_task_status(order, now, latest_failure_reason=last_failure_reason, expires_at=notice.get('expires_at'))
        if status and status[0] == 'auto_renew_pending':
            queue_status = 'due_now'
            queue_status_label = '本轮待执行'
        return _auto_renew_due_item_payload(
            order,
            queue_status=queue_status,
            queue_status_label=queue_status_label,
            next_run_at=next_run_at,
            last_failure_reason=last_failure_reason,
            notice=notice_by_order.get(order.id),
        )

    due_items.extend([
        retry_item_payload(order, queue_status, queue_status_label, last_failure_reason)
        for order, queue_status, queue_status_label, last_failure_reason in queue['retry_orders']
    ])
    due_items.extend([
        _auto_renew_due_item_payload(order, queue_status=queue_status, queue_status_label=queue_status_label, next_run_at=next_run_at, notice=notice_by_order.get(order.id))
        for order, queue_status, queue_status_label, _ in queue['fallback_orders']
    ])
    all_future_items = _auto_renew_future_plan_items(
        now,
        next_run_at,
        [
            *due_orders,
            *[item[0] for item in queue['retry_orders']],
            *[item[0] for item in queue['fallback_orders']],
        ],
        notice_by_order=notice_by_order,
    )
    future_plan_items = [
        item for item in all_future_items
        if item.get('queue_status') not in _AUTO_RENEW_PLAN_DUE_STATUSES
    ]
    return {
        'due_items': due_items,
        'future_plan_items': future_plan_items,
        'history_qs': history_qs,
        'last_run_at': last_run_at,
        'next_run_at': next_run_at,
    }


_NOTICE_TASK_TYPES = {
    'renew_notice': {'label': '到期提醒', 'field': 'renew_notice_sent_at', 'event': 'renew_notice_batch'},
    'auto_renew_notice': {'label': '自动续费预提醒', 'field': 'auto_renew_notice_sent_at', 'event': 'auto_renew_notice'},
    'delete_notice': {'label': '删机提醒', 'field': 'delete_notice_sent_at', 'event': 'delete_notice'},
    'recycle_notice': {'label': 'IP回收提醒', 'field': 'recycle_notice_sent_at', 'event': 'recycle_notice'},
}

_NOTICE_HISTORY_LABELS = {
    **{key: item['label'] for key, item in _NOTICE_TASK_TYPES.items()},
    'renew_notice_batch': '到期提醒',
}


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_task_time(order, notice_type: str, notice: dict | None = None):
    notice = notice or _notice_payload_for_order(order) or {}
    if notice_type == 'renew_notice':
        expires_at = notice.get('expires_at') or order_asset_expiry(order)
        try:
            notice_days = max(1, int(get_runtime_config('cloud_renew_notice_days', 5) or 5))
        except Exception:
            notice_days = 5
        return expires_at - timezone.timedelta(days=notice_days) if expires_at else None
    if notice_type == 'auto_renew_notice':
        expires_at = notice.get('expires_at') or order_asset_expiry(order)
        return expires_at - timezone.timedelta(days=2) if expires_at else None
    if notice_type == 'delete_notice':
        delete_at = notice.get('delete_at') or getattr(order, 'delete_at', None)
        return delete_at - timezone.timedelta(days=1) if delete_at else None
    if notice_type == 'recycle_notice':
        recycle_at = notice.get('ip_recycle_at') or getattr(order, 'ip_recycle_at', None)
        return recycle_at - timezone.timedelta(days=1) if recycle_at else None
    return None


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_task_text_preview(order, notice_type: str, notice: dict | None = None) -> str:
    notice = notice or _notice_payload_for_order(order) or {}
    ip = notice.get('ip') or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '未分配'
    expires_at = notice.get('expires_at') or order_asset_expiry(order)
    if notice_type == 'renew_notice':
        return f'到期提醒：IP {ip} 将于 {_iso(expires_at) or "-"} 到期，请及时续费或确认自动续费状态。'
    if notice_type == 'auto_renew_notice':
        auto_renew_at = expires_at - timezone.timedelta(days=1) if expires_at else None
        return f'自动续费预提醒：IP {ip} 预计于 {_iso(auto_renew_at) or "-"} 自动续费。'
    if notice_type == 'delete_notice':
        return f'删机提醒：IP {ip} 计划于 {_iso(notice.get("delete_at") or getattr(order, "delete_at", None)) or "-"} 删除。'
    if notice_type == 'recycle_notice':
        return f'IP回收提醒：IP {ip} 计划于 {_iso(notice.get("ip_recycle_at") or getattr(order, "ip_recycle_at", None)) or "-"} 回收。'
    return f'{_NOTICE_TASK_TYPES.get(notice_type, {}).get("label", notice_type)}：IP {ip}'


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_attempt_label(attempt: dict) -> str:
    channel = attempt.get('channel') or ''
    if channel == 'bot':
        name = attempt.get('channel_label') or 'Bot'
    elif channel == 'account':
        name = attempt.get('account_label') or (f"账号{attempt.get('account_id')}" if attempt.get('account_id') else '个人号')
    else:
        name = attempt.get('channel_label') or channel or '未知渠道'
    status = '成功' if attempt.get('ok') else '失败'
    error = str(attempt.get('error') or '').strip()
    return f'{name}{status}' + (f'：{error}' if error and not attempt.get('ok') else '')


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_attempts_label(log) -> str:
    attempts = ((getattr(log, 'extra', None) or {}).get('send_attempts') or []) if log else []
    return '；'.join(_notice_attempt_label(attempt) for attempt in attempts)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_attempt_payload(attempt: dict, *, pending: bool = False) -> dict:
    channel = attempt.get('channel') or ''
    if channel == 'bot':
        name = attempt.get('channel_label') or SiteConfig.get('bot_notice_sender_label', 'Bot')
    elif channel == 'account':
        name = attempt.get('account_label') or (f"账号{attempt.get('account_id')}" if attempt.get('account_id') else '个人号')
    else:
        name = attempt.get('channel_label') or channel or '未知渠道'
    ok = bool(attempt.get('ok'))
    status = 'pending' if pending else ('success' if ok else 'failed')
    return {
        'channel': channel,
        'label': name,
        'status': status,
        'status_label': '待轮询' if pending else ('成功' if ok else '失败'),
        'error': str(attempt.get('error') or '').strip(),
        'account_id': attempt.get('account_id'),
    }


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _planned_notice_account_attempts() -> list[dict]:
    account_attempts = []
    accounts = TelegramLoginAccount.objects.filter(status='logged_in', notify_enabled=True).exclude(session_string__isnull=True).exclude(session_string='').order_by('-updated_at', '-id')[:10]
    for account in accounts:
        username = str(account.username or '').strip().lstrip('@')
        account_label = f'{account.label} (@{username})' if username else (account.label or f'账号{account.id}')
        account_attempts.append({'channel': 'account', 'account_id': account.id, 'account_label': account_label, 'ok': False, 'error': ''})
    return account_attempts


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _planned_notice_attempts(user, account_attempts: list[dict] | None = None) -> list[dict]:
    attempts = [{'channel': 'bot', 'channel_label': SiteConfig.get('bot_notice_sender_label', 'Bot'), 'ok': False, 'error': ''}]
    attempts.extend(account_attempts if account_attempts is not None else _planned_notice_account_attempts())
    return [_notice_attempt_payload(attempt, pending=True) for attempt in attempts]


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_channel_attempts_payload(user, latest_log=None, account_attempts: list[dict] | None = None) -> list[dict]:
    attempts = ((getattr(latest_log, 'extra', None) or {}).get('send_attempts') or []) if latest_log else []
    if attempts:
        return [_notice_attempt_payload(attempt) for attempt in attempts]
    if getattr(user, 'tg_user_id', None) if user else None:
        return _planned_notice_attempts(user, account_attempts)
    return []


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_channel_payload(user, latest_log=None, account_attempts: list[dict] | None = None) -> dict:
    attempts = ((getattr(latest_log, 'extra', None) or {}).get('send_attempts') or []) if latest_log else []
    attempt_items = _notice_channel_attempts_payload(user, latest_log, account_attempts)
    success = next((attempt for attempt in attempts if attempt.get('ok')), None)
    if success:
        if success.get('channel') == 'bot':
            bot_label = success.get('channel_label') or SiteConfig.get('bot_notice_sender_label', 'Bot')
            return {'notice_channel': 'telegram_bot', 'notice_channel_label': f'{bot_label} 通知成功', 'notice_channel_attempts': attempt_items}
        account_label = success.get('account_label') or (f"账号{success.get('account_id')}" if success.get('account_id') else '个人号')
        return {'notice_channel': 'telegram_account', 'notice_channel_label': f'{account_label} 通知成功', 'notice_channel_attempts': attempt_items}
    if attempts:
        return {'notice_channel': 'telegram_fallback', 'notice_channel_label': '机器人优先，失败后账号轮询', 'notice_channel_attempts': attempt_items}
    tg_user_id = getattr(user, 'tg_user_id', None) if user else None
    if tg_user_id:
        account_count = max(len(attempt_items) - 1, 0)
        bot_label = SiteConfig.get('bot_notice_sender_label', 'Bot')
        label = f'{bot_label}优先，失败后轮询{account_count}个账号' if account_count else f'{bot_label}优先，暂无账号兜底'
        return {'notice_channel': 'telegram_fallback', 'notice_channel_label': label, 'notice_channel_attempts': attempt_items}
    return {'notice_channel': 'unbound', 'notice_channel_label': '未绑定通知渠道', 'notice_channel_attempts': []}


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_status_payload(*, sent_at=None, latest_log=None, queue_status='scheduled_future') -> dict:
    if sent_at:
        return {'notice_status': 'sent', 'notice_status_label': '已通知', 'retry_label': '-'}
    if latest_log and not latest_log.delivered:
        return {'notice_status': 'failed_retry', 'notice_status_label': '通知失败，待重试', 'retry_label': (_notice_attempts_label(latest_log) + '；' if _notice_attempts_label(latest_log) else '') + '未标记已通知，下一轮生命周期巡检会继续重试'}
    if queue_status in {'due_now', 'fallback_notice'}:
        return {'notice_status': 'pending', 'notice_status_label': '待本轮通知', 'retry_label': '发送失败不会写入已通知时间，会在后续巡检重试'}
    if queue_status == 'within_window':
        return {'notice_status': 'scheduled_soon', 'notice_status_label': '3天内待通知', 'retry_label': '到通知时间后自动发送，失败则重试'}
    return {'notice_status': 'scheduled', 'notice_status_label': '未来计划', 'retry_label': '未到通知时间'}


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_task_item_payload(order, notice_type: str, *, queue_status='scheduled_future', queue_status_label='未来计划', next_run_at=None, latest_log=None, account_attempts: list[dict] | None = None, notice: dict | None = None, fields: set[str] | None = None):
    user = getattr(order, 'user', None)
    usernames = list(getattr(user, 'usernames', []) or []) if user else []
    user_payload = _user_payload({
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'username': user.username,
        'first_name': user.first_name,
        'usernames': usernames,
        'primary_username': usernames[0] if usernames else '',
    }) if user else None
    fields = fields or {'basic', 'channels', 'ips', 'retry', 'text'}
    notice = notice or _notice_payload_for_order(order) or {}
    expires_at = notice.get('expires_at') or order_asset_expiry(order)
    sent_at = getattr(order, _NOTICE_TASK_TYPES.get(notice_type, {}).get('field', ''), None)
    payload = {
        'id': f'{notice_type}-{order.id}',
        'order_id': order.id,
        'order_no': order.order_no,
        'notice_type': notice_type,
        'notice_type_label': _NOTICE_TASK_TYPES.get(notice_type, {}).get('label', notice_type),
        'ip': notice.get('ip') or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '未分配',
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'queue_status': queue_status,
        'queue_status_label': queue_status_label,
        **_notice_status_payload(sent_at=sent_at, latest_log=latest_log, queue_status=queue_status),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'notice_at': _iso(_notice_task_time(order, notice_type, notice)),
        'actual_expires_at': _iso(expires_at),
        'auto_renew_at': _iso(expires_at - timezone.timedelta(days=1)) if expires_at else None,
        'next_run_at': _iso(next_run_at),
        'suspend_at': _iso(notice.get('suspend_at') or getattr(order, 'suspend_at', None)),
        'delete_at': _iso(notice.get('delete_at') or getattr(order, 'delete_at', None)),
        'ip_recycle_at': _iso(notice.get('ip_recycle_at') or getattr(order, 'ip_recycle_at', None)),
        'related_path': f'/admin/cloud-orders/{order.id}',
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
    }
    if 'channels' in fields:
        payload.update(_notice_channel_payload(user, latest_log, account_attempts))
    if 'text' in fields:
        payload['notice_text_preview'] = _notice_task_text_preview(order, notice_type, notice)
    return payload


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_event_type(notice_type: str) -> str:
    return _NOTICE_TASK_TYPES.get(notice_type, {}).get('event') or notice_type


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_actual_batch_payload(notice_type: str, order_ids: list[int]) -> dict:
    if notice_type == 'renew_notice':
        return async_to_sync(_renew_notice_batch_payload)(order_ids)
    if notice_type == 'auto_renew_notice':
        return async_to_sync(_auto_renew_notice_batch_payload)(order_ids)
    if notice_type == 'delete_notice':
        return async_to_sync(_lifecycle_notice_batch_payload)(
            '⚠️ 云服务器删机提醒',
            order_ids,
            '如仍需使用，请尽快续费或联系人工客服处理。',
        )
    if notice_type == 'recycle_notice':
        return async_to_sync(_lifecycle_notice_batch_payload)(
            '♻️ 固定 IP 回收提醒',
            order_ids,
            '固定 IP 回收后将无法继续保留原 IP；如需恢复，请尽快联系人工客服。',
        )
    return {'text': '', 'order_ids': order_ids, 'first_order_id': order_ids[0] if order_ids else None, 'count': len(order_ids)}


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_manual_text_payload(notice_type: str, user_id: int | None, order_ids: list[int]) -> dict:
    event = _notice_event_type(notice_type)
    manual_text = _get_notice_text_override(event, user_id, order_ids)
    return {
        'notice_event': event,
        'notice_override_key': _notice_override_key(event, user_id, order_ids),
        'notice_manual_text': manual_text,
        'notice_has_manual_text': bool(manual_text),
    }


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_latest_log_map():
    logs = CloudUserNoticeLog.objects.filter(event_type__in=list(_NOTICE_HISTORY_LABELS)).order_by('-created_at', '-id')[:1000]
    mapped = {}
    for log in logs:
        keys = [(log.event_type, log.order_id)]
        canonical_event = 'renew_notice' if log.event_type == 'renew_notice_batch' else log.event_type
        keys.append((canonical_event, log.order_id))
        for order_id in (log.extra or {}).get('order_ids') or []:
            keys.append((canonical_event, order_id))
        for key in keys:
            if key[1] and key not in mapped:
                mapped[key] = log
    return mapped


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_history_group_items(logs, account_attempts: list[dict] | None = None) -> list[dict]:
    items = []
    for log in logs:
        extra = log.extra or {}
        order_ids = extra.get('order_ids') or ([log.order_id] if log.order_id else [])
        notice_type = 'renew_notice' if log.event_type == 'renew_notice_batch' else log.event_type
        ip_count = len(order_ids) if order_ids else 1
        item = _notice_task_history_item_payload(log, account_attempts=account_attempts)
        item.update({
            'id': log.id,
            'log_id': log.id,
            'notice_event': log.event_type,
            'order_ids': order_ids,
            'notice_type': notice_type,
            'notice_type_label': _NOTICE_HISTORY_LABELS.get(log.event_type, log.event_type),
            'notice_count': 1,
            'ip_count': ip_count,
            'ips': [log.ip] if log.ip else [],
            'notice_text_preview': log.text_preview or '',
        })
        items.append(item)
    return items


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_task_history_item_payload(log, *, account_attempts: list[dict] | None = None):
    delivered = _notice_effective_delivered(log)
    order = getattr(log, 'order', None)
    return {
        'id': log.id,
        'log_id': log.id,
        'batch_id': log.batch_id,
        'order_id': log.order_id,
        'order_no': log.order_no or '-',
        'notice_type': log.event_type,
        'notice_type_label': _NOTICE_HISTORY_LABELS.get(log.event_type, log.event_type),
        'ip': log.ip or '-',
        'user_id': log.user_id,
        'tg_user_id': getattr(log.user, 'tg_user_id', None) if getattr(log, 'user', None) else None,
        'user_display_name': getattr(log.user, 'display_name', '') or getattr(log.user, 'username', '') or '未绑定用户' if getattr(log, 'user', None) else '未绑定用户',
        'username_label': f'@{log.user.username}' if getattr(log, 'user', None) and getattr(log.user, 'username', '') else '-',
        'delivered': delivered,
        'notice_status': 'sent' if delivered else 'failed_retry',
        'notice_status_label': '已通知' if delivered else '通知失败，待重试',
        'result_label': (_notice_attempts_label(log) or '已送达') if delivered else (_notice_attempts_label(log) or '未送达，后续巡检重试'),
        'account_label': getattr(order, 'account_label', '') if order else '',
        'region_code': getattr(order, 'region_code', '') if order else '',
        'region_name': getattr(order, 'region_name', '') if order else '',
        'server_name': getattr(order, 'server_name', '') if order else '',
        'instance_id': getattr(order, 'instance_id', '') if order else '',
        'order_status': getattr(order, 'status', '') if order else '',
        'order_status_label': _status_label(getattr(order, 'status', ''), CloudServerOrder.STATUS_CHOICES) if order else '',
        'target_chat_id': log.target_chat_id,
        **_notice_channel_payload(getattr(log, 'user', None), log, account_attempts),
        'text_preview': log.text_preview or '',
        'retry_label': '-' if delivered else (_notice_attempts_label(log) + '；' if _notice_attempts_label(log) else '') + '未成功送达，不会写入已通知时间；后续生命周期巡检会重试',
        'created_at': _iso(log.created_at),
        'related_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
        'detail_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
        'order_detail_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
        'order_link_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
    }


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_switch_items() -> list[dict]:
    return [
        {
            'notice_type': notice_type,
            'label': config['label'],
            'key': config['key'],
            'enabled': cloud_notice_type_enabled(notice_type),
        }
        for notice_type, config in NOTICE_TYPE_SWITCH_CONFIG.items()
    ]


# 功能：更新相关业务对象；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_superuser_required
@require_POST
def update_notice_switches(request):
    payload = _read_payload(request)
    switches = payload.get('switches') or []
    if not isinstance(switches, list):
        return _error('通知开关参数无效', status=400)
    known_keys = {config['key']: config for config in NOTICE_TYPE_SWITCH_CONFIG.values()}
    for item in switches:
        if not isinstance(item, dict):
            continue
        key = str(item.get('key') or '').strip()
        if key not in known_keys:
            continue
        enabled = '1' if bool(item.get('enabled')) else '0'
        SiteConfig.set(key, enabled)
    return _ok({'notice_switches': _notice_switch_items()})


NOTICE_EVENT_SENT_FIELD_MAP = {
    'renew_notice': 'renew_notice_sent_at',
    'renew_notice_batch': 'renew_notice_sent_at',
    'auto_renew_notice': 'auto_renew_notice_sent_at',
    'delete_notice': 'delete_notice_sent_at',
    'recycle_notice': 'recycle_notice_sent_at',
}


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _reset_notice_sent_fields_for_log(log) -> int:
    field_name = NOTICE_EVENT_SENT_FIELD_MAP.get(log.event_type)
    if not field_name:
        return 0
    order_ids = []
    extra_ids = ((log.extra or {}).get('order_ids') or []) if getattr(log, 'extra', None) else []
    for item in extra_ids:
        try:
            order_ids.append(int(item))
        except (TypeError, ValueError):
            continue
    if log.order_id:
        order_ids.append(log.order_id)
    order_ids = sorted(set(order_ids))
    if not order_ids:
        return 0
    return CloudServerOrder.objects.filter(id__in=order_ids).update(**{field_name: None})


# 功能：删除或标记删除相关业务对象；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_superuser_required
@require_POST
def delete_notice_history(request, identifier):
    identifier = str(identifier or '').strip()
    queryset = CloudUserNoticeLog.objects.none()
    if identifier.isdigit():
        log = CloudUserNoticeLog.objects.filter(id=int(identifier)).first()
        if log and log.batch_id:
            queryset = CloudUserNoticeLog.objects.filter(batch_id=log.batch_id)
        elif log:
            queryset = CloudUserNoticeLog.objects.filter(id=log.id)
    if not queryset.exists():
        queryset = CloudUserNoticeLog.objects.filter(batch_id=identifier)
    logs = list(queryset)
    if not logs:
        return _error('通知历史不存在', status=404)
    field_order_ids = {}
    for log in logs:
        field_name = NOTICE_EVENT_SENT_FIELD_MAP.get(log.event_type)
        if not field_name:
            continue
        ids = field_order_ids.setdefault(field_name, set())
        extra_ids = ((log.extra or {}).get('order_ids') or []) if getattr(log, 'extra', None) else []
        for item in extra_ids:
            try:
                ids.add(int(item))
            except (TypeError, ValueError):
                continue
        if log.order_id:
            ids.add(log.order_id)
    reset_count = 0
    for field_name, order_ids in field_order_ids.items():
        if order_ids:
            reset_count += CloudServerOrder.objects.filter(id__in=order_ids).update(**{field_name: None})
    deleted_count, _ = queryset.delete()
    return _ok({'deleted': True, 'deleted_count': deleted_count, 'reset_count': reset_count})


# 功能：更新相关业务对象；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_superuser_required
@require_POST
def update_notice_plan_text(request):
    payload = _read_payload(request)
    event = str(payload.get('notice_event') or payload.get('event') or '').strip()
    user_id = payload.get('user_id')
    order_ids = payload.get('order_ids') or []
    text = str(payload.get('notice_text') or payload.get('text') or '').strip()
    if not event:
        return _error('缺少通知事件类型', status=400)
    if not isinstance(order_ids, list) or not order_ids:
        return _error('缺少通知订单列表', status=400)
    try:
        normalized_user_id = int(user_id) if user_id else None
        normalized_order_ids = [int(item) for item in order_ids if item]
    except Exception:
        return _error('通知订单参数无效', status=400)
    key = _set_notice_text_override(event, normalized_user_id, normalized_order_ids, text)
    return _ok({
        'notice_override_key': key,
        'notice_manual_text': text,
        'notice_has_manual_text': bool(text),
    })


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _request_int_param(request, key: str, default: int, *, minimum: int = 1, maximum: int = 500) -> int:
    try:
        value = int(request.GET.get(key) or default)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _request_json_payload(request) -> dict:
    try:
        raw = request.body.decode('utf-8') if getattr(request, 'body', None) else ''
    except Exception:
        raw = ''
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _compact_notice_items(items: list[dict], *, text_limit: int = 1200, ip_limit: int = 50) -> list[dict]:
    for item in items:
        text = str(item.get('notice_text_preview') or item.get('text_preview') or '')
        if len(text) > text_limit:
            preview = text[:text_limit] + '\n...（文案过长，已折叠预览）'
            if 'notice_text_preview' in item:
                item['notice_text_preview'] = preview
            if 'text_preview' in item:
                item['text_preview'] = preview
        ips = item.get('ips')
        if isinstance(ips, list) and len(ips) > ip_limit:
            item['ips'] = ips[:ip_limit] + [f'... 另有 {len(ips) - ip_limit} 个 IP']
    return items


def _request_notice_fields(request) -> set[str]:
    raw = str(request.GET.get('fields') or '').strip()
    allowed = {'basic', 'actions', 'channels', 'ips', 'retry', 'text'}
    if not raw:
        return set(allowed)
    return {item.strip().lower() for item in raw.split(',') if item.strip().lower() in allowed}


def _strip_notice_item_fields(items: list[dict], fields: set[str]) -> list[dict]:
    if not items:
        return items
    hidden_keys: set[str] = set()
    if 'channels' not in fields:
        hidden_keys.update({
            'notice_channel',
            'notice_channel_attempts',
            'notice_channel_label',
            'target_chat_id',
        })
    if 'ips' not in fields:
        hidden_keys.update({'ip', 'ips'})
    if 'retry' not in fields:
        hidden_keys.update({'failed_retry_count', 'result_label', 'retry_label'})
    if 'text' not in fields:
        hidden_keys.update({
            'notice_has_manual_text',
            'notice_manual_text',
            'notice_override_key',
            'notice_text_preview',
            'order_ids',
            'text_preview',
        })
    if not hidden_keys:
        return items
    for item in items:
        for key in hidden_keys:
            item.pop(key, None)
    return items


_NOTICE_ASSET_EXCLUDED_STATUSES = {
    CloudAsset.STATUS_DELETED,
    CloudAsset.STATUS_DELETING,
    CloudAsset.STATUS_TERMINATED,
    CloudAsset.STATUS_TERMINATING,
}


def _notice_plan_base_asset_queryset():
    return (
        CloudAsset.objects
        .select_related('order', 'order__user', 'cloud_account', 'order__cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER, order__isnull=False, actual_expires_at__isnull=False)
        .exclude(public_ip__isnull=True)
        .exclude(public_ip='')
        .exclude(status__in=_NOTICE_ASSET_EXCLUDED_STATUSES)
    )


def _notice_deferred_lifecycle_time(stored_at, computed_at, now, *, prefer_stored=False):
    if prefer_stored and stored_at:
        return stored_at
    if stored_at and stored_at > now and (not computed_at or computed_at <= now):
        return stored_at
    return computed_at or stored_at


def _notice_schedule_from_asset(order: CloudServerOrder, asset: CloudAsset, *, now) -> dict:
    expires_at = asset.actual_expires_at
    schedule = compute_order_lifecycle_fields(expires_at)
    status = getattr(order, 'status', '')
    suspend_at = _notice_deferred_lifecycle_time(getattr(order, 'suspend_at', None), schedule.get('suspend_at'), now)
    delete_at = _notice_deferred_lifecycle_time(
        getattr(order, 'delete_at', None),
        schedule.get('delete_at'),
        now,
        prefer_stored=status in {'suspended', 'deleting'},
    )
    ip_recycle_at = _notice_deferred_lifecycle_time(
        getattr(order, 'ip_recycle_at', None),
        schedule.get('ip_recycle_at'),
        now,
        prefer_stored=status == 'deleted',
    )
    return {
        'ip': getattr(order, 'public_ip', None) or asset.public_ip,
        'expires_at': expires_at,
        'suspend_at': suspend_at,
        'delete_at': delete_at,
        'ip_recycle_at': ip_recycle_at,
        'auto_renew_enabled': bool(getattr(order, 'auto_renew_enabled', False)),
        'asset_id': asset.id,
    }


def _notice_queue_status_for_time(notice_at, now):
    if not notice_at or notice_at <= now:
        return 'due_now', '本轮待通知'
    if notice_at <= now + timezone.timedelta(days=3):
        return 'within_window', '3天内待通知'
    return 'scheduled_future', '未来计划'


def _notice_assets_for_type(notice_type: str, *, now, future: bool):
    active_statuses = ['completed', 'expiring', 'renew_pending']
    qs = _notice_plan_base_asset_queryset()
    if notice_type == 'renew_notice':
        try:
            notice_days = max(1, int(get_runtime_config('cloud_renew_notice_days', 5) or 5))
        except Exception:
            notice_days = 5
        notice_window = now + timezone.timedelta(days=notice_days + 3)
        qs = qs.filter(
            order__status__in=active_statuses,
            order__cloud_reminder_enabled=True,
        )
        if str(get_runtime_config('cloud_renew_notice_debug_repeat', '0') or '').strip().lower() not in {'1', 'true', 'yes', 'on'}:
            qs = qs.filter(order__renew_notice_sent_at__isnull=True)
        if future:
            qs = qs.filter(actual_expires_at__gt=notice_window)
        else:
            qs = qs.filter(actual_expires_at__gt=now, actual_expires_at__lte=notice_window)
        return qs.order_by('actual_expires_at', '-updated_at', '-id')
    if notice_type == 'auto_renew_notice':
        notice_window = now + timezone.timedelta(days=5)
        qs = qs.filter(
            order__status__in=active_statuses,
            order__auto_renew_enabled=True,
            order__auto_renew_notice_sent_at__isnull=True,
        )
        if future:
            qs = qs.filter(actual_expires_at__gt=notice_window)
        else:
            qs = qs.filter(
                actual_expires_at__gt=now + AUTO_RENEW_BEFORE_EXPIRY_WINDOW,
                actual_expires_at__lte=notice_window,
            )
        return qs.order_by('actual_expires_at', '-updated_at', '-id')
    if notice_type == 'delete_notice':
        notice_window = now + timezone.timedelta(days=4)
        qs = qs.filter(
            order__status__in=['suspended', 'deleting'],
            order__delete_reminder_enabled=True,
            order__delete_notice_sent_at__isnull=True,
            order__delete_at__isnull=False,
        )
        if future:
            qs = qs.filter(order__delete_at__gt=notice_window)
        else:
            qs = qs.filter(order__delete_at__gt=now, order__delete_at__lte=notice_window)
        return qs.order_by('order__delete_at', '-updated_at', '-id')
    if notice_type == 'recycle_notice':
        notice_window = now + timezone.timedelta(days=4)
        qs = qs.filter(
            order__status='deleted',
            order__ip_recycle_reminder_enabled=True,
            order__recycle_notice_sent_at__isnull=True,
            order__ip_recycle_at__isnull=False,
        )
        if future:
            qs = qs.filter(order__ip_recycle_at__gt=notice_window)
        else:
            qs = qs.filter(order__ip_recycle_at__gt=now, order__ip_recycle_at__lte=notice_window)
        return qs.order_by('order__ip_recycle_at', '-updated_at', '-id')
    return qs.none()


def _notice_plan_scope_for_future(future: bool) -> str:
    return 'future' if future else 'due'


def _notice_plan_scope_label(plan_scope: str) -> str:
    return '未来计划' if plan_scope == 'future' else '近期计划'


def _notice_group_user_payload(row: dict) -> dict:
    user_id = row.get('order__user_id')
    username = row.get('order__user__username') or ''
    user_payload = _user_payload({
        'id': user_id,
        'tg_user_id': row.get('order__user__tg_user_id'),
        'username': username,
        'first_name': row.get('order__user__first_name') or '',
        'usernames': [],
        'primary_username': '',
    }) if user_id else None
    return {
        'user_id': user_id,
        'tg_user_id': row.get('order__user__tg_user_id') if user_id else None,
        'user_display_name': (user_payload or {}).get('display_name') or '未绑定用户',
        'username_label': (user_payload or {}).get('username_label') or '-',
    }


def _notice_group_key_from_row(row: dict, notice_type: str, plan_scope: str) -> str:
    user_id = row.get('order__user_id')
    if user_id:
        user_key = user_id
    else:
        fallback = row.get('order__user__tg_user_id') or row.get('order__user__username') or row.get('order__user__first_name') or 'unknown'
        user_key = f'unbound:{fallback}'
    return f'{user_key}:{notice_type}:{plan_scope}'


def _notice_source_time_field(notice_type: str) -> str:
    if notice_type in {'renew_notice', 'auto_renew_notice'}:
        return 'actual_expires_at'
    if notice_type == 'delete_notice':
        return 'order__delete_at'
    if notice_type == 'recycle_notice':
        return 'order__ip_recycle_at'
    return 'actual_expires_at'


def _notice_group_next_at(notice_type: str, source_at):
    if not source_at:
        return None
    if notice_type == 'renew_notice':
        try:
            notice_days = max(1, int(get_runtime_config('cloud_renew_notice_days', 5) or 5))
        except Exception:
            notice_days = 5
        return source_at - timezone.timedelta(days=notice_days)
    if notice_type == 'auto_renew_notice':
        return source_at - timezone.timedelta(days=2)
    if notice_type in {'delete_notice', 'recycle_notice'}:
        return source_at - timezone.timedelta(days=1)
    return source_at


def _notice_group_base_queryset(notice_type: str, *, now, future: bool):
    qs = _notice_assets_for_type(notice_type, now=now, future=future)
    if notice_type == 'auto_renew_notice':
        qs = qs.exclude(
            provider='aws_lightsail',
            instance_id='',
            provider_status__icontains='未附加',
        )
    if notice_type == 'delete_notice':
        qs = qs.exclude(server_delete_enabled=False)
    if notice_type == 'recycle_notice':
        qs = qs.exclude(ip_delete_enabled=False)
    return qs


def _notice_group_rows_for_scope(now, *, future: bool) -> list[dict]:
    plan_scope = _notice_plan_scope_for_future(future)
    rows = []
    for notice_type, config in _NOTICE_TASK_TYPES.items():
        if not cloud_notice_type_enabled(notice_type):
            continue
        time_field = _notice_source_time_field(notice_type)
        qs = _notice_group_base_queryset(notice_type, now=now, future=future)
        for row in qs.values(
            'order__user_id',
            'order__user__tg_user_id',
            'order__user__username',
            'order__user__first_name',
        ).annotate(
            notice_count=Count('order_id', distinct=True),
            next_source_at=Min(time_field),
        ):
            user_payload = _notice_group_user_payload(row)
            next_notice_at = _notice_group_next_at(notice_type, row.get('next_source_at'))
            rows.append({
                'id': _notice_group_key_from_row(row, notice_type, plan_scope),
                'plan_scope': plan_scope,
                'plan_scope_label': _notice_plan_scope_label(plan_scope),
                **user_payload,
                'notice_type': notice_type,
                'notice_type_label': config.get('label') or notice_type,
                'notice_event': _notice_event_type(notice_type),
                'notice_count': int(row.get('notice_count') or 0),
                'ip_count': int(row.get('notice_count') or 0),
                'pending_count': 0,
                'failed_retry_count': 0,
                'next_notice_at': _iso(next_notice_at),
                '_next_notice_at_value': next_notice_at,
            })
    rows.sort(key=lambda item: (
        item.get('user_display_name') or '',
        item.get('username_label') or '',
        item.get('_next_notice_at_value') or timezone.datetime.max.replace(tzinfo=dt_timezone.utc),
        item.get('notice_type_label') or '',
    ))
    return rows


def _notice_group_order_queryset(row: dict, *, now):
    notice_type = row.get('notice_type') or ''
    future = row.get('plan_scope') == 'future'
    qs = _notice_group_base_queryset(notice_type, now=now, future=future)
    user_id = row.get('user_id')
    if user_id:
        qs = qs.filter(order__user_id=user_id)
    else:
        qs = qs.filter(order__user__isnull=True)
    return qs


def _notice_group_summary_from_row(row: dict, *, now, next_run_at, latest_logs: dict, account_attempts: list[dict] | None, fields: set[str]) -> dict:
    order_rows = []
    needs_order_rows = bool({'actions', 'ips', 'retry', 'text', 'full'} & fields)
    if needs_order_rows:
        order_row_limit = None if 'full' in fields else (1000 if {'ips', 'text'} & fields else 1)
        qs = _notice_group_order_queryset(row, now=now)
        order_values = qs.values(
            'order_id',
            'order__order_no',
            'order__account_label',
            'order__region_code',
            'order__region_name',
            'order__server_name',
            'order__instance_id',
            'order__status',
            'order__public_ip',
            'order__previous_public_ip',
            'account_label',
            'asset_name',
            'instance_id',
            'region_code',
            'region_name',
            'status',
            'provider_status',
            'public_ip',
        ).order_by(_notice_source_time_field(row.get('notice_type') or ''), 'order_id')
        if order_row_limit:
            order_values = order_values[:order_row_limit]
        order_rows = list(order_values)
    order_ids = []
    ips = []
    order_items = []
    for item in order_rows:
        order_id = item.get('order_id')
        if order_id and order_id not in order_ids:
            order_ids.append(order_id)
        ip = item.get('order__public_ip') or item.get('public_ip') or item.get('order__previous_public_ip') or '-'
        if ip not in ips:
            ips.append(ip)
        if 'full' in fields:
            asset_status = item.get('status') or ''
            order_status = item.get('order__status') or ''
            order_items.append({
                'order_id': order_id,
                'order_no': item.get('order__order_no') or '',
                'account_label': item.get('account_label') or item.get('order__account_label') or '',
                'region_code': item.get('region_code') or item.get('order__region_code') or '',
                'region_name': item.get('region_name') or item.get('order__region_name') or '',
                'asset_name': item.get('asset_name') or item.get('order__server_name') or item.get('instance_id') or item.get('order__instance_id') or '',
                'instance_id': item.get('instance_id') or item.get('order__instance_id') or '',
                'ip': ip,
                'asset_status': asset_status,
                'asset_status_label': _status_label(asset_status, CloudAsset.STATUS_CHOICES),
                'provider_status': item.get('provider_status') or '',
                'order_status': order_status,
                'order_status_label': _status_label(order_status, CloudServerOrder.STATUS_CHOICES),
            })
    first_order_id = order_ids[0] if order_ids else None
    user = None
    if row.get('user_id'):
        from bot.models import TelegramUser
        user = TelegramUser.objects.filter(id=row.get('user_id')).first()
    latest_log = None
    for order_id in order_ids[:20]:
        latest_log = latest_logs.get((row.get('notice_type'), order_id)) or latest_logs.get((row.get('notice_event'), order_id))
        if latest_log:
            break
    queue_status = 'scheduled_future' if row.get('plan_scope') == 'future' else 'within_window'
    queue_status_label = '未来计划' if queue_status == 'scheduled_future' else '3天内待通知'
    payload = {
        **{key: value for key, value in row.items() if not key.startswith('_')},
        'queue_status': queue_status,
        'queue_status_label': queue_status_label,
        'notice_channel': 'unbound',
        'notice_channel_label': '未绑定通知渠道',
        'notice_channel_attempts': [],
        'ips': ips,
        'order_ids': order_ids,
        'related_path': f'/admin/cloud-orders/{first_order_id}' if first_order_id else '',
        **_notice_status_payload(sent_at=None, latest_log=latest_log, queue_status=queue_status),
    }
    if 'channels' in fields:
        payload.update(_notice_channel_payload(user, latest_log, account_attempts))
    if 'text' in fields:
        manual_payload = _notice_manual_text_payload(row.get('notice_type') or '', row.get('user_id'), order_ids)
        batch_payload = _notice_actual_batch_payload(row.get('notice_type') or '', order_ids)
        manual_text = manual_payload.get('notice_manual_text') or ''
        payload.update(manual_payload)
        payload['notice_text_preview'] = manual_text or batch_payload.get('text') or payload.get('notice_text_preview') or ''
        payload['ip_count'] = int(batch_payload.get('count') or payload.get('ip_count') or 0)
    else:
        payload['notice_text_preview'] = f'{payload.get("notice_type_label") or "通知"}：{payload.get("user_display_name") or "未绑定用户"} 共 {payload.get("ip_count") or 0} 个 IP，系统会合并成一条通知发送。'
    if 'full' in fields:
        payload['order_items'] = order_items
    payload['notice_count'] = 1
    return payload


def _notice_group_summary_page(now, *, limit: int, offset: int, fields: set[str], latest_logs: dict, account_attempts: list[dict] | None, scope: str = 'active') -> tuple[list[dict], int]:
    due_rows = _notice_group_rows_for_scope(now, future=False) if scope in {'active', 'due'} else []
    future_rows = _notice_group_rows_for_scope(now, future=True) if scope in {'active', 'future'} else []
    return _notice_group_summary_page_from_rows(
        due_rows,
        future_rows,
        now=now,
        limit=limit,
        offset=offset,
        fields=fields,
        latest_logs=latest_logs,
        account_attempts=account_attempts,
    )


def _notice_sort_group_rows(rows: list[dict]) -> list[dict]:
    rows.sort(key=lambda item: (
        item.get('_next_notice_at_value') or timezone.datetime.max.replace(tzinfo=dt_timezone.utc),
        item.get('user_display_name') or '',
        item.get('username_label') or '',
        item.get('notice_type_label') or '',
    ))
    return rows


def _notice_group_summary_page_from_rows(due_rows: list[dict], future_rows: list[dict], *, now, limit: int, offset: int, fields: set[str], latest_logs: dict, account_attempts: list[dict] | None) -> tuple[list[dict], int]:
    rows = _notice_sort_group_rows([*due_rows, *future_rows])
    total = len(rows)
    visible_rows = rows[offset:offset + limit] if limit else rows[offset:]
    return [
        _notice_group_summary_from_row(
            row,
            now=now,
            next_run_at=now + timezone.timedelta(minutes=10),
            latest_logs=latest_logs,
            account_attempts=account_attempts,
            fields=fields,
        )
        for row in visible_rows
    ], total


def _notice_plan_total_counts_from_rows(due_rows: list[dict], future_rows: list[dict]) -> dict:
    due_count = sum(int(row.get('notice_count') or 0) for row in due_rows)
    future_count = sum(int(row.get('notice_count') or 0) for row in future_rows)
    due_user_groups = {row.get('id') for row in due_rows}
    future_user_groups = {row.get('id') for row in future_rows}
    return {
        'due_count': due_count,
        'future_count': future_count,
        'due_user_count': len(due_user_groups),
        'future_user_count': len(future_user_groups),
        'active_user_count': len(due_user_groups | future_user_groups),
    }


def _notice_plan_total_counts(now) -> dict:
    due_rows = _notice_group_rows_for_scope(now, future=False)
    future_rows = _notice_group_rows_for_scope(now, future=True)
    return _notice_plan_total_counts_from_rows(due_rows, future_rows)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _build_notice_plan_summary(*, limit=10, offset=0, history_limit=10, history_offset=0, fields: set[str] | None = None, include_total_counts: bool = True):
    now = timezone.now()
    fields = fields or {'basic', 'channels', 'ips', 'retry', 'text'}
    latest_logs = _notice_latest_log_map()
    account_attempts = _planned_notice_account_attempts() if 'channels' in fields else []
    due_rows = _notice_group_rows_for_scope(now, future=False)
    future_rows = _notice_group_rows_for_scope(now, future=True)
    active_user_summary_items, active_user_total = _notice_group_summary_page_from_rows(
        due_rows,
        future_rows,
        now=now,
        limit=limit,
        offset=offset,
        fields=fields,
        latest_logs=latest_logs,
        account_attempts=account_attempts,
    )
    history_qs = CloudUserNoticeLog.objects.select_related('order', 'user').filter(event_type__in=list(_NOTICE_HISTORY_LABELS)).order_by('-created_at', '-id')
    history_rows = _notice_history_group_items(history_qs[history_offset:history_offset + history_limit], account_attempts=account_attempts)
    result = {
        'active_user_summary_items': active_user_summary_items,
        'active_user_total': active_user_total,
        'history_items': history_rows,
        'history_count': history_qs.count(),
    }
    if include_total_counts:
        result['total_counts'] = _notice_plan_total_counts_from_rows(due_rows, future_rows)
    return result


def _notice_plan_preview_items(*, limit=1000, fields: set[str] | None = None):
    now = timezone.now()
    fields = fields or {'basic'}
    latest_logs = _notice_latest_log_map()
    account_attempts = _planned_notice_account_attempts() if 'channels' in fields else []
    rows, _ = _notice_group_summary_page(
        now,
        limit=limit,
        offset=0,
        fields=fields,
        latest_logs=latest_logs,
        account_attempts=account_attempts,
        scope='active',
    )
    return rows


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
# 功能：刷新缓存、快照或派生数据；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_login_required
@require_POST
def refresh_notice_plan_view(request):
    payload = _request_json_payload(request)
    limit = _request_int_param(request, 'limit', int(payload.get('limit') or 1000), maximum=1000)
    history_limit = _request_int_param(request, 'history_limit', int(payload.get('history_limit') or 1000), maximum=5000)
    summary = _build_notice_plan_summary(limit=limit, offset=0, history_limit=history_limit, history_offset=0, fields={'basic'}, include_total_counts=True)
    total_counts = summary.get('total_counts') or {}
    return _ok({
        'refreshed': True,
        'due_count': total_counts.get('due_count', 0),
        'future_count': total_counts.get('future_count', 0),
        'history_count': summary.get('history_count', 0),
    })


# 功能：处理 后台 API 接口 中的 notice task detail 业务流程。
@dashboard_login_required
@require_GET
def notice_task_detail(request):
    now = timezone.now()
    limit = _request_int_param(request, 'limit', 10, maximum=100)
    offset = _request_int_param(request, 'offset', 0, minimum=0, maximum=NOTICE_PLAN_MAX_OFFSET)
    history_limit = _request_int_param(request, 'history_limit', 10, maximum=100)
    history_offset = _request_int_param(request, 'history_offset', 0, minimum=0, maximum=NOTICE_PLAN_MAX_OFFSET)
    compact = str(request.GET.get('compact') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    fields = _request_notice_fields(request)

    next_run_at = now + timezone.timedelta(minutes=10)
    summary = _build_notice_plan_summary(
        limit=limit,
        offset=offset,
        history_limit=history_limit,
        history_offset=history_offset,
        fields=fields,
        include_total_counts=True,
    )
    active_user_summary_items = summary.get('active_user_summary_items') or []
    active_user_total = int(summary.get('active_user_total') or 0)
    visible_history_items = summary.get('history_items') or []
    recent_since = now - timezone.timedelta(days=1)
    recent_history_qs = CloudUserNoticeLog.objects.filter(event_type__in=list(_NOTICE_HISTORY_LABELS), created_at__gte=recent_since)
    recent_history_items = [
        _notice_task_history_item_payload(item)
        for item in recent_history_qs.select_related('order', 'user').order_by('-created_at', '-id')[:1000]
    ]
    latest_history_item = CloudUserNoticeLog.objects.filter(event_type__in=list(_NOTICE_HISTORY_LABELS)).order_by('-created_at', '-id').first()
    recent_success_count = sum(1 for item in recent_history_items if item.get('delivered'))
    recent_failure_count = sum(1 for item in recent_history_items if not item.get('delivered'))
    recent_success_user_count = len({item.get('user_id') for item in recent_history_items if item.get('delivered') and item.get('user_id')})
    recent_failure_user_count = len({item.get('user_id') for item in recent_history_items if not item.get('delivered') and item.get('user_id')})
    last_refresh_at = now

    active_summary_payload = _compact_notice_items(active_user_summary_items) if compact else active_user_summary_items
    history_payload = _compact_notice_items(visible_history_items) if compact else visible_history_items
    _strip_notice_item_fields(active_summary_payload, fields)
    _strip_notice_item_fields(history_payload, fields)
    total_counts = summary.get('total_counts') or {}

    return _ok({
        'task_key': 'cloud_notice_plan',
        'task_label': '通知计划',
        'status_label': '置顶任务',
        'interval_minutes': 10,
        'last_run_at': _iso(latest_history_item.created_at) if latest_history_item else None,
        'next_run_at': _iso(next_run_at),
        'last_refresh_at': _iso(last_refresh_at),
        'due_count': total_counts.get('due_count', 0),
        'due_user_count': total_counts.get('due_user_count', 0),
        'future_count': total_counts.get('future_count', 0),
        'future_user_count': total_counts.get('future_user_count', 0),
        'active_user_count': total_counts.get('active_user_count', active_user_total),
        'history_count': summary.get('history_count', len(history_payload)),
        'recent_success_count': recent_success_count,
        'recent_success_user_count': recent_success_user_count,
        'recent_failure_count': recent_failure_count,
        'recent_failure_user_count': recent_failure_user_count,
        'retry_policy_label': '通知失败不会写入已通知时间；生命周期巡检会在下一轮继续重试，直到成功送达。',
        'notice_switches': _notice_switch_items(),
        'active_user_summary_items': active_summary_payload,
        'history_items': history_payload,
    })


# 功能：处理 后台 API 接口 中的 auto renew task detail 业务流程。
@dashboard_login_required
@require_GET
def auto_renew_task_detail(request):
    now = timezone.now()
    force_refresh = str(request.GET.get('refresh') or request.GET.get('sync') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    bundle = _build_auto_renew_plan_items(now=now)
    did_refresh = bool(force_refresh)
    history_qs = bundle.get('history_qs') or CloudAutoRenewPatrolLog.objects.select_related('order', 'user').order_by('-executed_at', '-id')
    history_items = [_auto_renew_history_item_payload(item) for item in history_qs[:200]]
    latest_log = history_qs.first()
    recent_since = now - timezone.timedelta(days=1)
    recent_logs = history_qs.filter(executed_at__gte=recent_since)
    last_run_at = getattr(latest_log, 'executed_at', None)
    next_run_at = (last_run_at + timezone.timedelta(minutes=30)) if last_run_at else (now + timezone.timedelta(minutes=30))
    latest_batch_id = getattr(latest_log, 'batch_id', '') or ''
    latest_batch_qs = history_qs.filter(batch_id=latest_batch_id) if latest_batch_id else history_qs.none()
    latest_batch_count = latest_batch_qs.count() if latest_batch_id else 0
    latest_batch_success_count = latest_batch_qs.filter(is_success=True).count() if latest_batch_id else 0
    latest_batch_failure_count = latest_batch_qs.filter(is_success=False).count() if latest_batch_id else 0
    latest_failed_ips = list(latest_batch_qs.filter(is_success=False).values_list('ip', flat=True)[:20]) if latest_batch_id else []
    plan_items = [*bundle.get('due_items', []), *bundle.get('future_plan_items', [])]
    due_items = [item for item in plan_items if item.get('queue_status') in _AUTO_RENEW_PLAN_DUE_STATUSES]
    future_plan_items = [item for item in plan_items if item.get('queue_status') not in _AUTO_RENEW_PLAN_DUE_STATUSES]
    last_refresh_at = now
    return _ok({
        'task_key': 'auto_renew_patrol',
        'task_label': '自动续费巡检',
        'status_label': '置顶任务',
        'interval_minutes': 30,
        'last_run_at': _iso(last_run_at),
        'next_run_at': _iso(next_run_at),
        'last_refresh_at': _iso(last_refresh_at),
        'refreshed': did_refresh,
        'cache_mode': 'refreshed' if did_refresh else 'cached',
        'due_count': len(due_items),
        'recent_success_count': recent_logs.filter(is_success=True).count(),
        'recent_failure_count': recent_logs.filter(is_success=False).count(),
        'latest_batch_id': latest_batch_id,
        'latest_batch_count': latest_batch_count,
        'latest_batch_success_count': latest_batch_success_count,
        'latest_batch_failure_count': latest_batch_failure_count,
        'latest_failed_ips': latest_failed_ips,
        'due_items': due_items,
        'future_plan_items': future_plan_items,
        'history_items': history_items,
    })


# 功能：执行一次业务动作或后台任务；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_superuser_required
@require_POST
def run_auto_renew_tasks(request):
    now = timezone.now()
    queue = _collect_auto_renew_due_orders(now)
    orders = [(order, 'due_now') for order in queue['due_orders']]
    orders.extend((order, queue_status) for order, queue_status, _, _ in queue['retry_orders'])
    orders.extend((order, queue_status) for order, queue_status, _, _ in queue['fallback_orders'])
    if not orders:
        _refresh_dashboard_plan_snapshots('auto_renew_run_empty')
        return _ok({
            'batch_id': '',
            'items': [],
            'total': 0,
            'success_count': 0,
            'failure_count': 0,
            'message': '当前没有可执行的续费任务',
        })
    result = _manual_run_auto_renew_queue(orders)
    _refresh_dashboard_plan_snapshots('auto_renew_run_all')
    result['message'] = f"本次共执行 {result['total']} 条续费任务"
    return _ok(result)


# 功能：执行一次业务动作或后台任务；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_superuser_required
@require_POST
def run_auto_renew_order(request, order_id):
    order = CloudServerOrder.objects.select_related('user').filter(pk=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    if not order.auto_renew_enabled:
        return _error('该订单未开启自动续费', status=400)
    if order.status not in {'completed', 'expiring', 'renew_pending'}:
        return _error('当前订单状态不可执行续费', status=400)
    result = _manual_run_auto_renew_queue([(order, 'manual_single')])
    _refresh_dashboard_plan_snapshots(f'auto_renew_run_order:{order_id}')
    result['message'] = '续费任务已执行'
    return _ok(result)
