"""CloudAsset hard dedupe helpers."""

from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction

from cloud.asset_queries import asset_display_ip
from cloud.models import (
    CloudAsset,
    CloudAssetDashboardSnapshot,
    CloudIpLog,
    CloudLifecyclePlanNote,
    CloudLifecycleTask,
    CloudNoticeTask,
)


@dataclass
class CloudAssetDedupeResult:
    merged_groups: int = 0
    deleted_assets: int = 0
    relinked_ip_logs: int = 0
    relinked_plan_notes: int = 0
    relinked_lifecycle_tasks: int = 0
    relinked_notice_tasks: int = 0
    deleted_snapshots: int = 0
    details: list[str] | None = None

    def as_dict(self) -> dict:
        return {
            'merged_groups': self.merged_groups,
            'deleted_assets': self.deleted_assets,
            'relinked_ip_logs': self.relinked_ip_logs,
            'relinked_plan_notes': self.relinked_plan_notes,
            'relinked_lifecycle_tasks': self.relinked_lifecycle_tasks,
            'relinked_notice_tasks': self.relinked_notice_tasks,
            'deleted_snapshots': self.deleted_snapshots,
            'details': self.details or [],
        }


def cloud_asset_dedupe_score(asset: CloudAsset):
    is_unattached = '未附加' in str(asset.provider_status or '') or '固定IP仍存在但未附加' in str(asset.provider_status or '')
    is_deleted = asset.status in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED}
    return (
        1 if asset.kind == CloudAsset.KIND_SERVER else 0,
        3 if is_unattached else 0,
        2 if asset.status == CloudAsset.STATUS_DELETING else 0,
        1 if not is_deleted else 0,
        1 if asset.order_id else 0,
        1 if asset.user_id else 0,
        asset.updated_at.timestamp() if asset.updated_at else 0,
        asset.id,
    )


def _copy_if_empty(keeper: CloudAsset, duplicate: CloudAsset, field: str, update_fields: set[str]):
    if getattr(keeper, field, None):
        return
    value = getattr(duplicate, field, None)
    if value in (None, '', []):
        return
    setattr(keeper, field, value)
    update_fields.add(field)


def _merge_asset_fields(keeper: CloudAsset, duplicates: list[CloudAsset]) -> list[str]:
    update_fields = set()
    copied_fields = [
        'provider',
        'cloud_account_id',
        'account_label',
        'region_code',
        'region_name',
        'asset_name',
        'instance_id',
        'provider_resource_id',
        'previous_public_ip',
        'login_user',
        'login_password',
        'mtproxy_port',
        'mtproxy_link',
        'proxy_links',
        'mtproxy_secret',
        'mtproxy_host',
        'actual_expires_at',
        'price',
        'currency',
        'order_id',
        'user_id',
        'telegram_group_id',
        'note',
        'provider_status',
    ]
    for duplicate in duplicates:
        for field in copied_fields:
            _copy_if_empty(keeper, duplicate, field, update_fields)
        if not keeper.shutdown_enabled and duplicate.shutdown_enabled:
            keeper.shutdown_enabled = True
            update_fields.add('shutdown_enabled')
        if not keeper.server_delete_enabled and duplicate.server_delete_enabled:
            keeper.server_delete_enabled = True
            update_fields.add('server_delete_enabled')
        if not keeper.ip_delete_enabled and duplicate.ip_delete_enabled:
            keeper.ip_delete_enabled = True
            update_fields.add('ip_delete_enabled')
        if duplicate.sort_order and duplicate.sort_order > keeper.sort_order:
            keeper.sort_order = duplicate.sort_order
            update_fields.add('sort_order')
    if update_fields:
        update_fields.add('updated_at')
        keeper.save(update_fields=list(update_fields))
    return sorted(update_fields)


def merge_duplicate_cloud_asset_group(assets: list[CloudAsset]) -> CloudAssetDedupeResult:
    result = CloudAssetDedupeResult(details=[])
    alive_assets = [asset for asset in assets if getattr(asset, 'id', None)]
    if len(alive_assets) <= 1:
        return result
    ordered = sorted(alive_assets, key=cloud_asset_dedupe_score, reverse=True)
    keeper = ordered[0]
    duplicates = ordered[1:]
    duplicate_ids = [asset.id for asset in duplicates]
    display_ip = asset_display_ip(keeper) or asset_display_ip(duplicates[0])
    with transaction.atomic():
        _merge_asset_fields(keeper, duplicates)
        result.relinked_ip_logs += CloudIpLog.objects.filter(asset_id__in=duplicate_ids).update(asset=keeper)
        result.relinked_plan_notes += CloudLifecyclePlanNote.objects.filter(asset_id__in=duplicate_ids).update(asset=keeper)
        result.relinked_lifecycle_tasks += CloudLifecycleTask.objects.filter(asset_id__in=duplicate_ids).update(asset=keeper)
        result.relinked_notice_tasks += CloudNoticeTask.objects.filter(asset_id__in=duplicate_ids).update(asset=keeper)
        deleted_snapshots = CloudAssetDashboardSnapshot.objects.filter(asset_id__in=duplicate_ids).delete()[0]
        deleted_assets = CloudAsset.objects.filter(id__in=duplicate_ids).delete()[0]
    result.merged_groups = 1 if deleted_assets else 0
    result.deleted_assets = deleted_assets
    result.deleted_snapshots = deleted_snapshots
    result.details.append(f'ip={display_ip or "-"} 保留#{keeper.id} 删除{duplicate_ids}')
    return result


def merge_duplicate_cloud_assets_by_ip(*, asset_ids=None, public_ips=None) -> CloudAssetDedupeResult:
    queryset = CloudAsset.objects.all()
    scoped_ips = {str(value or '').strip() for value in (public_ips or []) if str(value or '').strip()}
    if asset_ids:
        scoped_assets = list(queryset.filter(id__in=list(asset_ids)))
        scoped_ips.update(asset_display_ip(asset) for asset in scoped_assets if asset_display_ip(asset))
        if not scoped_ips:
            return CloudAssetDedupeResult(details=[])
    if scoped_ips:
        queryset = queryset.filter(public_ip__in=scoped_ips)
    queryset = queryset.exclude(public_ip__isnull=True).exclude(public_ip='')
    groups: dict[str, list[CloudAsset]] = {}
    for asset in queryset.select_related('order', 'user').order_by('public_ip', '-updated_at', '-id'):
        ip = str(asset.public_ip or '').strip()
        if not ip:
            continue
        groups.setdefault(ip, []).append(asset)

    result = CloudAssetDedupeResult(details=[])
    for assets in groups.values():
        if len(assets) <= 1:
            continue
        partial = merge_duplicate_cloud_asset_group(assets)
        result.merged_groups += partial.merged_groups
        result.deleted_assets += partial.deleted_assets
        result.relinked_ip_logs += partial.relinked_ip_logs
        result.relinked_plan_notes += partial.relinked_plan_notes
        result.relinked_lifecycle_tasks += partial.relinked_lifecycle_tasks
        result.relinked_notice_tasks += partial.relinked_notice_tasks
        result.deleted_snapshots += partial.deleted_snapshots
        result.details.extend(partial.details or [])
    return result
