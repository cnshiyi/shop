"""兼容聚合层：仅保留旧入口到新域服务的最薄惰性映射。"""

_EXPORTS = {
    'record_balance_ledger': ('orders.ledger', 'record_balance_ledger'),
    'add_to_cart': ('biz.services.commerce', 'add_to_cart'),
    'buy_with_balance': ('biz.services.commerce', 'buy_with_balance'),
    'clear_cart': ('biz.services.commerce', 'clear_cart'),
    'create_address_order': ('biz.services.commerce', 'create_address_order'),
    'create_cart_address_orders': ('biz.services.commerce', 'create_cart_address_orders'),
    'create_cart_balance_orders': ('biz.services.commerce', 'create_cart_balance_orders'),
    'get_balance_detail': ('biz.services.commerce', 'get_balance_detail'),
    'get_cloud_order': ('biz.services.commerce', 'get_cloud_order'),
    'get_order': ('biz.services.commerce', 'get_order'),
    'get_product': ('biz.services.commerce', 'get_product'),
    'list_balance_details': ('biz.services.commerce', 'list_balance_details'),
    'list_cart_items': ('biz.services.commerce', 'list_cart_items'),
    'list_cloud_orders': ('biz.services.commerce', 'list_cloud_orders'),
    'list_orders': ('biz.services.commerce', 'list_orders'),
    'list_products': ('biz.services.commerce', 'list_products'),
    'remove_cart_item': ('biz.services.commerce', 'remove_cart_item'),
    'build_cloud_server_name': ('biz.services.custom', 'build_cloud_server_name'),
    'buy_cloud_server_with_balance': ('biz.services.custom', 'buy_cloud_server_with_balance'),
    'create_cloud_server_order': ('biz.services.custom', 'create_cloud_server_order'),
    'ensure_unique_cloud_server_name': ('biz.services.custom', 'ensure_unique_cloud_server_name'),
    'get_cloud_plan': ('biz.services.custom', 'get_cloud_plan'),
    'list_custom_regions': ('biz.services.custom', 'list_custom_regions'),
    'list_region_plans': ('biz.services.custom', 'list_region_plans'),
    'pay_cloud_server_order_with_balance': ('biz.services.custom', 'pay_cloud_server_order_with_balance'),
    'refresh_custom_plan_cache': ('biz.services.custom', 'refresh_custom_plan_cache'),
    'set_cloud_server_port': ('biz.services.custom', 'set_cloud_server_port'),
    'apply_cloud_server_renewal': ('biz.services.cloud_servers', 'apply_cloud_server_renewal'),
    'create_cloud_server_renewal': ('biz.services.cloud_servers', 'create_cloud_server_renewal'),
    'delay_cloud_server_expiry': ('biz.services.cloud_servers', 'delay_cloud_server_expiry'),
    'get_cloud_server_auto_renew': ('biz.services.cloud_servers', 'get_cloud_server_auto_renew'),
    'mark_cloud_server_ip_change_requested': ('biz.services.cloud_servers', 'mark_cloud_server_ip_change_requested'),
    'mark_cloud_server_reinit_requested': ('biz.services.cloud_servers', 'mark_cloud_server_reinit_requested'),
    'mute_cloud_reminders': ('biz.services.cloud_servers', 'mute_cloud_reminders'),
    'pay_cloud_server_renewal_with_balance': ('biz.services.cloud_servers', 'pay_cloud_server_renewal_with_balance'),
    'rebind_cloud_server_user': ('biz.services.cloud_servers', 'rebind_cloud_server_user'),
    'set_cloud_server_auto_renew': ('biz.services.cloud_servers', 'set_cloud_server_auto_renew'),
    'add_monitor': ('biz.services.monitoring', 'add_monitor'),
    'delete_monitor': ('biz.services.monitoring', 'delete_monitor'),
    'get_monitor': ('biz.services.monitoring', 'get_monitor'),
    'list_monitors': ('biz.services.monitoring', 'list_monitors'),
    'set_monitor_threshold': ('biz.services.monitoring', 'set_monitor_threshold'),
    'toggle_monitor_flag': ('biz.services.monitoring', 'toggle_monitor_flag'),
    'create_recharge': ('biz.services.payments', 'create_recharge'),
    'list_recharges': ('biz.services.payments', 'list_recharges'),
    'get_exchange_rate_display': ('biz.services.rates', 'get_exchange_rate_display'),
    'get_trx_price': ('biz.services.rates', 'get_trx_price'),
    'usdt_to_trx': ('biz.services.rates', 'usdt_to_trx'),
}

__all__ = sorted(_EXPORTS)


def __getattr__(name):
    import importlib
    target = _EXPORTS.get(name)
    if not target:
        raise AttributeError(name)
    module_name, attr_name = target
    return getattr(importlib.import_module(module_name), attr_name)
