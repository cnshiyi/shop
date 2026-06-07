"""bot 域后台 API。"""

import json
import re
import secrets
from copy import deepcopy
from datetime import datetime, timezone as dt_timezone
from decimal import Decimal

from django.db.models import Q, Count, Sum
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt, ensure_csrf_cookie
from django.views.decorators.http import require_GET, require_POST

from bot.models import TelegramUser
from bot.user_stats import active_cloud_asset_queryset as _active_cloud_asset_queryset, active_proxy_counts_by_user as _active_proxy_counts_by_user
from cloud.asset_expiry import order_asset_expiry
from cloud.lifecycle import (
    _asset_lifecycle_enabled_for_order,
    _is_cloud_unattached_ip_delete_time as _lifecycle_unattached_ip_delete_time,
    cloud_ip_delete_enabled,
    cloud_server_delete_enabled,
    cloud_server_shutdown_enabled,
)
from cloud.lifecycle_execution import run_orphan_asset_delete, run_shutdown_order_delete, run_unattached_ip_release
from cloud.lifecycle_plan_queries import (
    active_cloud_account_labels as query_active_cloud_account_labels,
    asset_waiting_manual_time_q as query_asset_waiting_manual_time_q,
    completed_unattached_ip_active_count,
    completed_unattached_ip_active_queryset,
    ip_delete_history_page_sources,
    ip_delete_plan_counts,
    unattached_ip_delete_plan_page,
    page_bounds,
    page_meta,
    server_lifecycle_plan_counts,
    server_lifecycle_plan_page,
    server_lifecycle_plan_queryset,
    server_shutdown_complete_q,
    unattached_ip_asset_q as query_unattached_ip_asset_q,
    unattached_ip_delete_active_queryset,
    unattached_ip_delete_history_asset_queryset,
    unattached_ip_delete_history_q,
)
from cloud.lifecycle_schedule import compute_order_lifecycle_schedule, compute_unattached_ip_release_at
from cloud.models import AddressMonitor, CloudAsset, CloudIpLog, CloudLifecyclePlanNote, CloudServerOrder
from cloud.sync_safety import missing_confirmation_state
from core.cloud_accounts import cloud_account_label, cloud_account_label_variants
from core.dashboard_api import DASHBOARD_SESSION_IDLE_SECONDS, _apply_keyword_filter, _authenticate_dashboard_request, _countdown_label, _dashboard_session_payload, _days_left, _decimal_to_str, _error, _get_keyword, _iso, _json_payload, _ok, _parse_decimal, _parse_runtime_time_point, _payload_bool, _provider_label, _provider_status_label, _read_payload, _region_label, _server_source_label, _session_token_for_request, _split_usernames, _staff_required, _status_label, _user_payload, _user_from_bearer_session, dashboard_login_required, dashboard_superuser_required
from core.dashboard_totp import dashboard_totp_secret as _totp_secret, generate_totp_secret as _generate_totp_secret, normalize_totp_secret as _normalize_totp_secret, totp_otpauth_url as _totp_otpauth_url, verify_totp_token as _verify_totp_token
from core.models import CloudAccountConfig, SiteConfig
from core.runtime_config import get_runtime_config
from orders.models import Order, Product, Recharge

PLAN_KIND_SHUTDOWN_ORDER = CloudLifecyclePlanNote.PLAN_KIND_SHUTDOWN_ORDER
PLAN_KIND_ORPHAN_ASSET_DELETE = CloudLifecyclePlanNote.PLAN_KIND_ORPHAN_ASSET_DELETE
PLAN_KIND_UNATTACHED_IP_DELETE = CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE

_LIFECYCLE_PLAN_CACHE = {
    'bundle': None,
    'counts': None,
    'generated_at': None,
    'limit': 0,
}
_LIFECYCLE_PLAN_COUNT_SNAPSHOT_KEY = 'cloud_lifecycle_plan_count_snapshot'


def _plan_item_identity(source_kind: str, source_id, *, plan_kind: str = '', plan_stage: str = '') -> dict:
    try:
        normalized_source_id = int(source_id)
    except (TypeError, ValueError):
        normalized_source_id = None
    key_parts = ['plan', str(source_kind or 'unknown'), str(normalized_source_id or 0)]
    if plan_kind:
        key_parts.append(str(plan_kind))
    if plan_stage:
        key_parts.append(str(plan_stage))
    return {
        'id': normalized_source_id,
        'source_kind': source_kind,
        'source_id': normalized_source_id,
        'plan_item_key': ':'.join(key_parts),
    }


@ensure_csrf_cookie
@require_GET
def csrf(request):
    return _ok({'csrf': True})


def _runtime_int(key: str, default: int) -> int:
    try:
        return max(int(str(get_runtime_config(key, str(default)) or default).strip()), 0)
    except (TypeError, ValueError):
        return default


def _runtime_time(key: str, default: str = '15:00') -> tuple[int, int]:
    raw = str(get_runtime_config(key, default) or default).strip()
    if '-' in raw:
        raw = raw.split('-', 1)[0].strip()
    return _parse_runtime_time_point(raw, default)


def _server_asset_lifecycle_times(asset):
    expires_at = getattr(asset, 'actual_expires_at', None)
    if not expires_at:
        return None, None, None
    schedule = compute_order_lifecycle_schedule(expires_at)
    return expires_at, schedule.suspend_at, schedule.delete_at


def _next_runtime_time(key: str, default: str = '15:00', now=None):
    now = now or timezone.now()
    hour, minute = _runtime_time(key, default)
    local_now = timezone.localtime(now)
    next_at = local_now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if next_at <= local_now:
        next_at += timezone.timedelta(days=1)
    return next_at if timezone.is_aware(next_at) else timezone.make_aware(next_at, timezone.get_current_timezone())


def _cloud_account_labels(item):
    account = getattr(item, 'cloud_account', None)
    account_name = getattr(account, 'name', '') or ''
    external_account_id = getattr(account, 'external_account_id', '') or getattr(item, 'account_label', '') or ''
    return account_name, external_account_id


def _asset_is_unattached_ip(asset):
    return bool(
        asset
        and (
            ('未附加' in (asset.provider_status or ''))
            or ('未附加IP' in (asset.note or ''))
            or ('未附加固定IP' in (asset.note or ''))
            or ('StaticIp' in str(getattr(asset, 'provider_resource_id', '') or ''))
        )
    )


def _unattached_ip_asset_q():
    return query_unattached_ip_asset_q()


def _ensure_unattached_ip_delete_due(asset, *, now=None):
    if not _asset_is_unattached_ip(asset) or getattr(asset, 'actual_expires_at', None):
        return getattr(asset, 'actual_expires_at', None)
    delete_at = compute_unattached_ip_release_at(now or timezone.now())
    updated = CloudAsset.objects.filter(id=asset.id, actual_expires_at__isnull=True).update(actual_expires_at=delete_at, updated_at=timezone.now())
    if updated:
        asset.actual_expires_at = delete_at
        asset.updated_at = timezone.now()
        return delete_at
    refreshed = CloudAsset.objects.filter(id=asset.id).values_list('actual_expires_at', flat=True).first()
    asset.actual_expires_at = refreshed
    return refreshed


def _asset_waiting_manual_time_q():
    return query_asset_waiting_manual_time_q()


def _active_cloud_asset_plan_rows(limit=None):
    return _proxy_list_cloud_asset_plan_rows(limit=limit)


def _active_cloud_account_labels():
    return query_active_cloud_account_labels()


def _proxy_list_cloud_asset_queryset():
    unattached_ip_values = list(
        CloudAsset.objects.filter(
            kind=CloudAsset.KIND_SERVER,
            provider_status__contains='未附加固定IP',
            public_ip__isnull=False,
        ).exclude(public_ip='').values_list('public_ip', flat=True)[:1000]
    )
    return (
        CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER)
        .exclude(
            Q(status__in=[CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED])
            & (Q(public_ip__in=unattached_ip_values) | Q(previous_public_ip__in=unattached_ip_values))
        )
    )


def _active_cloud_asset_plan_queryset(*, active_account_labels: set[str] | None = None):
    return _proxy_list_cloud_asset_queryset()


def _proxy_list_cloud_asset_plan_rows(limit=None, *, include_unattached=True):
    active_account_labels = set(_active_cloud_account_labels())
    if include_unattached:
        queryset = _active_cloud_asset_plan_queryset(active_account_labels=active_account_labels)
    else:
        queryset = (
            CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
            .filter(kind=CloudAsset.KIND_SERVER)
            .exclude(instance_id__isnull=True)
            .exclude(instance_id='')
            .exclude(status__in=[CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED])
        )
    queryset = queryset.order_by('-sort_order', 'actual_expires_at', '-updated_at', '-id')
    if limit:
        fetch_limit = max(int(limit) * 4, int(limit) + 200)
        fetch_limit = min(fetch_limit, 5000)
        queryset = queryset[:fetch_limit]
    rows = _dedupe_cloud_asset_plan_rows(list(queryset))
    if limit:
        return rows[: max(1, int(limit))]
    return rows


def _asset_is_sync_only_lifecycle(asset):
    return getattr(asset, 'provider', '') == 'aliyun_simple'


def _asset_deleted_or_missing(asset):
    status = getattr(asset, 'status', '')
    provider_status = str(getattr(asset, 'provider_status', '') or '')
    note = str(getattr(asset, 'note', '') or '')
    if status in {
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_TERMINATED,
        CloudAsset.STATUS_TERMINATING,
    }:
        return True
    if any(marker in provider_status for marker in ['云上未找到', '已到期删除', '已删除']):
        return True
    if any(marker in note for marker in ['云上不存在', '已标记删除']) and status not in {
        CloudAsset.STATUS_RUNNING,
        CloudAsset.STATUS_PENDING,
        CloudAsset.STATUS_STARTING,
        CloudAsset.STATUS_STOPPED,
        CloudAsset.STATUS_SUSPENDED,
        CloudAsset.STATUS_UNKNOWN,
    }:
        return True
    return False


def _cloud_asset_plan_stats(assets=None):
    if assets is None:
        queryset = _active_cloud_asset_plan_queryset()
        unattached_q = _unattached_ip_asset_q()
        server_queryset = queryset.exclude(unattached_q)
        return {
            'source_asset_count': queryset.count(),
            'server_asset_count': server_queryset.count(),
            'missing_expiry_count': server_queryset.filter(_asset_waiting_manual_time_q()).count(),
            'unattached_ip_count': queryset.filter(unattached_q).count(),
        }
    plan_assets = list(assets)
    server_assets = [
        asset for asset in plan_assets
        if not _asset_is_unattached_ip(asset)
        and 'StaticIp' not in str(getattr(asset, 'provider_resource_id', '') or '')
    ]
    unattached_assets = [
        asset for asset in plan_assets
        if _asset_is_unattached_ip(asset)
        or 'StaticIp' in str(getattr(asset, 'provider_resource_id', '') or '')
    ]
    missing_expiry_assets = [
        asset for asset in server_assets
        if not getattr(asset, 'actual_expires_at', None)
        or '待人工添加时间' in str(getattr(asset, 'provider_status', '') or '')
        or '等待人工添加真实到期时间' in str(getattr(asset, 'note', '') or '')
        or '等待人工添加时间' in str(getattr(asset, 'note', '') or '')
    ]
    return {
        'source_asset_count': len(plan_assets),
        'server_asset_count': len(server_assets),
        'missing_expiry_count': len(missing_expiry_assets),
        'unattached_ip_count': len(unattached_assets),
    }


def _lifecycle_plan_total_counts():
    return server_lifecycle_plan_counts()


def _server_shutdown_complete_q():
    return server_shutdown_complete_q()


def _server_lifecycle_plan_queryset():
    return server_lifecycle_plan_queryset()


def _page_bounds(page: int, page_size: int) -> tuple[int, int]:
    return page_bounds(page, page_size)


def _page_meta(page: int, page_size: int, total: int) -> dict:
    return page_meta(page, page_size, total)


def _server_lifecycle_plan_page_items(*, plan_stage: str, page: int, page_size: int, total: int | None = None) -> list[dict]:
    assets = server_lifecycle_plan_page(plan_stage=plan_stage, page=page, page_size=page_size, total=total)
    if plan_stage == 'shutdown':
        return [_shutdown_stage_item_payload(asset, queue_status='scheduled_future', queue_status_label='计划中') for asset in assets]
    return [_asset_delete_plan_item_payload(asset, queue_status='scheduled_future', queue_status_label='计划中') for asset in assets]


def _unattached_ip_delete_active_queryset():
    return unattached_ip_delete_active_queryset()


def _unattached_ip_delete_history_asset_queryset():
    return unattached_ip_delete_history_asset_queryset()


def _completed_unattached_ip_active_count():
    return completed_unattached_ip_active_count()


def _completed_unattached_ip_active_queryset():
    return completed_unattached_ip_active_queryset()


def _ip_delete_plan_total_counts():
    return ip_delete_plan_counts()


def _ip_delete_plan_asset_page_items(
    *,
    page: int,
    page_size: int,
    now=None,
    total: int | None = None,
    completed_active_count: int | None = None,
) -> list[dict]:
    now = now or timezone.now()
    global_ip_enabled = cloud_ip_delete_enabled()
    assets = unattached_ip_delete_plan_page(
        page=page,
        page_size=page_size,
        total=total,
        exclude_completed=bool(completed_active_count),
    )
    trace_maps = _cloud_ip_trace_maps_for_assets(assets)
    items = []
    for asset in assets:
        user_display_name, username_label = _telegram_user_labels(asset.user)
        delete_at = asset.actual_expires_at or _ensure_unattached_ip_delete_due(asset, now=now)
        trace = _cloud_ip_trace_from_maps(asset, trace_maps)
        trace_note = ''
        if trace:
            trace_note = _cloud_ip_trace_note_newest_first(trace.note)
            logged_at = _cloud_ip_trace_logged_at(trace.note, trace.created_at)
        else:
            logged_at = asset.updated_at
        note = _asset_note_text(asset)
        source_note = trace_note or note
        asset_name = asset.asset_name or getattr(asset, 'static_ip_name', '') or asset.instance_id or f'asset-{asset.id}'
        ip_delete_enabled = _asset_ip_delete_enabled(asset)
        item = {
            **_plan_item_identity('asset', asset.id, plan_kind=PLAN_KIND_UNATTACHED_IP_DELETE),
            'plan_kind': PLAN_KIND_UNATTACHED_IP_DELETE,
            'asset_id': asset.id,
            'asset_name': asset_name,
            'asset_detail_path': f'/admin/cloud-assets/{asset.id}',
            'detail_path': f'/admin/cloud-assets/{asset.id}',
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': (trace.public_ip if trace else None) or asset.public_ip or asset.previous_public_ip or '',
            'provider_status': asset.provider_status or (_status_label(trace.event_type, CloudIpLog.EVENT_CHOICES) if trace else ''),
            'deletion_source_label': _delete_source_label(trace_note, default='计划自动删除'),
            'actual_expires_at': _iso(asset.actual_expires_at),
            'delete_at': _iso(delete_at),
            'logged_at': _iso(logged_at),
            'source_note': source_note,
            'note': note,
            'display_note': _asset_display_note(asset, fallback=trace_note, max_chars=500),
            'sync_state': getattr(asset, 'sync_state', {}) or {},
            'is_overdue': bool(delete_at and delete_at <= now),
            'is_history': False,
            'shutdown_enabled': _asset_shutdown_enabled(asset),
            'ip_delete_enabled': ip_delete_enabled,
        }
        if not global_ip_enabled:
            item['queue_status'] = 'global_ip_delete_disabled'
            item['queue_status_label'] = '总开关关闭'
            item['execution_status'] = 'IP 删除总开关关闭，禁止真实释放固定 IP'
        elif not ip_delete_enabled:
            item['queue_status'] = 'ip_delete_disabled'
            item['queue_status_label'] = 'IP删除开关关闭'
            item['execution_status'] = '资产 IP 删除计划开关关闭，禁止真实释放固定 IP'
        item.update(_unattached_ip_delete_attempt_state(item, is_history=False))
        items.append(_ip_delete_item_quality(item))
    return items


def _ip_delete_history_trace_items_from_queryset(queryset) -> list[dict]:
    items = []
    for trace in queryset:
        asset = trace.asset
        order = trace.order
        user_display_name, username_label = _telegram_user_labels(trace.user or getattr(asset, 'user', None))
        asset_name = trace.asset_name or getattr(order, 'static_ip_name', '') or getattr(asset, 'asset_name', '') or trace.instance_id or f'trace-{trace.id}'
        logged_at = _cloud_ip_trace_logged_at(trace.note, trace.created_at)
        delete_at = logged_at or getattr(order, 'ip_recycle_at', None) or getattr(asset, 'actual_expires_at', None)
        item = {
            **_plan_item_identity('ip_log', trace.id, plan_kind=PLAN_KIND_UNATTACHED_IP_DELETE, plan_stage='history'),
            'plan_kind': PLAN_KIND_UNATTACHED_IP_DELETE,
            'asset_id': trace.asset_id,
            'asset_name': asset_name,
            'asset_detail_path': f'/admin/cloud-assets/{trace.asset_id}' if trace.asset_id else '',
            'detail_path': f'/admin/cloud-orders/{trace.order_id}' if trace.order_id else (f'/admin/cloud-assets/{trace.asset_id}' if trace.asset_id else ''),
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': trace.public_ip or trace.previous_public_ip or '',
            'provider_status': _status_label(trace.event_type, CloudIpLog.EVENT_CHOICES),
            'deletion_source_label': _delete_source_label(trace.note),
            'actual_expires_at': _iso(getattr(asset, 'actual_expires_at', None)),
            'delete_at': _iso(delete_at),
            'logged_at': _iso(logged_at),
            'source_note': _cloud_ip_trace_note_newest_first(trace.note),
            'note': _cloud_ip_trace_note_newest_first(trace.note),
            'display_note': _compact_dashboard_note(trace.note, max_chars=500),
            'is_overdue': True,
            'is_history': True,
        }
        item.update(_unattached_ip_delete_attempt_state(item, is_history=True))
        items.append(_ip_delete_item_quality(item))
    return items


def _ip_delete_history_asset_item(asset) -> dict:
    now = timezone.now()
    user_display_name, username_label = _telegram_user_labels(asset.user)
    executed_at = asset.updated_at or asset.actual_expires_at or now
    source_note = _asset_note_text(asset) or asset.provider_status or '固定 IP 已删除'
    item = {
        **_plan_item_identity('asset', asset.id, plan_kind=PLAN_KIND_UNATTACHED_IP_DELETE, plan_stage='history'),
        'plan_kind': PLAN_KIND_UNATTACHED_IP_DELETE,
        'asset_id': asset.id,
        'asset_name': asset.asset_name or asset.instance_id or f'asset-{asset.id}',
        'asset_detail_path': f'/admin/cloud-assets/{asset.id}',
        'detail_path': f'/admin/cloud-assets/{asset.id}',
        'user_display_name': user_display_name,
        'username_label': username_label,
        'public_ip': str(asset.public_ip or asset.previous_public_ip or '').strip(),
        'provider_status': asset.provider_status or '已删除',
        'deletion_source_label': _delete_source_label(source_note),
        'actual_expires_at': _iso(asset.actual_expires_at),
        'delete_at': _iso(executed_at),
        'logged_at': _iso(executed_at),
        'source_note': source_note,
        'note': source_note,
        'display_note': _asset_display_note(asset, fallback=source_note, max_chars=500),
        'is_overdue': True,
        'is_history': True,
    }
    item.update(_unattached_ip_delete_attempt_state(item, is_history=True))
    return _ip_delete_item_quality(item)


def _ip_delete_history_page_items(
    *,
    page: int,
    page_size: int,
    log_total: int | None = None,
    asset_total: int | None = None,
    completed_total: int | None = None,
) -> list[dict]:
    items = []
    for source_type, source in ip_delete_history_page_sources(
        page=page,
        page_size=page_size,
        log_total=log_total,
        asset_total=asset_total,
        completed_total=completed_total,
    ):
        if source_type == 'log':
            items.extend(_ip_delete_history_trace_items_from_queryset([source]))
        else:
            items.append(_ip_delete_history_asset_item(source))
    return items


def _cloud_asset_display_ip(asset):
    return str(getattr(asset, 'public_ip', '') or getattr(asset, 'previous_public_ip', '') or '').strip()


def _dedupe_cloud_asset_plan_rows(assets):
    best = {}
    for asset in assets:
        ip = _cloud_asset_display_ip(asset)
        cloud_account_id = getattr(asset, 'cloud_account_id', None)
        account_label = str(
            getattr(asset, 'account_label', '')
            or cloud_account_label(getattr(asset, 'cloud_account', None))
            or ''
        ).strip()
        account_key = f'cloud_account:{cloud_account_id}' if cloud_account_id else f'label:{account_label}'
        provider = str(getattr(asset, 'provider', '') or '').strip()
        region_code = str(getattr(asset, 'region_code', '') or '').strip()
        key = f'{provider}:{account_key}:{region_code}:{ip}' if ip else f'id:{asset.id}'
        is_unattached = _asset_is_unattached_ip(asset) or 'StaticIp' in str(getattr(asset, 'provider_resource_id', '') or '')
        is_deleted = asset.status in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED}
        score = (
            3 if is_unattached else 0,
            2 if asset.status == CloudAsset.STATUS_DELETING else 0,
            1 if not is_deleted else 0,
            1 if asset.order_id else 0,
            1 if asset.user_id else 0,
            asset.updated_at.timestamp() if asset.updated_at else 0,
            asset.id,
        )
        current = best.get(key)
        if not current or score > current[0]:
            best[key] = (score, asset)
    return [item[1] for item in best.values()]


def _fmt_dashboard_dt(value):
    if not value:
        return '-'
    try:
        return timezone.localtime(value).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(value)


def _extract_failure_reason(note):
    text = str(note or '').strip()
    if not text:
        return '-'
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    for line in reversed(lines):
        if any(keyword in line for keyword in ['失败', '异常', '错误', 'error', 'Error', 'ERROR']):
            return line[:300]
    return '-'


def _compact_failure_reason(note, *, fallback='失败/跳过'):
    text = _cloud_ip_trace_note_newest_first(note)
    if not text:
        return fallback
    normalized = text.lower()
    business_rules = [
        (['关闭关机计划', '关机计划关闭', '已关闭关机', '停用关机计划'], '账号已关闭关机计划'),
        (['关闭删机计划', '删机计划关闭', '关闭删除计划', '删除计划关闭', '停用删机计划'], '账号已关闭删除计划'),
        (['delete_at 未到', '删除时间未到', '删机时间未到', '服务器删除时间未到'], '未到服务器删除时间'),
        (['不在后台配置', '不在删除执行时间窗口', 'safe time'], '不在服务器删除执行时间窗口'),
        (['not found', '不存在', 'does not exist', 'not exist'], '云端资源不存在'),
        (['unauthorized', 'forbidden', 'accessdenied', 'access denied', '权限不足', '拒绝访问'], '云账号权限不足'),
        (['timeout', 'timed out', '超时'], '云接口请求超时'),
        (['throttl', 'rate exceeded', '限流', '频率'], '云接口限流'),
        (['invalid state', '状态不允许', '当前状态'], '云服务器状态不允许删除'),
        (['未配置', '缺少', 'missing'], '云账号或实例信息不完整'),
    ]
    for keywords, label in business_rules:
        if any(keyword.lower() in normalized for keyword in keywords):
            return label
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), '')
    for source in [first_line, text]:
        match = re.search(r'失败原因：([^；\n]+)', source)
        if match:
            reason = match.group(1).strip()
            if not reason or reason == '-':
                return fallback
            return reason[:180]
    for source in [first_line, text]:
        for pattern in [
            r'(?:错误|异常|失败)[:：]\s*([^；\n]+)',
            r'((?:AWS|Lightsail|请求|接口|实例|服务器|固定IP|权限|资源|删除|停止)[^；\n]*(?:失败|异常|错误|不存在|拒绝|超时)[^；\n]*)',
        ]:
            match = re.search(pattern, source, flags=re.IGNORECASE)
            if match:
                return match.group(1).strip()[:180]
    return fallback


def _shutdown_execution_note(*, status_label, is_success, executed_at, action, failure_reason, deletion_source=''):
    parts = [
        f'执行状态：{status_label or "-"}',
        f'是否成功：{"成功" if is_success else "失败"}',
        f'执行时间：{_fmt_dashboard_dt(executed_at)}',
        f'执行内容：{action or "-"}',
    ]
    if deletion_source:
        parts.append(f'删除来源：{deletion_source}')
    parts.append(f'失败原因：{failure_reason or "-"}')
    return '；'.join(parts)


def _delete_source_label(note='', *, default='到期自动删除'):
    text = str(note or '')
    match = re.search(r'删除来源：([^；\n]+)', text)
    if match:
        return match.group(1).strip() or default
    lowered = text.lower()
    if any(keyword in text for keyword in ['人工手动删除', '手动删除', '人工删除']) or 'manual' in lowered:
        return '人工手动删除'
    if any(keyword in text for keyword in ['云上不存在', '已标记删除', '云端已不存在', '同步删除', '同步校验']):
        return '同步校验删除'
    return default


def _with_delete_source(note, source):
    text = str(note or '').strip()
    if '删除来源：' in text:
        return text
    return f'删除来源：{source}；{text}' if text else f'删除来源：{source}'


def _compact_dashboard_note(note, *, max_chars=800):
    noisy_prefixes = (
        'Get:', 'Hit:', 'Ign:', 'Err:', 'Fetched ', 'Reading package lists',
        'Building dependency tree', 'Reading state information', 'Selecting previously',
        'Preparing to unpack', 'Unpacking ', 'Setting up ', 'Processing triggers',
        'Created symlink ', 'Synchronizing state', 'Need to get ', 'After this operation',
        'The following ', '0 upgraded,', 'debconf:', 'apt-listchanges:', 'WARNING:',
    )
    lines = []
    seen = set()
    for raw_line in str(note or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if 'tg://proxy?' in line or 'socks5://' in line:
            continue
        if line.startswith(('TG链接:', '分享链接:', '扩展链接:', 'SOCKS5链接:')):
            continue
        if line.startswith(noisy_prefixes):
            continue
        if line.startswith('状态: ') and ('最近同步:' in line or '覆盖同步时间:' in line):
            continue
        if line in seen:
            continue
        seen.add(line)
        lines.append(line)
    text = '\n'.join(lines)
    if max_chars and len(text) > max_chars:
        return text[:max_chars].rstrip() + '\n...（备注过长，已折叠预览）'
    return text


def _asset_note_text(asset) -> str:
    return str(getattr(asset, 'note', '') or '').strip()


def _asset_display_note(asset, *, fallback: str = '', max_chars: int = 500) -> str:
    return _compact_dashboard_note(_asset_note_text(asset) or fallback, max_chars=max_chars)


def _sync_asset_note_to_server(asset):
    return 0


def _is_cloud_unattached_ip_delete_time(now=None):
    return _lifecycle_unattached_ip_delete_time(now)


def _lifecycle_plan_note_scope(item_type='', *, order_id=None, asset_id=None):
    item_type = str(item_type or '').strip()
    if item_type == 'order' or order_id:
        return CloudLifecyclePlanNote.PLAN_KIND_SHUTDOWN_ORDER, int(order_id or 0), None
    if item_type == 'orphan_asset':
        return CloudLifecyclePlanNote.PLAN_KIND_ORPHAN_ASSET_DELETE, None, int(asset_id or 0)
    return CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE, None, int(asset_id or 0)


def _lifecycle_plan_note_maps(*, order_ids=None, orphan_asset_ids=None, unattached_asset_ids=None):
    order_ids = [int(item) for item in (order_ids or []) if int(item or 0) > 0]
    orphan_asset_ids = [int(item) for item in (orphan_asset_ids or []) if int(item or 0) > 0]
    unattached_asset_ids = [int(item) for item in (unattached_asset_ids or []) if int(item or 0) > 0]
    conditions = Q()
    if order_ids:
        conditions |= Q(plan_kind=CloudLifecyclePlanNote.PLAN_KIND_SHUTDOWN_ORDER, order_id__in=order_ids)
    if orphan_asset_ids:
        conditions |= Q(plan_kind=CloudLifecyclePlanNote.PLAN_KIND_ORPHAN_ASSET_DELETE, asset_id__in=orphan_asset_ids)
    if unattached_asset_ids:
        conditions |= Q(plan_kind=CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE, asset_id__in=unattached_asset_ids)
    if not conditions:
        return {}, {}, {}
    order_map = {}
    orphan_asset_map = {}
    unattached_asset_map = {}
    rows = CloudLifecyclePlanNote.objects.filter(conditions).order_by('-updated_at', '-id')
    for row in rows:
        if row.plan_kind == CloudLifecyclePlanNote.PLAN_KIND_SHUTDOWN_ORDER and row.order_id and row.order_id not in order_map:
            order_map[row.order_id] = row
        elif row.plan_kind == CloudLifecyclePlanNote.PLAN_KIND_ORPHAN_ASSET_DELETE and row.asset_id and row.asset_id not in orphan_asset_map:
            orphan_asset_map[row.asset_id] = row
        elif row.plan_kind == CloudLifecyclePlanNote.PLAN_KIND_UNATTACHED_IP_DELETE and row.asset_id and row.asset_id not in unattached_asset_map:
            unattached_asset_map[row.asset_id] = row
    return order_map, orphan_asset_map, unattached_asset_map


def _lifecycle_plan_note_text(note_obj) -> str:
    return str(getattr(note_obj, 'note', '') or '').strip()


def _save_lifecycle_plan_note(*, item_type='', note='', order=None, asset=None, actor=None):
    plan_kind, order_id, asset_id = _lifecycle_plan_note_scope(
        item_type,
        order_id=getattr(order, 'id', None),
        asset_id=getattr(asset, 'id', None),
    )
    filters = {'plan_kind': plan_kind}
    if order_id:
        filters['order_id'] = order_id
    elif asset_id:
        filters['asset_id'] = asset_id
    else:
        return None
    qs = CloudLifecyclePlanNote.objects.filter(**filters).order_by('-updated_at', '-id')
    value = str(note or '').strip()
    keep = qs.first()
    if not value:
        qs.delete()
        return None
    if keep:
        updates = []
        if keep.note != value:
            keep.note = value
            updates.append('note')
        if actor and getattr(actor, 'is_authenticated', False):
            keep.updated_by = actor
            updates.append('updated_by')
        if updates:
            keep.save(update_fields=[*updates, 'updated_at'])
        qs.exclude(id=keep.id).delete()
        return keep
    create_kwargs = {'note': value}
    if order_id:
        create_kwargs['order'] = order
    if asset_id:
        create_kwargs['asset'] = asset
    if actor and getattr(actor, 'is_authenticated', False):
        create_kwargs['created_by'] = actor
        create_kwargs['updated_by'] = actor
    return CloudLifecyclePlanNote.objects.create(plan_kind=plan_kind, **create_kwargs)


def _cloud_ip_trace_note_newest_first(note):
    text = _compact_dashboard_note(note, max_chars=1200)
    if not text:
        return ''
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return text

    def _line_time(line):
        match = re.search(r'执行时间：(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        return match.group(1) if match else ''

    first_time = _line_time(lines[0])
    last_time = _line_time(lines[-1])
    if first_time and last_time and first_time < last_time:
        lines = list(reversed(lines))
    return '\n'.join(lines)


def _refresh_plan_payload_from_assets(items):
    asset_ids = [item.get('asset_id') for item in items if item.get('asset_id')]
    if not asset_ids:
        return items
    assets = {
        asset.id: asset
        for asset in CloudAsset.objects.select_related('cloud_account', 'user', 'order').filter(id__in=asset_ids)
    }
    refreshed = []
    for item in items:
        asset = assets.get(item.get('asset_id'))
        if not asset:
            refreshed.append(item)
            continue
        item = dict(item)
        item['ip'] = asset.public_ip or asset.previous_public_ip or item.get('ip') or ''
        item['provider'] = asset.provider
        item['provider_label'] = _provider_label(asset.provider)
        item['status'] = asset.status
        item['status_label'] = _status_label(asset.status, CloudAsset.STATUS_CHOICES)
        item['provider_status'] = asset.provider_status or ''
        if item.get('plan_kind') == PLAN_KIND_UNATTACHED_IP_DELETE:
            item['actual_expires_at'] = _iso(asset.actual_expires_at)
            item['delete_at'] = _iso(asset.actual_expires_at)
            item['next_run_at'] = _iso(asset.actual_expires_at)
        else:
            expires_at, suspend_at, delete_at = _server_asset_lifecycle_times(asset)
            is_shutdown_stage = str(item.get('plan_stage') or '') == 'shutdown'
            item['actual_expires_at'] = _iso(expires_at)
            item['suspend_at'] = _iso(suspend_at)
            item['delete_at'] = _iso(delete_at)
            item['next_run_at'] = _iso(suspend_at if is_shutdown_stage else delete_at)
            if is_shutdown_stage:
                item['execution_plan'] = f'关机服务器 {_fmt_dashboard_dt(suspend_at)}' if suspend_at else '等待关机时间'
            else:
                item['execution_plan'] = f'删除服务器 {_fmt_dashboard_dt(delete_at)}' if delete_at else '等待删除时间'
        item['asset_name'] = asset.asset_name
        item['shutdown_enabled'] = _asset_shutdown_enabled(asset)
        item['server_delete_enabled'] = _asset_server_delete_enabled(asset)
        item['ip_delete_enabled'] = _asset_ip_delete_enabled(asset)
        item['source_note'] = str(item.get('source_note') or '').strip() or _asset_note_text(asset)
        if item.get('data_group') == 'active' and item.get('plan_kind') in {
            PLAN_KIND_ORPHAN_ASSET_DELETE,
            PLAN_KIND_UNATTACHED_IP_DELETE,
        }:
            item['note'] = _asset_note_text(asset)
            item['display_note'] = _asset_display_note(asset, fallback=item.get('display_note') or item.get('source_note') or '')
        item['detail_path'] = f'/admin/cloud-assets/{asset.id}'
        item['related_path'] = f'/admin/cloud-assets/{asset.id}'
        item['asset_detail_path'] = f'/admin/cloud-assets/{asset.id}'
        account_name, external_account_id = _cloud_account_labels(asset)
        item['cloud_account_id'] = asset.cloud_account_id
        item['cloud_account_name'] = account_name
        item['external_account_id'] = external_account_id
        refreshed.append(item)
    return refreshed


def _build_lifecycle_plan_count_snapshot():
    return {
        'plan_stats': _cloud_asset_plan_stats(),
        'total_counts': _lifecycle_plan_total_counts(),
        'ip_delete_total_counts': _ip_delete_plan_total_counts(),
    }


def _store_lifecycle_plan_count_snapshot(counts: dict, *, generated_at=None):
    generated_at = generated_at or timezone.now()
    payload = {
        'counts': counts,
        'generated_at': _iso(generated_at),
    }
    SiteConfig.set(_LIFECYCLE_PLAN_COUNT_SNAPSHOT_KEY, json.dumps(payload, ensure_ascii=False))


def _load_lifecycle_plan_count_snapshot():
    raw = SiteConfig.get(_LIFECYCLE_PLAN_COUNT_SNAPSHOT_KEY, '')
    if not raw:
        return None, None
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None, None
    counts = payload.get('counts') if isinstance(payload, dict) else None
    if not isinstance(counts, dict):
        return None, None
    required_top_keys = {'plan_stats', 'total_counts', 'ip_delete_total_counts'}
    if not required_top_keys.issubset(counts.keys()):
        return None, None
    generated_at = parse_datetime(str(payload.get('generated_at') or '')) if isinstance(payload, dict) else None
    if generated_at and timezone.is_naive(generated_at):
        generated_at = timezone.make_aware(generated_at, timezone.get_current_timezone())
    return counts, generated_at


def _sync_lifecycle_plan_table(*, limit=1000, page_size=None):
    page_size = max(1, min(int(page_size or limit or 1000), 1000))
    server_delete_items = _server_lifecycle_plan_page_items(plan_stage='delete', page=1, page_size=page_size)
    ip_delete_plan_items = _ip_delete_plan_asset_page_items(page=1, page_size=page_size)
    ip_delete_history_items = _ip_delete_history_page_items(page=1, page_size=page_size)
    bundle = {
        'shutdown_plan_items': _server_lifecycle_plan_page_items(plan_stage='shutdown', page=1, page_size=page_size),
        'server_delete_items': server_delete_items,
        'ip_delete_plan_items': ip_delete_plan_items,
        'ip_delete_history_items': ip_delete_history_items,
    }
    counts = _build_lifecycle_plan_count_snapshot()
    generated_at = timezone.now()
    _store_lifecycle_plan_count_snapshot(counts, generated_at=generated_at)
    _LIFECYCLE_PLAN_CACHE.update({
        'bundle': deepcopy(bundle),
        'counts': deepcopy(counts),
        'generated_at': generated_at,
        'limit': page_size,
    })
    return deepcopy(bundle)


def _refresh_lifecycle_plan_cache(*, page_size=1000):
    return _sync_lifecycle_plan_table(limit=None, page_size=page_size)


def _cached_lifecycle_plan_count_snapshot():
    cached = _LIFECYCLE_PLAN_CACHE.get('counts')
    if cached is not None:
        return deepcopy(cached)
    persisted, generated_at = _load_lifecycle_plan_count_snapshot()
    if persisted is not None:
        _LIFECYCLE_PLAN_CACHE['counts'] = deepcopy(persisted)
        _LIFECYCLE_PLAN_CACHE['generated_at'] = generated_at or timezone.now()
        return deepcopy(persisted)
    counts = _build_lifecycle_plan_count_snapshot()
    generated_at = timezone.now()
    _store_lifecycle_plan_count_snapshot(counts, generated_at=generated_at)
    _LIFECYCLE_PLAN_CACHE['counts'] = deepcopy(counts)
    _LIFECYCLE_PLAN_CACHE['generated_at'] = generated_at
    return counts


def _plan_item_dt(item: dict, *keys: str, default=None):
    for key in keys:
        value = item.get(key)
        if not value:
            continue
        parsed = parse_datetime(value) if isinstance(value, str) else value
        if not parsed:
            continue
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed
    return default


def _lifecycle_plan_generated_at():
    generated_at = _LIFECYCLE_PLAN_CACHE.get('generated_at')
    if generated_at:
        return generated_at
    return timezone.now()


def _request_field_set(request, *, allowed: set[str], default: set[str] | None = None) -> set[str]:
    raw = str(request.GET.get('fields') or '').strip()
    if not raw:
        return set(default or allowed)
    values = {item.strip().lower() for item in raw.split(',') if item.strip()}
    return {item for item in values if item in allowed}


def _strip_lifecycle_plan_fields(items: list[dict], fields: set[str]) -> list[dict]:
    if not items:
        return items
    hidden_keys: set[str] = set()
    if 'notes' not in fields:
        hidden_keys.update({
            'blocked_reason',
            'display_note',
            'note',
            'quality_flags',
            'quality_label',
            'source_note',
            'state_summary',
            'status_summary',
        })
    if 'execution' not in fields:
        hidden_keys.update({
            'delete_attempt_count',
            'delete_attempt_label',
            'delete_next_attempt',
            'deletion_source_label',
            'error',
            'execution_plan',
            'execution_status',
            'failure_reason',
            'last_failure_reason',
            'retry_label',
        })
    if 'account' not in fields:
        hidden_keys.update({
            'account_label',
            'cloud_account_id',
            'cloud_account_name',
            'external_account_id',
        })
    if 'provider' not in fields:
        hidden_keys.update({'provider', 'provider_label', 'provider_status'})
    if not hidden_keys:
        return items
    for item in items:
        for key in hidden_keys:
            item.pop(key, None)
    return items


def _cloud_ip_trace_logged_at(note, fallback=None):
    text = _cloud_ip_trace_note_newest_first(note)
    first_line = next((line for line in text.splitlines() if line.strip()), '')
    match = re.search(r'执行时间：(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', first_line)
    if not match:
        return fallback
    parsed = parse_datetime(match.group(1))
    if parsed is None:
        return fallback
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _cloud_ip_trace_for_asset_or_order(asset=None, order=None):
    lookup = CloudIpLog.objects.select_related('order', 'asset', 'user').all()
    if asset is not None:
        is_unattached_static_asset = (
            '未附加固定IP' in str(getattr(asset, 'provider_status', '') or '')
            or 'StaticIp' in str(getattr(asset, 'provider_resource_id', '') or '')
            or not getattr(asset, 'instance_id', None)
        )
        if is_unattached_static_asset:
            exact = lookup.filter(Q(asset=asset) | Q(asset_name=asset.asset_name, public_ip=asset.public_ip)).order_by('-id').first()
            if exact:
                return exact
        conditions = Q(asset=asset)
        if getattr(asset, 'order_id', None):
            conditions |= Q(order_id=asset.order_id)
        if getattr(asset, 'public_ip', None):
            conditions |= Q(public_ip=asset.public_ip) | Q(previous_public_ip=asset.public_ip)
        lookup = lookup.filter(conditions)
    elif order is not None:
        conditions = Q(order=order)
        if getattr(order, 'public_ip', None):
            conditions |= Q(public_ip=order.public_ip) | Q(previous_public_ip=order.public_ip)
        if getattr(order, 'previous_public_ip', None):
            conditions |= Q(public_ip=order.previous_public_ip) | Q(previous_public_ip=order.previous_public_ip)
        lookup = lookup.filter(conditions)
    else:
        return None
    return lookup.order_by('-id').first()


def _cloud_ip_trace_maps_for_assets(assets):
    asset_ids = [asset.id for asset in assets if getattr(asset, 'id', None)]
    order_ids = [asset.order_id for asset in assets if getattr(asset, 'order_id', None)]
    ips = [
        ip
        for asset in assets
        for ip in [str(getattr(asset, 'public_ip', '') or '').strip(), str(getattr(asset, 'previous_public_ip', '') or '').strip()]
        if ip
    ]
    conditions = Q()
    if asset_ids:
        conditions |= Q(asset_id__in=asset_ids)
    if order_ids:
        conditions |= Q(order_id__in=order_ids)
    if ips:
        conditions |= Q(public_ip__in=ips) | Q(previous_public_ip__in=ips)
    if not conditions:
        return {}, {}, {}
    logs = CloudIpLog.objects.select_related('order', 'asset', 'user').filter(conditions).order_by('-id')[:5000]
    by_asset = {}
    by_order = {}
    by_ip = {}
    for log in logs:
        if log.asset_id and log.asset_id not in by_asset:
            by_asset[log.asset_id] = log
        if log.order_id and log.order_id not in by_order:
            by_order[log.order_id] = log
        for ip in [str(log.public_ip or '').strip(), str(log.previous_public_ip or '').strip()]:
            if ip and ip not in by_ip:
                by_ip[ip] = log
    return by_asset, by_order, by_ip


def _cloud_ip_trace_from_maps(asset, trace_maps):
    by_asset, by_order, by_ip = trace_maps
    if getattr(asset, 'id', None) in by_asset:
        return by_asset[asset.id]
    if getattr(asset, 'order_id', None) in by_order:
        return by_order[asset.order_id]
    for ip in [str(getattr(asset, 'public_ip', '') or '').strip(), str(getattr(asset, 'previous_public_ip', '') or '').strip()]:
        if ip and ip in by_ip:
            return by_ip[ip]
    return None


def _shutdown_log_items(limit=100):
    cutoff = timezone.now() - timezone.timedelta(days=7)

    items = []
    assets = list(
        _active_cloud_asset_queryset()
        .filter(actual_expires_at__isnull=False)
        .order_by('actual_expires_at', '-updated_at')[:500]
    )
    trace_maps = _cloud_ip_trace_maps_for_assets(assets)
    seen_trace_ids = set()
    for asset in assets:
        if _asset_is_unattached_ip(asset) and asset.status in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_TERMINATING}:
            continue
        order = asset.order if asset.order_id and asset.order else None
        trace = _cloud_ip_trace_from_maps(asset, trace_maps)
        expires_at = asset.actual_expires_at
        user_display_name, username_label = _telegram_user_labels(asset.user or (order.user if order else None))
        account_name, external_account_id = _cloud_account_labels(asset)
        if not external_account_id and asset.order_id and asset.order:
            order_account_name, order_external_account_id = _cloud_account_labels(asset.order)
            account_name = account_name or order_account_name
            external_account_id = order_external_account_id
        if asset.provider == 'aliyun_simple' or not expires_at:
            suspend_at = None
            delete_at = None
        elif order and (order.suspend_at or order.delete_at):
            suspend_at = order.suspend_at
            delete_at = order.delete_at
        else:
            schedule = compute_order_lifecycle_schedule(expires_at)
            suspend_at = schedule.suspend_at
            delete_at = schedule.delete_at
        status = trace.event_type if trace else asset.status
        status_label = _status_label(status, CloudIpLog.EVENT_CHOICES if trace else CloudAsset.STATUS_CHOICES)
        is_terminal_failure = asset.status in {CloudAsset.STATUS_UNKNOWN, CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED}
        is_success = bool(asset.status in {CloudAsset.STATUS_RUNNING, 'completed'} and not is_terminal_failure)
        if trace:
            note = _cloud_ip_trace_note_newest_first(trace.note)
            logged_at = _cloud_ip_trace_logged_at(trace.note, trace.created_at)
            seen_trace_ids.add(trace.id)
        else:
            if asset.status in {CloudAsset.STATUS_STOPPED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATING}:
                action = '到期关机/删机流程执行中'
            elif asset.status in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED}:
                action = '到期删机已执行'
            elif suspend_at and timezone.now() >= suspend_at:
                action = '到期关机待执行或已执行'
            else:
                action = '等待到期关机计划'
            source_note = asset.note or getattr(order, 'provision_note', '') or ''
            note = _shutdown_execution_note(
                status_label=status_label,
                is_success=is_success,
                executed_at=asset.updated_at,
                action=action,
                failure_reason=_extract_failure_reason(source_note),
                deletion_source=_delete_source_label(source_note),
            )
            logged_at = asset.updated_at
        source_kind = 'ip_log' if trace else 'asset'
        source_id = trace.id if trace else asset.id
        items.append({
            **_plan_item_identity(source_kind, source_id, plan_kind='server_history'),
            'order_id': order.id if order else asset.order_id,
            'asset_id': asset.id,
            'order_no': (order.order_no if order else '') or asset.asset_name or asset.instance_id or f'asset-{asset.id}',
            'order_detail_path': f'/admin/cloud-orders/{order.id}' if order else '',
            'asset_detail_path': f'/admin/cloud-assets/{asset.id}',
            'detail_path': f'/admin/cloud-orders/{order.id}' if order else f'/admin/cloud-assets/{asset.id}',
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': (trace.public_ip if trace else None) or asset.public_ip or asset.previous_public_ip or '',
            'provider': (trace.provider if trace else None) or asset.provider or '',
            'provider_label': _provider_label((trace.provider if trace else None) or asset.provider),
            'cloud_account_id': asset.cloud_account_id or (order.cloud_account_id if order else None),
            'cloud_account_name': account_name,
            'external_account_id': external_account_id,
            'account_label': asset.account_label or (order.account_label if order else '') or '',
            'status': status,
            'status_label': status_label,
            'deletion_source_label': _delete_source_label(note),
            'actual_expires_at': expires_at,
            'suspend_at': suspend_at,
            'delete_at': delete_at,
            'note': note,
            'display_note': _compact_dashboard_note(note, max_chars=500),
            'logged_at': logged_at,
        })

    history_traces = CloudIpLog.objects.select_related('order', 'asset', 'user').filter(
        Q(note__icontains='执行内容：AWS 实例已执行关机')
        | Q(note__icontains='执行内容：实例已删除')
        | Q(note__icontains='执行内容：固定 IP 保留期结束')
    ).order_by('-id')[:300]
    for trace in history_traces:
        if trace.id in seen_trace_ids:
            continue
        order = trace.order
        asset = trace.asset
        user_display_name, username_label = _telegram_user_labels(trace.user or (order.user if order else None))
        account_name, external_account_id = _cloud_account_labels(asset or order or trace)
        suspend_at = getattr(order, 'suspend_at', None)
        delete_at = getattr(order, 'delete_at', None)
        expires_at = getattr(asset, 'actual_expires_at', None)
        note = _cloud_ip_trace_note_newest_first(trace.note)
        logged_at = _cloud_ip_trace_logged_at(trace.note, trace.created_at)
        items.append({
            **_plan_item_identity('ip_log', trace.id, plan_kind='server_history'),
            'order_id': trace.order_id,
            'asset_id': trace.asset_id,
            'order_no': trace.order_no or getattr(asset, 'asset_name', '') or getattr(order, 'server_name', '') or f'trace-{trace.id}',
            'order_detail_path': f'/admin/cloud-orders/{trace.order_id}' if trace.order_id else '',
            'asset_detail_path': f'/admin/cloud-assets/{trace.asset_id}' if trace.asset_id else '',
            'detail_path': f'/admin/cloud-orders/{trace.order_id}' if trace.order_id else (f'/admin/cloud-assets/{trace.asset_id}' if trace.asset_id else ''),
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': trace.public_ip or trace.previous_public_ip or '',
            'provider': trace.provider or '',
            'provider_label': _provider_label(trace.provider),
            'cloud_account_id': getattr(asset, 'cloud_account_id', None) or getattr(order, 'cloud_account_id', None),
            'cloud_account_name': account_name,
            'external_account_id': external_account_id,
            'account_label': getattr(asset, 'account_label', '') or getattr(order, 'account_label', '') or '',
            'status': trace.event_type,
            'status_label': _status_label(trace.event_type, CloudIpLog.EVENT_CHOICES),
            'deletion_source_label': _delete_source_label(note),
            'actual_expires_at': expires_at,
            'suspend_at': suspend_at,
            'delete_at': delete_at,
            'note': note,
            'display_note': _compact_dashboard_note(note, max_chars=500),
            'logged_at': logged_at,
        })

    deduped = {}
    for item in items:
        deduped[item['id']] = item
    items = list(deduped.values())

    def sort_key(item):
        suspend_at = item['suspend_at']
        sort_at = item['logged_at'] or item['actual_expires_at']
        is_old_shutdown = bool(suspend_at and suspend_at < cutoff)
        timestamp = sort_at.timestamp() if sort_at else float('inf')
        return (1 if is_old_shutdown else 0, -timestamp if is_old_shutdown else -timestamp, str(item['id']))

    sorted_items = sorted(items, key=sort_key)[:limit]
    return [
        {
            **item,
            'actual_expires_at': _iso(item['actual_expires_at']),
            'suspend_at': _iso(item['suspend_at']),
            'delete_at': _iso(item['delete_at']),
            'is_old_shutdown': bool(item['suspend_at'] and item['suspend_at'] < cutoff),
            'logged_at': _iso(item['logged_at']),
        }
        for item in sorted_items
    ]


def _cloud_asset_deleted_or_missing_q():
    inactive_status_q = Q(status__in=[
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_TERMINATED,
        CloudAsset.STATUS_TERMINATING,
    ])
    provider_missing_q = (
        Q(provider_status__icontains='云上未找到')
        | Q(provider_status__icontains='已到期删除')
        | Q(provider_status__icontains='已删除')
    )
    dirty_note_q = (Q(note__icontains='云上不存在') | Q(note__icontains='已标记删除')) & ~Q(status__in=[
        CloudAsset.STATUS_RUNNING,
        CloudAsset.STATUS_PENDING,
        CloudAsset.STATUS_STARTING,
        CloudAsset.STATUS_STOPPED,
        CloudAsset.STATUS_SUSPENDED,
        CloudAsset.STATUS_UNKNOWN,
    ])
    return inactive_status_q | provider_missing_q | dirty_note_q


def _unattached_ip_deleted_or_missing_q():
    inactive_status_q = Q(status__in=[
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_TERMINATED,
        CloudAsset.STATUS_TERMINATING,
    ])
    final_missing_q = (
        Q(provider_status__icontains='已到期删除')
        | Q(provider_status__icontains='已删除')
        | Q(note__icontains='IP校验发现云上不存在，已标记删除')
        | Q(note__icontains='固定 IP 云端已不存在')
        | Q(note__icontains='固定IP云端已不存在')
        | Q(note__icontains='云上不存在，已标记删除')
    )
    return inactive_status_q | final_missing_q


def _unattached_ip_deleted_or_missing_text(item: dict) -> bool:
    text = '\n'.join(
        str(item.get(key) or '')
        for key in ['source_note', 'note', 'provider_status', 'execution_status', 'deletion_source_label']
    ).replace('固定 IP', '固定IP')
    status = str(item.get('status') or '')
    if status in {
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_TERMINATED,
    }:
        return True
    return any(marker in text for marker in [
        '已到期删除',
        '已删除',
        '固定IP已释放',
        '释放固定IP成功',
        '固定IP云端已不存在',
        '云端已不存在',
        '云上已不存在',
        '云上不存在，已标记删除',
        'IP校验发现云上不存在，已标记删除',
    ])


def _ip_delete_completed_active_to_history(item: dict) -> dict:
    executed_at = item.get('logged_at') or item.get('delete_at') or item.get('next_run_at')
    source_note = item.get('source_note') or item.get('note') or item.get('display_note') or '固定 IP 已删除'
    history = {
        **item,
        'is_history': True,
        'executed_at': executed_at,
        'logged_at': executed_at,
        'provider_status': item.get('provider_status') or '已删除',
        'deletion_source_label': item.get('deletion_source_label') or _delete_source_label(source_note),
        'execution_status': item.get('execution_status') or '固定 IP 已删除',
        'note': source_note,
        'display_note': item.get('display_note') or _compact_dashboard_note(source_note, max_chars=500),
    }
    history.update(_unattached_ip_delete_attempt_state(history, is_history=True))
    return history


def _move_completed_ip_delete_rows_to_history(items: list[dict]) -> tuple[list[dict], list[dict], int]:
    converted_history_items = []
    active_items = []
    converted_count = 0
    for item in items:
        if item.get('is_history') or item.get('data_group') == 'history':
            converted_history_items.append(item)
            continue
        confirm_state = missing_confirmation_state(item)
        if 0 < confirm_state['count'] < confirm_state['threshold']:
            active_items.append(item)
            continue
        if item.get('plan_state') == 'completed' or _unattached_ip_deleted_or_missing_text(item):
            converted_history_items.append(_ip_delete_completed_active_to_history(item))
            converted_count += 1
            continue
        active_items.append(item)
    return active_items, converted_history_items, converted_count


def _unattached_ip_delete_history_q():
    terminal_q = Q(event_type__in=[CloudIpLog.EVENT_DELETED, CloudIpLog.EVENT_RECYCLED])
    explicit_note_q = (
        Q(note__icontains='未附加固定IP')
        | Q(note__icontains='未附加IP')
        | Q(note__icontains='AWS 同步删除未附加固定 IP')
        | Q(note__icontains='IP校验发现云上不存在，已标记删除')
        | Q(note__icontains='固定 IP 已释放')
        | Q(note__icontains='固定 IP 已真实释放')
        | Q(note__icontains='固定IP已真实释放')
        | Q(note__icontains='固定 IP 云端已不存在')
        | Q(note__icontains='release_static_ip')
    )
    asset_q = (
        Q(asset__provider_status__icontains='未附加')
        | Q(asset__note__icontains='未附加固定IP')
        | Q(asset__provider_resource_id__icontains='StaticIp')
    ) & (Q(asset__instance_id__isnull=True) | Q(asset__instance_id=''))
    return terminal_q & (explicit_note_q | asset_q)


def _ip_delete_item_quality(item: dict, duplicate_count: int = 0) -> dict:
    note = str(item.get('note') or '')
    source_note = str(item.get('source_note') or '')
    provider_status = str(item.get('provider_status') or '')
    confirm_note = '\n'.join(filter(None, [note, source_note, provider_status]))
    flags = []
    labels = []
    if duplicate_count > 0:
        flags.append('covered_duplicates')
        labels.append(f'已覆盖 {duplicate_count} 条同 IP 旧记录')
    if any(marker in confirm_note for marker in ['云上不存在', '云上未找到', '云端已不存在', '已标记删除']):
        flags.append('cloud_missing')
        labels.append('云上已不存在')
    if any(marker in confirm_note for marker in ['历史脏数据', '脏数据', '待确认']):
        flags.append('dirty_data')
        labels.append('脏数据')
    confirm_state = missing_confirmation_state(item)
    item['missing_confirm_count'] = confirm_state['count']
    item['missing_confirm_threshold'] = confirm_state['threshold']
    item['missing_confirm_remaining'] = confirm_state['remaining']
    item['missing_confirm_interval_minutes'] = confirm_state['interval_minutes']
    item['missing_confirm_checked_at'] = _iso(confirm_state['checked_at'])
    item['missing_confirm_next_check_at'] = _iso(confirm_state['next_check_at'])
    item['missing_confirm_due'] = confirm_state['due']
    if confirm_state['count'] > 0:
        flags.append('missing_confirming')
        labels.append(f'缺失确认 {confirm_state["count"]}/{confirm_state["threshold"]}')
        if confirm_state['next_check_at'] and not confirm_state['due']:
            labels.append(f'下次确认 {_fmt_dashboard_dt(confirm_state["next_check_at"])}')
    item['quality_flags'] = flags
    item['quality_label'] = '，'.join(labels)
    if labels:
        item['execution_status'] = f'{item.get("execution_status") or item.get("provider_status") or "-"}（{"，".join(labels)}）'
    return item


def _unattached_ip_delete_attempt_state(item: dict, *, is_history: bool | None = None) -> dict:
    text_parts = []
    seen_parts = set()
    for value in [
        item.get('source_note'),
        item.get('note'),
        item.get('provider_status'),
        item.get('execution_status'),
    ]:
        text_value = str(value or '').strip()
        if not text_value or text_value in seen_parts:
            continue
        seen_parts.add(text_value)
        text_parts.append(text_value)
    text = '\n'.join(text_parts)
    explicit_numbers = [
        int(match.group(1))
        for match in re.finditer(r'第\s*(\d+)\s*次(?:执行)?(?:删除|释放|删除确认)', text)
        if match.group(1).isdigit()
    ]
    attempt_markers = [
        'AWS API 删除失败',
        '系统已调用 AWS API 真实删除',
        'AWS 固定 IP 已真实释放',
        'AWS 固定 IP 真实释放失败',
        'AWS 固定 IP 云端已不存在',
        '已调用 AWS release_static_ip',
        'release_static_ip',
        '释放固定IP成功',
        '固定 IP 已释放',
        '固定IP已释放',
    ]
    marker_count = 0
    for line in [line.strip() for line in text.splitlines() if line.strip()]:
        if any(marker in line for marker in attempt_markers):
            marker_count += 1
    count = max([marker_count, *explicit_numbers], default=0)
    history = bool(item.get('is_history')) if is_history is None else bool(is_history)
    next_attempt = None if history else count + 1
    if history:
        label = f'第{max(count, 1)}次删除已完成'
    elif count > 0:
        label = f'已尝试{count}次，待第{next_attempt}次删除'
    else:
        label = '尚未执行，待第1次删除'
    return {
        'delete_attempt_count': count,
        'delete_next_attempt': next_attempt,
        'delete_attempt_label': label,
    }


def _dedupe_ip_delete_items_by_ip(items: list[dict]) -> list[dict]:
    buckets = {}
    no_ip_items = []
    for item in items:
        ip = str(item.get('public_ip') or '').strip()
        if not ip:
            no_ip_items.append(_ip_delete_item_quality(item))
            continue
        buckets.setdefault(ip, []).append(item)
    deduped = []
    for ip_items in buckets.values():
        ip_items = sorted(ip_items, key=lambda item: (
            parse_datetime(item.get('logged_at') or item.get('delete_at') or '') or datetime.min.replace(tzinfo=dt_timezone.utc),
            int(item.get('asset_id') or item.get('id') or 0) if str(item.get('asset_id') or item.get('id') or '').isdigit() else 0,
        ), reverse=True)
        deduped.append(_ip_delete_item_quality(ip_items[0], duplicate_count=len(ip_items) - 1))
    return deduped + no_ip_items


def _unattached_ip_delete_items(limit=50, assets=None):
    now = timezone.now()
    global_ip_enabled = cloud_ip_delete_enabled()
    limit = max(1, min(int(limit or 50), 1000))
    unattached_q = (
        Q(provider_status__icontains='未附加')
        | Q(note__icontains='未附加IP')
        | Q(note__icontains='未附加固定IP')
        | Q(note__icontains='固定 IP 已释放')
        | Q(note__icontains='固定IP已释放')
        | Q(note__icontains='固定 IP 云端已不存在')
        | Q(note__icontains='固定IP云端已不存在')
        | Q(provider_resource_id__icontains='StaticIp')
    )
    blank_instance_q = Q(instance_id__isnull=True) | Q(instance_id='')
    active_account_labels = _active_cloud_account_labels()
    inactive_account_labels = [
        label
        for account in CloudAccountConfig.objects.filter(
            provider__in=[CloudAccountConfig.PROVIDER_AWS, CloudAccountConfig.PROVIDER_ALIYUN],
            is_active=False,
        )
        for label in cloud_account_label_variants(account)
    ]
    active_account_q = (
        Q(cloud_account__isnull=True, account_label__isnull=True)
        | Q(cloud_account__isnull=True, account_label='')
        | Q(cloud_account__is_active=True)
        | Q(account_label__in=active_account_labels)
    )
    deleted_assets_for_history = []
    if assets is None:
        assets = list(
            CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
            .filter(kind=CloudAsset.KIND_SERVER)
            .filter(unattached_q)
            .filter(blank_instance_q)
            .filter(active_account_q)
            .exclude(Q(cloud_account__is_active=False) | Q(account_label__in=inactive_account_labels))
            .exclude(_unattached_ip_deleted_or_missing_q())
            .order_by('actual_expires_at', 'created_at', '-updated_at')[:limit]
        )
        deleted_assets_for_history = list(
            CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
            .filter(kind=CloudAsset.KIND_SERVER)
            .filter(unattached_q)
            .filter(blank_instance_q)
            .filter(_unattached_ip_deleted_or_missing_q())
            .order_by('-updated_at', '-id')[:limit]
        )
    else:
        assets = [
            asset for asset in assets
            if _asset_is_unattached_ip(asset) or 'StaticIp' in str(getattr(asset, 'provider_resource_id', '') or '')
        ]
        seen_asset_ids = {asset.id for asset in assets}
        extra_assets = list(
            CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
            .filter(kind=CloudAsset.KIND_SERVER)
            .filter(unattached_q)
            .filter(blank_instance_q)
            .exclude(id__in=seen_asset_ids)
            .exclude(_unattached_ip_deleted_or_missing_q())
            .order_by('actual_expires_at', 'created_at', '-updated_at')[:limit]
        )
        assets = [*assets, *extra_assets][:limit]
    trace_maps = _cloud_ip_trace_maps_for_assets(assets)
    items = []
    seen_trace_ids = set()
    active_unattached_ips = {str(asset.public_ip or '').strip() for asset in assets if str(asset.public_ip or '').strip()}
    for asset in assets:
        confirm_state = missing_confirmation_state(asset)
        if confirm_state['count'] >= confirm_state['threshold']:
            continue
        user_display_name, username_label = _telegram_user_labels(asset.user)
        delete_at = asset.actual_expires_at or _ensure_unattached_ip_delete_due(asset, now=now)
        trace = _cloud_ip_trace_from_maps(asset, trace_maps)
        trace_note = ''
        if trace:
            trace_note = _cloud_ip_trace_note_newest_first(trace.note)
            logged_at = _cloud_ip_trace_logged_at(trace.note, trace.created_at)
            seen_trace_ids.add(trace.id)
        else:
            logged_at = asset.updated_at
        note = _asset_note_text(asset)
        source_note = trace_note or note
        asset_name = asset.asset_name or getattr(asset, 'static_ip_name', '') or asset.instance_id or f'asset-{asset.id}'
        shutdown_enabled = _asset_shutdown_enabled(asset)
        ip_delete_enabled = _asset_ip_delete_enabled(asset)
        item = {
            **_plan_item_identity('asset', asset.id, plan_kind=PLAN_KIND_UNATTACHED_IP_DELETE),
            'plan_kind': PLAN_KIND_UNATTACHED_IP_DELETE,
            'asset_id': asset.id,
            'asset_name': asset_name,
            'asset_detail_path': f'/admin/cloud-assets/{asset.id}',
            'detail_path': f'/admin/cloud-assets/{asset.id}',
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': (trace.public_ip if trace else None) or asset.public_ip or asset.previous_public_ip or '',
            'provider_status': asset.provider_status or (_status_label(trace.event_type, CloudIpLog.EVENT_CHOICES) if trace else ''),
            'deletion_source_label': _delete_source_label(trace_note, default='计划自动删除'),
            'actual_expires_at': _iso(asset.actual_expires_at),
            'delete_at': _iso(delete_at),
            'logged_at': _iso(logged_at),
            'source_note': source_note,
            'note': note,
            'display_note': _asset_display_note(asset, fallback=trace_note, max_chars=500),
            'sync_state': getattr(asset, 'sync_state', {}) or {},
            'is_overdue': bool(delete_at and delete_at <= now),
            'is_history': False,
            'shutdown_enabled': shutdown_enabled,
            'ip_delete_enabled': ip_delete_enabled,
        }
        if not global_ip_enabled:
            item['queue_status'] = 'global_ip_delete_disabled'
            item['queue_status_label'] = '总开关关闭'
            item['execution_status'] = 'IP 删除总开关关闭，禁止真实释放固定 IP'
        elif not ip_delete_enabled:
            item['queue_status'] = 'ip_delete_disabled'
            item['queue_status_label'] = 'IP删除开关关闭'
            item['execution_status'] = '资产 IP 删除计划开关关闭，禁止真实释放固定 IP'
        item.update(_unattached_ip_delete_attempt_state(item, is_history=False))
        items.append(item)

    history_traces = CloudIpLog.objects.select_related('asset', 'order', 'user').filter(
        _unattached_ip_delete_history_q()
    ).order_by('-id')[:limit]
    for trace in history_traces:
        if trace.id in seen_trace_ids:
            continue
        trace_ip = str(trace.public_ip or trace.previous_public_ip or '').strip()
        if trace_ip and trace_ip in active_unattached_ips:
            continue
        asset = trace.asset
        order = trace.order
        user_display_name, username_label = _telegram_user_labels(trace.user or getattr(asset, 'user', None))
        asset_name = trace.asset_name or getattr(order, 'static_ip_name', '') or getattr(asset, 'asset_name', '') or trace.instance_id or f'trace-{trace.id}'
        logged_at = _cloud_ip_trace_logged_at(trace.note, trace.created_at)
        delete_at = logged_at or getattr(order, 'ip_recycle_at', None) or getattr(asset, 'actual_expires_at', None)
        item = {
            **_plan_item_identity('ip_log', trace.id, plan_kind=PLAN_KIND_UNATTACHED_IP_DELETE, plan_stage='history'),
            'plan_kind': PLAN_KIND_UNATTACHED_IP_DELETE,
            'asset_id': trace.asset_id,
            'asset_name': asset_name,
            'asset_detail_path': f'/admin/cloud-assets/{trace.asset_id}' if trace.asset_id else '',
            'detail_path': f'/admin/cloud-orders/{trace.order_id}' if trace.order_id else (f'/admin/cloud-assets/{trace.asset_id}' if trace.asset_id else ''),
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': trace.public_ip or trace.previous_public_ip or '',
            'provider_status': _status_label(trace.event_type, CloudIpLog.EVENT_CHOICES),
            'deletion_source_label': _delete_source_label(trace.note),
            'actual_expires_at': _iso(getattr(asset, 'actual_expires_at', None)),
            'delete_at': _iso(delete_at),
            'logged_at': _iso(logged_at),
            'source_note': _cloud_ip_trace_note_newest_first(trace.note),
            'note': _cloud_ip_trace_note_newest_first(trace.note),
            'display_note': _compact_dashboard_note(trace.note, max_chars=500),
            'is_overdue': True,
            'is_history': True,
        }
        item.update(_unattached_ip_delete_attempt_state(item, is_history=True))
        items.append(item)
    for asset in deleted_assets_for_history:
        asset_ip = str(asset.public_ip or asset.previous_public_ip or '').strip()
        if asset_ip and asset_ip in active_unattached_ips:
            continue
        if asset.id in {item.get('asset_id') for item in items if item.get('is_history')}:
            continue
        user_display_name, username_label = _telegram_user_labels(asset.user)
        executed_at = asset.updated_at or asset.actual_expires_at or now
        source_note = _asset_note_text(asset) or asset.provider_status or '固定 IP 已删除'
        item = {
            **_plan_item_identity('asset', asset.id, plan_kind=PLAN_KIND_UNATTACHED_IP_DELETE, plan_stage='history'),
            'plan_kind': PLAN_KIND_UNATTACHED_IP_DELETE,
            'asset_id': asset.id,
            'asset_name': asset.asset_name or asset.instance_id or f'asset-{asset.id}',
            'asset_detail_path': f'/admin/cloud-assets/{asset.id}',
            'detail_path': f'/admin/cloud-assets/{asset.id}',
            'user_display_name': user_display_name,
            'username_label': username_label,
            'public_ip': asset_ip,
            'provider_status': asset.provider_status or '已删除',
            'deletion_source_label': _delete_source_label(source_note),
            'actual_expires_at': _iso(asset.actual_expires_at),
            'delete_at': _iso(executed_at),
            'logged_at': _iso(executed_at),
            'source_note': source_note,
            'note': source_note,
            'display_note': _asset_display_note(asset, fallback=source_note, max_chars=500),
            'is_overdue': True,
            'is_history': True,
        }
        item.update(_unattached_ip_delete_attempt_state(item, is_history=True))
        items.append(item)
    def sort_key(item):
        if item.get('is_history'):
            parsed = parse_datetime(item.get('logged_at') or item.get('delete_at') or '')
            timestamp = parsed.timestamp() if parsed else 0
            return (1, -timestamp, str(item['id']))
        return (0, 0 if item['is_overdue'] else 1, item.get('delete_at') or '', str(item['id']))

    items = _dedupe_ip_delete_items_by_ip(items)
    sorted_items = sorted(items, key=sort_key)
    active_items = [item for item in sorted_items if not item.get('is_history')][:limit]
    history_items = [item for item in sorted_items if item.get('is_history')][:limit]
    return [*active_items, *history_items]


@dashboard_login_required
@require_GET
def overview(request):
    users_total = TelegramUser.objects.count()
    server_assets_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, is_active=True).exclude(status__in=[
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_TERMINATED,
        CloudAsset.STATUS_TERMINATING,
        CloudAsset.STATUS_EXPIRED,
        CloudAsset.STATUS_UNKNOWN,
    ]).count()
    products_total = Product.objects.count()
    cloud_orders_total = CloudServerOrder.objects.count()
    recharges_total = Recharge.objects.count()
    monitors_total = AddressMonitor.objects.count()
    orders_total = Order.objects.count()

    today = timezone.localdate()
    today_start = timezone.make_aware(timezone.datetime.combine(today, timezone.datetime.min.time()))
    renew_before = timezone.now() + timezone.timedelta(days=7)
    cloud_pending = CloudServerOrder.objects.filter(status='pending').count()
    recharge_pending = Recharge.objects.filter(status='pending').count()
    today_end = today_start + timezone.timedelta(days=1)
    new_orders_today = CloudServerOrder.objects.filter(created_at__gte=today_start).count()
    active_server_assets = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, status__in=[CloudAsset.STATUS_RUNNING, CloudAsset.STATUS_UNKNOWN])
    due_today = active_server_assets.filter(
        actual_expires_at__gte=today_start,
        actual_expires_at__lt=today_end,
    ).count()
    renew_due = active_server_assets.filter(
        actual_expires_at__isnull=False,
        actual_expires_at__lte=renew_before,
    ).count()
    paid_orders = CloudServerOrder.objects.filter(status__in=['paid', 'completed'])
    revenue = paid_orders.aggregate(total=Sum('pay_amount'))['total'] or Decimal('0')
    cost = Decimal('0')
    for order in paid_orders.select_related('plan').only('quantity', 'plan__cost_price'):
        cost += Decimal(str(getattr(order.plan, 'cost_price', 0) or 0)) * Decimal(str(order.quantity or 1))
    profit = revenue - cost

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
    shutdown_logs = _shutdown_log_items(limit=80)
    unattached_ip_delete_plans = _unattached_ip_delete_items(limit=30)

    month_start = today.replace(day=1)
    next_month = (month_start.replace(day=28) + timezone.timedelta(days=4)).replace(day=1)
    trend_start = timezone.make_aware(timezone.datetime.combine(month_start, timezone.datetime.min.time()))
    trend_end = timezone.make_aware(timezone.datetime.combine(next_month, timezone.datetime.min.time()))
    trend_labels = [str(day) for day in range(1, 32)]
    users_growth = [0 for _ in trend_labels]
    orders_growth = [0 for _ in trend_labels]
    servers_growth = [0 for _ in trend_labels]
    expiry_trend = [0 for _ in trend_labels]
    profit_trend = [0 for _ in trend_labels]

    for created_at in TelegramUser.objects.filter(created_at__gte=trend_start, created_at__lt=trend_end).values_list('created_at', flat=True):
        day = timezone.localtime(created_at).day if timezone.is_aware(created_at) else created_at.day
        users_growth[day - 1] += 1
    for created_at in CloudServerOrder.objects.filter(created_at__gte=trend_start, created_at__lt=trend_end).values_list('created_at', flat=True):
        day = timezone.localtime(created_at).day if timezone.is_aware(created_at) else created_at.day
        orders_growth[day - 1] += 1
    for created_at in CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, created_at__gte=trend_start, created_at__lt=trend_end).values_list('created_at', flat=True):
        day = timezone.localtime(created_at).day if timezone.is_aware(created_at) else created_at.day
        servers_growth[day - 1] += 1
    trend_assets = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, actual_expires_at__isnull=False).only('actual_expires_at')
    for asset in trend_assets:
        expires_at = asset.actual_expires_at
        if not expires_at or expires_at < trend_start or expires_at >= trend_end:
            continue
        day = timezone.localtime(expires_at).day if timezone.is_aware(expires_at) else expires_at.day
        expiry_trend[day - 1] += 1

    daily_paid_orders = list(
        CloudServerOrder.objects.filter(status__in=['paid', 'completed'], created_at__gte=trend_start, created_at__lt=trend_end)
        .select_related('plan')
        .only('created_at', 'pay_amount', 'quantity', 'plan__cost_price')
    )
    for order in daily_paid_orders:
        day = timezone.localtime(order.created_at).day if timezone.is_aware(order.created_at) else order.created_at.day
        order_revenue = Decimal(str(order.pay_amount or 0))
        order_cost = Decimal(str(getattr(order.plan, 'cost_price', 0) or 0)) * Decimal(str(order.quantity or 1))
        profit_trend[day - 1] = float(_decimal_to_str(Decimal(str(profit_trend[day - 1])) + order_revenue - order_cost))

    return _ok({
        'summary': {
            'users_total': users_total,
            'server_assets_total': server_assets_total,
            'products_total': products_total,
            'cloud_orders_total': cloud_orders_total,
            'recharges_total': recharges_total,
            'monitors_total': monitors_total,
            'orders_total': orders_total,
            'cloud_pending': cloud_pending,
            'recharge_pending': recharge_pending,
            'new_orders_today': new_orders_today,
            'due_today': due_today,
            'renew_due': renew_due,
            'revenue_total': _decimal_to_str(revenue, 2),
            'cost_total': _decimal_to_str(cost, 2),
            'profit_total': _decimal_to_str(profit, 2),
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
        'charts': {
            'trend': {
                'labels': trend_labels,
                'users': users_growth,
                'orders': orders_growth,
                'servers': servers_growth,
                'profit': profit_trend,
                'expiry': expiry_trend,
            },
        },
        'latest_recharges': [
            {
                **item,
                'status_label': _status_label(item['status'], Recharge.STATUS_CHOICES),
                'amount': _decimal_to_str(item['amount']),
                'created_at': _iso(item['created_at']),
            }
            for item in latest_recharges
        ],
        'shutdown_logs': shutdown_logs,
        'unattached_ip_delete_plans': unattached_ip_delete_plans,
    })


@dashboard_login_required
@require_GET
def ip_delete_logs(request):
    try:
        limit = int(request.GET.get('limit') or 100)
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 300))
    return _ok(_unattached_ip_delete_items(limit=limit))


def _shutdown_plan_item_payload(order, *, queue_status='scheduled_future', queue_status_label='计划中', next_run_at=None, last_failure_reason=None, note='', asset=None, plan_stage='delete'):
    user_display_name, username_label = _telegram_user_labels(order.user)
    notice_ip = order.public_ip or order.previous_public_ip or '未分配'
    is_shutdown_stage = plan_stage == 'shutdown'
    plan_at = next_run_at or (order.suspend_at if is_shutdown_stage else order.delete_at)
    shutdown_enabled = _asset_lifecycle_enabled_for_order(order, asset)
    server_delete_enabled = _asset_server_delete_enabled(asset)
    ip_delete_enabled = _asset_ip_delete_enabled(asset)
    global_shutdown_enabled = cloud_server_shutdown_enabled()
    global_server_delete_enabled = cloud_server_delete_enabled()
    global_ip_delete_enabled = cloud_ip_delete_enabled()
    if is_shutdown_stage and not global_shutdown_enabled:
        execution_status = '服务器关机总开关关闭，禁止真实关机'
        queue_status = 'global_shutdown_disabled'
        queue_status_label = '总开关关闭'
    elif is_shutdown_stage and not shutdown_enabled:
        execution_status = '资产关机计划开关关闭，禁止真实关机'
        queue_status = 'shutdown_disabled'
        queue_status_label = '资产开关关闭'
    elif not is_shutdown_stage and not global_server_delete_enabled:
        execution_status = '服务器删除总开关关闭，禁止真实删机'
        queue_status = 'global_server_delete_disabled'
        queue_status_label = '总开关关闭'
    elif not is_shutdown_stage and not server_delete_enabled:
        execution_status = '资产服务器删除计划开关关闭，禁止真实删机'
        queue_status = 'server_delete_disabled'
        queue_status_label = '删机开关关闭'
    elif not global_ip_delete_enabled and queue_status == 'ip_delete_disabled':
        execution_status = 'IP 删除总开关关闭，禁止真实释放固定 IP'
        queue_status_label = '总开关关闭'
    elif queue_status == 'waiting_manual_time':
        execution_status = '代理列表资产缺少到期时间，等待人工维护'
        queue_status_label = '待处理'
    elif queue_status == 'retry_failed':
        execution_status = '上次删除失败，等待重试'
    elif queue_status == 'fallback_retry':
        execution_status = '已到删除时间，待执行删除服务器'
    elif queue_status == 'due_now':
        execution_status = '已到关机时间，待执行关机服务器' if is_shutdown_stage else '已到删除时间，待执行删除服务器'
    elif queue_status == 'within_window':
        execution_status = '待执行关机服务器' if is_shutdown_stage else '待执行删除服务器'
    else:
        execution_status = '关机计划已生成' if is_shutdown_stage else '删除计划已生成'
    execution_plan = (
        f'关机服务器 {_fmt_dashboard_dt(plan_at)}'
        if is_shutdown_stage
        else (f'删除服务器 {_fmt_dashboard_dt(plan_at)}' if plan_at else '等待删除时间')
    )
    note = str(note or '').strip()
    return {
        **_plan_item_identity('order', order.id, plan_kind='server_shutdown' if is_shutdown_stage else 'server_delete', plan_stage=plan_stage),
        'plan_kind': 'server_shutdown' if is_shutdown_stage else 'server_delete',
        'plan_stage': plan_stage,
        'item_type': 'order',
        'asset_id': None,
        'order_id': order.id,
        'order_no': order.order_no,
        'ip': notice_ip,
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'queue_status': queue_status,
        'queue_status_label': queue_status_label,
        'user_id': order.user_id,
        'tg_user_id': getattr(order.user, 'tg_user_id', None) if order.user else None,
        'user_display_name': user_display_name,
        'username_label': username_label,
        'actual_expires_at': _iso(order_asset_expiry(order)),
        'suspend_at': _iso(order.suspend_at),
        'delete_at': _iso(order.delete_at),
        'ip_recycle_at': _iso(order.ip_recycle_at),
        'next_run_at': _iso(next_run_at),
        'last_failure_reason': last_failure_reason,
        'execution_status': execution_status,
        'execution_plan': execution_plan,
        'shutdown_enabled': shutdown_enabled,
        'server_delete_enabled': server_delete_enabled,
        'ip_delete_enabled': ip_delete_enabled,
        'source_note': str(getattr(order, 'provision_note', '') or '').strip(),
        'note': note,
        'display_note': _compact_dashboard_note(note, max_chars=500),
        'related_path': f'/admin/cloud-orders/{order.id}',
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
    }


def _linked_order_asset_delete_plan_item_payload(asset, linked_order, *, queue_status='scheduled_future', queue_status_label='计划中', note='', plan_stage='delete'):
    item = _shutdown_plan_item_payload(
        linked_order,
        queue_status=queue_status,
        queue_status_label=queue_status_label,
        note=note,
        asset=asset,
        plan_stage=plan_stage,
    )
    item.update({
        'asset_id': asset.id,
        'asset_name': asset.asset_name,
        'cloud_account_id': asset.cloud_account_id or getattr(linked_order, 'cloud_account_id', None),
        'asset_detail_path': f'/admin/cloud-assets/{asset.id}',
    })
    return item


def _asset_delete_plan_item_payload(asset, *, queue_status='scheduled_future', queue_status_label='计划中', note='', plan_stage='delete'):
    account_name, external_account_id = _cloud_account_labels(asset)
    ip = asset.public_ip or asset.previous_public_ip or '未分配'
    expires_at, suspend_at, delete_at = _server_asset_lifecycle_times(asset)
    is_shutdown_stage = plan_stage == 'shutdown'
    plan_at = suspend_at if is_shutdown_stage else delete_at
    shutdown_enabled = _asset_shutdown_enabled(asset)
    server_delete_enabled = _asset_server_delete_enabled(asset)
    ip_delete_enabled = _asset_ip_delete_enabled(asset)
    global_shutdown_enabled = cloud_server_shutdown_enabled()
    global_server_delete_enabled = cloud_server_delete_enabled()
    linked_order = getattr(asset, 'order', None)
    if _asset_is_sync_only_lifecycle(asset):
        suspend_at = None
        delete_at = None
        plan_at = None
        queue_status = 'sync_only'
        queue_status_label = '只同步/自然释放'
        execution_status = '阿里云只同步状态，按云厂商自然释放；本系统不执行真实关机和删机'
    elif linked_order and getattr(linked_order, 'status', '') not in {'deleted', 'cancelled', 'expired'}:
        return _linked_order_asset_delete_plan_item_payload(
            asset,
            linked_order,
            queue_status=queue_status,
            queue_status_label=queue_status_label,
            note=note,
            plan_stage=plan_stage,
        )
    elif linked_order and getattr(linked_order, 'status', '') in {'deleted', 'cancelled', 'expired'}:
        queue_status_label = '待处理'
        execution_status = '关联订单已结束，服务器仍存在，待执行删除服务器'
    elif linked_order:
        execution_status = '代理列表资产待删除，订单仅作为展示信息'
    else:
        execution_status = '无订单同步资产已到期，待执行删除服务器'
    if is_shutdown_stage and not global_shutdown_enabled:
        queue_status = 'global_shutdown_disabled'
        queue_status_label = '总开关关闭'
        execution_status = '服务器关机总开关关闭，禁止真实关机'
    elif is_shutdown_stage and not shutdown_enabled:
        queue_status = 'shutdown_disabled'
        queue_status_label = '资产开关关闭'
        execution_status = '资产关机计划开关关闭，禁止真实关机'
    elif not is_shutdown_stage and not global_server_delete_enabled:
        queue_status = 'global_server_delete_disabled'
        queue_status_label = '总开关关闭'
        execution_status = '服务器删除总开关关闭，禁止真实删机'
    elif not is_shutdown_stage and not server_delete_enabled:
        queue_status = 'server_delete_disabled'
        queue_status_label = '删机开关关闭'
        execution_status = '资产服务器删除计划开关关闭，禁止真实删机'
    elif queue_status == 'within_window':
        execution_status = '待执行关机服务器' if is_shutdown_stage else '待执行删除服务器'
    elif queue_status == 'scheduled_future':
        execution_status = '关机计划已生成' if is_shutdown_stage else '删除计划已生成'
    user_display_name, username_label = _telegram_user_labels(asset.user if getattr(asset, 'user', None) else None)
    note = str(note or _asset_note_text(asset)).strip()
    return {
        **_plan_item_identity('asset', asset.id, plan_kind='server_shutdown' if is_shutdown_stage else 'server_delete', plan_stage=plan_stage),
        'plan_kind': 'server_shutdown' if is_shutdown_stage else 'server_delete',
        'plan_stage': plan_stage,
        'item_type': 'orphan_asset',
        'asset_id': asset.id,
        'order_id': getattr(linked_order, 'id', None),
        'order_no': getattr(linked_order, 'order_no', None) or '-',
        'ip': ip,
        'provider': asset.provider,
        'provider_label': _provider_label(asset.provider),
        'status': asset.status,
        'status_label': _status_label(asset.status, CloudAsset.STATUS_CHOICES),
        'queue_status': queue_status,
        'queue_status_label': queue_status_label,
        'user_id': getattr(asset, 'user_id', None),
        'tg_user_id': getattr(asset.user, 'tg_user_id', None) if getattr(asset, 'user', None) else None,
        'user_display_name': user_display_name,
        'username_label': username_label,
        'actual_expires_at': _iso(expires_at),
        'suspend_at': _iso(suspend_at),
        'delete_at': _iso(delete_at),
        'ip_recycle_at': _iso(getattr(linked_order, 'ip_recycle_at', None)),
        'next_run_at': _iso(plan_at),
        'last_failure_reason': None,
        'execution_status': execution_status,
        'execution_plan': (
            '只同步/自然释放'
            if queue_status == 'sync_only'
            else (
                (f'关机服务器 {_fmt_dashboard_dt(plan_at)}' if plan_at else '等待关机时间')
                if is_shutdown_stage
                else (f'删除服务器 {_fmt_dashboard_dt(plan_at)}' if plan_at else '等待删除时间')
            )
        ),
        'source_note': _asset_note_text(asset) or str(getattr(linked_order, 'provision_note', '') or '').strip(),
        'note': note,
        'display_note': _asset_display_note(asset, fallback=note, max_chars=500),
        'shutdown_enabled': shutdown_enabled,
        'server_delete_enabled': server_delete_enabled,
        'ip_delete_enabled': ip_delete_enabled,
        'cloud_account_id': asset.cloud_account_id,
        'cloud_account_name': account_name,
        'external_account_id': external_account_id,
        'asset_name': asset.asset_name,
        'related_path': f'/admin/cloud-assets/{asset.id}',
        'detail_path': f'/admin/cloud-assets/{asset.id}',
        'order_detail_path': f'/admin/cloud-orders/{linked_order.id}' if linked_order else '',
        'order_link_path': f'/admin/cloud-orders/{linked_order.id}' if linked_order else '',
        'asset_detail_path': f'/admin/cloud-assets/{asset.id}',
    }


def _orphan_asset_delete_plan_item_payload(asset, *, queue_status='orphan_due', queue_status_label='无订单资产待删除', note=''):
    return _asset_delete_plan_item_payload(asset, queue_status=queue_status, queue_status_label=queue_status_label, note=note)


def _asset_shutdown_enabled(asset):
    return getattr(asset, 'shutdown_enabled', True) is not False


def _asset_server_delete_enabled(asset):
    return getattr(asset, 'server_delete_enabled', True) is not False


def _asset_ip_delete_enabled(asset):
    return getattr(asset, 'ip_delete_enabled', True) is not False


def _shutdown_stage_item_payload(asset, *, queue_status='scheduled_future', queue_status_label='计划中', note=''):
    linked_order = getattr(asset, 'order', None)
    if linked_order and getattr(linked_order, 'status', '') not in {'deleted', 'cancelled', 'expired'}:
        return _linked_order_asset_delete_plan_item_payload(
            asset,
            linked_order,
            queue_status=queue_status,
            queue_status_label=queue_status_label,
            note=note,
            plan_stage='shutdown',
        )
    item = _asset_delete_plan_item_payload(
        asset,
        queue_status=queue_status,
        queue_status_label=queue_status_label,
        note=note,
        plan_stage='shutdown',
    )
    item.update({
        'plan_kind': 'server_shutdown',
        'plan_stage': 'shutdown',
        'next_run_at': item.get('suspend_at'),
        'execution_plan': f'关机服务器 {_fmt_dashboard_dt(parse_datetime(item.get("suspend_at") or "") if item.get("suspend_at") else None)}' if item.get('suspend_at') else '等待关机时间',
    })
    return item


def _collect_shutdown_plan_queue(now, limit=100):
    pending_until = now + timezone.timedelta(days=7)
    server_assets = _proxy_list_cloud_asset_plan_rows(limit=limit, include_unattached=False)
    next_run_at = None
    seen_linked_order_ids = set()
    shutdown_due_items = []
    shutdown_future_items = []
    due_items = []
    future_items = []
    for asset in server_assets[:limit]:
        if not asset.actual_expires_at:
            continue
        if _asset_deleted_or_missing(asset):
            continue
        linked_order_id = getattr(asset, 'order_id', None)
        linked_order = getattr(asset, 'order', None)
        if linked_order_id and linked_order and getattr(linked_order, 'status', '') not in {'deleted', 'cancelled', 'expired'}:
            if linked_order_id in seen_linked_order_ids:
                continue
            seen_linked_order_ids.add(linked_order_id)
        if _asset_is_sync_only_lifecycle(asset):
            future_items.append(
                _asset_delete_plan_item_payload(
                    asset,
                    queue_status='sync_only',
                    queue_status_label='只同步/自然释放',
                )
            )
            continue
        _expires_at, suspend_at, delete_at = _server_asset_lifecycle_times(asset)
        if not next_run_at or (delete_at and delete_at < next_run_at):
            next_run_at = delete_at
        linked_status = str(getattr(linked_order, 'status', '') or '')
        shutdown_complete = linked_status in {'suspended', 'deleting', 'deleted'} or str(asset.status or '') in {
            CloudAsset.STATUS_STOPPED,
            CloudAsset.STATUS_SUSPENDED,
            CloudAsset.STATUS_DELETING,
            CloudAsset.STATUS_DELETED,
            CloudAsset.STATUS_TERMINATING,
            CloudAsset.STATUS_TERMINATED,
        }
        if suspend_at and not shutdown_complete:
            shutdown_payload = _shutdown_stage_item_payload(
                asset,
                queue_status='due_now' if suspend_at <= now else ('within_window' if suspend_at <= pending_until else 'scheduled_future'),
                queue_status_label='待执行' if suspend_at <= now else '计划中',
            )
            if suspend_at <= pending_until:
                shutdown_due_items.append(shutdown_payload)
            else:
                shutdown_future_items.append(shutdown_payload)
        delete_stage_ready = (
            shutdown_complete
            and (not linked_order or linked_status in {'suspended', 'deleting', 'failed', 'deleted', 'cancelled', 'expired'})
        )
        if delete_at and delete_stage_ready and delete_at <= pending_until:
            due_items.append(
                _asset_delete_plan_item_payload(
                    asset,
                    queue_status='due_now' if delete_at <= now else 'within_window',
                    queue_status_label='待执行' if delete_at <= now else '计划中',
                )
            )
            continue
        future_items.append(
            _asset_delete_plan_item_payload(
                asset,
                queue_status='scheduled_future',
                queue_status_label='计划中' if shutdown_complete else '等待关机完成',
            )
        )

    shutdown_due_items.sort(key=lambda item: parse_datetime(item.get('suspend_at') or item.get('next_run_at') or '') or datetime.max.replace(tzinfo=dt_timezone.utc))
    shutdown_future_items.sort(key=lambda item: parse_datetime(item.get('suspend_at') or item.get('next_run_at') or '') or datetime.max.replace(tzinfo=dt_timezone.utc))
    due_items.sort(key=lambda item: parse_datetime(item.get('delete_at') or '') or datetime.max.replace(tzinfo=dt_timezone.utc))
    future_items.sort(key=lambda item: parse_datetime(item.get('delete_at') or '') or datetime.max.replace(tzinfo=dt_timezone.utc))
    return {
        'due_orders': [],
        'retry_orders': [],
        'fallback_orders': [],
        'orphan_due_assets': server_assets,
        'source_assets': [],
        'shutdown_due_items': shutdown_due_items,
        'shutdown_future_items': shutdown_future_items,
        'shutdown_plan_items': [*shutdown_due_items, *shutdown_future_items],
        'due_items': due_items,
        'future_plan_items': future_items,
        'server_delete_items': [*due_items, *future_items],
        'next_run_at': next_run_at or (now + timezone.timedelta(minutes=30)),
    }


def _shutdown_history_item_payload(log):
    order = log.order
    user = log.user or getattr(order, 'user', None)
    user_display_name, username_label = _telegram_user_labels(user)
    ok = log.event_type == 'deleted'
    return {
        **_plan_item_identity('ip_log', log.id, plan_kind='server_history'),
        'order_id': log.order_id,
        'order_no': log.order_no or getattr(order, 'order_no', '') or '-',
        'ip': log.public_ip or log.previous_public_ip or getattr(order, 'public_ip', '') or getattr(order, 'previous_public_ip', '') or '未分配',
        'provider': log.provider or getattr(order, 'provider', ''),
        'provider_label': _provider_label(log.provider or getattr(order, 'provider', '')),
        'user_id': getattr(user, 'id', None) if user else None,
        'tg_user_id': getattr(user, 'tg_user_id', None) if user else None,
        'user_display_name': user_display_name,
        'username_label': username_label,
        'is_success': ok,
        'result_label': '成功' if ok else '失败/跳过',
        'failure_reason': '' if ok else _compact_failure_reason(log.note, fallback=_status_label(log.event_type, CloudIpLog.EVENT_CHOICES) or '失败/跳过'),
        'execution_status': _status_label(log.event_type, CloudIpLog.EVENT_CHOICES),
        'deletion_source_label': _delete_source_label(log.note),
        'source_note': log.note or '',
        'note': log.note or '',
        'display_note': _compact_dashboard_note(log.note or '', max_chars=500),
        'executed_at': _iso(log.created_at),
        'actual_expires_at': _iso(order_asset_expiry(order)),
        'suspend_at': _iso(getattr(order, 'suspend_at', None)),
        'delete_at': _iso(getattr(order, 'delete_at', None)),
        'related_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
        'detail_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
        'order_detail_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
        'order_link_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
    }


def _shutdown_history_order_payload(order):
    user_display_name, username_label = _telegram_user_labels(order.user)
    ip = order.public_ip or order.previous_public_ip or '未分配'
    executed_at = order.updated_at or order.delete_at
    return {
        **_plan_item_identity('order', order.id, plan_kind='server_history'),
        'order_id': order.id,
        'order_no': order.order_no or '-',
        'ip': ip,
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'user_id': order.user_id,
        'tg_user_id': getattr(order.user, 'tg_user_id', None) if order.user else None,
        'user_display_name': user_display_name,
        'username_label': username_label,
        'is_success': True,
        'result_label': '成功',
        'failure_reason': '',
        'execution_status': '服务器已删除',
        'deletion_source_label': _delete_source_label(order.provision_note),
        'source_note': order.provision_note or '',
        'note': order.provision_note or '',
        'display_note': _compact_dashboard_note(order.provision_note or '', max_chars=500),
        'executed_at': _iso(executed_at),
        'actual_expires_at': _iso(order_asset_expiry(order)),
        'suspend_at': _iso(order.suspend_at),
        'delete_at': _iso(order.delete_at),
        'related_path': f'/admin/cloud-orders/{order.id}',
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
    }


@dashboard_login_required
@require_GET
def lifecycle_plans(request):
    try:
        limit = int(request.GET.get('limit') or 1000)
    except (TypeError, ValueError):
        limit = 1000
    limit = max(1, min(limit, 1000))
    now = timezone.now()

    def parse_page_params(prefix: str) -> tuple[int, int]:
        try:
            page = int(request.GET.get(f'{prefix}_page') or 1)
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = int(request.GET.get(f'{prefix}_page_size') or limit)
        except (TypeError, ValueError):
            page_size = limit
        return max(page, 1), max(1, min(page_size, 200))

    shutdown_page, shutdown_page_size = parse_page_params('shutdown')
    server_delete_page, server_delete_page_size = parse_page_params('server_delete')
    ip_delete_page, ip_delete_page_size = parse_page_params('ip_delete')
    ip_delete_history_page, ip_delete_history_page_size = parse_page_params('ip_delete_history')

    def parse_item_dt(value, default=None):
        if not value:
            return default
        parsed = parse_datetime(value) if isinstance(value, str) else value
        if not parsed:
            return default
        if timezone.is_naive(parsed):
            parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
        return parsed

    def decorate_plan_item(item):
        note = str(item.get('note') or '')
        incoming_display_note = str(item.get('display_note') or '')
        source_note = str(item.get('source_note') or '')
        item['display_note'] = _compact_dashboard_note(incoming_display_note or note or source_note, max_chars=500)
        is_ip_delete_item = str(item.get('plan_kind') or '') == PLAN_KIND_UNATTACHED_IP_DELETE
        delete_attempt = _unattached_ip_delete_attempt_state(item) if is_ip_delete_item else {}
        if delete_attempt:
            item.update(delete_attempt)
        first_line = next((line.strip() for line in note.splitlines() if line.strip()), '')
        if first_line.startswith('执行内容：'):
            content_match = re.search(r'执行内容：([^\n]+?)(?:；(?:时间|账号|地区|IP|固定IP名|端口|secret|服务到期|宽限删机|用户续费)|$)', first_line)
            plan_match = re.search(r'执行计划：([^；\n]+)', first_line)
            status_text = (content_match.group(1).strip() if content_match else '')
            if status_text:
                item['execution_status'] = status_text[:120]
            if plan_match:
                item['execution_plan'] = plan_match.group(1).strip()[:120]

        provider_status = str(item.get('provider_status') or item.get('status_label') or item.get('status') or '')
        queue_status = str(item.get('queue_status') or '')
        item_type = str(item.get('item_type') or '')
        execution_status = str(item.get('execution_status') or '')
        merged_text = '\n'.join(filter(None, [source_note, note, provider_status, execution_status, str(item.get('deletion_source_label') or '')]))
        merged_text = merged_text.replace('固定 IP', '固定IP')
        confirm_state = missing_confirmation_state(item)
        confirm_summary = ''
        if confirm_state['count'] > 0:
            confirm_summary = f'删除确认进度：第{confirm_state["count"]}/{confirm_state["threshold"]}次删除确认'
        cloud_missing = any(marker in merged_text for marker in ['云上已不存在', '云上未找到实例/IP', '云端已不存在', '已标记删除'])
        instance_deleted = any(marker in merged_text for marker in ['已执行真实删机', '实例已删除', 'AWS 实例已执行删除', '服务器已删除'])
        ip_retained = any(marker in merged_text for marker in ['固定IP保留中', '固定IP仍存在但未附加', '未附加固定IP', '固定IP已分离为未附加状态'])
        global_shutdown_disabled = queue_status == 'global_shutdown_disabled' or '服务器关机总开关关闭' in merged_text
        global_server_delete_disabled = queue_status == 'global_server_delete_disabled' or '服务器删除总开关关闭' in merged_text
        global_ip_delete_disabled = queue_status == 'global_ip_delete_disabled' or 'IP 删除总开关关闭' in merged_text or 'IP删除总开关关闭' in merged_text
        shutdown_disabled = queue_status == 'shutdown_disabled' or '关机计划关闭' in merged_text or '资产自动生命周期开关关闭' in merged_text
        server_delete_disabled = queue_status == 'server_delete_disabled' or '服务器删除计划开关关闭' in merged_text
        ip_delete_disabled = queue_status == 'ip_delete_disabled' or 'IP 删除计划开关关闭' in merged_text or 'IP删除计划开关关闭' in merged_text
        is_history = bool(item.get('is_history') or item.get('executed_at'))

        resource_state = 'unknown'
        resource_state_label = '状态待确认'
        plan_state = 'pending'
        plan_state_label = '待执行'
        should_execute = not is_history
        blocked_reason = ''

        if is_history:
            plan_state = 'completed'
            plan_state_label = '历史记录'
            should_execute = False
        if confirm_state['count'] > 0 and not is_history:
            resource_state = 'missing_confirming'
            resource_state_label = f'云上缺失待确认（第{confirm_state["count"]}/{confirm_state["threshold"]}次）'
            plan_state = 'blocked'
            plan_state_label = '等待确认'
            should_execute = False
            blocked_reason = f'仍在缺失确认窗口，当前为第{confirm_state["count"]}/{confirm_state["threshold"]}次删除确认'
        elif cloud_missing:
            resource_state = 'cloud_missing'
            resource_state_label = '云上已不存在'
            plan_state = 'completed'
            plan_state_label = '无需执行'
            should_execute = False
            blocked_reason = '云上已不存在，无需继续执行删机'
        elif instance_deleted and ip_retained:
            resource_state = 'instance_deleted_ip_retained'
            resource_state_label = '实例已删除（固定IP保留中）'
            plan_state = 'completed'
            plan_state_label = '等待IP回收'
            should_execute = False
            blocked_reason = '实例已删除，仅剩固定IP保留或回收计划'
        elif instance_deleted:
            resource_state = 'instance_deleted'
            resource_state_label = '实例已删除'
            plan_state = 'completed'
            plan_state_label = '无需执行'
            should_execute = False
            blocked_reason = '实例已删除，无需继续执行删机'
        elif item_type == 'orphan_asset' and ip_retained:
            resource_state = 'fixed_ip_unattached'
            resource_state_label = '固定IP未附加'
            plan_state = 'completed'
            plan_state_label = '等待IP回收'
            should_execute = False
            blocked_reason = '当前已不是待删服务器，只剩固定IP回收事项'
        elif global_shutdown_disabled:
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'
            plan_state = 'global_shutdown_disabled'
            plan_state_label = '总开关关闭'
            should_execute = False
            blocked_reason = '服务器关机总开关关闭，禁止真实关机'
        elif global_server_delete_disabled:
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'
            plan_state = 'global_server_delete_disabled'
            plan_state_label = '总开关关闭'
            should_execute = False
            blocked_reason = '服务器删除总开关关闭，禁止真实删机'
        elif global_ip_delete_disabled:
            resource_state = 'fixed_ip_unattached' if is_ip_delete_item else 'instance_present'
            resource_state_label = '固定IP未附加' if is_ip_delete_item else '实例仍存在'
            plan_state = 'global_ip_delete_disabled'
            plan_state_label = '总开关关闭'
            should_execute = False
            blocked_reason = 'IP 删除总开关关闭，禁止真实释放固定 IP'
        elif shutdown_disabled:
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'
            plan_state = 'shutdown_disabled'
            plan_state_label = '关机开关关闭'
            should_execute = False
            blocked_reason = '资产关机计划开关关闭，禁止真实关机'
        elif server_delete_disabled:
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'
            plan_state = 'server_delete_disabled'
            plan_state_label = '删机开关关闭'
            should_execute = False
            blocked_reason = '资产服务器删除计划开关关闭，禁止真实删机'
        elif ip_delete_disabled:
            resource_state = 'fixed_ip_unattached' if is_ip_delete_item else 'instance_present'
            resource_state_label = '固定IP未附加' if is_ip_delete_item else '实例仍存在'
            plan_state = 'ip_delete_disabled'
            plan_state_label = 'IP删除开关关闭'
            should_execute = False
            blocked_reason = '资产 IP 删除计划开关关闭，禁止真实释放固定 IP'
        elif queue_status == 'retry_failed':
            resource_state = 'instance_present'
            resource_state_label = '实例待重试处理'
            plan_state = 'pending'
            plan_state_label = '待重试'
        elif queue_status == 'fallback_retry':
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'
            plan_state = 'pending'
            plan_state_label = '待执行'
        elif queue_status in {'due_now', 'within_window', 'orphan_due'}:
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'
            plan_state = 'pending'
            plan_state_label = '待执行'
        elif queue_status == 'waiting_manual_time':
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'
            plan_state = 'waiting_manual_time'
            plan_state_label = '待处理'
            should_execute = False
            blocked_reason = '代理列表资产缺少到期时间，请先维护到期时间'
        elif queue_status == 'sync_only':
            resource_state = 'sync_only'
            resource_state_label = '只同步/自然释放'
            plan_state = 'sync_only'
            plan_state_label = '只同步/自然释放'
            should_execute = False
            blocked_reason = '该云厂商不执行本地删机计划，仅同步状态，资源按云端自然释放'
        elif queue_status == 'scheduled_future':
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'
            plan_state = 'scheduled'
            plan_state_label = '已排期'
        elif item.get('is_history'):
            resource_state = 'history'
            resource_state_label = '历史记录'
        elif ip_retained:
            resource_state = 'fixed_ip_unattached'
            resource_state_label = '固定IP未附加'
            plan_state = 'scheduled'
            plan_state_label = '等待回收'
        elif str(item.get('status') or '') in {CloudAsset.STATUS_RUNNING, CloudAsset.STATUS_PENDING, CloudAsset.STATUS_STARTING, CloudAsset.STATUS_STOPPED, CloudAsset.STATUS_SUSPENDED}:
            resource_state = 'instance_present'
            resource_state_label = '实例仍存在'

        delete_attempt_label = str(item.get('delete_attempt_label') or '')
        if delete_attempt_label and is_ip_delete_item and confirm_state['count'] <= 0:
            resource_state_label = f'{resource_state_label}（{delete_attempt_label}）'

        display_note_parts = [item.get('display_note') or note or (source_note if is_ip_delete_item else '')]
        if confirm_summary and not is_history:
            display_note_parts.append(confirm_summary)
        if delete_attempt_label and is_ip_delete_item and confirm_state['count'] <= 0:
            display_note_parts.append(f'删除次数：{delete_attempt_label}')
        item['display_note'] = _compact_dashboard_note('\n'.join(filter(None, display_note_parts)), max_chars=500)

        item['resource_state'] = resource_state
        item['resource_state_label'] = resource_state_label
        item['plan_state'] = plan_state
        item['plan_state_label'] = plan_state_label
        item['should_execute'] = should_execute
        item['blocked_reason'] = blocked_reason
        item['status_summary'] = f'真实状态：{resource_state_label}；计划状态：{plan_state_label}' + (f'；原因：{blocked_reason}' if blocked_reason else '')
        return item

    def dedupe_shutdown_active_items(items):
        passthrough = []
        buckets = {}
        def resource_status_priority(entry):
            status = str(entry.get('status') or '').strip()
            if status in {CloudAsset.STATUS_RUNNING, CloudAsset.STATUS_STOPPED, CloudAsset.STATUS_SUSPENDED}:
                return 3
            if status in {CloudAsset.STATUS_PENDING, CloudAsset.STATUS_STARTING}:
                return 2
            return 1

        for item in items:
            if str(item.get('item_type') or '') != 'orphan_asset':
                passthrough.append(item)
                continue
            key = str(item.get('ip') or item.get('public_ip') or item.get('asset_id') or item.get('id') or '').strip()
            if not key:
                passthrough.append(item)
                continue
            buckets.setdefault(key, []).append(item)
        deduped = list(passthrough)
        for bucket in buckets.values():
            bucket = sorted(
                bucket,
                key=lambda entry: (
                    resource_status_priority(entry),
                    parse_item_dt(entry.get('logged_at') or entry.get('next_run_at') or entry.get('delete_at'), datetime.min.replace(tzinfo=dt_timezone.utc)),
                    int(entry.get('asset_id') or 0),
                ),
                reverse=True,
            )
            keep = bucket[0]
            duplicate_count = len(bucket) - 1
            if duplicate_count > 0:
                labels = [str(keep.get('quality_label') or '').strip(), f'已覆盖 {duplicate_count} 条同 IP 旧服务器记录']
                keep['quality_label'] = '，'.join([label for label in labels if label])
                flags = list(keep.get('quality_flags') or [])
                if 'covered_duplicates' not in flags:
                    flags.append('covered_duplicates')
                keep['quality_flags'] = flags
            deduped.append(keep)
        return deduped

    compact = str(request.GET.get('compact') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    fields = _request_field_set(
        request,
        allowed={'account', 'basic', 'execution', 'notes', 'provider'},
        default={'account', 'basic', 'execution', 'notes', 'provider'},
    )
    force_refresh = str(request.GET.get('refresh') or request.GET.get('sync') or '').strip().lower() in {'1', 'true', 'yes', 'on'}
    did_refresh = False
    if force_refresh:
        _sync_lifecycle_plan_table(limit=limit, page_size=limit)
        did_refresh = True

    server_delete_pending_until = now + timezone.timedelta(days=7)
    ip_delete_pending_until = now + timezone.timedelta(days=7)

    def is_server_delete_due(item):
        if item.get('is_history'):
            return False
        queue_status = str(item.get('queue_status') or '')
        if queue_status in {'due_now', 'within_window', 'orphan_due', 'retry_failed', 'fallback_retry'}:
            return True
        delete_at = item.get('delete_at') or item.get('next_run_at')
        parsed = parse_item_dt(delete_at)
        return bool(parsed and parsed <= server_delete_pending_until)

    def is_ip_delete_pending(item):
        if item.get('is_history'):
            return False
        if item.get('is_overdue'):
            return True
        delete_at = item.get('delete_at')
        parsed = parse_item_dt(delete_at)
        return bool(parsed and parsed <= ip_delete_pending_until)

    count_snapshot = _cached_lifecycle_plan_count_snapshot()
    plan_stats = count_snapshot['plan_stats']
    total_counts = count_snapshot['total_counts']
    ip_delete_total_counts = count_snapshot['ip_delete_total_counts']

    shutdown_plan_items = [
        decorate_plan_item(item)
        for item in _server_lifecycle_plan_page_items(
            plan_stage='shutdown',
            page=shutdown_page,
            page_size=shutdown_page_size,
            total=total_counts['shutdown_plan_count'],
        )
    ]
    shutdown_plan_items = dedupe_shutdown_active_items(shutdown_plan_items)

    server_delete_active_items = [
        decorate_plan_item(item)
        for item in _server_lifecycle_plan_page_items(
            plan_stage='delete',
            page=server_delete_page,
            page_size=server_delete_page_size,
            total=total_counts['server_delete_count'],
        )
    ]
    server_delete_active_items = [
        item for item in server_delete_active_items
        if not (item.get('plan_state') == 'completed' and not item.get('should_execute'))
    ]
    server_delete_active_items = dedupe_shutdown_active_items(server_delete_active_items)
    server_delete_due_items = [item for item in server_delete_active_items if is_server_delete_due(item)]

    ip_delete_plan_items = [
        decorate_plan_item(item)
        for item in _ip_delete_plan_asset_page_items(
            page=ip_delete_page,
            page_size=ip_delete_page_size,
            total=ip_delete_total_counts['ip_delete_count'],
            completed_active_count=ip_delete_total_counts.get('ip_delete_completed_active_count'),
        )
    ]
    pending_ip_delete_items = [item for item in ip_delete_plan_items if is_ip_delete_pending(item)]
    ip_delete_history_items = [
        decorate_plan_item(item)
        for item in _ip_delete_history_page_items(
            page=ip_delete_history_page,
            page_size=ip_delete_history_page_size,
            log_total=ip_delete_total_counts.get('ip_delete_history_log_count'),
            asset_total=ip_delete_total_counts.get('ip_delete_history_asset_count'),
            completed_total=ip_delete_total_counts.get('ip_delete_completed_active_count'),
        )
    ]
    recent_history = []

    if compact:
        def compact_notes(items):
            for item in items:
                note = str(item.get('note') or '')
                if len(note) > 1200:
                    item['note'] = note[:1200] + '\n...（备注过长，已折叠预览）'
            return items
        compact_notes(shutdown_plan_items)
        compact_notes(server_delete_active_items)
        compact_notes(ip_delete_plan_items)
        compact_notes(ip_delete_history_items)
    response_shutdown_plan_items = shutdown_plan_items[:shutdown_page_size]
    response_server_delete_items = server_delete_active_items[:server_delete_page_size]
    response_ip_delete_plan_items = ip_delete_plan_items[:ip_delete_page_size]
    response_ip_delete_history_items = ip_delete_history_items[:ip_delete_history_page_size]
    _strip_lifecycle_plan_fields(response_shutdown_plan_items, fields)
    _strip_lifecycle_plan_fields(response_server_delete_items, fields)
    _strip_lifecycle_plan_fields(response_ip_delete_plan_items, fields)
    _strip_lifecycle_plan_fields(response_ip_delete_history_items, fields)
    last_refresh_at = _lifecycle_plan_generated_at()
    return _ok({
        'task_key': 'server_delete_plans',
        'task_label': '计划',
        'status_label': '按代理列表资产生成',
        'interval_minutes': 1440,
        'last_run_at': None,
        'next_run_at': _iso(_next_runtime_time('cloud_delete_time', '15:00', now)),
        'last_refresh_at': _iso(last_refresh_at),
        'refreshed': did_refresh,
        'cache_mode': 'refreshed' if did_refresh else 'cached',
        'recent_success_count': sum(1 for item in recent_history if item.get('is_success')),
        'recent_failure_count': sum(1 for item in recent_history if not item.get('is_success')),
        'pending_ip_delete_count': len(pending_ip_delete_items),
        'missing_expiry_count': plan_stats['missing_expiry_count'],
        'unattached_ip_count': plan_stats['unattached_ip_count'],
        'source_asset_count': plan_stats['source_asset_count'],
        'server_asset_count': plan_stats['server_asset_count'],
        'ip_delete_history_count': ip_delete_total_counts['ip_delete_history_count'],
        'shutdown_plan_count': total_counts['shutdown_plan_count'],
        'shutdown_plan_due_count': sum(1 for item in shutdown_plan_items if item.get('queue_status') in {'due_now', 'within_window'}),
        'server_delete_count': total_counts['server_delete_count'],
        'server_delete_due_count': len(server_delete_due_items),
        'ip_delete_count': ip_delete_total_counts['ip_delete_count'],
        'ip_delete_due_count': len(pending_ip_delete_items),
        'shutdown_plan_items': response_shutdown_plan_items,
        'server_delete_items': response_server_delete_items,
        'ip_delete_plan_items': response_ip_delete_plan_items,
        'ip_delete_history_items': response_ip_delete_history_items,
        'pagination': {
            'shutdown_plan': _page_meta(shutdown_page, shutdown_page_size, total_counts['shutdown_plan_count']),
            'server_delete': _page_meta(server_delete_page, server_delete_page_size, total_counts['server_delete_count']),
            'ip_delete': _page_meta(ip_delete_page, ip_delete_page_size, ip_delete_total_counts['ip_delete_count']),
            'ip_delete_history': _page_meta(ip_delete_history_page, ip_delete_history_page_size, ip_delete_total_counts['ip_delete_history_count']),
        },
    })


@csrf_exempt
@dashboard_login_required
@require_POST
def refresh_lifecycle_plan_view(request):
    payload = _json_payload(request)
    try:
        page_size = int(payload.get('limit') or request.POST.get('limit') or request.GET.get('limit') or 1000)
    except (TypeError, ValueError):
        page_size = 1000
    page_size = max(1, min(page_size, 1000))
    bundle = _refresh_lifecycle_plan_cache(page_size=page_size)
    last_refresh_at = _lifecycle_plan_generated_at()
    count_snapshot = _cached_lifecycle_plan_count_snapshot()
    plan_stats = count_snapshot['plan_stats']
    total_counts = count_snapshot['total_counts']
    ip_delete_total_counts = count_snapshot['ip_delete_total_counts']
    return _ok({
        'refreshed': True,
        'last_refresh_at': _iso(last_refresh_at),
        'loaded_shutdown_plan_count': len(bundle.get('shutdown_plan_items') or []),
        'loaded_server_delete_count': len(bundle.get('server_delete_items') or []),
        'loaded_ip_delete_count': len(bundle.get('ip_delete_plan_items') or []),
        'loaded_ip_delete_history_count': len(bundle.get('ip_delete_history_items') or []),
        'shutdown_plan_count': total_counts['shutdown_plan_count'],
        'server_delete_count': total_counts['server_delete_count'],
        'missing_expiry_count': plan_stats['missing_expiry_count'],
        'unattached_ip_count': plan_stats['unattached_ip_count'],
        'source_asset_count': plan_stats['source_asset_count'],
        'server_asset_count': plan_stats['server_asset_count'],
        'ip_delete_count': ip_delete_total_counts['ip_delete_count'],
        'ip_delete_history_count': ip_delete_total_counts['ip_delete_history_count'],
    })


@csrf_exempt
@dashboard_login_required
@require_POST
def update_lifecycle_plan_note(request):
    payload = _json_payload(request)
    item_type = str(payload.get('item_type') or '').strip()
    note = str(payload.get('note') or '').strip()
    order_id = payload.get('order_id')
    asset_id = payload.get('asset_id') or payload.get('id')
    actor = request.user if getattr(request, 'user', None) and getattr(request.user, 'is_authenticated', False) else None
    if item_type == 'order' or order_id:
        try:
            order_id = int(order_id or 0)
        except (TypeError, ValueError):
            order_id = 0
        order = CloudServerOrder.objects.filter(id=order_id).first()
        if not order:
            return _error('订单不存在', status=404)
        plan_note = _save_lifecycle_plan_note(item_type='order', note=note, order=order, actor=actor)
        note_text = _lifecycle_plan_note_text(plan_note)
        return _ok({'item_type': 'order', 'order_id': order.id, 'note': note_text, 'display_note': _compact_dashboard_note(note_text, max_chars=500)})
    try:
        asset_id = int(asset_id or 0)
    except (TypeError, ValueError):
        asset_id = 0
    asset = CloudAsset.objects.filter(id=asset_id).first()
    if not asset:
        return _error('资产不存在', status=404)
    effective_item_type = item_type or 'asset'
    note_text = note
    asset.note = note_text or None
    asset.save(update_fields=['note', 'updated_at'])
    _sync_asset_note_to_server(asset)
    display_note = _asset_display_note(asset, max_chars=500)
    return _ok({'item_type': effective_item_type, 'asset_id': asset.id, 'note': note_text, 'display_note': display_note})


def _run_shutdown_order_sync(order_id: int, queue_status='manual_single', enforce_schedule: bool = True):
    return run_shutdown_order_delete(order_id, queue_status=queue_status, enforce_schedule=enforce_schedule)


@csrf_exempt
@dashboard_superuser_required
@require_POST
def run_shutdown_plan_order(request, order_id):
    result = _run_shutdown_order_sync(order_id, 'manual_single', enforce_schedule=True)
    _refresh_lifecycle_plan_cache(page_size=1000)
    return _ok({
        'batch_id': secrets.token_hex(8),
        'items': [result],
        'total': 1,
        'success_count': 1 if result['ok'] else 0,
        'failure_count': 0 if result['ok'] else 1,
        'message': '服务器删除任务已执行' if result['ok'] else result.get('error') or '服务器删除任务执行失败',
    })


def _run_orphan_asset_delete_sync(asset_id: int, enforce_schedule: bool = True):
    return run_orphan_asset_delete(asset_id, enforce_schedule=enforce_schedule)


@csrf_exempt
@dashboard_superuser_required
@require_POST
def run_orphan_asset_delete_plan(request, asset_id):
    result = _run_orphan_asset_delete_sync(asset_id, enforce_schedule=True)
    _refresh_lifecycle_plan_cache(page_size=1000)
    return _ok({
        'batch_id': secrets.token_hex(8),
        'items': [result],
        'total': 1,
        'success_count': 1 if result['ok'] else 0,
        'failure_count': 0 if result['ok'] else 1,
        'message': '服务器删除任务已执行' if result['ok'] else result.get('error') or '服务器删除任务执行失败',
    })


def _run_unattached_ip_delete_sync(asset_id: int, enforce_schedule: bool = True):
    now = timezone.now()
    asset = CloudAsset.objects.filter(id=asset_id, kind=CloudAsset.KIND_SERVER).first()
    should_check_window = bool(
        enforce_schedule
        and asset
        and getattr(asset, 'shutdown_enabled', True) is not False
        and not getattr(asset, 'instance_id', None)
        and asset.actual_expires_at
        and asset.actual_expires_at <= now
    )
    if should_check_window and not _is_cloud_unattached_ip_delete_time(now):
        return {'ok': False, 'error': '未到 IP 删除时间，不在 IP 删除执行时间窗口', 'asset_id': asset_id}
    return run_unattached_ip_release(asset_id, enforce_schedule=enforce_schedule)


@csrf_exempt
@dashboard_superuser_required
@require_POST
def run_unattached_ip_delete_plan(request, asset_id):
    result = _run_unattached_ip_delete_sync(asset_id, enforce_schedule=True)
    _refresh_lifecycle_plan_cache(page_size=1000)
    return _ok({
        'batch_id': secrets.token_hex(8),
        'items': [result],
        'total': 1,
        'success_count': 1 if result['ok'] else 0,
        'failure_count': 0 if result['ok'] else 1,
        'message': 'IP 删除任务已执行' if result['ok'] else result.get('error') or 'IP 删除任务执行失败',
    })


from bot.api_auth import (  # noqa: E402
    auth_codes,
    auth_login,
    auth_logout,
    auth_refresh,
    auth_totp_bind,
    auth_totp_start,
    me,
    user_info,
)


from bot.api_site_configs import (  # noqa: E402
    _masked_sensitive_preview,
    _site_config_group_map,
    _site_config_payload,
    button_config_detail,
    init_button_config_view,
    init_site_configs,
    init_text_site_configs,
    site_config_groups,
    site_configs_list,
    send_daily_expiry_summary_test_notification,
    update_button_config,
    update_site_config,
)


from bot.api_cloud_accounts import (  # noqa: E402
    _cloud_account_detail_payload,
    _cloud_account_duplicate_error,
    _cloud_account_payload,
    _default_cloud_account_region,
    _external_sync_log_payload,
    _fetch_aliyun_account_id,
    _find_duplicate_cloud_account,
    _mask_secret,
    _normalize_cloud_account_region,
    cloud_accounts_list,
    create_cloud_account,
    delete_cloud_account,
    update_cloud_account,
    verify_cloud_account,
)


from bot.api_admin_users import (  # noqa: E402
    admin_users_list,
    change_my_password,
    create_admin_user,
    delete_admin_user,
    update_admin_user,
)


from bot.api_operation_logs import (  # noqa: E402
    _bot_operation_log_payload,
    bot_operation_logs,
)


from bot.api_users import (  # noqa: E402
    _ledger_payload,
    _record_balance_ledger,
    update_user_balance,
    update_user_discount,
    user_balance_details,
    users_list,
)


from bot.api_telegram import (  # noqa: E402
    _limited_username_string,
    _merge_login_account_usernames,
    _normalize_telegram_group_username,
    _telegram_api_credentials,
    _telegram_chat_payload,
    _telegram_chat_user_payload,
    _telegram_check_session,
    _telegram_group_filter_payload,
    _telegram_group_identity_label,
    _telegram_group_member_payload,
    _telegram_login_account_payload,
    _telegram_message_payload,
    _telegram_send_code,
    _telegram_send_message,
    _telegram_sign_in_code,
    _telegram_sign_in_password,
    _telegram_user_labels,
    _update_login_account_from_me,
    _validate_telegram_group_filter_payload,
    archive_telegram_chat,
    check_telegram_login_account_status,
    create_telegram_group_filter,
    create_telegram_login_account,
    send_telegram_chat_message,
    telegram_accounts_overview,
    telegram_chat_messages,
    telegram_group_filter_detail,
    telegram_group_filters_list,
    telegram_login_code,
    telegram_login_password,
    telegram_login_start,
    update_telegram_account_notify,
    update_telegram_group_filter,
)


from bot.api_products import (  # noqa: E402
    create_product,
    products_list,
    update_product,
)


__all__ = [
    'auth_codes',
    'auth_login',
    'auth_totp_bind',
    'auth_totp_start',
    'auth_logout',
    'auth_refresh',
    'bot_operation_logs',
    'cloud_accounts_list',
    'create_cloud_account',
    'create_product',
    'csrf',
    'delete_cloud_account',
    'init_site_configs',
    'ip_delete_logs',
    'lifecycle_plans',
    'run_orphan_asset_delete_plan',
    'run_shutdown_plan_order',
    'run_unattached_ip_delete_plan',
    'me',
    'overview',
    'products_list',
    'site_config_groups',
    'site_configs_list',
    'send_daily_expiry_summary_test_notification',
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
