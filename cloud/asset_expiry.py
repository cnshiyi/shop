from django.utils import timezone

from cloud.lifecycle_schedule import compute_order_lifecycle_schedule, normalize_service_expiry
from cloud.models import CloudAsset


def order_primary_asset(order):
    if not order or not getattr(order, 'pk', None):
        return None
    return (
        CloudAsset.objects.filter(order_id=order.pk, kind=CloudAsset.KIND_SERVER)
        .order_by('-sort_order', 'actual_expires_at', '-updated_at', '-id')
        .first()
    )


def order_asset_expiry(order, asset=None):
    asset = asset or order_primary_asset(order)
    return getattr(asset, 'actual_expires_at', None)


def compute_expiry_from_start(started_at, lifecycle_days):
    if not started_at:
        return None
    return normalize_service_expiry(started_at + timezone.timedelta(days=lifecycle_days or 31))


def apply_order_lifecycle_from_asset_expiry(order, expires_at, *, save=True, update_fields=None):
    expires_at = normalize_service_expiry(expires_at)
    if not order or not expires_at:
        return expires_at
    schedule = compute_order_lifecycle_schedule(expires_at)
    order.renew_grace_expires_at = schedule.renew_grace_expires_at
    order.suspend_at = schedule.suspend_at
    order.delete_at = schedule.delete_at
    order.ip_recycle_at = schedule.ip_recycle_at
    if save and getattr(order, 'pk', None):
        fields = set(update_fields or [])
        fields.update({'renew_grace_expires_at', 'suspend_at', 'delete_at', 'ip_recycle_at', 'updated_at'})
        order.save(update_fields=list(fields))
    return expires_at


def set_order_asset_expiry(order, expires_at, *, asset=None, update_lifecycle=True):
    expires_at = normalize_service_expiry(expires_at)
    if not order or not getattr(order, 'pk', None):
        return expires_at
    if update_lifecycle:
        apply_order_lifecycle_from_asset_expiry(order, expires_at, save=True)
    queryset = CloudAsset.objects.filter(order_id=order.pk, kind=CloudAsset.KIND_SERVER)
    if asset is not None:
        queryset = queryset.filter(pk=asset.pk)
    queryset.update(actual_expires_at=expires_at, updated_at=timezone.now())
    return expires_at
