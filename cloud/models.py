"""过渡层：统一暴露云资源域模型，后续逐步从 mall/monitoring 迁入这里。"""

from mall.models import CloudAsset, CloudServerOrder, CloudServerPlan, Server, ServerPrice
from monitoring.models import AddressMonitor, DailyAddressStat, ResourceSnapshot

__all__ = [
    'AddressMonitor',
    'CloudAsset',
    'CloudServerOrder',
    'CloudServerPlan',
    'DailyAddressStat',
    'ResourceSnapshot',
    'Server',
    'ServerPrice',
]
