from asgiref.sync import sync_to_async
from django.db.models import Q

from cloud.models import CloudAsset, CloudServerOrder, Server


_ACTIVE_ORDER_STATUSES = {'completed', 'expiring', 'suspended', 'renew_pending', 'paid', 'provisioning'}
_INACTIVE_ASSET_STATUSES = {'deleted', 'deleting', 'terminated', 'terminating', 'expired'}


@sync_to_async
def list_user_cloud_servers(user_id: int):
    return list(
        CloudServerOrder.objects.filter(user_id=user_id)
        .exclude(status__in=['deleted', 'deleting', 'expired'])
        .order_by('-created_at')
    )


@sync_to_async
def get_user_cloud_server(order_id: int, user_id: int):
    return CloudServerOrder.objects.filter(id=order_id, user_id=user_id).exclude(status__in=['deleted', 'deleting', 'expired']).first()


@sync_to_async
def get_cloud_server_by_ip(ip: str):
    normalized_ip = (ip or '').strip()
    if not normalized_ip:
        return None
    asset = CloudAsset.objects.filter(
        Q(public_ip=normalized_ip) | Q(previous_public_ip=normalized_ip)
    ).exclude(status__in=_INACTIVE_ASSET_STATUSES).select_related('order').order_by('-updated_at', '-id').first()
    if asset and asset.order_id and asset.order and asset.order.status in _ACTIVE_ORDER_STATUSES:
        return asset.order
    server = Server.objects.filter(
        Q(public_ip=normalized_ip) | Q(previous_public_ip=normalized_ip)
    ).exclude(status__in=_INACTIVE_ASSET_STATUSES).select_related('order').order_by('-updated_at', '-id').first()
    if server and server.order_id and server.order and server.order.status in _ACTIVE_ORDER_STATUSES:
        return server.order
    return CloudServerOrder.objects.filter(
        Q(public_ip=normalized_ip) | Q(previous_public_ip=normalized_ip),
        status__in=_ACTIVE_ORDER_STATUSES,
    ).order_by('-created_at').first()
