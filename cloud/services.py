"""过渡层：统一暴露 cloud 域服务，后续逐步从 biz/services 迁入这里。"""

from cloud.models import CloudIpLog
from biz.services.cloud_servers import (
    apply_cloud_server_renewal,
    create_cloud_server_renewal,
    delay_cloud_server_expiry,
    get_cloud_server_auto_renew,
    mark_cloud_server_ip_change_requested,
    mark_cloud_server_reinit_requested,
    mute_cloud_reminders,
    pay_cloud_server_renewal_with_balance,
    rebind_cloud_server_user,
    set_cloud_server_auto_renew,
)
from biz.services.custom import (
    build_cloud_server_name,
    buy_cloud_server_with_balance,
    create_cloud_server_order,
    ensure_cloud_server_pricing,
    ensure_unique_cloud_server_name,
    get_cloud_plan,
    list_custom_regions,
    list_region_plans,
    pay_cloud_server_order_with_balance,
    refresh_custom_plan_cache,
    set_cloud_server_port,
)

def record_cloud_ip_log(*, event_type, order=None, asset=None, server=None, public_ip=None, previous_public_ip=None, note=''):
    asset_obj = asset
    server_obj = server
    order_obj = order or getattr(asset_obj, 'order', None) or getattr(server_obj, 'order', None)
    user_obj = (
        getattr(order_obj, 'user', None)
        or getattr(asset_obj, 'user', None)
        or getattr(server_obj, 'user', None)
    )
    provider = (
        getattr(order_obj, 'provider', None)
        or getattr(asset_obj, 'provider', None)
        or getattr(server_obj, 'provider', None)
    )
    region_code = (
        getattr(order_obj, 'region_code', None)
        or getattr(asset_obj, 'region_code', None)
        or getattr(server_obj, 'region_code', None)
    )
    region_name = (
        getattr(order_obj, 'region_name', None)
        or getattr(asset_obj, 'region_name', None)
        or getattr(server_obj, 'region_name', None)
    )
    asset_name = (
        getattr(asset_obj, 'asset_name', None)
        or getattr(server_obj, 'server_name', None)
        or getattr(order_obj, 'server_name', None)
    )
    instance_id = (
        getattr(asset_obj, 'instance_id', None)
        or getattr(server_obj, 'instance_id', None)
        or getattr(order_obj, 'instance_id', None)
    )
    provider_resource_id = (
        getattr(asset_obj, 'provider_resource_id', None)
        or getattr(server_obj, 'provider_resource_id', None)
        or getattr(order_obj, 'provider_resource_id', None)
    )
    current_ip = public_ip
    if current_ip is None:
        current_ip = (
            getattr(asset_obj, 'public_ip', None)
            or getattr(server_obj, 'public_ip', None)
            or getattr(order_obj, 'public_ip', None)
        )
    previous_ip = previous_public_ip
    if previous_ip is None:
        previous_ip = (
            getattr(asset_obj, 'previous_public_ip', None)
            or getattr(server_obj, 'previous_public_ip', None)
            or getattr(order_obj, 'previous_public_ip', None)
        )
    return CloudIpLog.objects.create(
        order=order_obj,
        asset=asset_obj,
        server=server_obj,
        user=user_obj,
        provider=provider,
        region_code=region_code,
        region_name=region_name,
        order_no=getattr(order_obj, 'order_no', None),
        asset_name=asset_name,
        instance_id=instance_id,
        provider_resource_id=provider_resource_id,
        public_ip=current_ip,
        previous_public_ip=previous_ip,
        event_type=event_type,
        note=note or '',
    )


__all__ = [
    'apply_cloud_server_renewal',
    'build_cloud_server_name',
    'buy_cloud_server_with_balance',
    'create_cloud_server_order',
    'create_cloud_server_renewal',
    'delay_cloud_server_expiry',
    'ensure_cloud_server_pricing',
    'ensure_unique_cloud_server_name',
    'get_cloud_plan',
    'get_cloud_server_auto_renew',
    'list_custom_regions',
    'list_region_plans',
    'mark_cloud_server_ip_change_requested',
    'mark_cloud_server_reinit_requested',
    'mute_cloud_reminders',
    'pay_cloud_server_order_with_balance',
    'pay_cloud_server_renewal_with_balance',
    'rebind_cloud_server_user',
    'refresh_custom_plan_cache',
    'record_cloud_ip_log',
    'set_cloud_server_auto_renew',
    'set_cloud_server_port',
]
