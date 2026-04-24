"""兼容层：云服务实现已迁入 `cloud.services`。"""

from django.utils import timezone

from cloud.models import CloudAsset, CloudServerOrder, CloudServerPlan, Server
from cloud.services import (
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

__all__ = [
    'apply_cloud_server_renewal',
    'create_cloud_server_renewal',
    'delay_cloud_server_expiry',
    'get_cloud_server_auto_renew',
    'mark_cloud_server_ip_change_requested',
    'mark_cloud_server_reinit_requested',
    'mute_cloud_reminders',
    'pay_cloud_server_renewal_with_balance',
    'rebind_cloud_server_user',
    'set_cloud_server_auto_renew',
]
