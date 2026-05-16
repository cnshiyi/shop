"""统一暴露支付扫描与链上资源巡检接口。"""

from cloud.resource_monitor import (
    check_resources,
    get_resource_detail as get_cloud_resource_detail,
    set_bot as set_resource_bot,
)
from orders.payment_scanner import (
    get_resource_detail as get_payment_resource_detail,
    get_tx_detail,
    scan_block,
    scan_forever,
    set_bot,
)


def get_resource_detail(detail_key: str) -> dict | None:
    return get_cloud_resource_detail(detail_key) or get_payment_resource_detail(detail_key)

__all__ = [
    'check_resources',
    'get_resource_detail',
    'get_tx_detail',
    'scan_block',
    'scan_forever',
    'set_bot',
    'set_resource_bot',
]
