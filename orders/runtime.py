"""过渡层：统一暴露支付/链上运行时接口，后续逐步替代 tron.*。"""

from tron.resource_checker import check_resources, get_resource_detail, set_bot as set_resource_bot
from tron.scanner import get_tx_detail, scan_block, set_bot

__all__ = [
    'check_resources',
    'get_resource_detail',
    'get_tx_detail',
    'scan_block',
    'set_bot',
    'set_resource_bot',
]
