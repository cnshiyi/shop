"""兼容聚合层：按需惰性转发到新领域服务，避免旧包聚合导致循环导入。"""

__all__ = [
    'add_monitor',
    'add_to_cart',
    'apply_cloud_server_renewal',
    'build_cloud_server_name',
    'buy_cloud_server_with_balance',
    'buy_with_balance',
    'clear_cart',
    'create_address_order',
    'create_cart_address_orders',
    'create_cart_balance_orders',
    'create_cloud_server_order',
    'create_cloud_server_renewal',
    'create_recharge',
    'delay_cloud_server_expiry',
    'delete_monitor',
    'ensure_unique_cloud_server_name',
    'get_balance_detail',
    'get_cloud_order',
    'get_cloud_plan',
    'get_cloud_server_auto_renew',
    'get_cloud_server_by_ip',
    'get_exchange_rate_display',
    'get_monitor',
    'get_or_create_user',
    'get_order',
    'get_product',
    'get_trx_price',
    'get_user_cloud_server',
    'list_balance_details',
    'list_cart_items',
    'list_cloud_orders',
    'list_custom_regions',
    'list_monitors',
    'list_orders',
    'list_products',
    'list_recharges',
    'list_region_plans',
    'list_user_cloud_servers',
    'mark_cloud_server_ip_change_requested',
    'mark_cloud_server_reinit_requested',
    'mute_cloud_reminders',
    'pay_cloud_server_order_with_balance',
    'pay_cloud_server_renewal_with_balance',
    'rebind_cloud_server_user',
    'record_balance_ledger',
    'refresh_custom_plan_cache',
    'remove_cart_item',
    'set_cloud_server_auto_renew',
    'set_cloud_server_port',
    'set_monitor_threshold',
    'toggle_monitor_flag',
    'usdt_to_trx',
]


def __getattr__(name):
    if name == 'record_balance_ledger':
        from orders.ledger import record_balance_ledger
        return record_balance_ledger

    if name in {
        'add_to_cart', 'buy_with_balance', 'clear_cart', 'create_address_order',
        'create_cart_address_orders', 'create_cart_balance_orders', 'get_balance_detail',
        'get_cloud_order', 'get_order', 'get_product', 'list_balance_details',
        'list_cart_items', 'list_cloud_orders', 'list_orders', 'list_products',
        'remove_cart_item',
    }:
        from . import commerce
        return getattr(commerce, name)

    if name in {
        'create_cloud_server_order', 'buy_cloud_server_with_balance', 'pay_cloud_server_order_with_balance',
        'get_cloud_plan', 'list_custom_regions', 'list_region_plans', 'refresh_custom_plan_cache',
        'set_cloud_server_port', 'build_cloud_server_name', 'ensure_unique_cloud_server_name',
    }:
        from . import custom
        return getattr(custom, name)

    if name in {
        'apply_cloud_server_renewal', 'create_cloud_server_renewal', 'delay_cloud_server_expiry',
        'get_cloud_server_auto_renew', 'mark_cloud_server_ip_change_requested',
        'mark_cloud_server_reinit_requested', 'mute_cloud_reminders',
        'pay_cloud_server_renewal_with_balance', 'rebind_cloud_server_user', 'set_cloud_server_auto_renew',
    }:
        from . import cloud_servers
        return getattr(cloud_servers, name)

    if name in {
        'add_monitor', 'delete_monitor', 'get_monitor', 'list_monitors',
        'set_monitor_threshold', 'toggle_monitor_flag',
    }:
        from . import monitoring
        return getattr(monitoring, name)

    if name in {'create_recharge', 'list_recharges'}:
        from . import payments
        return getattr(payments, name)

    if name in {'get_exchange_rate_display', 'get_trx_price', 'usdt_to_trx'}:
        from . import rates
        return getattr(rates, name)

    raise AttributeError(name)
