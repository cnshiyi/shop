"""User-facing cloud asset statistics for dashboard views."""

from django.db.models import Q

from cloud.models import CloudAsset
from core.cloud_accounts import cloud_account_label_variants
from core.models import CloudAccountConfig


def active_cloud_asset_queryset():
    active_accounts = list(CloudAccountConfig.objects.filter(is_active=True))
    inactive_accounts = list(CloudAccountConfig.objects.filter(is_active=False))
    active_account_ids = [account.id for account in active_accounts]
    active_account_labels = [
        label
        for account in active_accounts
        for label in cloud_account_label_variants(account)
    ]
    inactive_account_labels = [
        label
        for account in inactive_accounts
        for label in cloud_account_label_variants(account)
    ]
    return (
        CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'order__cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER, is_active=True)
        .exclude(status__in=[
            CloudAsset.STATUS_DELETED,
            CloudAsset.STATUS_DELETING,
            CloudAsset.STATUS_TERMINATED,
            CloudAsset.STATUS_TERMINATING,
        ])
        .exclude(Q(cloud_account__is_active=False) | Q(account_label__in=inactive_account_labels))
        .filter(
            Q(cloud_account_id__in=active_account_ids)
            | Q(account_label__in=active_account_labels)
            | Q(cloud_account__isnull=True, account_label__isnull=True)
            | Q(cloud_account__isnull=True, account_label='')
            | Q(cloud_account__isnull=True, account_label__in=active_account_labels)
            | Q(cloud_account_id__isnull=False, cloud_account__is_active=True)
        )
    )


def proxy_asset_count(asset):
    return 1 if asset.kind == CloudAsset.KIND_SERVER else 0


def active_proxy_counts_by_user(user_ids=None):
    qs = active_cloud_asset_queryset().filter(
        Q(user_id__isnull=False) | Q(order__user_id__isnull=False)
    ).select_related('order')
    if user_ids is not None:
        user_ids = set(user_ids)
        qs = qs.filter(Q(user_id__in=user_ids) | Q(order__user_id__in=user_ids))
    counts = {}
    for asset in qs:
        user_id = asset.user_id or (asset.order.user_id if asset.order_id and asset.order else None)
        if not user_id:
            continue
        counts[user_id] = counts.get(user_id, 0) + proxy_asset_count(asset)
    return counts
