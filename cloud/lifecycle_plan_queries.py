"""生命周期计划页查询层。

这里只做 CloudAsset / CloudIpLog 的筛选、计数和分页，不拼后台展示文案。
"""

from __future__ import annotations

from django.db.models import Q

from cloud.models import CloudAsset, CloudIpLog, CloudServerOrder
from core.cloud_accounts import cloud_account_label_variants
from core.models import CloudAccountConfig


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
    return (
        CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER, actual_expires_at__isnull=False)
        .exclude(status__in=[
            CloudAsset.STATUS_DELETED,
            CloudAsset.STATUS_DELETING,
            CloudAsset.STATUS_TERMINATED,
            CloudAsset.STATUS_TERMINATING,
        ])
        .exclude(unattached_ip_asset_q())
        .exclude(asset_waiting_manual_time_q())
    )


def server_lifecycle_plan_counts() -> dict[str, int]:
    queryset = server_lifecycle_plan_queryset()
    return {
        'shutdown_plan_count': queryset.exclude(server_shutdown_complete_q()).count(),
        'server_delete_count': queryset.filter(server_shutdown_complete_q()).count(),
    }


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
    start, _end = page_bounds(page, page_size)
    items: list[tuple[str, object]] = []
    orders = server_delete_history_order_queryset()
    if order_total is None:
        order_total = orders.count()
    if start < order_total:
        order_rows = paged_queryset(
            orders,
            ordering=('-updated_at', '-id'),
            page=page,
            page_size=page_size,
            total=order_total,
        )
        items.extend(('order', order) for order in order_rows)
    remaining = page_size - len(items)
    if remaining <= 0:
        return items

    asset_start = max(start - order_total, 0)
    assets = server_delete_history_asset_queryset().order_by('-updated_at', '-id')
    if asset_total is None:
        asset_total = assets.count()
    if asset_start < asset_total:
        asset_end = min(asset_start + remaining, asset_total)
        items.extend(('asset', asset) for asset in assets[asset_start:asset_end])
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


def unattached_ip_delete_plan_page(*, page: int, page_size: int, total: int | None = None, exclude_completed=True):
    queryset = unattached_ip_delete_active_queryset()
    if exclude_completed:
        queryset = queryset.exclude(id__in=completed_unattached_ip_active_queryset().values('id'))
    return paged_queryset(queryset, ordering=('actual_expires_at', 'id'), page=page, page_size=page_size, total=total)


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
    items: list[tuple[str, object]] = []
    history_logs = CloudIpLog.objects.filter(unattached_ip_delete_history_q())
    if log_total is None:
        log_total = history_logs.count()
    if start < log_total and len(items) < page_size:
        traces = paged_queryset(
            history_logs.select_related('asset', 'order', 'user'),
            ordering=('-id',),
            page=page,
            page_size=page_size,
            total=log_total,
        )
        items.extend(('log', trace) for trace in traces)

    remaining = page_size - len(items)
    if remaining <= 0:
        return items

    asset_start = max(start - log_total, 0)
    history_assets = unattached_ip_delete_history_asset_queryset().order_by('-updated_at', '-id')
    if asset_total is None:
        asset_total = history_assets.count()
    if asset_start < asset_total:
        asset_end = min(asset_start + remaining, asset_total)
        items.extend(('asset', asset) for asset in history_assets[asset_start:asset_end])
        remaining = page_size - len(items)
    if remaining <= 0:
        return items

    completed_start = max(start - log_total - asset_total, 0)
    if completed_total is not None and completed_start >= completed_total:
        return items
    completed_assets = completed_unattached_ip_active_queryset().order_by('-updated_at', '-id')
    items.extend(('asset', asset) for asset in completed_assets[completed_start:completed_start + remaining])
    return items
