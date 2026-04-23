"""过渡层：统一暴露云监控缓存接口，后续逐步替代 monitoring.cache。"""

from monitoring.cache import (
    add_monitor_to_cache,
    get_monitor_addresses,
    init_monitor_cache,
    maybe_sync_monitors,
    remove_monitor_from_cache,
    update_monitor_flag_in_cache,
    update_monitor_threshold_in_cache,
)

__all__ = [
    'add_monitor_to_cache',
    'get_monitor_addresses',
    'init_monitor_cache',
    'maybe_sync_monitors',
    'remove_monitor_from_cache',
    'update_monitor_flag_in_cache',
    'update_monitor_threshold_in_cache',
]
