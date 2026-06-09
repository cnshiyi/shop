"""云资产列表快照读写与分页辅助。"""

import hashlib
import logging
import threading
from datetime import datetime, timezone as datetime_timezone

from django.core.cache import cache
from django.db import close_old_connections
from django.db.models import F, Min, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from bot.models import TelegramUser
from cloud.asset_queries import cloud_assets_base_queryset, dedupe_cloud_asset_rows
from cloud.models import CloudAsset, CloudAssetDashboardSnapshot
from core.dashboard_api import _countdown_label, _days_left, _decimal_to_str

logger = logging.getLogger(__name__)

_SNAPSHOT_SYNC_REFRESH_LIMIT = 1000
_SNAPSHOT_BACKFILL_BATCH_SIZE = 5000
_SNAPSHOT_BACKFILL_MAX_BATCH_SIZE = 10000
_SNAPSHOT_BACKFILL_MAX_BATCHES = 500
_SNAPSHOT_COUNTS_CACHE_TTL = 300
_SNAPSHOT_COUNTS_VERSION_KEY = 'cloud:asset-dashboard-snapshot:counts-version'


def _normalize_snapshot_asset_ids(asset_ids):
    if not asset_ids:
        return []
    normalized = []
    seen = set()
    for value in asset_ids:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed <= 0 or parsed in seen:
            continue
        normalized.append(parsed)
        seen.add(parsed)
    return normalized


def _snapshot_counts_cache_version() -> str:
    version = cache.get(_SNAPSHOT_COUNTS_VERSION_KEY)
    if version is None:
        version = '0'
        cache.set(_SNAPSHOT_COUNTS_VERSION_KEY, version, timeout=None)
    return str(version)


def _bump_snapshot_counts_cache_version():
    cache.set(
        _SNAPSHOT_COUNTS_VERSION_KEY,
        str(int(timezone.now().timestamp() * 1000)),
        timeout=None,
    )


def _snapshot_queryset_cache_key(prefix: str, queryset, *parts) -> str | None:
    try:
        sql, params = queryset.order_by().query.sql_with_params()
    except Exception:
        return None
    raw = repr((prefix, _snapshot_counts_cache_version(), str(sql), tuple(params), parts))
    digest = hashlib.sha1(raw.encode('utf-8')).hexdigest()
    return f'cloud:asset-dashboard-snapshot:{prefix}:{digest}'


def _defer_cloud_asset_dashboard_snapshot_refresh(*, reason: str, asset_ids=None, full=False) -> bool:
    normalized_asset_ids = _normalize_snapshot_asset_ids(asset_ids)
    if full:
        scope_key = 'full'
    else:
        scope = ','.join(str(value) for value in normalized_asset_ids)
        digest = hashlib.sha1(scope.encode('utf-8')).hexdigest()[:16] if scope else 'empty'
        scope_key = f'assets:{digest}'
    lock_key = f'cloud-asset-dashboard-snapshot-refresh:{scope_key}'
    if not cache.add(lock_key, reason or 'pending', timeout=300):
        return False

    def _run():
        close_old_connections()
        try:
            refresh_cloud_asset_dashboard_snapshots(
                asset_ids=None if full else normalized_asset_ids,
                reason=reason,
                full=full,
            )
        except Exception:
            logger.exception(
                'CLOUD_ASSET_DASHBOARD_SNAPSHOT_DEFERRED_REFRESH_FAILED reason=%s full=%s assets=%s',
                reason,
                full,
                len(normalized_asset_ids),
            )
        finally:
            cache.delete(lock_key)
            close_old_connections()

    threading.Thread(target=_run, name='cloud-asset-dashboard-snapshot-refresh', daemon=True).start()
    return True


def _next_missing_snapshot_asset_ids(limit: int) -> list[int]:
    asset_total = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER).count()
    snapshot_total = CloudAssetDashboardSnapshot.objects.count()
    if snapshot_total >= asset_total:
        return []
    return list(
        cloud_assets_base_queryset()
        .filter(dashboard_snapshot__isnull=True)
        .order_by('id')
        .values_list('id', flat=True)[:limit]
    )


def _next_stale_snapshot_asset_ids(limit: int) -> list[int]:
    scan_limit = min(max(int(limit or 1), 1000), 5000)
    rows = list(
        CloudAssetDashboardSnapshot.objects
        .order_by('asset_updated_at', 'asset_id')
        .values_list('asset_id', 'asset_updated_at')[:scan_limit]
    )
    if not rows:
        return []
    asset_ids = [asset_id for asset_id, _ in rows if asset_id]
    asset_updated_at = {}
    chunk_size = _SNAPSHOT_BACKFILL_MAX_BATCH_SIZE
    for index in range(0, len(asset_ids), chunk_size):
        chunk_ids = asset_ids[index:index + chunk_size]
        asset_updated_at.update(
            CloudAsset.objects
            .filter(kind=CloudAsset.KIND_SERVER, id__in=chunk_ids)
            .values_list('id', 'updated_at')
        )
    stale_ids = []
    for asset_id, snapshot_updated_at in rows:
        updated_at = asset_updated_at.get(asset_id)
        if updated_at and (snapshot_updated_at is None or updated_at > snapshot_updated_at):
            stale_ids.append(asset_id)
            if len(stale_ids) >= limit:
                break
    return stale_ids


def backfill_cloud_asset_dashboard_snapshots(*, reason: str = '', batch_size: int = _SNAPSHOT_BACKFILL_BATCH_SIZE, max_batches: int | None = None, include_stale: bool = False) -> dict:
    started_at = timezone.now()
    batch_size = min(max(int(batch_size or _SNAPSHOT_BACKFILL_BATCH_SIZE), 1), _SNAPSHOT_BACKFILL_MAX_BATCH_SIZE)
    max_batches = _SNAPSHOT_BACKFILL_MAX_BATCHES if max_batches is None else max(int(max_batches), 1)
    batches = 0
    refreshed = 0
    created = 0
    updated = 0
    while batches < max_batches:
        asset_ids = _next_missing_snapshot_asset_ids(batch_size)
        batch_reason = f'{reason}:missing'
        if not asset_ids and include_stale:
            asset_ids = _next_stale_snapshot_asset_ids(batch_size)
            batch_reason = f'{reason}:stale'
        if not asset_ids:
            break
        summary = refresh_cloud_asset_dashboard_snapshots(asset_ids=asset_ids, reason=batch_reason, full=False)
        batches += 1
        refreshed += int(summary.get('assets') or 0)
        created += int(summary.get('created') or 0)
        updated += int(summary.get('updated') or 0)
    duration = max((timezone.now() - started_at).total_seconds(), 0)
    logger.info(
        'CLOUD_ASSET_DASHBOARD_SNAPSHOT_BACKFILL reason=%s batches=%s refreshed=%s created=%s updated=%s duration=%.3f',
        reason,
        batches,
        refreshed,
        created,
        updated,
        duration,
    )
    return {
        'batches': batches,
        'assets': refreshed,
        'created': created,
        'updated': updated,
        'duration_seconds': round(duration, 3),
    }


def _defer_cloud_asset_dashboard_snapshot_backfill(*, reason: str) -> bool:
    lock_key = 'cloud-asset-dashboard-snapshot-backfill'
    if not cache.add(lock_key, reason or 'pending', timeout=1800):
        return False

    def _run():
        close_old_connections()
        try:
            backfill_cloud_asset_dashboard_snapshots(reason=reason)
        except Exception:
            logger.exception('CLOUD_ASSET_DASHBOARD_SNAPSHOT_BACKFILL_FAILED reason=%s', reason)
        finally:
            cache.delete(lock_key)
            close_old_connections()

    threading.Thread(target=_run, name='cloud-asset-dashboard-snapshot-backfill', daemon=True).start()
    return True


def _default_cloud_asset_payloads(assets, *, allow_mutation=False):
    from cloud.api_assets import _cloud_asset_payloads

    return _cloud_asset_payloads(assets, allow_mutation=allow_mutation)


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

_HIDDEN_DISPLAY_STATUSES = {
    CloudAsset.STATUS_DELETED,
    CloudAsset.STATUS_DELETING,
    CloudAsset.STATUS_EXPIRED,
    CloudAsset.STATUS_TERMINATED,
    CloudAsset.STATUS_TERMINATING,
    CloudAsset.STATUS_UNKNOWN,
}


def _snapshot_group_key(item: dict, group_by='user') -> str:
    if group_by == 'telegram_group' and item.get('telegram_group_id'):
        return f"group:{item.get('telegram_group_id')}"
    user_id = item.get('user_id')
    if user_id:
        return f'user:{user_id}'
    tg_user_id = item.get('tg_user_id')
    if tg_user_id:
        return f'tg:{tg_user_id}'
    return 'user:unbound'


def _snapshot_group_label(item: dict, group_by='user') -> str:
    if group_by == 'telegram_group' and item.get('telegram_group_id'):
        return str(item.get('telegram_group_title') or item.get('telegram_group_username') or item.get('telegram_group_chat_id') or '未绑定群组')
    return str(item.get('user_display_name') or item.get('username_label') or item.get('tg_user_id') or '未绑定用户')


def _proxy_link_search_terms(proxy_links) -> list[str]:
    values = []
    if not isinstance(proxy_links, list):
        return values
    for item in proxy_links:
        if not isinstance(item, dict):
            continue
        for key in ('name', 'label', 'mode', 'server', 'port'):
            value = item.get(key)
            if value not in {None, ''}:
                values.append(str(value))
    return values


def _snapshot_search_text(item: dict) -> str:
    values = [
        item.get('asset_name'), item.get('instance_id'), item.get('provider_resource_id'),
        item.get('public_ip'), item.get('previous_public_ip'), item.get('mtproxy_host'),
        item.get('note'), item.get('provider_status'),
        item.get('region_code'), item.get('region_name'), item.get('region_label'),
        item.get('account_label'), item.get('user_display_name'), item.get('username_label'),
        item.get('tg_user_id'), item.get('telegram_group_title'), item.get('telegram_group_username'),
        item.get('telegram_group_chat_id'), item.get('order_no'),
    ]
    values.extend(_proxy_link_search_terms(item.get('proxy_links') or []))
    return '\n'.join(str(value) for value in values if value not in {None, ''})


def _snapshot_defaults_from_payload(item: dict) -> dict:
    statuses = item.get('risk_statuses') or [item.get('risk_status') or 'other']
    statuses = list(dict.fromkeys(str(status or 'other') for status in statuses))
    actual_expires_at = parse_datetime(item.get('actual_expires_at')) if item.get('actual_expires_at') else None
    status = item.get('status') or ''
    is_active = bool(item.get('is_active'))
    flags = {field: False for field in _DASHBOARD_RISK_FLAGS.values()}
    for risk_status in statuses:
        field = _DASHBOARD_RISK_FLAGS.get(risk_status)
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
        'status': status,
        'is_active': is_active,
        'is_display_visible': bool(flags['risk_unattached_ip'] or (is_active and status not in _HIDDEN_DISPLAY_STATUSES)),
        'sort_order': int(item.get('sort_order') or 99),
        'user_id': item.get('user_id'),
        'tg_user_id': item.get('tg_user_id'),
        'telegram_group_id': item.get('telegram_group_id'),
        'group_user_key': _snapshot_group_key(item, 'user'),
        'group_user_label': _snapshot_group_label(item, 'user')[:191],
        'group_telegram_key': _snapshot_group_key(item, 'telegram_group'),
        'group_telegram_label': _snapshot_group_label(item, 'telegram_group')[:191],
        'asset_due_sort_at': actual_expires_at,
        'asset_due_sort_null_rank': 1 if actual_expires_at is None else 0,
        'risk_status': item.get('risk_status') or 'other',
        'risk_rank': int(item.get('risk_rank') or 99),
        'risk_statuses': statuses,
        'asset_updated_at': parse_datetime(item.get('updated_at')) if item.get('updated_at') else None,
        **flags,
    }


def refresh_cloud_asset_dashboard_snapshots(asset_ids=None, *, reason: str = '', full: bool | None = None) -> dict:
    started_at = timezone.now()
    if full is None:
        full = not asset_ids
    queryset = cloud_assets_base_queryset()
    if asset_ids:
        queryset = queryset.filter(id__in=list(asset_ids))
    assets = dedupe_cloud_asset_rows(list(queryset.order_by('-sort_order', F('actual_expires_at').asc(nulls_last=True), '-updated_at', '-id')))
    payloads = _default_cloud_asset_payloads(assets, allow_mutation=False)
    existing = {
        row.asset_id: row
        for row in CloudAssetDashboardSnapshot.objects.filter(asset_id__in=[item['id'] for item in payloads])
    }
    create_rows = []
    update_rows = []
    update_fields = [
        'payload', 'search_text', 'provider', 'cloud_account_id', 'account_label', 'region_code',
        'public_ip', 'status', 'is_active', 'is_display_visible', 'sort_order', 'user_id', 'tg_user_id',
        'telegram_group_id', 'group_user_key', 'group_user_label', 'group_telegram_key',
        'group_telegram_label', 'asset_due_sort_at', 'asset_due_sort_null_rank', 'risk_status', 'risk_rank',
        'risk_statuses', 'risk_normal', 'risk_due_soon', 'risk_expired', 'risk_unattached_ip', 'risk_abnormal',
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
    _bump_snapshot_counts_cache_version()
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


def _ensure_cloud_asset_dashboard_snapshots(reason: str = 'list') -> bool:
    asset_queryset = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER)
    if not CloudAssetDashboardSnapshot.objects.exists():
        asset_total = asset_queryset.count()
        if not asset_total:
            return False
        if asset_total <= _SNAPSHOT_SYNC_REFRESH_LIMIT:
            refresh_cloud_asset_dashboard_snapshots(reason=f'{reason}:empty', full=True)
            return True
        _defer_cloud_asset_dashboard_snapshot_refresh(reason=f'{reason}:empty-large', full=True)
        return False
    missing_ids = _next_missing_snapshot_asset_ids(_SNAPSHOT_SYNC_REFRESH_LIMIT + 1)
    if missing_ids:
        if len(missing_ids) <= _SNAPSHOT_SYNC_REFRESH_LIMIT:
            refresh_cloud_asset_dashboard_snapshots(asset_ids=missing_ids, reason=f'{reason}:missing', full=False)
            return True
        _defer_cloud_asset_dashboard_snapshot_backfill(reason=f'{reason}:missing-large')
        return False
    latest_asset = (
        asset_queryset
        .order_by('-updated_at')
        .values_list('updated_at', flat=True)
        .first()
    )
    latest_snapshot = CloudAssetDashboardSnapshot.objects.order_by('-asset_updated_at').values_list('asset_updated_at', flat=True).first()
    if latest_asset and (not latest_snapshot or latest_snapshot < latest_asset):
        stale_queryset = asset_queryset.order_by('-updated_at')
        if latest_snapshot:
            stale_queryset = stale_queryset.filter(updated_at__gt=latest_snapshot)
        stale_ids = list(
            stale_queryset.values_list('id', flat=True)[:_SNAPSHOT_SYNC_REFRESH_LIMIT + 1]
        )
        refresh_ids = _normalize_snapshot_asset_ids(stale_ids)
        if refresh_ids and len(refresh_ids) <= _SNAPSHOT_SYNC_REFRESH_LIMIT:
            refresh_cloud_asset_dashboard_snapshots(asset_ids=refresh_ids, reason=f'{reason}:stale', full=False)
            return True
        logger.info(
            'CLOUD_ASSET_DASHBOARD_SNAPSHOT_STALE_LARGE_DEFERRED reason=%s candidates=%s limit=%s',
            reason,
            len(stale_ids),
            _SNAPSHOT_SYNC_REFRESH_LIMIT,
        )
        return False
    return False


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


def _filter_dashboard_snapshots_by_risk(queryset, risk_status: str):
    risk_status = str(risk_status or '').strip()
    if not risk_status or risk_status == 'all':
        return queryset
    field = _DASHBOARD_RISK_FLAGS.get(risk_status)
    if not field:
        return queryset.none()
    queryset = queryset.filter(**{field: True})
    if risk_status != 'account_disabled':
        queryset = queryset.filter(risk_account_disabled=False)
    return queryset


def _dashboard_snapshot_risk_counts(queryset) -> dict:
    cache_key = _snapshot_queryset_cache_key('risk-counts', queryset)
    if cache_key:
        cached = cache.get(cache_key)
        if isinstance(cached, dict):
            return {key: int(value or 0) for key, value in cached.items()}
    result = {'all': int(queryset.count() or 0)}
    for status, field in _DASHBOARD_RISK_FLAGS.items():
        count_queryset = queryset.filter(**{field: True})
        if status != 'account_disabled':
            count_queryset = count_queryset.filter(risk_account_disabled=False)
        result[status] = int(count_queryset.count() or 0)
    if cache_key:
        cache.set(cache_key, result, timeout=_SNAPSHOT_COUNTS_CACHE_TTL)
    return result


def _dashboard_snapshot_group_total(queryset, group_field: str) -> int:
    cache_key = _snapshot_queryset_cache_key('group-total', queryset, group_field)
    if cache_key:
        cached = cache.get(cache_key)
        if cached is not None:
            return int(cached or 0)
    total = queryset.order_by().values(group_field).distinct().count()
    if cache_key:
        cache.set(cache_key, total, timeout=_SNAPSHOT_COUNTS_CACHE_TTL)
    return int(total or 0)


def _dashboard_snapshot_ordering(sort_by: str, sort_direction: str, risk_status: str = ''):
    if sort_by in {'actual_expires_at', 'expires_at', 'days_left', 'remaining_days'}:
        expires = '-asset_due_sort_at' if sort_direction == 'desc' else 'asset_due_sort_at'
        return ['asset_due_sort_null_rank', expires, 'risk_rank', '-sort_order', '-asset_id']
    risk_status = str(risk_status or '').strip()
    if risk_status in {
        'abnormal',
        'due_soon',
        'expired',
        'normal',
        'unattached_ip',
        'unbound_group',
        'unbound_user',
    }:
        return ['asset_due_sort_null_rank', 'asset_due_sort_at', 'group_user_label', 'group_user_key', '-asset_id']
    if risk_status in {'auto_renew_off', 'shutdown_disabled'}:
        return ['group_telegram_key', 'group_telegram_label', '-asset_id']
    return ['risk_rank', 'asset_due_sort_null_rank', 'asset_due_sort_at', '-sort_order', '-asset_id']


def _snapshot_payload(row):
    payload = dict(row.payload or {})
    if not payload.get('id'):
        return _compact_snapshot_payload(row)
    payload.setdefault('group_user_key', row.group_user_key)
    payload.setdefault('group_user_label', row.group_user_label)
    payload.setdefault('group_telegram_key', row.group_telegram_key)
    payload.setdefault('group_telegram_label', row.group_telegram_label)
    payload.setdefault('tg_user_id', row.tg_user_id)
    payload.setdefault('telegram_group_id', row.telegram_group_id)
    payload.setdefault('user_id', row.user_id)
    return payload


def _snapshot_payloads(rows):
    return [_snapshot_payload(row) for row in rows]


def _compact_snapshot_payload(row):
    asset = row.asset
    user = row.user
    telegram_group = row.telegram_group
    expires_at = asset.actual_expires_at
    username_label = ''
    if user:
        username_label = f'@{user.username}' if user.username else str(user.tg_user_id or '')
    return {
        'id': asset.id,
        'actual_expires_at': expires_at.isoformat() if expires_at else None,
        'asset_name': asset.asset_name,
        'currency': asset.currency or 'USDT',
        'days_left': _days_left(expires_at),
        'is_active': asset.is_active,
        'note': asset.note,
        'price': _decimal_to_str(asset.price, 2) if asset.price is not None else '',
        'provider_status': asset.provider_status,
        'public_ip': asset.public_ip or asset.previous_public_ip,
        'risk_rank': row.risk_rank,
        'sort_order': asset.sort_order,
        'group_user_key': row.group_user_key,
        'group_telegram_key': row.group_telegram_key,
        'status': asset.status,
        'status_countdown': _countdown_label(expires_at),
        'status_label': dict(CloudAsset.STATUS_CHOICES).get(asset.status, asset.status),
        'telegram_group_chat_id': getattr(telegram_group, 'chat_id', None),
        'telegram_group_id': row.telegram_group_id,
        'telegram_group_title': getattr(telegram_group, 'title', '') or '',
        'telegram_group_username': getattr(telegram_group, 'username', '') or '',
        'tg_user_id': row.tg_user_id,
        'updated_at': asset.updated_at.isoformat() if asset.updated_at else None,
        'user_display_name': getattr(user, 'first_name', '') or row.group_user_label or '未绑定用户',
        'user_id': row.user_id,
        'username_label': username_label or row.group_user_label or '-',
    }


def _compact_snapshot_payloads(rows):
    return [_compact_snapshot_payload(row) for row in rows]


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


def _reverse_dashboard_ordering(ordering):
    reversed_ordering = []
    for field in ordering:
        if not isinstance(field, str):
            return None
        reversed_ordering.append(field[1:] if field.startswith('-') else f'-{field}')
    return reversed_ordering


def _paginate_dashboard_snapshot_queryset(queryset, request, *, sort_by='', sort_direction='', risk_status='', default_size=20, min_size=1, max_size=200, compact=False):
    page, page_size = _parse_dashboard_page(request, default_size=default_size, min_size=min_size, max_size=max_size)
    total = queryset.count()
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    ordering = _dashboard_snapshot_ordering(sort_by, sort_direction, risk_status)
    reverse_ordering = _reverse_dashboard_ordering(ordering)
    end = min(start + page_size, total)
    if reverse_ordering and start > max(total // 2, page_size * 100):
        reverse_start = max(total - end, 0)
        rows_queryset = queryset.order_by(*reverse_ordering)[reverse_start:reverse_start + (end - start)]
        reverse_rows = True
    else:
        rows_queryset = queryset.order_by(*ordering)[start:end]
        reverse_rows = False
    if compact:
        rows_queryset = rows_queryset.select_related('asset', 'user', 'telegram_group').defer('payload', 'search_text')
    rows = list(rows_queryset)
    if reverse_rows:
        rows.reverse()
    payloads = _compact_snapshot_payloads(rows) if compact else _snapshot_payloads(rows)
    return payloads, total, total_pages, page, page_size


def _group_cloud_asset_payloads(items, group_by='telegram_group'):
    groups = {}
    for item in items:
        if group_by == 'user':
            user_id = item.get('user_id') or item.get('tg_user_id')
            key = item.get('group_user_key') or (f'user:{user_id}' if user_id else 'user:unbound')
            group = groups.setdefault(key, {
                'user_key': key,
                'tg_user_id': item.get('tg_user_id'),
                'user_display_name': item.get('user_display_name') or item.get('group_user_label') or '未绑定用户',
                'username_label': item.get('username_label') or (str(item.get('tg_user_id') or '-') if user_id else '-'),
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
                key = item.get('group_telegram_key') or (f'user:{user_id}' if user_id else 'user:unbound')
                group = groups.setdefault(key, {
                    'user_key': key,
                    'tg_user_id': item.get('tg_user_id'),
                    'user_display_name': item.get('user_display_name') or item.get('group_user_label') or '未绑定用户',
                    'username_label': item.get('username_label') or (str(item.get('tg_user_id') or '-') if user_id else '-'),
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
        min((row.get('actual_expires_at') or '9999-12-31T23:59:59') for row in group['items']),
        str(group.get('user_display_name') or group.get('telegram_group_title') or '未绑定'),
    ))
    return ordered_groups


def _dashboard_snapshot_group_keys_from_ordered_rows(
    queryset,
    *,
    group_field: str,
    group_label: str,
    start: int,
    page_size: int,
    duplicate_excess: int = 0,
):
    target_count = start + page_size
    if target_count <= 0:
        return []
    fetch_limit = max(target_count + max(int(duplicate_excess or 0), 0) + page_size * 2, page_size * 25, 500)
    max_fetch_limit = max(fetch_limit, 250000)
    ordering = ('asset_due_sort_null_rank', 'asset_due_sort_at', group_label, group_field)
    while fetch_limit <= max_fetch_limit:
        rows = (
            queryset
            .order_by(*ordering)
            .values_list(group_field, flat=True)[:fetch_limit]
        )
        seen = set()
        ordered_keys = []
        for key in rows:
            if key in seen:
                continue
            seen.add(key)
            ordered_keys.append(key)
            if len(ordered_keys) >= target_count:
                break
        if len(ordered_keys) >= target_count or fetch_limit >= max_fetch_limit:
            return ordered_keys[start:target_count] if len(ordered_keys) > start else []
        fetch_limit = min(fetch_limit * 2, max_fetch_limit)
    return []


def _dashboard_snapshot_group_keys_from_reverse_tail(queryset, *, group_field: str, group_label: str, start: int, end: int, total: int):
    tail_group_count = max(total - start, 0)
    if tail_group_count <= 0:
        return []
    duplicate_excess = max(queryset.count() - total, 0)
    candidate_limit = tail_group_count + duplicate_excess + max(end - start, 20)
    if candidate_limit > 100000:
        return []
    reverse_ordering = ('-asset_due_sort_null_rank', '-asset_due_sort_at', f'-{group_label}', f'-{group_field}')
    candidate_rows = (
        queryset
        .order_by(*reverse_ordering)
        .values_list(group_field, flat=True)[:candidate_limit]
    )
    candidate_keys = []
    seen = set()
    for key in candidate_rows:
        if key in seen:
            continue
        seen.add(key)
        candidate_keys.append(key)
        if len(candidate_keys) >= tail_group_count:
            break
    if len(candidate_keys) < tail_group_count:
        return []
    grouped_rows = list(
        queryset
        .filter(**{f'{group_field}__in': candidate_keys})
        .values(group_field)
        .annotate(group_due_null_rank=Min('asset_due_sort_null_rank'), group_expires=Min('asset_due_sort_at'), group_name=Min(group_label))
    )
    grouped_rows.sort(key=lambda row: (
        row.get('group_due_null_rank') if row.get('group_due_null_rank') is not None else 1,
        row.get('group_expires') or datetime.max.replace(tzinfo=datetime_timezone.utc),
        row.get('group_name') or '',
        row.get(group_field) or '',
    ))
    tail_keys = [row[group_field] for row in grouped_rows[-tail_group_count:]]
    return tail_keys[:end - start]


def _dashboard_snapshot_can_use_forward_row_paging(*, start: int, duplicate_excess: int) -> bool:
    return start <= 100000 or (duplicate_excess == 0 and start <= 150000)


def _dashboard_snapshot_group_page(queryset, request, *, group_by='user', sort_by='', sort_direction='', compact=False):
    page, page_size = _parse_dashboard_page(request, default_size=20, min_size=1, max_size=100)
    group_field = 'group_telegram_key' if group_by == 'telegram_group' else 'group_user_key'
    group_label = 'group_telegram_label' if group_by == 'telegram_group' else 'group_user_label'
    total = _dashboard_snapshot_group_total(queryset, group_field)
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    end = min(start + page_size, total)
    page_keys = []
    duplicate_excess = max(queryset.count() - total, 0)
    exact_row_paging_safe = start + (end - start) + duplicate_excess <= 250000
    reverse_row_paging_safe = duplicate_excess <= 100000
    if duplicate_excess == 0 and page == 1:
        for fetch_limit in (max(page_size * 25, 500), 2000, 5000):
            candidates = list(
                queryset
                .only('id', 'asset_id', group_field, group_label, 'risk_rank', 'asset_due_sort_null_rank', 'asset_due_sort_at')
                .order_by('asset_due_sort_null_rank', 'asset_due_sort_at', group_label, group_field)[:fetch_limit]
            )
            seen = set()
            page_keys = []
            for row in candidates:
                key = getattr(row, group_field)
                if key in seen:
                    continue
                seen.add(key)
                page_keys.append(key)
                if len(page_keys) >= page_size:
                    break
            if len(page_keys) >= page_size:
                break
        if len(page_keys) < page_size:
            page_keys = []
    if (
        not page_keys
        and exact_row_paging_safe
        and _dashboard_snapshot_can_use_forward_row_paging(start=start, duplicate_excess=duplicate_excess)
    ):
        page_keys = _dashboard_snapshot_group_keys_from_ordered_rows(
            queryset,
            group_field=group_field,
            group_label=group_label,
            start=start,
            page_size=end - start,
            duplicate_excess=duplicate_excess,
        )
    if not page_keys and reverse_row_paging_safe and start > max(total // 2, page_size * 100):
        page_keys = _dashboard_snapshot_group_keys_from_reverse_tail(
            queryset,
            group_field=group_field,
            group_label=group_label,
            start=start,
            end=end,
            total=total,
        )
    if not page_keys:
        reverse_rows = False
        if start > max(total // 2, page_size * 100):
            reverse_start = max(total - end, 0)
            grouped_queryset = (
                queryset.order_by()
                .values(group_field)
                .annotate(group_due_null_rank=Min('asset_due_sort_null_rank'), group_expires=Min('asset_due_sort_at'), group_name=Min(group_label))
                .order_by('-group_due_null_rank', F('group_expires').desc(nulls_first=True), '-group_name', f'-{group_field}')
            )
            grouped = list(grouped_queryset[reverse_start:reverse_start + (end - start)])
            reverse_rows = True
        else:
            grouped_queryset = (
                queryset.order_by()
                .values(group_field)
                .annotate(group_due_null_rank=Min('asset_due_sort_null_rank'), group_expires=Min('asset_due_sort_at'), group_name=Min(group_label))
                .order_by('group_due_null_rank', 'group_expires', 'group_name', group_field)
            )
            grouped = list(grouped_queryset[start:end])
        if reverse_rows:
            grouped.reverse()
        page_keys = [row[group_field] for row in grouped]
    if not page_keys:
        return [], [], total, total_pages, page, page_size
    order_index = {key: index for index, key in enumerate(page_keys)}
    rows_queryset = (
        queryset
        .filter(**{f'{group_field}__in': page_keys})
        .order_by(*_dashboard_snapshot_ordering(sort_by, sort_direction))
    )
    if compact:
        rows_queryset = rows_queryset.select_related('asset', 'user', 'telegram_group').defer('payload', 'search_text')
    rows = list(rows_queryset)
    items = _compact_snapshot_payloads(rows) if compact else _snapshot_payloads(rows)
    ordered_groups = _group_cloud_asset_payloads(items, group_by)
    ordered_groups.sort(key=lambda group: order_index.get(group.get('user_key'), 999999))
    page_items = [row for group in ordered_groups for row in group['items']]
    return ordered_groups, page_items, total, total_pages, page, page_size
