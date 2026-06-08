"""生命周期计划页查询层。

这里只做 CloudAsset / CloudIpLog 的筛选、计数和分页，不拼后台展示文案。
"""

from __future__ import annotations

import heapq

from django.core.cache import cache
from django.db.models import Q

from cloud.models import CloudAsset, CloudIpLog, CloudServerOrder
from core.cloud_accounts import cloud_account_label_variants
from core.models import CloudAccountConfig

_LIFECYCLE_PLAN_COUNTS_CACHE_KEY = 'cloud:lifecycle-plan:server-counts:v2'
_LIFECYCLE_PLAN_COUNTS_CACHE_TTL = 30


def page_bounds(page: int, page_size: int) -> tuple[int, int]:
    page = max(int(page or 1), 1)
    page_size = max(int(page_size or 1), 1)
    start = (page - 1) * page_size
    return start, start + page_size


def page_meta(page: int, page_size: int, total: int) -> dict:
    page = max(int(page or 1), 1)
    page_size = max(int(page_size or 1), 1)
    total = max(int(total or 0), 0)
    return {
        'page': page,
        'page_size': page_size,
        'total': total,
        'loaded': min(page_size, max(total - ((page - 1) * page_size), 0)),
    }


def _page_from_single_source(kind: str, queryset, *, page: int, page_size: int, total: int | None = None) -> list[tuple[str, object]]:
    start, end = page_bounds(page, page_size)
    if total is None:
        total = queryset.count()
    end = min(end, total)
    if total <= 0 or start >= end:
        return []
    return [(kind, item) for item in queryset[start:end]]


def clear_lifecycle_plan_counts_cache():
    cache.delete(_LIFECYCLE_PLAN_COUNTS_CACHE_KEY)


def reverse_ordering(ordering: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(field[1:] if field.startswith('-') else f'-{field}' for field in ordering)


def paged_queryset(queryset, *, ordering: tuple[str, ...], page: int, page_size: int, total: int | None = None):
    start, end = page_bounds(page, page_size)
    if total is None:
        total = queryset.count()
    end = min(end, total)
    if start >= end:
        return []
    if start > max(total // 2, page_size * 100):
        reverse_start = max(total - end, 0)
        rows = list(queryset.order_by(*reverse_ordering(ordering))[reverse_start:reverse_start + (end - start)])
        rows.reverse()
        return rows
    return list(queryset.order_by(*ordering)[start:end])


def active_cloud_account_labels() -> list[str]:
    labels = []
    for account in CloudAccountConfig.objects.filter(
        provider__in=[CloudAccountConfig.PROVIDER_AWS, CloudAccountConfig.PROVIDER_ALIYUN],
        is_active=True,
    ):
        labels.extend(cloud_account_label_variants(account))
    return list(dict.fromkeys(labels))


def unattached_ip_asset_q():
    return (
        Q(provider_status__icontains='未附加')
        | Q(note__icontains='未附加IP')
        | Q(note__icontains='未附加固定IP')
        | Q(provider_resource_id__icontains='StaticIp')
    )


def broad_unattached_ip_asset_q():
    return (
        unattached_ip_asset_q()
        | Q(note__icontains='固定 IP 已释放')
        | Q(note__icontains='固定IP已释放')
        | Q(note__icontains='固定 IP 云端已不存在')
        | Q(note__icontains='固定IP云端已不存在')
    )


def asset_waiting_manual_time_q():
    return (
        Q(actual_expires_at__isnull=True)
        | Q(provider_status__icontains='待人工添加时间')
        | Q(note__icontains='等待人工添加真实到期时间')
        | Q(note__icontains='等待人工添加时间')
    )


def server_shutdown_complete_q():
    return (
        Q(order__status__in=['suspended', 'deleting', 'deleted'])
        | Q(status__in=[
            CloudAsset.STATUS_STOPPED,
            CloudAsset.STATUS_SUSPENDED,
            CloudAsset.STATUS_DELETING,
            CloudAsset.STATUS_DELETED,
            CloudAsset.STATUS_TERMINATING,
            CloudAsset.STATUS_TERMINATED,
        ])
    )


def server_lifecycle_plan_queryset():
    blank_instance_q = Q(instance_id__isnull=True) | Q(instance_id='')
    unattached_asset_ids = (
        CloudAsset.objects
        .filter(kind=CloudAsset.KIND_SERVER)
        .filter(blank_instance_q)
        .filter(unattached_ip_asset_q())
        .values('id')
    )
    return (
        CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER, actual_expires_at__isnull=False)
        .exclude(status__in=[
            CloudAsset.STATUS_DELETED,
            CloudAsset.STATUS_DELETING,
            CloudAsset.STATUS_TERMINATED,
            CloudAsset.STATUS_TERMINATING,
        ])
        .exclude(id__in=unattached_asset_ids)
    )


def server_lifecycle_plan_counts() -> dict[str, int]:
    cached = cache.get(_LIFECYCLE_PLAN_COUNTS_CACHE_KEY)
    if isinstance(cached, dict):
        return {
            'shutdown_plan_count': int(cached.get('shutdown_plan_count') or 0),
            'server_delete_count': int(cached.get('server_delete_count') or 0),
        }
    queryset = server_lifecycle_plan_queryset()
    counts = {
        'shutdown_plan_count': queryset.exclude(server_shutdown_complete_q()).count(),
        'server_delete_count': queryset.filter(server_shutdown_complete_q()).count(),
    }
    cache.set(_LIFECYCLE_PLAN_COUNTS_CACHE_KEY, counts, timeout=_LIFECYCLE_PLAN_COUNTS_CACHE_TTL)
    return counts


def server_lifecycle_plan_page(*, plan_stage: str, page: int, page_size: int, total: int | None = None):
    queryset = server_lifecycle_plan_queryset()
    if plan_stage == 'shutdown':
        queryset = queryset.exclude(server_shutdown_complete_q())
    else:
        queryset = queryset.filter(server_shutdown_complete_q())
    return paged_queryset(queryset, ordering=('actual_expires_at', 'id'), page=page, page_size=page_size, total=total)


def server_delete_history_order_queryset():
    return CloudServerOrder.objects.select_related('user').filter(status='deleted')


def server_delete_history_asset_queryset():
    return (
        CloudAsset.objects.select_related('user', 'cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER, order__isnull=True)
        .filter(status__in=[CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED])
        .exclude(broad_unattached_ip_asset_q())
    )


def server_delete_history_counts() -> dict[str, int]:
    order_count = server_delete_history_order_queryset().count()
    asset_count = server_delete_history_asset_queryset().count()
    return {
        'server_history_asset_count': asset_count,
        'server_history_count': order_count + asset_count,
        'server_history_order_count': order_count,
    }


def server_delete_history_page_sources(
    *,
    page: int,
    page_size: int,
    order_total: int | None = None,
    asset_total: int | None = None,
) -> list[tuple[str, object]]:
    start, end = page_bounds(page, page_size)
    orders = server_delete_history_order_queryset().order_by('-updated_at', '-id')
    assets = server_delete_history_asset_queryset().order_by('-updated_at', '-id')
    if order_total is None:
        order_total = orders.count()
    if asset_total is None:
        asset_total = assets.count()
    total = order_total + asset_total
    if total <= 0 or start >= total:
        return []
    if order_total <= 0:
        return _page_from_single_source('asset', assets, page=page, page_size=page_size, total=asset_total)
    if asset_total <= 0:
        return _page_from_single_source('order', orders, page=page, page_size=page_size, total=order_total)

    chunk_size = max(page_size * 4, 50)
    source_specs = [
        ('order', orders, order_total),
        ('asset', assets, asset_total),
    ]
    source_state = {}
    heap: list[tuple[object, int, str, object]] = []

    def history_sort_key(item) -> tuple[float, int]:
        updated_at = getattr(item, 'updated_at', None)
        updated_at_ts = updated_at.timestamp() if updated_at is not None else 0.0
        return (-updated_at_ts, -int(getattr(item, 'id', 0) or 0))

    def refill(kind: str):
        state = source_state[kind]
        if state['buffer'] or state['offset'] >= state['total']:
            return
        batch_end = min(state['offset'] + chunk_size, state['total'])
        state['buffer'] = list(state['queryset'][state['offset']:batch_end])
        state['offset'] = batch_end
        if state['buffer']:
            first = state['buffer'].pop(0)
            heapq.heappush(heap, (history_sort_key(first), state['priority'], kind, first))

    for priority, (kind, queryset, source_total) in enumerate(source_specs):
        source_state[kind] = {
            'queryset': queryset,
            'total': source_total,
            'offset': 0,
            'buffer': [],
            'priority': priority,
        }
        refill(kind)

    items: list[tuple[str, object]] = []
    index = 0
    while heap and index < end:
        _sort_key, _priority, kind, item = heapq.heappop(heap)
        if index >= start:
            items.append((kind, item))
        index += 1
        state = source_state[kind]
        if state['buffer']:
            next_item = state['buffer'].pop(0)
            heapq.heappush(heap, (history_sort_key(next_item), state['priority'], kind, next_item))
        else:
            refill(kind)
    return items


def unattached_ip_deleted_or_missing_q():
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


def unattached_ip_delete_active_queryset():
    blank_instance_q = Q(instance_id__isnull=True) | Q(instance_id='')
    return (
        CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER)
        .filter(broad_unattached_ip_asset_q())
        .filter(blank_instance_q)
        .exclude(unattached_ip_deleted_or_missing_q())
    )


def unattached_ip_delete_history_asset_queryset():
    blank_instance_q = Q(instance_id__isnull=True) | Q(instance_id='')
    return (
        CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER)
        .filter(broad_unattached_ip_asset_q())
        .filter(blank_instance_q)
        .filter(unattached_ip_deleted_or_missing_q())
    )


def completed_unattached_ip_active_queryset():
    retained_ip_q = (
        Q(ip_logs__note__icontains='固定 IP 保留')
        | Q(ip_logs__note__icontains='固定IP保留')
        | Q(ip_logs__note__icontains='固定 IP 继续保留')
        | Q(ip_logs__note__icontains='固定IP继续保留')
    )
    instance_deleted_q = (
        Q(ip_logs__note__icontains='实例已删除')
        | Q(ip_logs__note__icontains='AWS 实例已执行删除')
    )
    return unattached_ip_delete_active_queryset().filter(instance_deleted_q & retained_ip_q).distinct()


def completed_unattached_ip_active_count() -> int:
    return completed_unattached_ip_active_queryset().count()


def unattached_ip_delete_plan_page(
    *,
    page: int,
    page_size: int,
    total: int | None = None,
    exclude_completed=True,
    completed_total: int | None = None,
):
    queryset = unattached_ip_delete_active_queryset()
    start, end = page_bounds(page, page_size)
    if total is None:
        total = queryset.count()
    end = min(end, total)
    if total <= 0 or start >= end:
        return []
    if exclude_completed:
        if completed_total is None:
            completed_total = completed_unattached_ip_active_count()
        if completed_total > 0:
            if completed_total <= 1000:
                completed_ids = list(completed_unattached_ip_active_queryset().values_list('id', flat=True)[:completed_total])
                if completed_ids:
                    queryset = queryset.exclude(id__in=completed_ids)
            else:
                queryset = queryset.exclude(id__in=completed_unattached_ip_active_queryset().values('id'))
    if start > max(total // 2, page_size * 100):
        reverse_start = max(total - end, 0)
        if reverse_start <= max(page_size * 20, 1000):
            tail_page = _unattached_ip_delete_tail_page(
                queryset,
                reverse_start=reverse_start,
                count=end - start,
            )
            if tail_page is not None:
                return tail_page
    return paged_queryset(queryset, ordering=('actual_expires_at', 'id'), page=page, page_size=page_size, total=total)


def _unattached_ip_delete_tail_page(queryset, *, reverse_start: int, count: int):
    if count <= 0:
        return []
    target = reverse_start + count
    if target <= 0:
        return []
    candidate_sources = [
        CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, instance_id='').order_by('-actual_expires_at', '-id'),
        CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, instance_id__isnull=True).order_by('-actual_expires_at', '-id'),
    ]
    source_state = {}
    heap = []
    chunk_size = max(target * 4, 500)

    def candidate_sort_key(row):
        item_id, actual_expires_at = row
        ts_value = actual_expires_at.timestamp() if actual_expires_at is not None else float('-inf')
        return (-ts_value, -int(item_id or 0))

    def refill(source_index: int):
        state = source_state[source_index]
        if state['buffer'] or state['done']:
            return
        start = state['offset']
        rows = list(state['queryset'].values_list('id', 'actual_expires_at')[start:start + chunk_size])
        state['offset'] += len(rows)
        if len(rows) < chunk_size:
            state['done'] = True
        state['buffer'] = rows
        if state['buffer']:
            row = state['buffer'].pop(0)
            heapq.heappush(heap, (candidate_sort_key(row), source_index, row))

    for source_index, candidate_qs in enumerate(candidate_sources):
        source_state[source_index] = {
            'buffer': [],
            'done': False,
            'offset': 0,
            'queryset': candidate_qs,
        }
        refill(source_index)

    collected_ids = []
    scanned = 0
    max_scan = max(target * 50, 5000)
    pending_ids = []

    def flush_pending_ids():
        if not pending_ids:
            return
        active_ids = set(queryset.filter(id__in=pending_ids).values_list('id', flat=True))
        collected_ids.extend([item_id for item_id in pending_ids if item_id in active_ids])
        pending_ids.clear()

    while len(collected_ids) < target and scanned < max_scan and heap:
        _sort_key, source_index, row = heapq.heappop(heap)
        item_id, _actual_expires_at = row
        pending_ids.append(item_id)
        scanned += 1
        state = source_state[source_index]
        if state['buffer']:
            next_row = state['buffer'].pop(0)
            heapq.heappush(heap, (candidate_sort_key(next_row), source_index, next_row))
        else:
            refill(source_index)
        if len(pending_ids) >= chunk_size:
            flush_pending_ids()
    flush_pending_ids()
    if len(collected_ids) < target:
        return None
    selected_ids = collected_ids[reverse_start:reverse_start + count]
    assets = {asset.id: asset for asset in queryset.filter(id__in=selected_ids)}
    rows = [assets[item_id] for item_id in selected_ids if item_id in assets]
    if len(rows) != len(selected_ids):
        return None
    rows.reverse()
    return rows


def unattached_ip_delete_history_q():
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


def ip_delete_plan_counts() -> dict[str, int]:
    active_count = unattached_ip_delete_active_queryset().count()
    completed_active_count = completed_unattached_ip_active_count()
    history_log_count = CloudIpLog.objects.filter(unattached_ip_delete_history_q()).count()
    history_asset_count = unattached_ip_delete_history_asset_queryset().count()
    return {
        'ip_delete_count': max(active_count - completed_active_count, 0),
        'ip_delete_completed_active_count': completed_active_count,
        'ip_delete_history_asset_count': history_asset_count,
        'ip_delete_history_count': history_log_count + history_asset_count + completed_active_count,
        'ip_delete_history_log_count': history_log_count,
    }


def ip_delete_history_page_sources(
    *,
    page: int,
    page_size: int,
    log_total: int | None = None,
    asset_total: int | None = None,
    completed_total: int | None = None,
) -> list[tuple[str, object]]:
    start, end = page_bounds(page, page_size)
    history_logs = CloudIpLog.objects.filter(unattached_ip_delete_history_q()).select_related('asset', 'order', 'user').order_by('-created_at', '-id')
    history_assets = unattached_ip_delete_history_asset_queryset().order_by('-updated_at', '-id')
    completed_assets = completed_unattached_ip_active_queryset().order_by('-updated_at', '-id')
    if log_total is None:
        log_total = history_logs.count()
    if asset_total is None:
        asset_total = history_assets.count()
    if completed_total is None:
        completed_total = completed_assets.count()
    total = log_total + asset_total + completed_total
    if total <= 0 or start >= total:
        return []
    end = min(end, total)

    if asset_total <= 0 and completed_total <= 0:
        return _page_from_single_source('log', history_logs, page=page, page_size=page_size, total=log_total)
    if log_total <= 0 and completed_total <= 0:
        return _page_from_single_source('asset', history_assets, page=page, page_size=page_size, total=asset_total)
    if log_total <= 0 and asset_total <= 0:
        return _page_from_single_source('asset', completed_assets, page=page, page_size=page_size, total=completed_total)

    if log_total > 0 and 0 < asset_total + completed_total <= 100:
        page_items = _ip_delete_history_page_with_sparse_assets(
            history_logs=history_logs,
            history_assets=history_assets,
            completed_assets=completed_assets,
            start=start,
            end=end,
            log_total=log_total,
            asset_total=asset_total,
            completed_total=completed_total,
        )
        if page_items is not None:
            return page_items

    reverse_page = start > max(total // 2, page_size * 100)
    if reverse_page:
        reverse_start = max(total - end, 0)
        reverse_end = reverse_start + (end - start)
        start, end = reverse_start, reverse_end
        history_logs = CloudIpLog.objects.filter(unattached_ip_delete_history_q()).select_related('asset', 'order', 'user').order_by('created_at', 'id')
        history_assets = unattached_ip_delete_history_asset_queryset().order_by('updated_at', 'id')
        completed_assets = completed_unattached_ip_active_queryset().order_by('updated_at', 'id')

    chunk_size = max(page_size * 4, 50)
    source_specs = [
        ('log', history_logs, log_total, lambda item: getattr(item, 'created_at', None)),
        ('asset', history_assets, asset_total, lambda item: getattr(item, 'updated_at', None)),
        ('asset', completed_assets, completed_total, lambda item: getattr(item, 'updated_at', None)),
    ]
    source_state = {}
    heap: list[tuple[object, int, str, object]] = []

    def history_sort_key(item, time_getter) -> tuple[float, int]:
        timestamp = time_getter(item)
        ts_value = timestamp.timestamp() if timestamp is not None else 0.0
        item_id = int(getattr(item, 'id', 0) or 0)
        if reverse_page:
            return (ts_value, item_id)
        return (-ts_value, -item_id)

    def refill(state_key: int):
        state = source_state[state_key]
        if state['buffer'] or state['offset'] >= state['total']:
            return
        batch_end = min(state['offset'] + chunk_size, state['total'])
        state['buffer'] = list(state['queryset'][state['offset']:batch_end])
        state['offset'] = batch_end
        if state['buffer']:
            first = state['buffer'].pop(0)
            priority_key = -state['priority'] if reverse_page else state['priority']
            heapq.heappush(heap, (history_sort_key(first, state['time_getter']), priority_key, state_key, first))

    for priority, (kind, queryset, source_total, time_getter) in enumerate(source_specs):
        source_state[priority] = {
            'kind': kind,
            'queryset': queryset,
            'total': source_total,
            'offset': 0,
            'buffer': [],
            'priority': priority,
            'time_getter': time_getter,
        }
        refill(priority)

    items: list[tuple[str, object]] = []
    index = 0
    while heap and index < end:
        _sort_key, _priority, state_key, item = heapq.heappop(heap)
        state = source_state[state_key]
        if index >= start:
            items.append((state['kind'], item))
        index += 1
        if state['buffer']:
            next_item = state['buffer'].pop(0)
            priority_key = -state['priority'] if reverse_page else state['priority']
            heapq.heappush(heap, (history_sort_key(next_item, state['time_getter']), priority_key, state_key, next_item))
        else:
            refill(state_key)
    if reverse_page:
        items.reverse()
    return items


def _ip_delete_history_page_with_sparse_assets(
    *,
    history_logs,
    history_assets,
    completed_assets,
    start: int,
    end: int,
    log_total: int,
    asset_total: int,
    completed_total: int,
) -> list[tuple[str, object]] | None:
    small_sources = []
    for priority, (queryset, source_total) in enumerate(((history_assets, asset_total), (completed_assets, completed_total)), start=1):
        if source_total <= 0:
            continue
        rows = list(queryset[:source_total])
        if len(rows) != source_total:
            return None
        for row in rows:
            small_sources.append((priority, row))
    if not small_sources:
        return _page_from_single_source('log', history_logs, page=(start // max(end - start, 1)) + 1, page_size=max(end - start, 1), total=log_total)

    def timestamp_value(item) -> float:
        updated_at = getattr(item, 'updated_at', None)
        return updated_at.timestamp() if updated_at is not None else 0.0

    small_sources.sort(key=lambda entry: (-timestamp_value(entry[1]), -int(getattr(entry[1], 'id', 0) or 0), entry[0]))
    small_positions = []
    for small_index, (priority, item) in enumerate(small_sources):
        updated_at = getattr(item, 'updated_at', None)
        if updated_at is None:
            log_before = log_total
        else:
            log_before = history_logs.filter(
                Q(created_at__gt=updated_at)
                | (Q(created_at=updated_at) & Q(id__gte=int(getattr(item, 'id', 0) or 0)))
            ).count()
        small_positions.append((log_before + small_index, priority, item))
    small_positions.sort(key=lambda entry: entry[0])
    small_by_position = {position: (priority, item) for position, priority, item in small_positions}
    position_values = [position for position, _priority, _item in small_positions]

    def small_before(global_index: int) -> int:
        count = 0
        for position in position_values:
            if position >= global_index:
                break
            count += 1
        return count

    output_slots = []
    log_indices = []
    for global_index in range(start, end):
        small = small_by_position.get(global_index)
        if small is not None:
            output_slots.append(('asset', small[1]))
            continue
        log_index = global_index - small_before(global_index)
        if log_index < 0 or log_index >= log_total:
            return None
        output_slots.append(('log_index', log_index))
        log_indices.append(log_index)
    if not log_indices:
        return [(kind, item) for kind, item in output_slots]

    min_log_index = min(log_indices)
    max_log_index = max(log_indices)
    if min_log_index > max(log_total // 2, (end - start) * 100):
        reverse_start = max(log_total - max_log_index - 1, 0)
        reverse_count = max_log_index - min_log_index + 1
        log_rows = list(
            CloudIpLog.objects
            .filter(unattached_ip_delete_history_q())
            .select_related('asset', 'order', 'user')
            .order_by('created_at', 'id')[reverse_start:reverse_start + reverse_count]
        )
        log_rows.reverse()
    else:
        log_rows = list(history_logs[min_log_index:max_log_index + 1])
    log_by_index = {
        min_log_index + offset: row
        for offset, row in enumerate(log_rows)
    }
    result = []
    for kind, value in output_slots:
        if kind == 'asset':
            result.append((kind, value))
        else:
            log = log_by_index.get(value)
            if log is None:
                return None
            result.append(('log', log))
    return result
