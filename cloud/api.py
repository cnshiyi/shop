"""cloud 域后台 API。"""

import inspect
import io
import json
import logging
import re
import uuid
from datetime import timezone as dt_timezone
from decimal import Decimal

from urllib.parse import urlparse

from asgiref.sync import async_to_sync
from django.db import IntegrityError, transaction
from django.db.models import Case, CharField, Count, F, IntegerField, Min, Q, Value, When
from django.db.models.functions import Cast
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from bot.models import TelegramGroupFilter, TelegramLoginAccount, TelegramUser
from cloud.lifecycle import NOTICE_TYPE_SWITCH_CONFIG, _auto_renew_notice_batch_payload, _notice_effective_delivered, _get_due_orders, _get_notice_text_override, _lifecycle_notice_batch_payload, _notice_payload_for_order, _notice_override_key, _record_auto_renew_patrol_log, _renew_notice_batch_payload, _run_auto_renew, _set_notice_text_override, cloud_notice_type_enabled
from cloud.lifecycle_schedule import compute_order_lifecycle_fields, compute_unattached_ip_release_at
from cloud.lifecycle_state import primary_record_updates_for_order_status
from cloud.asset_queries import cloud_assets_base_queryset, dedupe_cloud_asset_rows
from cloud.dashboard_api_helpers import _dashboard_expiry_ordering, _dashboard_sort_direction, _generate_cloud_plan_config_id, _preserve_link_status_label, _preserve_link_status_with_countdown
from cloud.dashboard_snapshots import _refresh_dashboard_plan_snapshots, _refresh_dashboard_plan_snapshots_deferred, _refresh_lifecycle_plan_snapshot
from cloud.services import AWS_REGION_NAMES, RenewalPriceMissingError, _renewal_price, _update_order_primary_records, create_cloud_server_rebuild_order, drop_asset_note_update, ensure_cloud_asset_operation_order, ensure_cloud_server_pricing, ensure_manual_expiry_operation_order, ensure_manual_owner_operation_order, ensure_manual_price_operation_order, record_cloud_ip_log, refresh_custom_plan_cache, replace_cloud_asset_order_by_admin, set_cloud_server_auto_renew_admin, sync_cloud_asset_user_binding
from cloud.models import CloudAsset, CloudAssetDashboardSnapshot, CloudAutoRenewPatrolLog, CloudAutoRenewPlan, CloudIpLog, CloudNoticePlan, CloudServerOrder, CloudServerPlan, CloudUserNoticeLog, ServerPrice
from cloud.note_utils import append_note, prepend_note
from core.cloud_accounts import cloud_account_label, cloud_account_label_variants, list_cloud_account_labels
from core.dashboard_api import _apply_keyword_filter, _countdown_label, _days_left, _decimal_to_str, _error, _get_keyword, _iso, _ok, _parse_decimal, _provider_label, _provider_status_label, _read_payload, _region_label, _server_source_label, _split_usernames, _status_label, _user_payload, dashboard_login_required, dashboard_superuser_required
from core.models import CloudAccountConfig, ExternalSyncLog, SiteConfig
from core.runtime_config import get_runtime_config
from cloud.provisioning import provision_cloud_server
from cloud.api_monitors import (  # noqa: E402
    _fetch_address_chain_balances,
    cloud_ip_logs_list,
    monitors_list,
)
from cloud.task_center import task_center_overview  # noqa: E402
from cloud.sync_jobs import (  # noqa: E402
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

logger = logging.getLogger(__name__)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _is_unattached_ip_asset(asset: CloudAsset) -> bool:
    return '未附加' in str(asset.provider_status or '')


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _unattached_ip_delete_due_at(*, now=None):
    return compute_unattached_ip_release_at(now or timezone.now())


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _ensure_unattached_ip_expiry(asset: CloudAsset, *, now=None) -> bool:
    """未附加固定 IP 必须有计划删除时间；缺失时按系统配置补齐。"""
    if not _is_unattached_ip_asset(asset) or asset.actual_expires_at:
        return False
    asset.actual_expires_at = _unattached_ip_delete_due_at(now=now)
    asset.save(update_fields=['actual_expires_at', 'updated_at'])
    return True


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _telegram_user_lookup_terms(value):
    raw = str(value or '').strip()
    if not raw:
        return []

    terms = []

    # 功能：处理 后台 API 接口 中的 add 业务流程。
    def add(term):
        normalized = str(term or '').strip().strip('`"\'<>，,。；;：:').lstrip('@')
        if normalized and normalized not in terms:
            terms.append(normalized)

    add(raw)
    parsed = urlparse(raw if '://' in raw else f'https://{raw}')
    if parsed.netloc.lower() in {'t.me', 'telegram.me', 'www.t.me', 'www.telegram.me'}:
        path_parts = [part for part in parsed.path.split('/') if part]
        if path_parts:
            add(path_parts[0])
    for match in re.findall(r'@([A-Za-z0-9_]{3,64})', raw):
        add(match)
    for match in re.findall(r'(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,64})', raw, flags=re.I):
        add(match)
    for match in re.findall(r'\b\d{5,20}\b', raw):
        add(match)
    return terms


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _username_matches(saved_value, lookup_value) -> bool:
    lookup_names = {item.lower() for item in TelegramUser.normalize_usernames(lookup_value)}
    if not lookup_names:
        return False
    saved_names = {item.lower() for item in TelegramUser.normalize_usernames(saved_value)}
    return bool(saved_names & lookup_names)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _resolve_telegram_user(value):
    terms = _telegram_user_lookup_terms(value)
    if not terms:
        return None
    queryset = TelegramUser.objects.all()
    for raw in terms:
        if raw.isdigit():
            found = queryset.filter(Q(id=int(raw)) | Q(tg_user_id=int(raw))).first()
            if found:
                return found
            continue
        candidates = list(queryset.filter(username__icontains=raw).order_by('-updated_at', '-id')[:20])
        found = next((item for item in candidates if _username_matches(item.username, raw)), None)
        if found:
            return found
    for raw in terms:
        account_query = Q(tg_user_id=int(raw)) if raw.isdigit() else Q(username__icontains=raw)
        accounts = TelegramLoginAccount.objects.filter(account_query).exclude(tg_user_id__isnull=True).order_by('-updated_at', '-id')[:20]
        account = next((item for item in accounts if raw.isdigit() or _username_matches(item.username, raw)), None)
        if not account or not account.tg_user_id:
            continue
        user, _ = TelegramUser.objects.get_or_create(
            tg_user_id=account.tg_user_id,
            defaults={
                'username': TelegramUser.serialize_usernames(account.username),
                'first_name': account.label or '',
            },
        )
        _sync_telegram_username(user, account.username)
        return user
    return None


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _parse_iso_datetime(value, field_label='时间'):
    raw = str(value or '').strip()
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        parsed_date = parse_date(raw)
        if parsed_date is not None:
            parsed = timezone.datetime.combine(parsed_date, timezone.datetime.min.time())
    if parsed is None:
        raise ValueError(f'{field_label}格式不正确，请使用 ISO 时间或 YYYY-MM-DD 日期')
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _sync_telegram_username(user, username=None):
    incoming = _split_usernames(username)
    if not incoming:
        return
    merged = []
    seen = set()
    for item in [*user.usernames, *incoming]:
        key = str(item).lower()
        if item and key not in seen:
            merged.append(item)
            seen.add(key)
    user.username = ','.join(merged)
    user.save(update_fields=['username', 'updated_at'])


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _infer_asset_order(asset):
    order = getattr(asset, 'order', None)
    if order:
        return order
    provider = str(getattr(asset, 'provider', '') or '').strip()
    region_code = str(getattr(asset, 'region_code', '') or '').strip()
    account = getattr(asset, 'cloud_account', None)
    account_labels = cloud_account_label_variants(account) if account else []
    asset_account_label = str(getattr(asset, 'account_label', '') or '').strip()
    if asset_account_label:
        account_labels.append(asset_account_label)
    account_labels = list(dict.fromkeys(label for label in account_labels if label))
    names = {
        str(getattr(asset, 'asset_name', '') or '').strip(),
        str(getattr(asset, 'instance_id', '') or '').strip(),
        str(getattr(asset, 'provider_resource_id', '') or '').strip(),
    }
    ips = {
        str(getattr(asset, 'public_ip', '') or '').strip(),
        str(getattr(asset, 'previous_public_ip', '') or '').strip(),
    }
    names.discard('')
    ips.discard('')
    if not names and not ips:
        return None
    queryset = CloudServerOrder.objects.select_related('user', 'plan', 'cloud_account')
    if provider:
        queryset = queryset.filter(provider=provider)
    if region_code:
        queryset = queryset.filter(Q(region_code=region_code) | Q(region_code='') | Q(region_code__isnull=True))
    if getattr(asset, 'user_id', None):
        queryset = queryset.filter(Q(user_id=asset.user_id) | Q(user__isnull=True))
    if getattr(asset, 'cloud_account_id', None):
        queryset = queryset.filter(Q(cloud_account_id=asset.cloud_account_id) | Q(account_label__in=account_labels))
    elif account_labels:
        queryset = queryset.filter(Q(account_label__in=account_labels) | Q(account_label='') | Q(account_label__isnull=True))
    if ips:
        ip_lookup = Q(public_ip__in=ips) | Q(previous_public_ip__in=ips)
        found = queryset.filter(ip_lookup).order_by('-updated_at', '-id').first()
        if found:
            return found
    if names:
        name_lookup = Q(server_name__in=names) | Q(instance_id__in=names) | Q(provider_resource_id__in=names)
        return queryset.filter(name_lookup).order_by('-updated_at', '-id').first()
    return None


# 类型说明：封装 后台 API 接口 中 CloudAssetPayloadContext 相关的数据和行为。
class CloudAssetPayloadContext:
    # 功能：初始化对象状态和依赖。
    def __init__(self, *, active_account_labels=None, inferred_orders=None, allow_mutation=True, now=None):
        self.active_account_labels = set(active_account_labels or [])
        self.inferred_orders = inferred_orders or {}
        self.allow_mutation = allow_mutation
        self.now = now or timezone.now()


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _asset_lookup_values(asset):
    names = {
        str(getattr(asset, 'asset_name', '') or '').strip(),
        str(getattr(asset, 'instance_id', '') or '').strip(),
        str(getattr(asset, 'provider_resource_id', '') or '').strip(),
    }
    ips = {
        str(getattr(asset, 'public_ip', '') or '').strip(),
        str(getattr(asset, 'previous_public_ip', '') or '').strip(),
    }
    names.discard('')
    ips.discard('')
    return names, ips


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _asset_account_label_variants(asset):
    account = getattr(asset, 'cloud_account', None)
    labels = cloud_account_label_variants(account) if account else []
    asset_account_label = str(getattr(asset, 'account_label', '') or '').strip()
    if asset_account_label:
        labels.append(asset_account_label)
    return list(dict.fromkeys(label for label in labels if label))


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _order_matches_asset_lookup(order, asset, account_labels) -> bool:
    provider = str(getattr(asset, 'provider', '') or '').strip()
    region_code = str(getattr(asset, 'region_code', '') or '').strip()
    if provider and order.provider != provider:
        return False
    if region_code and str(order.region_code or '') not in {region_code, ''}:
        return False
    if getattr(asset, 'user_id', None) and order.user_id != asset.user_id:
        return False
    if getattr(asset, 'cloud_account_id', None):
        return order.cloud_account_id == asset.cloud_account_id or str(order.account_label or '') in account_labels
    if account_labels:
        return str(order.account_label or '') in account_labels or not str(order.account_label or '').strip()
    return True


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _bulk_infer_asset_orders(assets):
    targets = []
    all_names = set()
    all_ips = set()
    providers = set()
    for asset in assets:
        if getattr(asset, 'order_id', None):
            continue
        names, ips = _asset_lookup_values(asset)
        if not names and not ips:
            continue
        account_labels = _asset_account_label_variants(asset)
        targets.append((asset, names, ips, account_labels))
        all_names.update(names)
        all_ips.update(ips)
        provider = str(getattr(asset, 'provider', '') or '').strip()
        if provider:
            providers.add(provider)
    if not targets:
        return {}

    lookup = Q()
    if all_ips:
        lookup |= Q(public_ip__in=all_ips) | Q(previous_public_ip__in=all_ips)
    if all_names:
        lookup |= Q(server_name__in=all_names) | Q(instance_id__in=all_names) | Q(provider_resource_id__in=all_names)
    queryset = CloudServerOrder.objects.select_related('user', 'plan', 'cloud_account').filter(lookup)
    if providers:
        queryset = queryset.filter(provider__in=providers)
    orders = list(queryset.order_by('-updated_at', '-id'))

    by_ip = {}
    by_name = {}
    for order in orders:
        for value in {str(order.public_ip or '').strip(), str(order.previous_public_ip or '').strip()}:
            if value:
                by_ip.setdefault(value, []).append(order)
        for value in {str(order.server_name or '').strip(), str(order.instance_id or '').strip(), str(order.provider_resource_id or '').strip()}:
            if value:
                by_name.setdefault(value, []).append(order)

    inferred = {}
    for asset, names, ips, account_labels in targets:
        for ip in ips:
            for order in by_ip.get(ip, []):
                if _order_matches_asset_lookup(order, asset, account_labels):
                    inferred[asset.id] = order
                    break
            if asset.id in inferred:
                break
        if asset.id in inferred:
            continue
        for name in names:
            for order in by_name.get(name, []):
                if _order_matches_asset_lookup(order, asset, account_labels):
                    inferred[asset.id] = order
                    break
            if asset.id in inferred:
                break
    return inferred


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _build_cloud_asset_payload_context(assets, *, allow_mutation=False):
    asset_list = list(assets)
    return CloudAssetPayloadContext(
        active_account_labels=list_cloud_account_labels(True),
        inferred_orders=_bulk_infer_asset_orders(asset_list),
        allow_mutation=allow_mutation,
        now=timezone.now(),
    )


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_payloads(assets, *, allow_mutation=False):
    asset_list = list(assets)
    context = _build_cloud_asset_payload_context(asset_list, allow_mutation=allow_mutation)
    return [_asset_payload(asset, context=context) for asset in asset_list]


_DASHBOARD_RISK_FLAGS = {
    'normal': 'risk_normal',
    'due_soon': 'risk_due_soon',
    'expired': 'risk_expired',
    'unattached_ip': 'risk_unattached_ip',
    'abnormal': 'risk_abnormal',
    'account_disabled': 'risk_account_disabled',
    'shutdown_disabled': 'risk_shutdown_disabled',
    'unbound_user': 'risk_unbound_user',
    'unbound_group': 'risk_unbound_group',
    'auto_renew_off': 'risk_auto_renew_off',
    'deleted': 'risk_deleted',
}


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _snapshot_group_key(item: dict, group_by='user') -> str:
    if group_by == 'telegram_group' and item.get('telegram_group_id'):
        return f"group:{item.get('telegram_group_id')}"
    user_id = item.get('user_id')
    if user_id:
        return f'user:{user_id}'
    tg_user_id = item.get('tg_user_id')
    if tg_user_id:
        return f'tg:{tg_user_id}'
    return f"unbound:{item.get('id', '')}"


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _snapshot_group_label(item: dict, group_by='user') -> str:
    if group_by == 'telegram_group' and item.get('telegram_group_id'):
        return str(item.get('telegram_group_title') or item.get('telegram_group_username') or item.get('telegram_group_chat_id') or '未绑定群组')
    return str(item.get('user_display_name') or item.get('username_label') or item.get('tg_user_id') or '未绑定用户')


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _snapshot_search_text(item: dict) -> str:
    values = [
        item.get('asset_name'), item.get('instance_id'), item.get('provider_resource_id'),
        item.get('public_ip'), item.get('previous_public_ip'), item.get('mtproxy_host'),
        item.get('mtproxy_link'), item.get('note'), item.get('provider_status'),
        item.get('region_code'), item.get('region_name'), item.get('region_label'),
        item.get('account_label'), item.get('user_display_name'), item.get('username_label'),
        item.get('tg_user_id'), item.get('telegram_group_title'), item.get('telegram_group_username'),
        item.get('telegram_group_chat_id'), item.get('order_no'),
    ]
    proxy_links = item.get('proxy_links') or []
    if isinstance(proxy_links, list):
        values.extend(proxy_links)
    return '\n'.join(str(value) for value in values if value not in {None, ''})


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _snapshot_defaults_from_payload(item: dict) -> dict:
    statuses = item.get('risk_statuses') or [item.get('risk_status') or 'other']
    statuses = list(dict.fromkeys(str(status or 'other') for status in statuses))
    flags = {field: False for field in _DASHBOARD_RISK_FLAGS.values()}
    for status in statuses:
        field = _DASHBOARD_RISK_FLAGS.get(status)
        if field:
            flags[field] = True
    return {
        'payload': item,
        'search_text': _snapshot_search_text(item),
        'provider': item.get('provider') or '',
        'cloud_account_id': item.get('cloud_account_id'),
        'account_label': item.get('account_label') or '',
        'region_code': item.get('region_code') or '',
        'public_ip': item.get('public_ip') or '',
        'status': item.get('status') or '',
        'is_active': bool(item.get('is_active')),
        'actual_expires_at': parse_datetime(item.get('actual_expires_at')) if item.get('actual_expires_at') else None,
        'sort_order': int(item.get('sort_order') or 99),
        'user_id': item.get('user_id'),
        'tg_user_id': item.get('tg_user_id'),
        'telegram_group_id': item.get('telegram_group_id'),
        'group_user_key': _snapshot_group_key(item, 'user'),
        'group_user_label': _snapshot_group_label(item, 'user')[:191],
        'group_telegram_key': _snapshot_group_key(item, 'telegram_group'),
        'group_telegram_label': _snapshot_group_label(item, 'telegram_group')[:191],
        'risk_status': item.get('risk_status') or 'other',
        'risk_rank': int(item.get('risk_rank') or 99),
        'risk_statuses': statuses,
        'asset_updated_at': parse_datetime(item.get('updated_at')) if item.get('updated_at') else None,
        **flags,
    }


# 功能：刷新缓存、快照或派生数据；当前函数属于 后台 API 接口。
def refresh_cloud_asset_dashboard_snapshots(asset_ids=None, *, reason: str = '', full: bool | None = None) -> dict:
    started_at = timezone.now()
    if full is None:
        full = not asset_ids
    queryset = cloud_assets_base_queryset()
    if asset_ids:
        queryset = queryset.filter(id__in=list(asset_ids))
    assets = dedupe_cloud_asset_rows(list(queryset.order_by('-sort_order', F('actual_expires_at').asc(nulls_last=True), '-updated_at', '-id')))
    payloads = _cloud_asset_payloads(assets, allow_mutation=False)
    existing = {
        row.asset_id: row
        for row in CloudAssetDashboardSnapshot.objects.filter(asset_id__in=[item['id'] for item in payloads])
    }
    create_rows = []
    update_rows = []
    update_fields = [
        'payload', 'search_text', 'provider', 'cloud_account_id', 'account_label', 'region_code',
        'public_ip', 'status', 'is_active', 'actual_expires_at', 'sort_order', 'user_id', 'tg_user_id',
        'telegram_group_id', 'group_user_key', 'group_user_label', 'group_telegram_key',
        'group_telegram_label', 'risk_status', 'risk_rank', 'risk_statuses', 'risk_normal',
        'risk_due_soon', 'risk_expired', 'risk_unattached_ip', 'risk_abnormal',
        'risk_account_disabled', 'risk_shutdown_disabled', 'risk_unbound_user',
        'risk_unbound_group', 'risk_auto_renew_off', 'risk_deleted', 'asset_updated_at',
    ]
    for item in payloads:
        defaults = _snapshot_defaults_from_payload(item)
        row = existing.get(item['id'])
        if row:
            for key, value in defaults.items():
                setattr(row, key, value)
            update_rows.append(row)
        else:
            create_rows.append(CloudAssetDashboardSnapshot(asset_id=item['id'], **defaults))
    if create_rows:
        CloudAssetDashboardSnapshot.objects.bulk_create(create_rows, batch_size=500)
    if update_rows:
        CloudAssetDashboardSnapshot.objects.bulk_update(update_rows, update_fields, batch_size=500)
    if full:
        keep_ids = [item['id'] for item in payloads]
        stale_qs = CloudAssetDashboardSnapshot.objects.all()
        if keep_ids:
            stale_qs = stale_qs.exclude(asset_id__in=keep_ids)
        stale_qs.delete()
    duration = max((timezone.now() - started_at).total_seconds(), 0)
    logger.info(
        'CLOUD_ASSET_DASHBOARD_SNAPSHOT_REFRESH reason=%s full=%s assets=%s created=%s updated=%s duration=%.3f',
        reason,
        full,
        len(payloads),
        len(create_rows),
        len(update_rows),
        duration,
    )
    return {'assets': len(payloads), 'created': len(create_rows), 'updated': len(update_rows), 'duration_seconds': round(duration, 3)}


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _ensure_cloud_asset_dashboard_snapshots(reason: str = 'list') -> bool:
    asset_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
    snapshot_total = CloudAssetDashboardSnapshot.objects.count()
    if asset_total and not snapshot_total:
        refresh_cloud_asset_dashboard_snapshots(reason=f'{reason}:empty', full=True)
        return True
    latest_asset = (
        CloudAsset.objects
        .filter(kind=CloudAsset.KIND_SERVER)
        .order_by('-updated_at')
        .values_list('updated_at', flat=True)
        .first()
    )
    latest_snapshot = CloudAssetDashboardSnapshot.objects.order_by('-asset_updated_at').values_list('asset_updated_at', flat=True).first()
    if latest_asset and (not latest_snapshot or latest_snapshot < latest_asset):
        refresh_cloud_asset_dashboard_snapshots(reason=f'{reason}:stale', full=True)
        return True
    return False


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _dashboard_snapshot_queryset(keyword=''):
    queryset = CloudAssetDashboardSnapshot.objects.all()
    keyword_text = str(keyword or '').strip()
    if not keyword_text:
        return queryset
    normalized_keyword = keyword_text.lstrip('@')
    keyword_q = Q(search_text__icontains=keyword_text)
    if normalized_keyword != keyword_text:
        keyword_q |= Q(search_text__icontains=normalized_keyword)
    matched_user_ids = set(
        queryset.filter(keyword_q).exclude(user_id__isnull=True).values_list('user_id', flat=True)[:500]
    )
    user_condition = (
        Q(username__icontains=keyword_text)
        | Q(first_name__icontains=keyword_text)
        | Q(tg_user_id__icontains=keyword_text)
    )
    if normalized_keyword != keyword_text:
        user_condition |= Q(username__icontains=normalized_keyword)
    matched_user_ids.update(TelegramUser.objects.filter(user_condition).values_list('id', flat=True)[:500])
    if matched_user_ids:
        keyword_q |= Q(user_id__in=matched_user_ids)
    return queryset.filter(keyword_q)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _filter_dashboard_snapshots_by_risk(queryset, risk_status: str):
    risk_status = str(risk_status or '').strip()
    if not risk_status or risk_status == 'all':
        return queryset.filter(risk_account_disabled=False)
    field = _DASHBOARD_RISK_FLAGS.get(risk_status)
    if not field:
        return queryset.none()
    queryset = queryset.filter(**{field: True})
    if risk_status != 'account_disabled':
        queryset = queryset.filter(risk_account_disabled=False)
    return queryset


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _dashboard_snapshot_risk_counts(queryset) -> dict:
    aggregates = {
        'all': Count('id', filter=Q(risk_account_disabled=False)),
    }
    for status, field in _DASHBOARD_RISK_FLAGS.items():
        if status == 'account_disabled':
            aggregates[status] = Count('id', filter=Q(**{field: True}))
        else:
            aggregates[status] = Count('id', filter=Q(risk_account_disabled=False, **{field: True}))
    counts = queryset.aggregate(**aggregates)
    return {key: int(value or 0) for key, value in counts.items()}


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _dashboard_snapshot_ordering(sort_by: str, sort_direction: str):
    if sort_by in {'actual_expires_at', 'expires_at', 'days_left', 'remaining_days'}:
        expires = F('actual_expires_at').desc(nulls_last=True) if sort_direction == 'desc' else F('actual_expires_at').asc(nulls_last=True)
        return [expires, 'risk_rank', '-sort_order', '-asset_id']
    return ['risk_rank', F('actual_expires_at').asc(nulls_last=True), '-sort_order', '-asset_id']


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _snapshot_payloads(rows):
    return [dict(row.payload or {}) for row in rows]


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
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


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _paginate_dashboard_snapshot_queryset(queryset, request, *, sort_by='', sort_direction='', default_size=20, min_size=1, max_size=200):
    page, page_size = _parse_dashboard_page(request, default_size=default_size, min_size=min_size, max_size=max_size)
    total = queryset.count()
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    rows = list(queryset.order_by(*_dashboard_snapshot_ordering(sort_by, sort_direction))[start:start + page_size])
    return _snapshot_payloads(rows), total, total_pages, page, page_size


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _dashboard_snapshot_group_page(queryset, request, *, group_by='user', sort_by='', sort_direction=''):
    page, page_size = _parse_dashboard_page(request, default_size=20, min_size=1, max_size=100)
    group_field = 'group_telegram_key' if group_by == 'telegram_group' else 'group_user_key'
    group_label = 'group_telegram_label' if group_by == 'telegram_group' else 'group_user_label'
    grouped = list(
        queryset.values(group_field)
        .annotate(group_expires=Min('actual_expires_at'), group_name=Min(group_label), min_risk=Min('risk_rank'))
        .order_by(F('group_expires').asc(nulls_last=True), 'group_name', group_field)
    )
    total = len(grouped)
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = min(page, total_pages)
    page_keys = [row[group_field] for row in grouped[(page - 1) * page_size:page * page_size]]
    if not page_keys:
        return [], [], total, total_pages, page, page_size
    order_index = {key: index for index, key in enumerate(page_keys)}
    rows = list(
        queryset
        .filter(**{f'{group_field}__in': page_keys})
        .order_by(*_dashboard_snapshot_ordering(sort_by, sort_direction))
    )
    items = _snapshot_payloads(rows)
    ordered_groups = _group_cloud_asset_payloads(items, group_by)
    ordered_groups.sort(key=lambda group: order_index.get(group.get('user_key'), 999999))
    page_items = [row for group in ordered_groups for row in group['items']]
    return ordered_groups, page_items, total, total_pages, page, page_size


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _display_cloud_asset_note(note: str | None) -> str:
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
    return '\n'.join(lines)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_shutdown_enabled(asset, order=None) -> bool:
    account = getattr(asset, 'cloud_account', None) or getattr(order, 'cloud_account', None)
    if account is not None:
        return bool(getattr(account, 'shutdown_enabled', True))
    return True


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _static_ip_name_from_resource_id(value) -> str:
    text = str(value or '').strip()
    if not text or 'StaticIp' not in text:
        return ''
    return text.rsplit('/', 1)[-1] or text


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_static_ip_name(asset, order=None) -> str:
    asset_static_ip_name = ''
    provider_status = str(getattr(asset, 'provider_status', '') or '')
    provider_resource_id = str(getattr(asset, 'provider_resource_id', '') or '')
    if (
        '未附加' in provider_status
        or '固定IP保留' in provider_status
        or 'StaticIp' in provider_resource_id
    ):
        asset_static_ip_name = (
            _static_ip_name_from_resource_id(provider_resource_id)
            or (
                str(getattr(asset, 'asset_name', '') or '').strip()
                if not str(getattr(asset, 'instance_id', '') or '').strip()
                else ''
            )
        )
    return asset_static_ip_name or str(getattr(order, 'static_ip_name', '') if order else '').strip()


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_provider_status_label(asset, account_label: str | None = None, *, active_account_labels=None) -> str:
    if active_account_labels is None:
        active_account_labels = set(list_cloud_account_labels(True))
    else:
        active_account_labels = set(active_account_labels)
    account = getattr(asset, 'cloud_account', None)
    asset_account_label = str(account_label or getattr(asset, 'account_label', '') or '').strip()
    account_disabled = (
        getattr(account, 'is_active', True) is False
        or (asset_account_label and asset_account_label not in active_account_labels)
    )
    if account_disabled:
        base_label = _provider_status_label(asset.provider_status)
        return f'云账号已停用 / {base_label}' if base_label and base_label != '-' else '云账号已停用'
    if asset.status == CloudAsset.STATUS_DELETED:
        return '已删除'
    if asset.status == CloudAsset.STATUS_TERMINATED:
        return '已终止'
    label = _provider_status_label(asset.provider_status)
    parts = [part.strip() for part in str(label or '').split('/') if part.strip()]
    if len(parts) > 1 and '运行中' in parts and all(part in {'运行中', '正常'} for part in parts):
        return '运行中'
    return label


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_risk_state(asset, order, expires_at, provider_status_label, display_status, user) -> dict:
    now = timezone.now()
    reasons = []
    risk_statuses = []
    risk_status = 'other'
    risk_label = '其他'
    risk_rank = 99
    provider_text = str(provider_status_label or asset.provider_status or '')
    status_text = str(display_status or asset.status or '')
    note_text = str(asset.note or '')
    days_left = _days_left(expires_at)
    shutdown_enabled = _cloud_asset_shutdown_enabled(asset, order)
    is_unattached_ip = (
        '未附加' in provider_text
        or '固定IP保留中' in provider_text
        or '固定 IP 保留中' in provider_text
        or '未附加IP' in note_text
        or '未附加 IP' in note_text
        or '未附加固定IP' in note_text
        or '固定IP保留中' in note_text
        or '固定 IP 保留中' in note_text
        or status_text == 'unattached'
    )

    # 功能：设置运行状态或配置值；当前函数属于 后台 API 接口。
    def set_risk(status: str, label: str, rank: int, reason: str):
        nonlocal risk_status, risk_label, risk_rank
        if status and status not in risk_statuses:
            risk_statuses.append(status)
        if rank < risk_rank:
            risk_status = status
            risk_label = label
            risk_rank = rank
        if reason and reason not in reasons:
            reasons.append(reason)

    if status_text == CloudAsset.STATUS_RUNNING and isinstance(days_left, int) and days_left > 7:
        set_risk('normal', '运行中', 20, '')
    if not user:
        set_risk('unbound_user', '未绑定用户', 12, '未绑定用户')
    if not getattr(asset, 'telegram_group_id', None):
        set_risk('unbound_group', '未绑定群组', 14, '未绑定群组')
    if order and not getattr(order, 'auto_renew_enabled', False):
        set_risk('auto_renew_off', '续费关闭', 13, '自动续费关闭')
    if not shutdown_enabled:
        set_risk('shutdown_disabled', '关机计划关闭', 4, '云账号已关闭关机计划')
    if is_unattached_ip:
        set_risk('unattached_ip', '未附加固定IP', 3, '固定IP未附加实例')
    if not is_unattached_ip and expires_at and expires_at <= now:
        set_risk('expired', '已过期', 1, '服务已过期')
    elif not is_unattached_ip and isinstance(days_left, int) and days_left <= 7:
        set_risk('due_soon', '即将到期', 2, f'剩余 {days_left} 天')
    if (
        status_text in {'failed', 'unknown'}
        or '失败' in provider_text
        or '异常' in provider_text
        or '云账号已停用' in provider_text
        or '云上未找到' in provider_text
        or '云上不存在' in provider_text
        or '待确认' in provider_text
    ):
        set_risk('abnormal', '异常/待确认', 5, provider_text or '状态异常')
    if '云账号已停用' in provider_text:
        set_risk('account_disabled', '云账号已停用', 6, '云账号已停用')
    if status_text in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_TERMINATING}:
        set_risk('deleted', '已删除/终止', 30, '资产已删除或终止')

    return {
        'risk_status': risk_status,
        'risk_statuses': risk_statuses or ['other'],
        'risk_label': risk_label,
        'risk_rank': risk_rank,
        'risk_reasons': reasons,
        'shutdown_enabled': shutdown_enabled,
    }


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _filter_cloud_asset_payloads_by_risk(items: list[dict], risk_status: str) -> list[dict]:
    risk_status = str(risk_status or '').strip()
    # 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
    def _statuses(item):
        return item.get('risk_statuses') or [item.get('risk_status') or 'normal']

    if not risk_status or risk_status == 'all':
        return [
            item for item in items
            if 'account_disabled' not in _statuses(item)
        ]
    return [
        item for item in items
        if risk_status in _statuses(item)
        and (risk_status == 'account_disabled' or 'account_disabled' not in _statuses(item))
    ]


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_risk_counts(items: list[dict]) -> dict:
    counts = {'all': 0}
    for item in items:
        statuses = item.get('risk_statuses') or [item.get('risk_status') or 'normal']
        if 'account_disabled' in statuses:
            counts['account_disabled'] = counts.get('account_disabled', 0) + 1
            continue
        counts['all'] += 1
        for status in set(statuses):
            counts[status] = counts.get(status, 0) + 1
    return counts


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _paginate_payloads(items: list[dict], page: int, page_size: int):
    total = len(items)
    total_pages = max((total + page_size - 1) // page_size, 1)
    safe_page = min(max(page, 1), total_pages)
    start = (safe_page - 1) * page_size
    return items[start:start + page_size], total, total_pages, safe_page


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_payload_page_group_key(item: dict, group_by='user'):
    if group_by == 'telegram_group' and item.get('telegram_group_id'):
        return f"group:{item.get('telegram_group_id')}"
    user_id = item.get('user_id')
    if user_id:
        return f'user:{user_id}'
    tg_user_id = item.get('tg_user_id')
    if tg_user_id:
        return f'tg:{tg_user_id}'
    return f"unbound:{item.get('id', '')}"


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _paginate_payloads_keep_groups(items: list[dict], page: int, page_size: int, group_by='user'):
    grouped_items = []
    group_index = {}
    for item in items:
        key = _cloud_asset_payload_page_group_key(item, group_by)
        if key not in group_index:
            group_index[key] = len(grouped_items)
            grouped_items.append([])
        grouped_items[group_index[key]].append(item)

    pages = []
    current_page = []
    current_count = 0
    for group in grouped_items:
        group_count = len(group)
        if current_page and current_count + group_count > page_size:
            pages.append(current_page)
            current_page = []
            current_count = 0
        current_page.extend(group)
        current_count += group_count
    if current_page or not pages:
        pages.append(current_page)

    page_count = len(pages)
    safe_page = min(max(page, 1), page_count)
    return pages[safe_page - 1], len(items), page_count, safe_page


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _sort_cloud_asset_payloads(items: list[dict], sort_by: str, sort_direction: str) -> list[dict]:
    # 功能：处理 后台 API 接口 中的 expires key 业务流程。
    def expires_key(item):
        return item.get('actual_expires_at') or '9999-12-31T23:59:59'

    if sort_by in {'actual_expires_at', 'expires_at', 'days_left', 'remaining_days'}:
        base_items = sorted(items, key=lambda item: (
            int(item.get('risk_rank') or 20),
            -int(item.get('sort_order') or 0),
            -int(item.get('id') or 0),
        ))
        return sorted(base_items, key=expires_key, reverse=sort_direction == 'desc')
    return sorted(items, key=lambda item: (
        int(item.get('risk_rank') or 20),
        expires_key(item),
        -int(item.get('sort_order') or 0),
        -int(item.get('id') or 0),
    ))


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _asset_payload(asset, *, context: CloudAssetPayloadContext | None = None):
    context = context or CloudAssetPayloadContext(active_account_labels=list_cloud_account_labels(True))
    order = getattr(asset, 'order', None) or context.inferred_orders.get(getattr(asset, 'id', None))
    if not order and context.allow_mutation:
        order = _infer_asset_order(asset)
    if context.allow_mutation and order and not getattr(asset, 'order_id', None):
        asset.order = order
        asset.order_id = order.id
    user = asset.user or getattr(order, 'user', None)
    if not user and context.allow_mutation:
        user = sync_cloud_asset_user_binding(asset)
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
    expires_at = asset.actual_expires_at
    if _is_unattached_ip_asset(asset) and not expires_at:
        expires_at = _unattached_ip_delete_due_at(now=context.now)
        if context.allow_mutation:
            asset.actual_expires_at = expires_at
            asset.save(update_fields=['actual_expires_at', 'updated_at'])
    countdown_label = _countdown_label(expires_at)
    preserve_link_status = _preserve_link_status_with_countdown(
        _preserve_link_status_label(asset.note, getattr(order, 'provision_note', None)),
        countdown_label,
    )
    account_label = asset.account_label or cloud_account_label(getattr(asset, 'cloud_account', None)) or getattr(order, 'account_label', '')
    cloud_account_id = asset.cloud_account_id or getattr(order, 'cloud_account_id', None)
    display_status = asset.status
    display_status_label = '旧机保留中' if asset.status == CloudAsset.STATUS_DELETING and '旧机保留期' in str(asset.provider_status or '') else _status_label(asset.status, CloudAsset.STATUS_CHOICES)
    provider_status_label = _cloud_asset_provider_status_label(asset, account_label, active_account_labels=context.active_account_labels)
    provider_account_disabled = '云账号已停用' in str(provider_status_label or '')
    if asset.status == CloudAsset.STATUS_UNKNOWN and '未附加' in str(asset.provider_status or ''):
        display_status = 'unattached'
        display_status_label = '未附加固定IP'
        provider_status_label = '云账号已停用 / 未附加固定IP' if provider_account_disabled else '未附加固定IP'
    elif asset.status == CloudAsset.STATUS_UNKNOWN and '固定IP仍存在但未附加' in str(asset.provider_status or ''):
        display_status = 'unattached'
        display_status_label = '未附加固定IP'
        provider_status_label = '云账号已停用 / 固定IP仍存在但未附加' if provider_account_disabled else '固定IP仍存在但未附加'
    risk_state = _cloud_asset_risk_state(asset, order, expires_at, provider_status_label, display_status, user)
    return {
        'id': asset.id,
        'kind': asset.kind,
        'source': asset.source,
        'source_label': _server_source_label(asset.source),
        'provider': asset.provider,
        'provider_label': _provider_label(asset.provider),
        'cloud_account_id': cloud_account_id,
        'account_label': account_label,
        'region_code': asset.region_code,
        'region_label': _region_label(getattr(asset, 'region_code', None), asset.region_name),
        'region_name': asset.region_name,
        'asset_name': asset.asset_name,
        'instance_id': asset.instance_id,
        'provider_resource_id': asset.provider_resource_id,
        'static_ip_name': _cloud_asset_static_ip_name(asset, order),
        'public_ip': asset.public_ip or asset.previous_public_ip or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None),
        'previous_public_ip': asset.previous_public_ip or getattr(order, 'previous_public_ip', None),
        'mtproxy_link': asset.mtproxy_link or getattr(order, 'mtproxy_link', None),
        'proxy_links': asset.proxy_links or getattr(order, 'proxy_links', None) or [],
        'mtproxy_port': asset.mtproxy_port or getattr(order, 'mtproxy_port', None),
        'mtproxy_secret': _mask_secret(asset.mtproxy_secret or getattr(order, 'mtproxy_secret', None)),
        'has_mtproxy_secret': bool(asset.mtproxy_secret or getattr(order, 'mtproxy_secret', None)),
        'mtproxy_host': asset.mtproxy_host or getattr(order, 'mtproxy_host', None),
        'note': _display_cloud_asset_note(asset.note),
        'sort_order': asset.sort_order,
        'actual_expires_at': _iso(expires_at),
        'days_left': _days_left(expires_at),
        'status_countdown': countdown_label,
        'preserve_link_status': preserve_link_status,
        'ip_change_quota': max(int(getattr(order, 'ip_change_quota', 0) or 0), 0) if order else 0,
        'price': _decimal_to_str(asset.price if asset.price is not None else (order.total_amount if order and order.total_amount is not None else None), 2),
        'currency': asset.currency or (order.currency if order else ''),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'telegram_group_id': asset.telegram_group_id,
        'telegram_group_chat_id': asset.telegram_group.chat_id if asset.telegram_group_id and asset.telegram_group else None,
        'telegram_group_title': asset.telegram_group.title if asset.telegram_group_id and asset.telegram_group else '',
        'telegram_group_username': asset.telegram_group.username if asset.telegram_group_id and asset.telegram_group else '',
        'order_id': order.id if order else None,
        'order_no': order.order_no if order else '',
        'order_detail_path': f'/admin/cloud-orders/{order.id}' if order else '',
        'order_link_path': f'/admin/cloud-orders/{order.id}' if order else '',
        'can_auto_renew': bool(user and not (display_status == 'unattached' or '未附加' in str(provider_status_label or ''))),
        'auto_renew_enabled': bool(getattr(order, 'auto_renew_enabled', False)),
        'status': display_status,
        'status_label': display_status_label,
        'provider_status': provider_status_label,
        **risk_state,
        'is_active': asset.is_active,
        'updated_at': _iso(asset.updated_at),
    }


# 功能：更新相关业务对象；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_login_required
@require_http_methods(['GET', 'POST', 'PUT', 'PATCH'])
def update_cloud_asset(request, asset_id):
    if request.method == 'GET':
        asset = CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').filter(pk=asset_id).first()
        if not asset:
            return _error('云资产不存在', status=404)
        payload = _asset_payload(asset)
        order = _infer_asset_order(asset)
        ip_values = {str(asset.public_ip or '').strip(), str(asset.previous_public_ip or '').strip()}
        ip_values.discard('')
        log_lookup = Q(asset=asset)
        name_lookup = Q()
        if asset.asset_name:
            name_lookup |= Q(asset_name=asset.asset_name)
        if asset.instance_id:
            name_lookup |= Q(instance_id=asset.instance_id)
        if name_lookup and ip_values:
            log_lookup |= name_lookup & (Q(public_ip__in=ip_values) | Q(previous_public_ip__in=ip_values))
        logs = list(CloudIpLog.objects.filter(log_lookup).distinct().order_by('-created_at', '-id')[:100])
        lifecycle_order_nos = set()
        for log_item in logs:
            if log_item.order_no:
                lifecycle_order_nos.add(log_item.order_no)
            for matched_order_no in re.findall(r'订单号：([^；\n]+)|旧机订单\s+([^；\n]+)|新实例订单\s+([^；\n]+)', log_item.note or ''):
                for value in matched_order_no:
                    value = str(value or '').strip().rstrip('。')
                    if value and value != '-':
                        lifecycle_order_nos.add(value)
        lifecycle_order_links = {
            item.order_no: f'/admin/cloud-orders/{item.id}'
            for item in CloudServerOrder.objects.filter(order_no__in=lifecycle_order_nos).only('id', 'order_no')
        }
        payload.update({
            'order_status': getattr(order, 'status', '') or '',
            'order_status_label': _status_label(getattr(order, 'status', ''), CloudServerOrder.STATUS_CHOICES) if order else '',
            'service_started_at': _iso(getattr(order, 'service_started_at', None)),
            'service_expires_at': _iso(getattr(order, 'service_expires_at', None)),
            'renew_grace_expires_at': _iso(getattr(order, 'renew_grace_expires_at', None)),
            'suspend_at': _iso(getattr(order, 'suspend_at', None)),
            'delete_at': _iso(getattr(order, 'delete_at', None)),
            'ip_recycle_at': _iso(getattr(order, 'ip_recycle_at', None)),
            'last_renewed_at': _iso(getattr(order, 'last_renewed_at', None)),
            'provision_note': getattr(order, 'provision_note', '') or '',
            'created_at': _iso(asset.created_at),
            'related_order': _cloud_order_summary_payload(order),
            'history_orders': _related_order_history_payload(order),
            'ip_logs': [
                {
                    'id': item.id,
                    'event_type': item.event_type,
                    'event_label': dict(CloudIpLog.EVENT_CHOICES).get(item.event_type, item.event_type),
                    'order_no': item.order_no,
                    'asset_name': item.asset_name,
                    'public_ip': item.public_ip,
                    'previous_public_ip': item.previous_public_ip,
                    'note': item.note,
                    'created_at': _iso(item.created_at),
                    'order_detail_path': lifecycle_order_links.get(item.order_no, ''),
                    'order_link_path': lifecycle_order_links.get(item.order_no, ''),
                }
                for item in logs
            ],
            'lifecycle_order_links': lifecycle_order_links,
        })
        return _ok(payload)
    if not getattr(request.user, 'is_superuser', False):
        return _error('需要超级管理员权限', status=403)
    payload = _read_payload(request)
    owner_change_requested = False
    expiry_change_requested = False
    owner_target_after_commit = None
    previous_owner = None
    previous_expires_at = None
    previous_price = None
    price_change_requested = False
    public_ip_changed = False
    changed_public_ip_before = None
    changed_public_ip_after = None
    is_unattached_ip = False
    linked_order_id = None
    pending_order_updates = {}
    refresh_snapshots_needed = False
    try:
        with transaction.atomic():
            asset = CloudAsset.objects.select_for_update().select_related('order', 'user', 'cloud_account', 'telegram_group').get(pk=asset_id)
            is_unattached_ip = _is_unattached_ip_asset(asset)
            previous_owner = asset.user
            previous_expires_at = asset.actual_expires_at
            previous_price = asset.price if asset.price is not None else getattr(asset.order, 'total_amount', None)
            linked_order_id = asset.order_id

            user_lookup = payload.get('user_query') or payload.get('user_id') or payload.get('tg_user_id') or payload.get('username')
            username_raw = payload.get('user_query') or payload.get('username')
            clear_user = str(payload.get('clear_user') or '').lower() in {'1', 'true', 'yes', 'on'}
            owner_changed = clear_user or user_lookup not in (None, '')
            owner_change_requested = owner_changed and not is_unattached_ip
            owner_target = asset.user
            if clear_user:
                owner_target = None
                asset.user = None
                if asset.order_id and not is_unattached_ip:
                    pending_order_updates['user_id'] = None
                    pending_order_updates['last_user_id'] = None
            elif user_lookup not in (None, ''):
                owner_target = _resolve_telegram_user(user_lookup)
                if not owner_target:
                    return _error('未找到匹配的 Telegram 用户', status=404)
                asset.user = owner_target
                _sync_telegram_username(owner_target, username_raw)
                if asset.order_id and not is_unattached_ip:
                    pending_order_updates['user_id'] = owner_target.id
                    pending_order_updates['last_user_id'] = getattr(owner_target, 'tg_user_id', None)

            group_lookup_provided = 'telegram_group_query' in payload or 'telegram_group_id' in payload
            if group_lookup_provided:
                refresh_snapshots_needed = True
            group_lookup = payload.get('telegram_group_query')
            if group_lookup is None and 'telegram_group_id' in payload:
                group_lookup = payload.get('telegram_group_id')
            if group_lookup_provided:
                if group_lookup in (None, ''):
                    asset.telegram_group = None
                else:
                    group_lookup_text = str(group_lookup).strip().lstrip('@')
                    group_query = Q(username__iexact=group_lookup_text) | Q(title__icontains=group_lookup_text)
                    try:
                        numeric_group_id = int(group_lookup_text)
                        group_query |= Q(id=numeric_group_id) | Q(chat_id=numeric_group_id)
                    except (TypeError, ValueError):
                        pass
                    group = TelegramGroupFilter.objects.filter(group_query, collapsed=False).order_by('-updated_at', '-id').first()
                    if not group:
                        return _error('未找到匹配的 Telegram 群组，或该群组已在绑定页隐藏', status=404)
                    asset.telegram_group = group

            if 'price' in payload:
                try:
                    price = _parse_decimal(payload.get('price'), '价格').quantize(Decimal('0.01'))
                except ValueError as exc:
                    return _error(str(exc), status=400)
                asset.price = price
                price_change_requested = previous_price != price
                refresh_snapshots_needed = refresh_snapshots_needed or price_change_requested
                if asset.order_id and not str(getattr(asset.order, 'order_no', '') or '').startswith('SRVMANUAL'):
                    pending_order_updates['total_amount'] = price
                    if getattr(asset.order, 'auto_renew_enabled', False):
                        pending_order_updates['auto_renew_failure_notice_sent_at'] = None
                        if getattr(asset.order, 'status', '') == 'renew_pending' and not getattr(asset.order, 'paid_at', None):
                            pending_order_updates['pay_amount'] = price

            if 'currency' in payload:
                asset.currency = (payload.get('currency') or 'USDT').strip() or 'USDT'
                refresh_snapshots_needed = True
                if asset.order_id and asset.order.currency != asset.currency:
                    pending_order_updates['currency'] = asset.currency

            manual_expires_at = None
            if 'actual_expires_at' in payload:
                try:
                    manual_expires_at = _parse_iso_datetime(payload.get('actual_expires_at'), '到期时间')
                    asset.actual_expires_at = manual_expires_at
                except ValueError as exc:
                    return _error(str(exc), status=400)
                if asset.order_id and not is_unattached_ip:
                    refresh_snapshots_needed = True
                    same_order_active_assets = CloudAsset.objects.filter(
                        order_id=asset.order_id,
                        kind=CloudAsset.KIND_SERVER,
                    ).exclude(status__in=[
                        CloudAsset.STATUS_DELETED,
                        CloudAsset.STATUS_DELETING,
                        CloudAsset.STATUS_TERMINATED,
                        CloudAsset.STATUS_TERMINATING,
                    ]).count()
                    if same_order_active_assets <= 1:
                        pending_order_updates.update({
                            'service_expires_at': manual_expires_at,
                            'renew_notice_sent_at': None,
                            'auto_renew_notice_sent_at': None,
                            'auto_renew_failure_notice_sent_at': None,
                            'delete_notice_sent_at': None,
                            'recycle_notice_sent_at': None,
                            **compute_order_lifecycle_fields(manual_expires_at),
                        })

            if asset.order_id:
                if 'mtproxy_link' in payload:
                    refresh_snapshots_needed = True
                    pending_order_updates['mtproxy_link'] = payload.get('mtproxy_link') or None
                if 'mtproxy_secret' in payload:
                    pending_order_updates['mtproxy_secret'] = payload.get('mtproxy_secret') or None
                if 'mtproxy_host' in payload:
                    pending_order_updates['mtproxy_host'] = payload.get('mtproxy_host') or None
                if 'mtproxy_port' in payload:
                    mtproxy_port = payload.get('mtproxy_port')
                    pending_order_updates['mtproxy_port'] = int(mtproxy_port) if mtproxy_port not in (None, '') else None
                if 'provider_resource_id' in payload:
                    pending_order_updates['provider_resource_id'] = payload.get('provider_resource_id') or None
                if 'public_ip' in payload:
                    refresh_snapshots_needed = True
                    pending_order_updates['public_ip'] = payload.get('public_ip') or None
                if 'asset_name' in payload:
                    pending_order_updates['server_name'] = payload.get('asset_name') or None

            old_public_ip = asset.public_ip
            old_provider_status = str(asset.provider_status or '')
            new_public_ip = payload.get('public_ip') or None if 'public_ip' in payload else asset.public_ip
            if 'public_ip' in payload:
                if old_public_ip and old_public_ip != new_public_ip:
                    asset.previous_public_ip = old_public_ip
                    if asset.order_id and not is_unattached_ip:
                        pending_order_updates['previous_public_ip'] = old_public_ip

            for field in ('asset_name', 'public_ip', 'provider_resource_id', 'instance_id', 'mtproxy_link', 'mtproxy_secret', 'mtproxy_host', 'note'):
                if field in payload:
                    setattr(asset, field, payload.get(field) or None)
            if 'mtproxy_port' in payload:
                mtproxy_port = payload.get('mtproxy_port')
                asset.mtproxy_port = int(mtproxy_port) if mtproxy_port not in (None, '') else None
            for field in ('provider', 'region_name', 'region_code'):
                if field in payload:
                    setattr(asset, field, payload.get(field) or None)
            if 'is_active' in payload:
                refresh_snapshots_needed = True
                asset.is_active = str(payload.get('is_active')).lower() in {'1', 'true', 'yes', 'on'}

            if 'sort_order' in payload:
                sort_order = payload.get('sort_order')
                try:
                    asset.sort_order = int(sort_order) if sort_order not in (None, '') else 99
                except (TypeError, ValueError):
                    return _error('排序必须是数字', status=400)
            rebound_to_instance = bool(
                old_provider_status and '未附加' in old_provider_status and str(asset.instance_id or '').strip()
            )
            refresh_unattached_delete_due = bool(is_unattached_ip and payload and 'actual_expires_at' not in payload and not rebound_to_instance)
            if rebound_to_instance:
                asset.actual_expires_at = None
                asset.provider_status = '已重新绑定实例-待人工添加时间'
                asset.is_active = True
                if asset.status == CloudAsset.STATUS_UNKNOWN:
                    asset.status = CloudAsset.STATUS_RUNNING

            if refresh_unattached_delete_due:
                refreshed_due_at = _unattached_ip_delete_due_at()
                asset.actual_expires_at = refreshed_due_at
                if linked_order_id:
                    pending_order_updates['ip_recycle_at'] = refreshed_due_at
                    pending_order_updates['recycle_notice_sent_at'] = None

            asset.save()
            owner_target_after_commit = owner_target
            expiry_change_requested = manual_expires_at is not None and not is_unattached_ip
            refresh_snapshots_needed = refresh_snapshots_needed or owner_change_requested or expiry_change_requested
            public_ip_changed = 'public_ip' in payload and str(old_public_ip or '') != str(new_public_ip or '')
            refresh_snapshots_needed = refresh_snapshots_needed or public_ip_changed or bool(pending_order_updates)
            changed_public_ip_before = old_public_ip
            changed_public_ip_after = new_public_ip
    except CloudAsset.DoesNotExist:
        return _error('云资产不存在', status=404)

    manual_replace_requested = owner_change_requested or expiry_change_requested
    manual_replace_authoritative = bool(
        manual_replace_requested
        and asset.provider == CloudServerPlan.PROVIDER_AWS_LIGHTSAIL
    )
    if linked_order_id and pending_order_updates and not manual_replace_authoritative:
        try:
            CloudServerOrder.objects.filter(pk=linked_order_id).update(**pending_order_updates, updated_at=timezone.now())
        except Exception as exc:
            logger.warning('CLOUD_ASSET_MANUAL_ORDER_SYNC_SKIPPED asset_id=%s order_id=%s fields=%s error=%s', asset_id, linked_order_id, sorted(pending_order_updates), exc)

    asset = CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').get(pk=asset_id)
    if manual_replace_authoritative:
        try:
            order, err = replace_cloud_asset_order_by_admin(
                asset,
                new_user=owner_target_after_commit,
                new_expires_at=asset.actual_expires_at if expiry_change_requested else None,
                new_price=asset.price if price_change_requested else None,
                previous_user=previous_owner,
                previous_expires_at=previous_expires_at,
                previous_price=previous_price,
            )
            if err:
                logger.warning('CLOUD_ASSET_MANUAL_REPLACE_ORDER_SKIPPED asset_id=%s error=%s', asset_id, err)
                if owner_change_requested:
                    fallback_order, fallback_err = ensure_manual_owner_operation_order(asset, owner_target_after_commit, previous_user=previous_owner, previous_expires_at=previous_expires_at)
                    if fallback_err:
                        logger.warning('CLOUD_ASSET_MANUAL_OWNER_ORDER_SKIPPED asset_id=%s error=%s', asset_id, fallback_err)
                if expiry_change_requested:
                    fallback_order, fallback_err = ensure_manual_expiry_operation_order(asset, asset.actual_expires_at, previous_expires_at=previous_expires_at)
                    if fallback_err:
                        logger.warning('CLOUD_ASSET_MANUAL_EXPIRY_ORDER_SKIPPED asset_id=%s error=%s', asset_id, fallback_err)
        except Exception as exc:
            logger.exception('CLOUD_ASSET_MANUAL_REPLACE_ORDER_FAILED asset_id=%s error=%s', asset_id, exc)
    elif owner_change_requested:
        try:
            owner_order, owner_err = ensure_manual_owner_operation_order(
                asset,
                owner_target_after_commit,
                previous_user=previous_owner,
                previous_expires_at=previous_expires_at,
            )
            if owner_err:
                logger.warning('CLOUD_ASSET_MANUAL_OWNER_AUDIT_ORDER_SKIPPED asset_id=%s error=%s', asset_id, owner_err)
        except Exception as exc:
            logger.exception('CLOUD_ASSET_MANUAL_OWNER_AUDIT_ORDER_FAILED asset_id=%s error=%s', asset_id, exc)
    if price_change_requested and asset.price is not None and not manual_replace_authoritative:
        try:
            price_order, price_err = ensure_manual_price_operation_order(
                asset,
                asset.price,
                previous_price=previous_price,
            )
            if price_err:
                logger.warning('CLOUD_ASSET_MANUAL_PRICE_AUDIT_ORDER_SKIPPED asset_id=%s error=%s', asset_id, price_err)
        except Exception as exc:
            logger.exception('CLOUD_ASSET_MANUAL_PRICE_AUDIT_ORDER_FAILED asset_id=%s error=%s', asset_id, exc)
    if public_ip_changed:
        record_cloud_ip_log(
            event_type='changed',
            order=asset.order,
            asset=asset,
            previous_public_ip=changed_public_ip_before,
            public_ip=changed_public_ip_after,
            note=f'后台手动更新IP：{changed_public_ip_before or "未分配"} → {changed_public_ip_after or "未分配"}',
        )
    if 'actual_expires_at' in payload:
        _refresh_lifecycle_plan_snapshot(f'cloud_asset_expiry:{asset_id}', lifecycle_limit=1000)
    if refresh_snapshots_needed:
        _refresh_dashboard_plan_snapshots_deferred(f'cloud_asset:{asset_id}', cloud_asset_ids=[asset_id])
    asset = CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').get(pk=asset_id)
    return _ok(_asset_payload(asset))


# 功能：处理 后台 API 接口 中的 toggle cloud asset auto renew 业务流程。
@csrf_exempt
@dashboard_superuser_required
@require_POST
def toggle_cloud_asset_auto_renew(request, asset_id):
    asset = CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'telegram_group').filter(pk=asset_id).first()
    if not asset:
        return _error('云资产不存在', status=404)
    payload = _read_payload(request)
    enabled = str(payload.get('enabled')).lower() in {'1', 'true', 'yes', 'on'}
    if not asset.user_id:
        sync_cloud_asset_user_binding(asset)
    if not asset.user_id:
        return _error('该代理未绑定用户，无法设置自动续费', status=400)
    if not asset.order_id:
        order, err = async_to_sync(ensure_cloud_asset_operation_order)(asset.id, asset.user_id, True)
        if err:
            return _error(err, status=400)
        if not order:
            return _error('该代理无法生成操作订单，无法设置自动续费', status=400)
        asset.order = order
        asset.order_id = order.id
    order = async_to_sync(set_cloud_server_auto_renew_admin)(asset.order_id, enabled)
    if order is False:
        return _error('当前状态不可开启自动续费', status=400)
    if not order:
        return _error('订单不存在', status=404)
    _refresh_dashboard_plan_snapshots(f'cloud_asset_auto_renew:{asset_id}')
    asset = CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').get(pk=asset_id)
    return _ok(_asset_payload(asset))


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_page_group_key(asset, group_by='user'):
    if group_by == 'telegram_group' and getattr(asset, 'telegram_group_id', None):
        return f'group:{asset.telegram_group_id}'
    user_id = getattr(asset, 'user_id', None)
    if user_id:
        return f'user:{user_id}'
    tg_user_id = getattr(getattr(asset, 'user', None), 'tg_user_id', None)
    if tg_user_id:
        return f'tg:{tg_user_id}'
    return f'unbound:{getattr(asset, "id", "")}'


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _paginate_cloud_assets_keep_groups(assets, page: int, page_size: int, group_by='user'):
    grouped_assets = []
    group_index = {}
    for asset in assets:
        key = _cloud_asset_page_group_key(asset, group_by)
        if key not in group_index:
            group_index[key] = len(grouped_assets)
            grouped_assets.append([])
        grouped_assets[group_index[key]].append(asset)

    pages = []
    current_page = []
    current_count = 0
    for group in grouped_assets:
        group_count = len(group)
        if current_page and current_count + group_count > page_size:
            pages.append(current_page)
            current_page = []
            current_count = 0
        current_page.extend(group)
        current_count += group_count
    if current_page or not pages:
        pages.append(current_page)

    page_count = len(pages)
    safe_page = min(max(page, 1), page_count)
    return pages[safe_page - 1], page_count, safe_page


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _group_cloud_asset_payloads(items, group_by='telegram_group'):
    groups = {}
    for item in items:
        if group_by == 'user':
            user_id = item.get('user_id') or item.get('tg_user_id')
            key = f'user:{user_id}' if user_id else 'user:unbound'
            group = groups.setdefault(key, {
                'user_key': key,
                'tg_user_id': item['tg_user_id'],
                'user_display_name': item['user_display_name'],
                'username_label': item['username_label'],
                'telegram_group_id': None,
                'telegram_group_chat_id': None,
                'telegram_group_title': '',
                'telegram_group_username': '',
                'default_expanded': True,
                'items': [],
            })
        else:
            group_id = item.get('telegram_group_id')
            if group_id:
                key = f'group:{group_id}'
                group_title = item.get('telegram_group_title') or str(item.get('telegram_group_chat_id') or group_id)
                group_username = item.get('telegram_group_username') or ''
                group = groups.setdefault(key, {
                    'user_key': key,
                    'tg_user_id': None,
                    'user_display_name': group_title,
                    'username_label': f'@{group_username}' if group_username else str(item.get('telegram_group_chat_id') or '-'),
                    'telegram_group_id': group_id,
                    'telegram_group_chat_id': item.get('telegram_group_chat_id'),
                    'telegram_group_title': group_title,
                    'telegram_group_username': group_username,
                    'default_expanded': True,
                    'items': [],
                })
            else:
                user_id = item.get('user_id') or item.get('tg_user_id')
                key = f'user:{user_id}' if user_id else 'user:unbound'
                group = groups.setdefault(key, {
                    'user_key': key,
                    'tg_user_id': item['tg_user_id'],
                    'user_display_name': item['user_display_name'],
                    'username_label': item['username_label'] or (str(item.get('tg_user_id') or '-') if user_id else '-'),
                    'telegram_group_id': None,
                    'telegram_group_chat_id': None,
                    'telegram_group_title': '',
                    'telegram_group_username': '',
                    'default_expanded': True,
                    'items': [],
                })
        group['items'].append(item)
    ordered_groups = list(groups.values())
    ordered_groups.sort(key=lambda group: (
        min((row['actual_expires_at'] or '9999-12-31T23:59:59') for row in group['items']),
        str(group.get('user_display_name') or group.get('telegram_group_title') or '未绑定'),
    ))
    return ordered_groups


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _apply_cloud_assets_keyword(queryset, keyword):
    keyword_text = str(keyword or '').strip()
    if not keyword_text:
        return queryset
    normalized_keyword = keyword_text.lstrip('@')
    direct_fields = [
        'asset_name', 'instance_id', 'provider_resource_id', 'public_ip', 'previous_public_ip',
        'mtproxy_host', 'mtproxy_link', 'mtproxy_secret', 'note', 'provider_status',
        'region_code', 'region_name', 'account_label', 'cloud_account__external_account_id',
        'cloud_account__name', 'user__tg_user_id', 'user__username', 'user__first_name',
        'telegram_group__title', 'telegram_group__username', 'telegram_group__chat_id',
        'order__order_no', 'order__server_name', 'order__plan_name', 'order__region_code',
        'order__region_name', 'order__instance_id', 'order__provider_resource_id',
        'order__static_ip_name', 'order__public_ip', 'order__previous_public_ip',
        'order__mtproxy_host', 'order__mtproxy_link', 'order__provision_note',
    ]
    direct_condition = Q()
    for field in direct_fields:
        direct_condition |= Q(**{f'{field}__icontains': keyword_text})
        if normalized_keyword != keyword_text:
            direct_condition |= Q(**{f'{field}__icontains': normalized_keyword})
    matched_user_ids = set(
        queryset.filter(direct_condition)
        .exclude(user_id__isnull=True)
        .values_list('user_id', flat=True)[:500]
    )
    user_condition = (
        Q(username__icontains=keyword_text)
        | Q(first_name__icontains=keyword_text)
        | Q(tg_user_id__icontains=keyword_text)
    )
    if normalized_keyword != keyword_text:
        user_condition |= Q(username__icontains=normalized_keyword)
    matched_user_ids.update(
        TelegramUser.objects.filter(user_condition).values_list('id', flat=True)[:500]
    )
    if not matched_user_ids:
        return queryset.filter(direct_condition)
    return queryset.filter(direct_condition | Q(user_id__in=matched_user_ids))


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _apply_cloud_assets_direct_keyword(queryset, keyword):
    return _apply_keyword_filter(
        queryset,
        keyword,
        [
            'asset_name', 'instance_id', 'provider_resource_id', 'public_ip', 'previous_public_ip',
            'mtproxy_host', 'mtproxy_link', 'mtproxy_secret', 'note', 'provider_status',
            'region_code', 'region_name', 'account_label', 'cloud_account__external_account_id',
            'cloud_account__name', 'user__tg_user_id', 'user__username', 'user__first_name',
            'telegram_group__title', 'telegram_group__username', 'telegram_group__chat_id',
            'order__order_no', 'order__server_name', 'order__plan_name', 'order__region_code',
            'order__region_name', 'order__instance_id', 'order__provider_resource_id',
            'order__static_ip_name', 'order__public_ip', 'order__previous_public_ip',
            'order__mtproxy_host', 'order__mtproxy_link', 'order__provision_note',
        ],
    )


# 功能：处理 后台 API 接口 中的 cloud assets list 业务流程。
@dashboard_login_required
@require_GET
def cloud_assets_list(request):
    keyword = _get_keyword(request)
    grouped = (request.GET.get('grouped') or '').lower() in {'1', 'true', 'yes'}
    group_by = (request.GET.get('group_by') or 'telegram_group').strip().lower()
    if group_by not in {'telegram_group', 'user'}:
        group_by = 'telegram_group'
    paginated = (request.GET.get('paginated') or '').lower() in {'1', 'true', 'yes'}
    risk_status = (request.GET.get('risk_status') or 'all').strip()
    show_deleted = (request.GET.get('show_deleted') or '').lower() in {'1', 'true', 'yes'}
    sort_by = (request.GET.get('sort_by') or '').strip().lower()
    sort_direction = _dashboard_sort_direction(request)
    try:
        _ensure_cloud_asset_dashboard_snapshots('cloud_assets_list')
        base_queryset = _dashboard_snapshot_queryset(keyword)
        risk_counts = _dashboard_snapshot_risk_counts(base_queryset)
        queryset = _filter_dashboard_snapshots_by_risk(base_queryset, risk_status)
        if not show_deleted and risk_status in {'', 'all'}:
            queryset = queryset.filter(
                Q(risk_unattached_ip=True)
                | (
                    Q(is_active=True)
                    & ~Q(status__in=[
                        CloudAsset.STATUS_DELETED,
                        CloudAsset.STATUS_DELETING,
                        CloudAsset.STATUS_EXPIRED,
                        CloudAsset.STATUS_TERMINATED,
                        CloudAsset.STATUS_TERMINATING,
                        CloudAsset.STATUS_UNKNOWN,
                    ])
                )
            )
        if not grouped and paginated:
            page_items, total, total_pages, page, page_size = _paginate_dashboard_snapshot_queryset(
                queryset,
                request,
                sort_by=sort_by,
                sort_direction=sort_direction,
                default_size=20,
                min_size=10,
                max_size=200,
            )
            return _ok({'items': page_items, 'total': total, 'page': page, 'page_size': page_size, 'total_pages': total_pages, 'risk_counts': risk_counts})
        if grouped and paginated:
            page_groups, page_items, total, total_pages, page, page_size = _dashboard_snapshot_group_page(
                queryset,
                request,
                group_by=group_by,
                sort_by=sort_by,
                sort_direction=sort_direction,
            )
            return _ok({'groups': page_groups, 'items': page_items, 'total': total, 'page': page, 'page_size': page_size, 'total_pages': total_pages, 'risk_counts': risk_counts})
        items = _snapshot_payloads(list(queryset.order_by(*_dashboard_snapshot_ordering(sort_by, sort_direction))))
    except (OperationalError, ProgrammingError):
        if grouped and paginated:
            return _ok({'groups': [], 'items': [], 'total': 0, 'page': 1, 'page_size': 20, 'total_pages': 1, 'risk_counts': {'all': 0}})
        if grouped:
            return _ok({'groups': [], 'items': [], 'risk_counts': {'all': 0}})
        if paginated:
            return _ok({'items': [], 'total': 0, 'page': 1, 'page_size': 20, 'total_pages': 1, 'risk_counts': {'all': 0}})
        return _ok([])

    if not grouped:
        return _ok(items)

    ordered_groups = _group_cloud_asset_payloads(items, group_by)
    return _ok({'groups': ordered_groups, 'items': items, 'risk_counts': risk_counts})


# 功能：处理 后台 API 接口 中的 cloud assets risk summary 业务流程。
@dashboard_login_required
@require_GET
def cloud_assets_risk_summary(request):
    keyword = _get_keyword(request)
    try:
        _ensure_cloud_asset_dashboard_snapshots('cloud_assets_risk_summary')
        queryset = _dashboard_snapshot_queryset(keyword)
        return _ok({'risk_counts': _dashboard_snapshot_risk_counts(queryset), 'total': queryset.count()})
    except (OperationalError, ProgrammingError):
        return _ok({'risk_counts': {'all': 0}, 'total': 0})


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
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
    due = async_to_sync(_get_due_orders)()
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
    due = async_to_sync(_get_due_orders)()
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


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_execution_status(note: str | None):
    text = str(note or '').strip()
    if not text:
        return '', ''
    if '阿里云真实续费失败' in text:
        return 'aliyun_renew_failed', '阿里云续费失败，待重试'
    if '关机失败' in text:
        return 'suspend_failed', '关机失败，待重试'
    if '删除失败' in text:
        return 'delete_failed', '删机失败，待重试'
    if '旧实例删除失败' in text or '旧服务器删除失败' in text:
        return 'migration_delete_failed', '迁移旧机删除失败，待重试'
    return '', ''


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _mask_secret(value, keep=4):
    text = str(value or '')
    if not text:
        return ''
    if len(text) <= keep * 2:
        return '*' * len(text)
    return f'{text[:keep]}***{text[-keep:]}'


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_order_source_tags(order):
    note = str(getattr(order, 'provision_note', '') or '')
    order_no = str(getattr(order, 'order_no', '') or '')
    tags: list[tuple[str, str]] = []
    seen = set()

    # 功能：处理 后台 API 接口 中的 add 业务流程。
    def add(tag_key: str, tag_label: str):
        if tag_key in seen:
            return
        seen.add(tag_key)
        tags.append((tag_key, tag_label))

    if '人工编辑' in note or order_no.startswith('SRVMANUAL') or order_no.startswith('SRVADMIN'):
        if '所属人' in note or '用户' in note:
            add('manual_owner_change', '人工改用户')
        if '到期时间' in note:
            add('manual_expiry_change', '人工改时间')
        if '价格' in note:
            add('manual_price_change', '人工改价格')
        if ('所属人' in note or '用户' in note) and '时间' in note and not tags:
            add('manual_owner_expiry_change', '人工改用户+时间')
    if not tags:
        if getattr(order, 'replacement_for_id', None):
            add('renewal_rebuild', '续费恢复')
        elif getattr(order, 'last_renewed_at', None) or getattr(order, 'status', '') == 'renew_pending' or '续费' in note:
            add('renewal', '续费')
        else:
            add('new', '新购')
    return tags


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_order_source_label(order):
    tags = _cloud_order_source_tags(order)
    first_tag = tags[0] if tags else ('new', '新购')
    return first_tag[0], first_tag[1]


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_order_summary_payload(order):
    if not order:
        return None
    order_source, order_source_label = _cloud_order_source_label(order)
    order_source_tags = _cloud_order_source_tags(order)
    return {
        'id': order.id,
        'order_id': order.id,
        'order_no': order.order_no,
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'order_source': order_source,
        'order_source_label': order_source_label,
        'order_source_tags': [item[0] for item in order_source_tags],
        'order_source_tag_labels': [item[1] for item in order_source_tags],
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'public_ip': order.public_ip,
        'previous_public_ip': order.previous_public_ip,
        'service_started_at': _iso(order.service_started_at),
        'service_expires_at': _iso(order.service_expires_at),
        'created_at': _iso(order.created_at),
        'updated_at': _iso(order.updated_at),
        'replacement_for_id': order.replacement_for_id,
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
    }


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _order_lineage_ids(order):
    if not order:
        return set()
    seen = set()
    queue = [order.id]
    while queue:
        current_id = queue.pop(0)
        if not current_id or current_id in seen:
            continue
        seen.add(current_id)
        parent_id = CloudServerOrder.objects.filter(id=current_id).values_list('replacement_for_id', flat=True).first()
        if parent_id and parent_id not in seen:
            queue.append(parent_id)
        child_ids = list(CloudServerOrder.objects.filter(replacement_for_id=current_id).values_list('id', flat=True))
        for child_id in child_ids:
            if child_id not in seen:
                queue.append(child_id)
    return seen


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _cloud_asset_detail_log_queryset(asset, order):
    order_ids = _order_lineage_ids(order)
    asset_names = {str(asset.asset_name or '').strip(), str(asset.instance_id or '').strip()}
    ip_values = {str(asset.public_ip or '').strip(), str(asset.previous_public_ip or '').strip()}
    order_nos = set()
    if order_ids:
        for item in CloudServerOrder.objects.filter(id__in=order_ids).only('order_no', 'server_name', 'instance_id', 'public_ip', 'previous_public_ip'):
            order_nos.add(str(item.order_no or '').strip())
            asset_names.add(str(item.server_name or '').strip())
            asset_names.add(str(item.instance_id or '').strip())
            ip_values.add(str(item.public_ip or '').strip())
            ip_values.add(str(item.previous_public_ip or '').strip())
    asset_names.discard('')
    ip_values.discard('')
    order_nos.discard('')
    related_asset_ids = set([asset.id])
    asset_lookup = Q()
    if order_ids:
        asset_lookup |= Q(order_id__in=order_ids)
    if asset_names:
        asset_lookup |= Q(asset_name__in=asset_names) | Q(instance_id__in=asset_names)
    if ip_values:
        asset_lookup |= Q(public_ip__in=ip_values) | Q(previous_public_ip__in=ip_values)
    if asset_lookup:
        related_asset_ids.update(CloudAsset.objects.filter(asset_lookup).values_list('id', flat=True)[:200])
    log_lookup = Q(asset_id__in=related_asset_ids)
    if order_ids:
        log_lookup |= Q(order_id__in=order_ids)
    if asset_names:
        log_lookup |= Q(asset_name__in=asset_names) | Q(instance_id__in=asset_names)
    if ip_values:
        log_lookup |= Q(public_ip__in=ip_values) | Q(previous_public_ip__in=ip_values)
    for order_no in order_nos:
        log_lookup |= Q(order_no=order_no) | Q(note__icontains=order_no)
    return CloudIpLog.objects.filter(log_lookup).distinct().order_by('-created_at', '-id')


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _related_order_history_payload(order):
    if not order:
        return []
    root = order
    seen = set()
    while root.replacement_for_id and root.replacement_for_id not in seen:
        seen.add(root.id)
        parent = CloudServerOrder.objects.select_related('user', 'plan').filter(id=root.replacement_for_id).first()
        if not parent:
            break
        root = parent
    chain = list(
        CloudServerOrder.objects.select_related('user', 'plan')
        .filter(Q(id=root.id) | Q(replacement_for_id=root.id) | Q(replacement_for__replacement_for_id=root.id) | Q(replacement_for__replacement_for__replacement_for_id=root.id))
        .order_by('-created_at', '-id')[:20]
    )
    if order.id not in {item.id for item in chain}:
        chain.insert(0, order)
    deduped = []
    seen_ids = set()
    for item in chain:
        if item.id in seen_ids:
            continue
        seen_ids.add(item.id)
        deduped.append(item)
    deduped.sort(key=lambda item: (0 if item.id == order.id else 1, -(item.created_at.timestamp() if item.created_at else 0), -item.id))
    return [_cloud_order_summary_payload(item) for item in deduped]


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
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
    order_source, order_source_label = _cloud_order_source_label(order)
    payload = {
        'id': order.id,
        'order_no': order.order_no,
        'provider': order.provider,
        'cloud_account_id': order.cloud_account_id,
        'account_label': order.account_label,
        'region_code': order.region_code,
        'region_label': _region_label(order.region_code, order.region_name),
        'region_name': order.region_name,
        'plan_name': order.plan_name,
        'quantity': order.quantity,
        'currency': order.currency,
        'total_amount': _decimal_to_str(order.total_amount),
        'pay_amount': _decimal_to_str(order.pay_amount) if order.pay_amount is not None else None,
        'pay_method': order.pay_method,
        'order_source': order_source,
        'order_source_label': order_source_label,
        'order_source_tags': [item[0] for item in _cloud_order_source_tags(order)],
        'order_source_tag_labels': [item[1] for item in _cloud_order_source_tags(order)],
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'tx_hash': order.tx_hash,
        'payer_address': order.payer_address,
        'receive_address': order.receive_address,
        'tronscan_url': f'https://tronscan.org/#/transaction/{order.tx_hash}' if order.tx_hash else '',
        'image_name': order.image_name,
        'server_name': order.server_name,
        'lifecycle_days': order.lifecycle_days,
        'service_started_at': _iso(order.service_started_at),
        'service_expires_at': _iso(order.service_expires_at),
        'renew_grace_expires_at': _iso(order.renew_grace_expires_at),
        'suspend_at': _iso(order.suspend_at),
        'delete_at': _iso(order.delete_at),
        'ip_recycle_at': _iso(order.ip_recycle_at),
        'suspend_time_config': str(get_runtime_config('cloud_suspend_time', '15:00') or '15:00').strip() or '15:00',
        'delete_time_config': str(get_runtime_config('cloud_delete_time', '15:00') or '15:00').strip() or '15:00',
        'last_renewed_at': _iso(order.last_renewed_at),
        'auto_renew_enabled': order.auto_renew_enabled,
        'last_user_id': order.last_user_id,
        'mtproxy_port': order.mtproxy_port,
        'mtproxy_link': order.mtproxy_link,
        'proxy_links': order.proxy_links or [],
        'mtproxy_secret': _mask_secret(order.mtproxy_secret),
        'has_mtproxy_secret': bool(order.mtproxy_secret),
        'mtproxy_host': order.mtproxy_host,
        'instance_id': order.instance_id,
        'provider_resource_id': order.provider_resource_id,
        'static_ip_name': order.static_ip_name,
        'public_ip': order.public_ip,
        'previous_public_ip': order.previous_public_ip,
        'login_user': order.login_user,
        'login_password': _mask_secret(order.login_password),
        'has_login_password': bool(order.login_password),
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
        'execution_status': _cloud_execution_status(order.provision_note)[0],
        'execution_status_label': _cloud_execution_status(order.provision_note)[1],
    }
    payload.update({
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
        'replacement_for_detail_path': f'/admin/cloud-orders/{order.replacement_for_id}' if order.replacement_for_id else '',
        'history_orders': _related_order_history_payload(order),
    })
    return payload


# 功能：处理 后台 API 接口 中的 cloud orders list 业务流程。
@dashboard_login_required
@require_GET
def cloud_orders_list(request):
    keyword = _get_keyword(request)
    queryset = (
        CloudServerOrder.objects.select_related('user', 'plan')
        .exclude(Q(order_no__startswith='SRVMANUAL'))
        .annotate(
            deleted_rank=Case(
                When(status='deleted', then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .order_by('deleted_rank', '-created_at', '-id')
    )
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

        if status == 'renew_pending':
            item['renew_status'] = 'renew_pending'
            item['renew_status_label'] = '续费待支付'
        elif status == 'expiring':
            item['renew_status'] = 'expiring'
            item['renew_status_label'] = '已到期待处理'
        elif status == 'suspended':
            item['renew_status'] = 'suspended'
            item['renew_status_label'] = '已关机待续费'
        elif status == 'deleting':
            item['renew_status'] = 'deleting'
            item['renew_status_label'] = '删除中'
        elif status == 'deleted':
            item['renew_status'] = 'deleted'
            item['renew_status_label'] = '实例已删除'
        elif status == 'expired':
            item['renew_status'] = 'expired'
            item['renew_status_label'] = '已过期'
        elif status in {'pending', 'cancelled', 'failed'}:
            item['renew_status'] = 'unpaid'
            item['renew_status_label'] = '未付款'
        elif status in {'paid', 'provisioning'}:
            item['renew_status'] = 'paid'
            item['renew_status_label'] = '已付款'
        elif status == 'completed' and service_expires_dt and service_expires_dt <= now:
            item['renew_status'] = 'expiring'
            item['renew_status_label'] = '已到期待处理'
        elif status == 'completed':
            item['renew_status'] = 'completed'
            item['renew_status_label'] = '已完成'
        else:
            item['renew_status'] = 'unknown'
            item['renew_status_label'] = '状态未知'

        item['can_renew'] = status not in {'pending', 'cancelled', 'failed', 'paid', 'provisioning'}
        item['auto_renew_enabled'] = auto_renew_enabled
        item['expired_by_time'] = bool(service_expires_dt and service_expires_dt <= now)
        item['grace_expired'] = bool(renew_grace_dt and renew_grace_dt <= now)
        item['delete_scheduled'] = bool(delete_dt and delete_dt > now)
        item['is_expired'] = status in {'deleted', 'expired'} or item['grace_expired']
        item['expires_in_days'] = _days_left(service_expires_dt) if service_expires_dt else None
        item['grace_expires_in_days'] = _days_left(renew_grace_dt) if renew_grace_dt else None
    return _ok(items)


# 功能：删除或标记删除相关业务对象；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_superuser_required
@require_POST
def delete_cloud_order(request, order_id):
    order = CloudServerOrder.objects.filter(id=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    linked_asset_count = CloudAsset.objects.filter(order=order).count()
    cloud_identity_values = [
        order.public_ip,
        order.previous_public_ip,
        order.instance_id,
        order.provider_resource_id,
        order.server_name,
        order.static_ip_name,
    ]
    if linked_asset_count or any(str(value or '').strip() for value in cloud_identity_values):
        logger.warning(
            'DASHBOARD_CLOUD_ORDER_DELETE_BLOCKED order_id=%s order_no=%s assets=%s user=%s',
            order_id,
            order.order_no,
            linked_asset_count,
            getattr(request.user, 'id', None),
        )
        return _error(
            '订单已关联云资源，已阻止物理删除；请先在订单详情里改状态，或处理关联资产后再删除。',
            status=409,
        )
    order_no = order.order_no
    order.delete()
    logger.info('DASHBOARD_CLOUD_ORDER_DELETE order_id=%s order_no=%s user=%s', order_id, order_no, getattr(request.user, 'id', None))
    return _ok(True)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _append_provision_note(order, note):
    if not note:
        return order.provision_note
    return prepend_note(order.provision_note, note)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
def _primary_record_updates_for_order_status(order_status: str, note: str | None = None):
    return primary_record_updates_for_order_status(order_status)


# 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
@transaction.atomic
def _apply_cloud_order_status(order, new_status):
    now = timezone.now()
    allowed_statuses = {choice[0] for choice in CloudServerOrder.STATUS_CHOICES}
    if new_status not in allowed_statuses:
        raise ValueError('订单状态不正确')
    order = CloudServerOrder.objects.select_related('user', 'plan').select_for_update().get(pk=order.pk)
    old_status = order.status
    if new_status == old_status:
        return order

    note = None
    trigger_provision = False
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

    asset_updates, server_updates = _primary_record_updates_for_order_status(new_status, order.provision_note)
    if asset_updates or server_updates:
        _update_order_primary_records(
            order,
            asset_updates=asset_updates,
            server_updates=server_updates,
            now=now,
        )

    if trigger_provision:
        async_to_sync(provision_cloud_server)(order.id)
        order.refresh_from_db()

    return order


# 功能：处理 后台 API 接口 中的 cloud order detail 业务流程。
@csrf_exempt
@dashboard_superuser_required
@require_http_methods(['GET', 'POST', 'PUT', 'PATCH'])
def cloud_order_detail(request, order_id):
    order = CloudServerOrder.objects.select_related('user', 'plan').filter(pk=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    if request.method == 'GET':
        return _ok(_cloud_order_detail_payload(order))

    payload = _read_payload(request)
    try:
        with transaction.atomic():
            order = CloudServerOrder.objects.select_for_update().select_related('user', 'plan').get(pk=order_id)
            changed_fields = set()
            user_lookup = payload.get('user_query') or payload.get('user_id') or payload.get('tg_user_id') or payload.get('username')
            clear_user = str(payload.get('clear_user') or '').lower() in {'1', 'true', 'yes', 'on'}
            if clear_user:
                return _error('订单必须绑定用户，不能清空所属用户', status=400)
            elif user_lookup not in (None, ''):
                user = _resolve_telegram_user(user_lookup)
                if not user:
                    return _error('未找到匹配的 Telegram 用户', status=404)
                order.user = user
                order.last_user_id = user.tg_user_id
                changed_fields.update({'user', 'last_user_id'})
                _sync_telegram_username(user, user_lookup)

            original_public_ip = order.public_ip
            for field in ('server_name', 'public_ip', 'previous_public_ip', 'instance_id', 'provider_resource_id', 'static_ip_name', 'mtproxy_host', 'mtproxy_link', 'provision_note'):
                if field in payload:
                    setattr(order, field, payload.get(field) or None)
                    changed_fields.add(field)
            if 'public_ip' in payload and original_public_ip and original_public_ip != order.public_ip and 'previous_public_ip' not in payload:
                order.previous_public_ip = original_public_ip
                changed_fields.add('previous_public_ip')
            if 'mtproxy_port' in payload:
                mtproxy_port = payload.get('mtproxy_port')
                order.mtproxy_port = int(mtproxy_port) if mtproxy_port not in (None, '') else None
                changed_fields.add('mtproxy_port')
            if 'total_amount' in payload:
                order.total_amount = _parse_decimal(payload.get('total_amount'), '总金额')
                changed_fields.add('total_amount')
            if 'pay_amount' in payload:
                pay_amount = payload.get('pay_amount')
                order.pay_amount = _parse_decimal(pay_amount, '应付金额') if pay_amount not in (None, '') else None
                changed_fields.add('pay_amount')
            if 'status' in payload:
                status = str(payload.get('status') or '').strip()
                if status and status not in {choice[0] for choice in CloudServerOrder.STATUS_CHOICES}:
                    return _error('订单状态不正确', status=400)
                if status:
                    order.status = status
                    changed_fields.add('status')
            for field, label in (
                ('service_started_at', '服务开始时间'),
                ('service_expires_at', '服务到期时间'),
                ('renew_grace_expires_at', '续费宽限到期'),
                ('suspend_at', '计划关机时间'),
                ('delete_at', '计划删机时间'),
                ('ip_recycle_at', 'IP保留到期'),
            ):
                if field in payload:
                    setattr(order, field, _parse_iso_datetime(payload.get(field), label) if payload.get(field) else None)
                    changed_fields.add(field)
            if 'service_expires_at' in changed_fields and 'service_expires_at' in payload:
                lifecycle_updates = compute_order_lifecycle_fields(order.service_expires_at) if order.service_expires_at else {
                    'renew_grace_expires_at': None,
                    'suspend_at': None,
                    'delete_at': None,
                    'ip_recycle_at': None,
                }
                for field, value in lifecycle_updates.items():
                    if field not in payload:
                        setattr(order, field, value)
                        changed_fields.add(field)
            if changed_fields:
                update_values = {field: getattr(order, field) for field in changed_fields}
                update_values['updated_at'] = timezone.now()
                CloudServerOrder.objects.filter(pk=order.pk).update(**update_values)
                order.refresh_from_db()
                asset_updates = {}
                server_updates = {}
                if 'user' in changed_fields:
                    asset_updates['user'] = order.user
                    server_updates['user'] = order.user
                if 'public_ip' in changed_fields:
                    asset_updates['public_ip'] = order.public_ip
                    server_updates['public_ip'] = order.public_ip
                if 'previous_public_ip' in changed_fields:
                    asset_updates['previous_public_ip'] = order.previous_public_ip
                    server_updates['previous_public_ip'] = order.previous_public_ip
                if 'server_name' in changed_fields:
                    asset_updates['asset_name'] = order.server_name
                    server_updates['server_name'] = order.server_name
                if 'instance_id' in changed_fields:
                    asset_updates['instance_id'] = order.instance_id
                    server_updates['instance_id'] = order.instance_id
                if 'provider_resource_id' in changed_fields:
                    asset_updates['provider_resource_id'] = order.provider_resource_id
                    server_updates['provider_resource_id'] = order.provider_resource_id
                for mtproxy_field in ('mtproxy_host', 'mtproxy_link', 'mtproxy_port'):
                    if mtproxy_field in changed_fields:
                        asset_updates[mtproxy_field] = getattr(order, mtproxy_field)
                if 'service_expires_at' in changed_fields:
                    asset_updates['actual_expires_at'] = order.service_expires_at
                    server_updates['expires_at'] = order.service_expires_at
                if 'status' in changed_fields:
                    status_asset_updates, status_server_updates = _primary_record_updates_for_order_status(order.status, order.provision_note)
                    asset_updates.update(status_asset_updates)
                    server_updates.update(status_server_updates)
                if asset_updates or server_updates:
                    _update_order_primary_records(order, asset_updates=asset_updates, server_updates=server_updates)
    except ValueError as exc:
        return _error(str(exc), status=400)
    order.refresh_from_db()
    return _ok(_cloud_order_detail_payload(order))


# 功能：更新相关业务对象；当前函数属于 后台 API 接口。
@csrf_exempt
@dashboard_superuser_required
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

    residual_statuses = {
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_TERMINATED,
        CloudAsset.STATUS_TERMINATING,
        CloudAsset.STATUS_EXPIRED,
    }
    residual_order_statuses = {'deleted', 'deleting', 'expired', 'cancelled', 'refunded', 'failed'}

    # 功能：提供 后台 API 接口 的内部辅助逻辑，供同模块流程复用。
    def _looks_like_local_residual(item):
        provider_status = str(getattr(item, 'provider_status', '') or '')
        item_note = str(getattr(item, 'note', '') or '')
        asset_provider_status = str(getattr(asset, 'provider_status', '') or '')
        asset_note = str(getattr(asset, 'note', '') or '')
        return (
            asset.status in residual_statuses
            or item.status in residual_statuses
            or (order and order.status in residual_order_statuses)
            or not getattr(item, 'is_active', True)
            or '云上未找到' in provider_status
            or '云上未找到' in item_note
            or '云上未找到' in asset_provider_status
            or '云上未找到' in asset_note
        )

    if not CloudIpLog.objects.filter(asset_id=asset.id, event_type=CloudIpLog.EVENT_DELETED, note__contains='后台手动删除代理列表记录').exists():
        record_cloud_ip_log(event_type=CloudIpLog.EVENT_DELETED, order=order, asset=asset, previous_public_ip=previous_public_ip, public_ip=None, note=note)
    order_status_changed = _clear_order_cloud_binding(order)
    asset.delete()
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
    errors = []
    synced = {'aliyun': False, 'aws': False}
    missing = {'aliyun': 0, 'aws': 0}
    aws_regions = []
    command_output = io.StringIO()
    aliyun_accounts = _active_sync_accounts(CloudAccountConfig.PROVIDER_ALIYUN)
    aws_accounts = _active_sync_accounts(CloudAccountConfig.PROVIDER_AWS)
    aws_command = None
    warnings = []
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
