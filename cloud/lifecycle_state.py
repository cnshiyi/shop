"""Cloud order and asset state mapping."""

from cloud.models import CloudAsset

ACTIVE_ORDER_STATUSES = {'completed', 'renew_pending', 'expiring'}
INACTIVE_ORDER_STATUSES = {'failed', 'cancelled', 'expired', 'deleted', 'suspended', 'deleting', 'pending'}

ORDER_TO_ASSET_STATUS = {
    'completed': CloudAsset.STATUS_RUNNING,
    'renew_pending': CloudAsset.STATUS_RUNNING,
    'expiring': CloudAsset.STATUS_RUNNING,
    'deleted': CloudAsset.STATUS_DELETED,
    'deleting': CloudAsset.STATUS_DELETING,
    'expired': CloudAsset.STATUS_EXPIRED,
    'suspended': CloudAsset.STATUS_STOPPED,
    'failed': CloudAsset.STATUS_UNKNOWN,
    'cancelled': CloudAsset.STATUS_UNKNOWN,
    'pending': CloudAsset.STATUS_PENDING,
}

TERMINAL_ASSET_STATUSES = {
    CloudAsset.STATUS_DELETED,
    CloudAsset.STATUS_DELETING,
    CloudAsset.STATUS_TERMINATED,
    CloudAsset.STATUS_TERMINATING,
    CloudAsset.STATUS_EXPIRED,
}

RETAINED_IP_ASSET_STATUSES = {
    CloudAsset.STATUS_DELETED,
    CloudAsset.STATUS_DELETING,
}


def primary_record_updates_for_order_status(order_status: str) -> tuple[dict, dict]:
    status = str(order_status or '').strip()
    if status in ACTIVE_ORDER_STATUSES:
        updates = {
            'is_active': True,
            'status': ORDER_TO_ASSET_STATUS[status],
        }
        return updates, dict(updates)
    if status in INACTIVE_ORDER_STATUSES:
        updates = {
            'is_active': False,
            'status': ORDER_TO_ASSET_STATUS.get(status, CloudAsset.STATUS_UNKNOWN),
        }
        return updates, dict(updates)
    return {}, {}


def order_status_from_cloud_status(status: str, *, expires_at=None, now=None, default='completed') -> str:
    from django.utils import timezone

    cloud_status = str(status or '').strip()
    if cloud_status in {CloudAsset.STATUS_PENDING, CloudAsset.STATUS_STARTING}:
        return 'provisioning'
    if cloud_status in {CloudAsset.STATUS_STOPPED, CloudAsset.STATUS_STOPPING, CloudAsset.STATUS_SUSPENDED}:
        return 'suspended'
    if cloud_status in {CloudAsset.STATUS_TERMINATING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_DELETED}:
        return 'deleted'
    if cloud_status == CloudAsset.STATUS_EXPIRED:
        return 'expired'
    if expires_at and expires_at <= (now or timezone.now()):
        return 'expiring'
    return default
