"""生命周期计划页查询层。

这里只做 CloudAsset / CloudIpLog 的筛选、计数和分页，不拼后台展示文案。
"""

from __future__ import annotations

from django.db.models import Q

from cloud.models import CloudAsset, CloudIpLog
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


def active_cloud_account_labels() -> list[str]:
    labels = []
    for account in CloudAccountConfig.objects.filter(
        provider__in=[CloudAccountConfig.PROVIDER_AWS, CloudAccountConfig.PROVIDER_ALIYUN],
        is_active=True,
    ):
        labels.extend(cloud_account_label_variants(account))
    return list(dict.fromkeys(labels))


def active_cloud_asset_account_disabled_q(active_account_labels: set[str] | None = None):
    active_account_labels = active_account_labels if active_account_labels is not None else set(active_cloud_account_labels())
    account_label_present = Q(account_label__isnull=False) & ~Q(account_label='')
    disabled_q = Q(cloud_account__is_active=False)
    if active_account_labels:
        disabled_q |= account_label_present & ~Q(account_label__in=list(active_account_labels))
    else:
        disabled_q |= account_label_present
    return disabled_q


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
    active_labels = set(active_cloud_account_labels())
    return (
        CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER, actual_expires_at__isnull=False)
        .exclude(status__in=[
            CloudAsset.STATUS_DELETED,
            CloudAsset.STATUS_DELETING,
            CloudAsset.STATUS_TERMINATED,
            CloudAsset.STATUS_TERMINATING,
        ])
        .exclude(active_cloud_asset_account_disabled_q(active_labels))
        .exclude(unattached_ip_asset_q())
        .exclude(asset_waiting_manual_time_q())
    )


def server_lifecycle_plan_counts() -> dict[str, int]:
    queryset = server_lifecycle_plan_queryset()
    return {
        'shutdown_plan_count': queryset.exclude(server_shutdown_complete_q()).count(),
        'server_delete_count': queryset.count(),
    }


def server_lifecycle_plan_page(*, plan_stage: str, page: int, page_size: int):
    queryset = server_lifecycle_plan_queryset()
    if plan_stage == 'shutdown':
        queryset = queryset.exclude(server_shutdown_complete_q())
    start, end = page_bounds(page, page_size)
    return list(queryset.order_by('actual_expires_at', 'id')[start:end])


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
    active_labels = active_cloud_account_labels()
    inactive_labels = [
        label
        for account in CloudAccountConfig.objects.filter(
            provider__in=[CloudAccountConfig.PROVIDER_AWS, CloudAccountConfig.PROVIDER_ALIYUN],
            is_active=False,
        )
        for label in cloud_account_label_variants(account)
    ]
    blank_instance_q = Q(instance_id__isnull=True) | Q(instance_id='')
    active_account_q = (
        Q(cloud_account__isnull=True, account_label__isnull=True)
        | Q(cloud_account__isnull=True, account_label='')
        | Q(cloud_account__is_active=True)
        | Q(account_label__in=active_labels)
    )
    return (
        CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER)
        .filter(broad_unattached_ip_asset_q())
        .filter(blank_instance_q)
        .filter(active_account_q)
        .exclude(Q(cloud_account__is_active=False) | Q(account_label__in=inactive_labels))
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


def unattached_ip_delete_plan_page(*, page: int, page_size: int):
    start, end = page_bounds(page, page_size)
    return list(
        unattached_ip_delete_active_queryset()
        .exclude(id__in=completed_unattached_ip_active_queryset().values('id'))
        .order_by('actual_expires_at', 'id')[start:end]
    )


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
    return {
        'ip_delete_count': max(active_count - completed_active_count, 0),
        'ip_delete_history_count': (
            CloudIpLog.objects.filter(unattached_ip_delete_history_q()).count()
            + unattached_ip_delete_history_asset_queryset().count()
            + completed_active_count
        ),
    }


def ip_delete_history_page_sources(*, page: int, page_size: int) -> list[tuple[str, object]]:
    start, end = page_bounds(page, page_size)
    items: list[tuple[str, object]] = []
    history_logs = CloudIpLog.objects.filter(unattached_ip_delete_history_q())
    log_total = history_logs.count()
    if start < log_total and len(items) < page_size:
        log_start = start
        log_end = min(end, log_total)
        traces = (
            history_logs.select_related('asset', 'order', 'user')
            .order_by('-id')[log_start:log_end]
        )
        items.extend(('log', trace) for trace in traces)

    remaining = page_size - len(items)
    if remaining <= 0:
        return items

    asset_start = max(start - log_total, 0)
    history_assets = unattached_ip_delete_history_asset_queryset().order_by('-updated_at', '-id')
    asset_total = history_assets.count()
    if asset_start < asset_total:
        asset_end = min(asset_start + remaining, asset_total)
        items.extend(('asset', asset) for asset in history_assets[asset_start:asset_end])
        remaining = page_size - len(items)
    if remaining <= 0:
        return items

    completed_start = max(start - log_total - asset_total, 0)
    completed_assets = completed_unattached_ip_active_queryset().order_by('-updated_at', '-id')
    items.extend(('asset', asset) for asset in completed_assets[completed_start:completed_start + remaining])
    return items
