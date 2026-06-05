"""云资产列表快照读写与分页辅助。"""

import logging

from django.db.models import Count, F, Min, Q
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from bot.models import TelegramUser
from cloud.asset_queries import cloud_assets_base_queryset, dedupe_cloud_asset_rows
from cloud.models import CloudAsset, CloudAssetDashboardSnapshot
from core.dashboard_api import _countdown_label, _days_left, _decimal_to_str

logger = logging.getLogger(__name__)


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
        'public_ip', 'status', 'is_active', 'sort_order', 'user_id', 'tg_user_id',
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
    aggregates = {
        'all': Count('id'),
    }
    for status, field in _DASHBOARD_RISK_FLAGS.items():
        if status == 'account_disabled':
            aggregates[status] = Count('id', filter=Q(**{field: True}))
        else:
            aggregates[status] = Count('id', filter=Q(risk_account_disabled=False, **{field: True}))
    counts = queryset.aggregate(**aggregates)
    return {key: int(value or 0) for key, value in counts.items()}


def _dashboard_snapshot_ordering(sort_by: str, sort_direction: str):
    if sort_by in {'actual_expires_at', 'expires_at', 'days_left', 'remaining_days'}:
        expires = F('asset__actual_expires_at').desc(nulls_last=True) if sort_direction == 'desc' else F('asset__actual_expires_at').asc(nulls_last=True)
        return [expires, 'risk_rank', '-sort_order', '-asset_id']
    return ['risk_rank', F('asset__actual_expires_at').asc(nulls_last=True), '-sort_order', '-asset_id']


def _snapshot_payloads(rows):
    return [dict(row.payload or {}) for row in rows]


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


def _paginate_dashboard_snapshot_queryset(queryset, request, *, sort_by='', sort_direction='', default_size=20, min_size=1, max_size=200, compact=False):
    page, page_size = _parse_dashboard_page(request, default_size=default_size, min_size=min_size, max_size=max_size)
    total = queryset.count()
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    rows_queryset = queryset.order_by(*_dashboard_snapshot_ordering(sort_by, sort_direction))[start:start + page_size]
    if compact:
        rows_queryset = rows_queryset.select_related('asset', 'user', 'telegram_group').defer('payload', 'search_text')
    rows = list(rows_queryset)
    payloads = _compact_snapshot_payloads(rows) if compact else _snapshot_payloads(rows)
    return payloads, total, total_pages, page, page_size


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


def _dashboard_snapshot_group_page(queryset, request, *, group_by='user', sort_by='', sort_direction='', compact=False):
    page, page_size = _parse_dashboard_page(request, default_size=20, min_size=1, max_size=100)
    group_field = 'group_telegram_key' if group_by == 'telegram_group' else 'group_user_key'
    group_label = 'group_telegram_label' if group_by == 'telegram_group' else 'group_user_label'
    total = queryset.values(group_field).distinct().count()
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    page_keys = []
    if compact and page == 1:
        for fetch_limit in (max(page_size * 25, 500), 2000, 5000):
            candidates = list(
                queryset
                .only('id', 'asset_id', group_field, group_label, 'risk_rank', 'asset__actual_expires_at')
                .select_related('asset')
                .order_by(F('asset__actual_expires_at').asc(nulls_last=True), group_label, group_field)[:fetch_limit]
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
    if not page_keys:
        grouped = list(
            queryset.values(group_field)
            .annotate(group_expires=Min('asset__actual_expires_at'), group_name=Min(group_label), min_risk=Min('risk_rank'))
            .order_by(F('group_expires').asc(nulls_last=True), 'group_name', group_field)
            [start:start + page_size]
        )
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
