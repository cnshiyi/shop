"""生命周期任务、通知计划与自动续费后台 API。"""

import inspect
import json
import logging
import uuid
from datetime import timezone as dt_timezone

from asgiref.sync import async_to_sync
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST

from bot.models import TelegramLoginAccount
from cloud.dashboard_snapshots import _refresh_dashboard_plan_snapshots
from cloud.lifecycle import NOTICE_TYPE_SWITCH_CONFIG, _auto_renew_notice_batch_payload, _notice_effective_delivered, _get_due_orders, _get_notice_text_override, _lifecycle_notice_batch_payload, _notice_payload_for_order, _notice_override_key, _record_auto_renew_patrol_log, _renew_notice_batch_payload, _run_auto_renew, _set_notice_text_override, cloud_notice_type_enabled
from cloud.models import CloudAsset, CloudAutoRenewPatrolLog, CloudAutoRenewPlan, CloudNoticePlan, CloudServerOrder, CloudUserNoticeLog
from cloud.services import RenewalPriceMissingError, _renewal_price
from core.dashboard_api import _decimal_to_str, _error, _iso, _ok, _provider_label, _read_payload, _status_label, _user_payload, dashboard_login_required, dashboard_superuser_required
from core.models import SiteConfig
from core.runtime_config import get_runtime_config

logger = logging.getLogger(__name__)


def _cloud_api_override(name: str, fallback):
    try:
        from cloud import api as cloud_api
    except Exception:
        return fallback
    return getattr(cloud_api, name, fallback)


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
def _auto_renew_task_status(order, now, *, latest_failure_reason: str | None = None):
    if not getattr(order, 'auto_renew_enabled', False):
        return None
    last_renewed_at = getattr(order, 'last_renewed_at', None)
    if last_renewed_at and last_renewed_at >= now - timezone.timedelta(days=1):
        return 'auto_renew_success', '自动续费成功'
    expires_at = getattr(order, 'service_expires_at', None)
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
    if _auto_renew_plan_table_stale(max_age_seconds=300):
        _sync_auto_renew_plan_table(now=now)
    plan_qs = CloudAutoRenewPlan.objects.filter(data_group=CloudAutoRenewPlan.DATA_GROUP_ACTIVE)
    total_enabled = CloudServerOrder.objects.filter(auto_renew_enabled=True).count()
    failed_count = plan_qs.filter(queue_status='retry_failed').count()
    if failed_count:
        _sync_auto_renew_plan_table(now=now)
        plan_qs = CloudAutoRenewPlan.objects.filter(data_group=CloudAutoRenewPlan.DATA_GROUP_ACTIVE)
        failed_count = plan_qs.filter(queue_status='retry_failed').count()
    pending_count = plan_qs.filter(queue_status__in=list(_AUTO_RENEW_PLAN_DUE_STATUSES)).count()
    success_count = CloudAutoRenewPatrolLog.objects.filter(is_success=True, executed_at__gte=now - timezone.timedelta(days=1)).count()
    latest_time = (
        plan_qs.order_by('-updated_at').values_list('updated_at', flat=True).first()
        or CloudAutoRenewPatrolLog.objects.order_by('-executed_at').values_list('executed_at', flat=True).first()
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
def _auto_renew_due_item_payload(order, *, queue_status: str = 'due_now', queue_status_label: str = '本轮待执行', next_run_at=None, last_failure_reason: str | None = None):
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
    notice = _notice_payload_for_order(order) or {}
    expires_at = notice.get('expires_at') or getattr(order, 'service_expires_at', None)
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
        'service_expires_at': _iso(expires_at),
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


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _auto_renew_future_plan_items(now, next_run_at, due_orders: list):
    plan_items = []
    seen = set()
    for order in due_orders:
        if not _auto_renew_order_has_active_notice(order):
            continue
        seen.add(order.id)
        plan_items.append(_auto_renew_due_item_payload(order, queue_status='due_now', queue_status_label='本轮待执行', next_run_at=next_run_at))
    future_qs = CloudServerOrder.objects.select_related('user').filter(auto_renew_enabled=True, status__in=['completed', 'expiring', 'renew_pending']).exclude(id__in=list(seen)).order_by('service_expires_at', 'id')[:50]
    for order in future_qs:
        if not _auto_renew_order_has_active_notice(order):
            continue
        expires_at = getattr(order, 'service_expires_at', None)
        if not expires_at:
            continue
        if expires_at <= now:
            queue_status = 'fallback_retry'
            queue_status_label = '过期后兜底重试'
        elif expires_at <= now + timezone.timedelta(days=1):
            queue_status = 'within_window'
            queue_status_label = '24小时内进入执行窗口'
        else:
            queue_status = 'scheduled_future'
            queue_status_label = '未来计划'
        plan_items.append(_auto_renew_due_item_payload(order, queue_status=queue_status, queue_status_label=queue_status_label, next_run_at=next_run_at))
    return plan_items


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _collect_auto_renew_due_orders(now):
    due = async_to_sync(_cloud_api_override('_get_due_orders', _get_due_orders))()
    due_orders = [order for order in list(due.get('auto_renew') or []) if _auto_renew_order_has_active_notice(order)]
    due_ids = {order.id for order in due_orders}
    history_qs = CloudAutoRenewPatrolLog.objects.select_related('order', 'user').order_by('-executed_at', '-id')

    retry_orders = []
    recent_logs = history_qs.filter(executed_at__gte=now - timezone.timedelta(days=7))
    seen_history_order_ids = set()
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
            continue
        if not _auto_renew_order_has_active_notice(order):
            continue
        due_ids.add(order.id)
        retry_orders.append((order, 'retry_failed', '失败待重试', log.failure_reason))

    fallback_orders = []
    fallback_qs = CloudServerOrder.objects.select_related('user').filter(
        auto_renew_enabled=True,
        status__in=['completed', 'expiring', 'renew_pending'],
        service_expires_at__isnull=False,
        service_expires_at__lte=now,
    ).exclude(id__in=list(due_ids)).order_by('service_expires_at', 'id')[:50]
    for order in fallback_qs:
        if not _auto_renew_order_has_active_notice(order):
            continue
        due_ids.add(order.id)
        fallback_orders.append((order, 'fallback_retry', '过期后兜底重试', None))

    return {
        'due_orders': due_orders,
        'retry_orders': retry_orders,
        'fallback_orders': fallback_orders,
        'history_qs': history_qs,
        'due_ids': due_ids,
    }


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
async def _await_result(awaitable):
    return await awaitable


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _run_auto_renew_sync(order_id: int):
    result = _cloud_api_override('_run_auto_renew', _run_auto_renew)(order_id)
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
        'service_expires_at': _iso(log.service_expires_at),
        'executed_at': _iso(log.executed_at),
        'related_path': f'/admin/cloud-orders/{order_id}' if order_id else '',
        'detail_path': f'/admin/cloud-orders/{order_id}' if order_id else '',
        'order_detail_path': f'/admin/cloud-orders/{order_id}' if order_id else '',
        'order_link_path': f'/admin/cloud-orders/{order_id}' if order_id else '',
    }


_AUTO_RENEW_PLAN_DUE_STATUSES = {'due_now', 'retry_failed', 'fallback_retry'}


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _parse_api_datetime(value):
    if not value:
        return None
    parsed = parse_datetime(value) if isinstance(value, str) else value
    if not parsed:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _auto_renew_plan_source_key(item: dict) -> str:
    return f"order:{item.get('order_id') or item.get('id')}"


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _auto_renew_plan_row_defaults(item: dict) -> dict:
    return {
        'source_key': _auto_renew_plan_source_key(item),
        'data_group': CloudAutoRenewPlan.DATA_GROUP_ACTIVE,
        'queue_status': item.get('queue_status') or '',
        'queue_status_label': item.get('queue_status_label') or '',
        'order_id': item.get('order_id') or None,
        'user_id': item.get('user_id') or None,
        'order_no': item.get('order_no') or '',
        'ip': item.get('ip') or '',
        'provider': item.get('provider') or '',
        'provider_label': item.get('provider_label') or '',
        'status': item.get('status') or '',
        'status_label': item.get('status_label') or '',
        'user_display_name': item.get('user_display_name') or '',
        'username_label': item.get('username_label') or '',
        'balance': item.get('balance') or '',
        'service_expires_at': _parse_api_datetime(item.get('service_expires_at')),
        'auto_renew_at': _parse_api_datetime(item.get('auto_renew_at')),
        'next_run_at': _parse_api_datetime(item.get('next_run_at')),
        'suspend_at': _parse_api_datetime(item.get('suspend_at')),
        'delete_at': _parse_api_datetime(item.get('delete_at')),
        'ip_recycle_at': _parse_api_datetime(item.get('ip_recycle_at')),
        'last_failure_reason': item.get('last_failure_reason') or '',
        'related_path': item.get('related_path') or '',
        'detail_path': item.get('detail_path') or '',
        'order_detail_path': item.get('order_detail_path') or '',
        'order_link_path': item.get('order_link_path') or '',
        'source_snapshot': item,
    }


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _auto_renew_plan_row_payload(row) -> dict:
    snapshot = dict(row.source_snapshot or {})
    snapshot.update({
        'id': row.id,
        'order_id': row.order_id,
        'order_no': row.order_no or '',
        'ip': row.ip or '',
        'provider': row.provider or '',
        'provider_label': row.provider_label or '',
        'status': row.status or '',
        'status_label': row.status_label or '',
        'queue_status': row.queue_status or '',
        'queue_status_label': row.queue_status_label or '',
        'user_id': row.user_id,
        'tg_user_id': snapshot.get('tg_user_id'),
        'user_display_name': row.user_display_name or '未绑定用户',
        'username_label': row.username_label or '-',
        'balance': row.balance or None,
        'service_expires_at': _iso(row.service_expires_at),
        'auto_renew_at': _iso(row.auto_renew_at),
        'next_run_at': _iso(row.next_run_at),
        'last_failure_reason': row.last_failure_reason or None,
        'suspend_at': _iso(row.suspend_at),
        'delete_at': _iso(row.delete_at),
        'ip_recycle_at': _iso(row.ip_recycle_at),
        'related_path': row.related_path or '',
        'detail_path': row.detail_path or row.related_path or '',
        'order_detail_path': row.order_detail_path or row.related_path or '',
        'order_link_path': row.order_link_path or row.related_path or '',
    })
    return snapshot


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _upsert_auto_renew_plan_rows(items: list[dict]):
    payloads = {
        _auto_renew_plan_source_key(item): _auto_renew_plan_row_defaults(item)
        for item in items
        if item.get('order_id') or item.get('id')
    }
    source_keys = list(payloads)
    existing_rows = {row.source_key: row for row in CloudAutoRenewPlan.objects.filter(source_key__in=source_keys)}
    update_fields = [
        'data_group', 'queue_status', 'queue_status_label', 'order_id', 'user_id', 'order_no', 'ip', 'provider',
        'provider_label', 'status', 'status_label', 'user_display_name', 'username_label', 'balance',
        'service_expires_at', 'auto_renew_at', 'next_run_at', 'suspend_at', 'delete_at', 'ip_recycle_at',
        'last_failure_reason', 'related_path', 'detail_path', 'order_detail_path', 'order_link_path',
        'source_snapshot', 'updated_at',
    ]
    create_rows = []
    update_rows = []
    for source_key, defaults in payloads.items():
        row = existing_rows.get(source_key)
        if not row:
            create_rows.append(CloudAutoRenewPlan(**defaults))
            continue
        for field, value in defaults.items():
            setattr(row, field, value)
        update_rows.append(row)
    if create_rows:
        CloudAutoRenewPlan.objects.bulk_create(create_rows, batch_size=500)
    if update_rows:
        CloudAutoRenewPlan.objects.bulk_update(update_rows, update_fields, batch_size=500)
    qs = CloudAutoRenewPlan.objects.filter(data_group=CloudAutoRenewPlan.DATA_GROUP_ACTIVE)
    if source_keys:
        qs.exclude(source_key__in=source_keys).delete()
    else:
        qs.delete()


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _build_auto_renew_plan_items(now=None):
    now = now or timezone.now()
    queue = _collect_auto_renew_due_orders(now)
    history_qs = queue['history_qs']
    latest_log = history_qs.first()
    last_run_at = getattr(latest_log, 'executed_at', None)
    next_run_at = (last_run_at + timezone.timedelta(minutes=30)) if last_run_at else (now + timezone.timedelta(minutes=30))
    due_orders = queue['due_orders']
    latest_failure_by_order = {}
    for log in history_qs.filter(is_success=False, executed_at__gte=now - timezone.timedelta(days=7)):
        if log.order_id and log.order_id not in latest_failure_by_order:
            latest_failure_by_order[log.order_id] = log.failure_reason
    # 功能：处理 后台 API 接口 中的 due queue state 业务流程。
    def due_queue_state(order):
        status = _auto_renew_task_status(order, now, latest_failure_reason=latest_failure_by_order.get(order.id))
        return status[0] if status else ''
    due_items = [
        _auto_renew_due_item_payload(
            order,
            queue_status='retry_failed' if due_queue_state(order) == 'auto_renew_failed' else 'due_now',
            queue_status_label='失败待重试' if due_queue_state(order) == 'auto_renew_failed' else '本轮待执行',
            next_run_at=next_run_at,
            last_failure_reason=latest_failure_by_order.get(order.id),
        )
        for order in due_orders
    ]
    # 功能：处理 后台 API 接口 中的 retry item payload 业务流程。
    def retry_item_payload(order, queue_status, queue_status_label, last_failure_reason):
        status = _auto_renew_task_status(order, now, latest_failure_reason=last_failure_reason)
        if status and status[0] == 'auto_renew_pending':
            queue_status = 'due_now'
            queue_status_label = '本轮待执行'
        return _auto_renew_due_item_payload(
            order,
            queue_status=queue_status,
            queue_status_label=queue_status_label,
            next_run_at=next_run_at,
            last_failure_reason=last_failure_reason,
        )

    due_items.extend([
        retry_item_payload(order, queue_status, queue_status_label, last_failure_reason)
        for order, queue_status, queue_status_label, last_failure_reason in queue['retry_orders']
    ])
    due_items.extend([
        _auto_renew_due_item_payload(order, queue_status=queue_status, queue_status_label=queue_status_label, next_run_at=next_run_at)
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


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _sync_auto_renew_plan_table(now=None):
    bundle = _build_auto_renew_plan_items(now=now)
    _upsert_auto_renew_plan_rows([*bundle['due_items'], *bundle['future_plan_items']])
    return bundle


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _auto_renew_plan_table_stale(max_age_seconds: int = 300) -> bool:
    latest_updated_at = CloudAutoRenewPlan.objects.order_by('-updated_at').values_list('updated_at', flat=True).first()
    if not latest_updated_at:
        return True
    return latest_updated_at < timezone.now() - timezone.timedelta(seconds=max_age_seconds)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_auto_renew_plan_items():
    rows = CloudAutoRenewPlan.objects.filter(data_group=CloudAutoRenewPlan.DATA_GROUP_ACTIVE).order_by('auto_renew_at', 'next_run_at', '-updated_at', '-id')
    return [_auto_renew_plan_row_payload(row) for row in rows]


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
        expires_at = notice.get('expires_at') or getattr(order, 'service_expires_at', None)
        try:
            notice_days = max(1, int(get_runtime_config('cloud_renew_notice_days', 5) or 5))
        except Exception:
            notice_days = 5
        return expires_at - timezone.timedelta(days=notice_days) if expires_at else None
    if notice_type == 'auto_renew_notice':
        expires_at = notice.get('expires_at') or getattr(order, 'service_expires_at', None)
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
    expires_at = notice.get('expires_at') or getattr(order, 'service_expires_at', None)
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
def _notice_task_item_payload(order, notice_type: str, *, queue_status='scheduled_future', queue_status_label='未来计划', next_run_at=None, latest_log=None, account_attempts: list[dict] | None = None, notice: dict | None = None):
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
    notice = notice or _notice_payload_for_order(order) or {}
    expires_at = notice.get('expires_at') or getattr(order, 'service_expires_at', None)
    sent_at = getattr(order, _NOTICE_TASK_TYPES.get(notice_type, {}).get('field', ''), None)
    return {
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
        **_notice_channel_payload(user, latest_log, account_attempts),
        'notice_text_preview': _notice_task_text_preview(order, notice_type, notice),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'notice_at': _iso(_notice_task_time(order, notice_type, notice)),
        'service_expires_at': _iso(expires_at),
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
def _notice_task_future_items(now, next_run_at, seen_keys: set[tuple[str, int]], latest_logs: dict, *, due_window_days=3, future_limit=10, account_attempts: list[dict] | None = None, notice_cache: dict[int, dict | None] | None = None):
    items = []
    qs = CloudServerOrder.objects.select_related('user').filter(
        status__in=['completed', 'expiring', 'renew_pending', 'suspended', 'deleting', 'deleted'],
    ).order_by('service_expires_at', 'delete_at', 'ip_recycle_at', 'id')[:1000]
    for order in qs:
        if notice_cache is not None and order.id in notice_cache:
            notice = notice_cache[order.id]
        else:
            notice = _notice_payload_for_order(order)
            if notice_cache is not None:
                notice_cache[order.id] = notice
        if not notice:
            continue
        for notice_type, config in _NOTICE_TASK_TYPES.items():
            if (notice_type, order.id) in seen_keys:
                continue
            if not cloud_notice_type_enabled(notice_type):
                continue
            sent_at = getattr(order, config['field'], None)
            if sent_at:
                continue
            if notice_type == 'renew_notice' and (not order.cloud_reminder_enabled or order.status not in {'completed', 'expiring', 'renew_pending'}):
                continue
            if notice_type == 'auto_renew_notice' and (not order.auto_renew_enabled or order.status not in {'completed', 'expiring', 'renew_pending'}):
                continue
            if notice_type == 'delete_notice' and (not order.delete_reminder_enabled or order.status not in {'suspended', 'deleting'}):
                continue
            if notice_type == 'recycle_notice' and (not order.ip_recycle_reminder_enabled or order.status != 'deleted'):
                continue
            notice_at = _notice_task_time(order, notice_type, notice)
            if not notice_at:
                continue
            if notice_at <= now:
                queue_status, queue_status_label = 'fallback_notice', '已到通知时间'
            elif notice_at <= now + timezone.timedelta(days=due_window_days):
                queue_status, queue_status_label = 'within_window', '3天内待通知'
            else:
                queue_status, queue_status_label = 'scheduled_future', '未来计划'
            items.append(_notice_task_item_payload(
                order,
                notice_type,
                queue_status=queue_status,
                queue_status_label=queue_status_label,
                next_run_at=next_run_at,
                latest_log=latest_logs.get((notice_type, order.id)) or latest_logs.get((_notice_event_type(notice_type), order.id)),
                account_attempts=account_attempts,
                notice=notice,
            ))
            seen_keys.add((notice_type, order.id))
            if len(items) >= 200:
                break
    items.sort(key=lambda item: parse_datetime(item.get('notice_at') or '') or timezone.datetime.max.replace(tzinfo=dt_timezone.utc))
    due_items = [item for item in items if item.get('queue_status') in {'fallback_notice', 'within_window'}]
    future_items = [item for item in items if item.get('queue_status') == 'scheduled_future']
    return due_items, future_items


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_group_summary_items(items: list[dict], *, limit: int | None = None, offset: int = 0) -> tuple[list[dict], int]:
    grouped = {}
    for item in items:
        notice_type = item.get('notice_type') or ''
        queue_status = item.get('queue_status') or ''
        plan_scope = 'future' if queue_status == 'scheduled_future' else 'due'
        user_key = item.get('user_id') or f"unbound:{item.get('tg_user_id') or item.get('user_display_name') or 'unknown'}"
        key = f'{user_key}:{notice_type}:{plan_scope}'
        group = grouped.setdefault(key, {
            'id': key,
            'plan_scope': plan_scope,
            'plan_scope_label': '未来计划' if plan_scope == 'future' else '近期计划',
            'user_id': item.get('user_id'),
            'tg_user_id': item.get('tg_user_id'),
            'user_display_name': item.get('user_display_name') or '未绑定用户',
            'username_label': item.get('username_label') or '-',
            'notice_channel': item.get('notice_channel') or 'unbound',
            'notice_channel_label': item.get('notice_channel_label') or '未绑定通知渠道',
            'notice_channel_attempts': item.get('notice_channel_attempts') or [],
            'notice_type': notice_type,
            'notice_type_label': item.get('notice_type_label') or notice_type,
            'notice_event': _notice_event_type(notice_type),
            'notice_count': 0,
            'ip_count': 0,
            'ips': [],
            'order_ids': [],
            'pending_count': 0,
            'failed_retry_count': 0,
            'next_notice_at': item.get('notice_at'),
            'notice_text_preview': '',
            'retry_label': item.get('retry_label') or '-',
            'related_path': item.get('related_path') or '',
        })
        if item.get('notice_channel_attempts') and not group.get('notice_channel_attempts'):
            group['notice_channel_attempts'] = item.get('notice_channel_attempts') or []
        group['notice_count'] += 1
        order_id = item.get('order_id')
        if order_id and order_id not in group['order_ids']:
            group['order_ids'].append(order_id)
        ip = item.get('ip') or '-'
        if ip not in group['ips']:
            group['ips'].append(ip)
            group['ip_count'] += 1
        if item.get('notice_status') in {'pending', 'scheduled_soon'}:
            group['pending_count'] += 1
        if item.get('notice_status') == 'failed_retry':
            group['failed_retry_count'] += 1
            group['retry_label'] = item.get('retry_label') or group['retry_label']
        notice_at = item.get('notice_at')
        if notice_at and (not group.get('next_notice_at') or notice_at < group['next_notice_at']):
            group['next_notice_at'] = notice_at
            group['related_path'] = item.get('related_path') or group.get('related_path') or ''
        if not group.get('notice_text_preview'):
            label = group.get('notice_type_label') or '通知'
            group['notice_text_preview'] = f'{label}：{group["user_display_name"]} 共 {group["ip_count"]} 个 IP，系统会合并成一条通知发送。'
    summary = sorted(grouped.values(), key=lambda item: (
        item.get('user_display_name') or '',
        item.get('username_label') or '',
        item.get('next_notice_at') or '',
        item.get('notice_type_label') or '',
    ))
    total = len(summary)
    visible_summary = summary[offset:offset + limit] if limit else summary[offset:]
    for group in visible_summary:
        order_ids = group.get('order_ids') or []
        payload = _notice_actual_batch_payload(group.get('notice_type') or '', order_ids)
        manual_payload = _notice_manual_text_payload(group.get('notice_type') or '', group.get('user_id'), order_ids)
        manual_text = manual_payload.get('notice_manual_text') or ''
        group.update(manual_payload)
        group['notice_text_preview'] = manual_text or payload.get('text') or group.get('notice_text_preview') or ''
        group['notice_count'] = 1
        group['ip_count'] = int(payload.get('count') or group.get('ip_count') or 0)
    return visible_summary, total


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
            'id': log.batch_id or log.id,
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
    plan_history_qs = CloudNoticePlan.objects.filter(data_group=CloudNoticePlan.DATA_GROUP_HISTORY)
    if identifier.isdigit():
        plan_history_qs = plan_history_qs.filter(Q(log_id=int(identifier)) | Q(batch_id=identifier))
    else:
        plan_history_qs = plan_history_qs.filter(batch_id=identifier)
    plan_history_qs.delete()
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


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_plan_row_source_key(item: dict, *, data_group: str) -> str:
    notice_type = str(item.get('notice_type') or '').strip()
    if not notice_type:
        return ''
    if data_group == 'history':
        source_id = item.get('log_id') or item.get('batch_id') or item.get('id')
        return f'{data_group}:{notice_type}:{source_id}' if source_id is not None else ''
    source_id = item.get('order_id') or item.get('id')
    return f'{data_group}:{notice_type}:{source_id}' if source_id is not None else ''


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_plan_row_defaults(item: dict, *, data_group: str, source_key: str) -> dict:
    return {
        'source_key': source_key,
        'notice_type': str(item.get('notice_type') or '').strip(),
        'data_group': data_group,
        'queue_status': item.get('queue_status'),
        'queue_status_label': item.get('queue_status_label'),
        'order_id': item.get('order_id'),
        'user_id': item.get('user_id'),
        'order_no': item.get('order_no'),
        'ip': item.get('ip'),
        'provider': item.get('provider'),
        'provider_label': item.get('provider_label'),
        'status': item.get('status'),
        'status_label': item.get('status_label'),
        'user_display_name': item.get('user_display_name'),
        'username_label': item.get('username_label'),
        'notice_channel': item.get('notice_channel'),
        'notice_channel_label': item.get('notice_channel_label'),
        'notice_channel_attempts': item.get('notice_channel_attempts') or [],
        'notice_status': item.get('notice_status'),
        'notice_status_label': item.get('notice_status_label'),
        'retry_label': item.get('retry_label'),
        'notice_text_preview': item.get('notice_text_preview'),
        'notice_at': parse_datetime(item['notice_at']) if item.get('notice_at') else None,
        'next_run_at': parse_datetime(item['next_run_at']) if item.get('next_run_at') else None,
        'sent_at': parse_datetime(item['sent_at']) if item.get('sent_at') else None,
        'logged_at': parse_datetime(item['created_at']) if item.get('created_at') else (parse_datetime(item['logged_at']) if item.get('logged_at') else None),
        'delivered': bool(item.get('delivered')),
        'batch_id': item.get('batch_id'),
        'log_id': item.get('log_id'),
        'related_path': item.get('related_path'),
        'detail_path': item.get('detail_path'),
        'order_detail_path': item.get('order_detail_path'),
        'order_link_path': item.get('order_link_path'),
        'source_snapshot': dict(item),
    }


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _notice_plan_row_payload(row) -> dict:
    payload = dict(row.source_snapshot or {})
    payload.update({
        'id': row.id,
        'source_key': row.source_key,
        'notice_type': row.notice_type,
        'data_group': row.data_group,
        'queue_status': row.queue_status,
        'queue_status_label': row.queue_status_label,
        'order_id': row.order_id or payload.get('order_id'),
        'user_id': row.user_id or payload.get('user_id'),
        'order_no': row.order_no or payload.get('order_no') or '-',
        'ip': row.ip or payload.get('ip') or '未分配',
        'provider': row.provider or payload.get('provider'),
        'provider_label': row.provider_label or payload.get('provider_label'),
        'status': row.status or payload.get('status'),
        'status_label': row.status_label or payload.get('status_label'),
        'user_display_name': row.user_display_name or payload.get('user_display_name') or '未绑定用户',
        'username_label': row.username_label or payload.get('username_label') or '-',
        'notice_channel': row.notice_channel or payload.get('notice_channel') or 'unbound',
        'notice_channel_label': row.notice_channel_label or payload.get('notice_channel_label') or '未绑定通知渠道',
        'notice_channel_attempts': row.notice_channel_attempts or payload.get('notice_channel_attempts') or [],
        'notice_status': row.notice_status or payload.get('notice_status') or 'pending',
        'notice_status_label': row.notice_status_label or payload.get('notice_status_label') or '未来计划',
        'retry_label': row.retry_label or payload.get('retry_label') or '-',
        'notice_text_preview': row.notice_text_preview or payload.get('notice_text_preview') or payload.get('text_preview') or '',
        'notice_at': _iso(row.notice_at) or payload.get('notice_at'),
        'next_run_at': _iso(row.next_run_at) or payload.get('next_run_at'),
        'sent_at': _iso(row.sent_at) or payload.get('sent_at'),
        'logged_at': _iso(row.logged_at) or payload.get('created_at') or payload.get('logged_at'),
        'delivered': bool(row.delivered),
        'batch_id': row.batch_id or payload.get('batch_id') or '',
        'log_id': row.log_id or payload.get('log_id'),
        'related_path': row.related_path or payload.get('related_path') or '',
        'detail_path': row.detail_path or payload.get('detail_path') or '',
        'order_detail_path': row.order_detail_path or payload.get('order_detail_path') or '',
        'order_link_path': row.order_link_path or payload.get('order_link_path') or '',
    })
    return payload


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _upsert_notice_plan_rows(items: list[dict], *, data_group: str):
    source_keys = []
    payload_map = {}
    for item in items:
        source_key = _notice_plan_row_source_key(item, data_group=data_group)
        if not source_key:
            continue
        source_keys.append(source_key)
        payload_map[source_key] = _notice_plan_row_defaults(item, data_group=data_group, source_key=source_key)
    existing_rows = {row.source_key: row for row in CloudNoticePlan.objects.filter(source_key__in=source_keys, notice_type__in=[item.get('notice_type') for item in items if item.get('notice_type')], data_group=data_group)}
    create_rows = []
    update_rows = []
    update_fields = ['queue_status', 'queue_status_label', 'order', 'user', 'order_no', 'ip', 'provider', 'provider_label', 'status', 'status_label', 'user_display_name', 'username_label', 'notice_channel', 'notice_channel_label', 'notice_channel_attempts', 'notice_status', 'notice_status_label', 'retry_label', 'notice_text_preview', 'notice_at', 'next_run_at', 'sent_at', 'logged_at', 'delivered', 'batch_id', 'log_id', 'related_path', 'detail_path', 'order_detail_path', 'order_link_path', 'source_snapshot', 'updated_at']
    for source_key, defaults in payload_map.items():
        notice_type = defaults['notice_type']
        row = existing_rows.get(source_key)
        if row:
            changed = False
            for field, value in defaults.items():
                if getattr(row, field) != value:
                    setattr(row, field, value)
                    changed = True
            if changed:
                update_rows.append(row)
        else:
            row = CloudNoticePlan(**{key: value for key, value in defaults.items() if key not in {'order_id', 'user_id'}})
            if defaults.get('order_id'):
                row.order_id = defaults['order_id']
            if defaults.get('user_id'):
                row.user_id = defaults['user_id']
            create_rows.append(row)
    if create_rows:
        CloudNoticePlan.objects.bulk_create(create_rows, batch_size=500)
    if update_rows:
        CloudNoticePlan.objects.bulk_update(update_rows, update_fields, batch_size=500)
    qs = CloudNoticePlan.objects.filter(data_group=data_group)
    if source_keys:
        qs.exclude(source_key__in=source_keys).delete()
    else:
        qs.delete()


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _sync_notice_plan_table(*, limit=1000, future_limit=200, history_limit=1000):
    now = timezone.now()
    due = async_to_sync(_cloud_api_override('_get_due_orders', _get_due_orders))()
    next_run_at = now + timezone.timedelta(minutes=10)
    latest_logs = _notice_latest_log_map()
    account_attempts = _planned_notice_account_attempts()
    notice_cache: dict[int, dict | None] = {}
    due_items = []
    seen_keys = set()
    for notice_type, config in _NOTICE_TASK_TYPES.items():
        for order in list(due.get(notice_type) or []):
            if getattr(order, config['field'], None):
                continue
            notice = notice_cache.get(order.id)
            if order.id not in notice_cache:
                notice = _notice_payload_for_order(order)
                notice_cache[order.id] = notice
            due_items.append(_notice_task_item_payload(
                order,
                notice_type,
                queue_status='due_now',
                queue_status_label='本轮待通知',
                next_run_at=next_run_at,
                latest_log=latest_logs.get((notice_type, order.id)) or latest_logs.get((_notice_event_type(notice_type), order.id)),
                account_attempts=account_attempts,
                notice=notice,
            ))
            seen_keys.add((notice_type, order.id))
    due_items.sort(key=lambda item: parse_datetime(item.get('notice_at') or '') or timezone.datetime.max.replace(tzinfo=dt_timezone.utc))
    window_due_items, future_plan_items = _notice_task_future_items(
        now,
        next_run_at,
        seen_keys,
        latest_logs,
        future_limit=max(limit + future_limit, 200),
        account_attempts=account_attempts,
        notice_cache=notice_cache,
    )
    due_items.extend(window_due_items)
    due_items.sort(key=lambda item: parse_datetime(item.get('notice_at') or '') or timezone.datetime.max.replace(tzinfo=dt_timezone.utc))
    active_items = [*due_items, *future_plan_items]
    history_qs = CloudUserNoticeLog.objects.select_related('order', 'user').filter(event_type__in=list(_NOTICE_HISTORY_LABELS)).order_by('-created_at', '-id')
    history_rows = _notice_history_group_items(history_qs[:max(history_limit, 1000)], account_attempts=account_attempts)
    _upsert_notice_plan_rows(active_items, data_group='active')
    _upsert_notice_plan_rows(history_rows, data_group='history')
    return {'active_items': active_items, 'history_items': history_rows}


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_notice_plan_items(*, data_group: str):
    rows = CloudNoticePlan.objects.filter(data_group=data_group).order_by('notice_at', 'next_run_at', '-logged_at', '-updated_at', '-id')
    return [_notice_plan_row_payload(row) for row in rows]


# 功能：刷新缓存、快照或派生数据；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_login_required
@require_POST
def refresh_notice_plan_table(request):
    payload = _request_json_payload(request)
    limit = _request_int_param(request, 'limit', int(payload.get('limit') or 1000), maximum=1000)
    future_limit = _request_int_param(request, 'future_limit', int(payload.get('future_limit') or 200), maximum=2000)
    history_limit = _request_int_param(request, 'history_limit', int(payload.get('history_limit') or 1000), maximum=5000)
    bundle = _sync_notice_plan_table(limit=limit, future_limit=future_limit, history_limit=history_limit)
    active_items = bundle.get('active_items') or []
    due_items = [item for item in active_items if item.get('queue_status') in {'due_now', 'fallback_notice', 'within_window'}]
    future_items = [item for item in active_items if item.get('queue_status') == 'scheduled_future']
    history_items = bundle.get('history_items') or []
    return _ok({
        'refreshed': True,
        'due_count': len(due_items),
        'future_count': len(future_items),
        'history_count': len(history_items),
    })


# 功能：处理 后台 API 接口 中的 notice task detail 业务流程。
@dashboard_login_required
@require_GET
def notice_task_detail(request):
    now = timezone.now()
    limit = _request_int_param(request, 'limit', 10, maximum=100)
    offset = _request_int_param(request, 'offset', 0, minimum=0, maximum=100000)
    future_limit = _request_int_param(request, 'future_limit', 10, maximum=100)
    future_offset = _request_int_param(request, 'future_offset', 0, minimum=0, maximum=100000)
    history_limit = _request_int_param(request, 'history_limit', 10, maximum=100)
    history_offset = _request_int_param(request, 'history_offset', 0, minimum=0, maximum=100000)
    compact = str(request.GET.get('compact') or '').strip().lower() in {'1', 'true', 'yes', 'on'}

    next_run_at = now + timezone.timedelta(minutes=10)
    _sync_notice_plan_table(limit=max(limit, 200), future_limit=max(future_limit, 200), history_limit=max(history_limit, 200))

    active_items = _cloud_notice_plan_items(data_group='active')
    due_items = [item for item in active_items if item.get('queue_status') in {'due_now', 'fallback_notice', 'within_window'}]
    future_plan_items = [item for item in active_items if item.get('queue_status') == 'scheduled_future']
    due_items.sort(key=lambda item: parse_datetime(item.get('notice_at') or '') or timezone.datetime.max.replace(tzinfo=dt_timezone.utc))
    future_plan_items.sort(key=lambda item: parse_datetime(item.get('notice_at') or '') or timezone.datetime.max.replace(tzinfo=dt_timezone.utc))

    due_user_summary_items, due_user_total = _notice_group_summary_items(due_items, limit=limit, offset=offset)
    future_user_summary_items, future_user_total = _notice_group_summary_items(future_plan_items, limit=future_limit, offset=future_offset)
    active_user_summary_items, active_user_total = _notice_group_summary_items(
        [*due_items, *future_plan_items],
        limit=limit,
        offset=offset,
    )
    visible_due_items = due_items[offset:offset + limit]
    visible_future_plan_items = future_plan_items[future_offset:future_offset + future_limit]

    history_all_items = _cloud_notice_plan_items(data_group='history')
    history_all_items.sort(key=lambda item: parse_datetime(item.get('created_at') or item.get('logged_at') or '') or timezone.datetime.min.replace(tzinfo=dt_timezone.utc), reverse=True)
    visible_history_items = history_all_items[history_offset:history_offset + history_limit]
    recent_since = now - timezone.timedelta(days=1)
    recent_history_items = [
        item for item in history_all_items
        if parse_datetime(item.get('created_at') or item.get('logged_at') or '')
        and parse_datetime(item.get('created_at') or item.get('logged_at') or '') >= recent_since
    ]
    latest_history_item = history_all_items[0] if history_all_items else None
    recent_success_count = sum(1 for item in recent_history_items if item.get('delivered'))
    recent_failure_count = sum(1 for item in recent_history_items if not item.get('delivered'))
    recent_success_user_count = len({item.get('user_id') for item in recent_history_items if item.get('delivered') and item.get('user_id')})
    recent_failure_user_count = len({item.get('user_id') for item in recent_history_items if not item.get('delivered') and item.get('user_id')})
    last_refresh_at = CloudNoticePlan.objects.order_by('-updated_at').values_list('updated_at', flat=True).first()

    return _ok({
        'task_key': 'cloud_notice_plan',
        'task_label': '通知计划',
        'status_label': '置顶任务',
        'interval_minutes': 10,
        'last_run_at': (latest_history_item or {}).get('created_at') or (latest_history_item or {}).get('logged_at'),
        'next_run_at': _iso(next_run_at),
        'last_refresh_at': _iso(last_refresh_at),
        'due_count': len(due_items),
        'due_user_count': due_user_total,
        'future_count': len(future_plan_items),
        'future_user_count': future_user_total,
        'active_user_count': active_user_total,
        'history_count': len(history_all_items),
        'recent_success_count': recent_success_count,
        'recent_success_user_count': recent_success_user_count,
        'recent_failure_count': recent_failure_count,
        'recent_failure_user_count': recent_failure_user_count,
        'retry_policy_label': '通知失败不会写入已通知时间；生命周期巡检会在下一轮继续重试，直到成功送达。',
        'notice_switches': _notice_switch_items(),
        'due_items': _compact_notice_items(visible_due_items) if compact else visible_due_items,
        'due_user_summary_items': _compact_notice_items(due_user_summary_items) if compact else due_user_summary_items,
        'future_plan_items': _compact_notice_items(visible_future_plan_items) if compact else visible_future_plan_items,
        'future_user_summary_items': _compact_notice_items(future_user_summary_items) if compact else future_user_summary_items,
        'active_user_summary_items': _compact_notice_items(active_user_summary_items) if compact else active_user_summary_items,
        'history_items': _compact_notice_items(visible_history_items) if compact else visible_history_items,
    })


# 功能：处理 后台 API 接口 中的 auto renew task detail 业务流程。
@dashboard_login_required
@require_GET
def auto_renew_task_detail(request):
    now = timezone.now()
    force_refresh = str(request.GET.get('refresh') or request.GET.get('sync') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    did_refresh = False
    if force_refresh or _auto_renew_plan_table_stale():
        bundle = _sync_auto_renew_plan_table(now=now)
        did_refresh = True
    else:
        bundle = {}
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
    plan_items = _cloud_auto_renew_plan_items()
    due_items = [item for item in plan_items if item.get('queue_status') in _AUTO_RENEW_PLAN_DUE_STATUSES]
    future_plan_items = [item for item in plan_items if item.get('queue_status') not in _AUTO_RENEW_PLAN_DUE_STATUSES]
    last_refresh_at = CloudAutoRenewPlan.objects.order_by('-updated_at').values_list('updated_at', flat=True).first()
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
        _cloud_api_override('_refresh_dashboard_plan_snapshots', _refresh_dashboard_plan_snapshots)('auto_renew_run_empty')
        return _ok({
            'batch_id': '',
            'items': [],
            'total': 0,
            'success_count': 0,
            'failure_count': 0,
            'message': '当前没有可执行的续费任务',
        })
    result = _manual_run_auto_renew_queue(orders)
    _cloud_api_override('_refresh_dashboard_plan_snapshots', _refresh_dashboard_plan_snapshots)('auto_renew_run_all')
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
    _cloud_api_override('_refresh_dashboard_plan_snapshots', _refresh_dashboard_plan_snapshots)(f'auto_renew_run_order:{order_id}')
    result['message'] = '续费任务已执行'
    return _ok(result)
