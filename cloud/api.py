"""过渡层：统一暴露 cloud 域后台 API。"""

from dashboard_api.views import (
    cloud_assets_list,
    cloud_order_detail,
    cloud_orders_list,
    cloud_plans_list,
    cloud_pricing_list,
    create_cloud_plan,
    delete_cloud_plan,
    monitors_list,
    servers_list,
    servers_statistics,
    sync_cloud_assets,
    sync_cloud_plans,
    sync_servers,
    update_cloud_asset,
    update_cloud_order_status,
    update_cloud_plan,
)

__all__ = [
    'cloud_assets_list',
    'cloud_order_detail',
    'cloud_orders_list',
    'cloud_plans_list',
    'cloud_pricing_list',
    'create_cloud_plan',
    'delete_cloud_plan',
    'monitors_list',
    'servers_list',
    'servers_statistics',
    'sync_cloud_assets',
    'sync_cloud_plans',
    'sync_servers',
    'update_cloud_asset',
    'update_cloud_order_status',
    'update_cloud_plan',
]
