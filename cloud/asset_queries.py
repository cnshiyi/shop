"""Shared CloudAsset query helpers used by APIs and management commands."""

from django.db.models import Q

from cloud.models import CloudAsset


def asset_display_ip(asset):
    return str(asset.public_ip or asset.previous_public_ip or '').strip()


def dedupe_cloud_asset_rows(assets):
    best = {}
    for asset in assets:
        ip = asset_display_ip(asset)
        key = f'ip:{ip}' if ip else f'id:{asset.id}'
        is_unattached = '未附加' in str(asset.provider_status or '') or '固定IP仍存在但未附加' in str(asset.provider_status or '')
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


def cloud_assets_base_queryset():
    unattached_ip_values = list(
        CloudAsset.objects.filter(
            kind=CloudAsset.KIND_SERVER,
            provider_status__contains='未附加固定IP',
            public_ip__isnull=False,
        ).exclude(public_ip='').values_list('public_ip', flat=True)[:1000]
    )
    return CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').filter(kind=CloudAsset.KIND_SERVER).exclude(
        Q(status__in=[CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED])
        & (Q(public_ip__in=unattached_ip_values) | Q(previous_public_ip__in=unattached_ip_values))
    )
