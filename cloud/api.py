"""cloud 域后台 API。"""

from django.views.decorators.http import require_GET

from cloud.models import AddressMonitor
from dashboard_api.views import (
    _apply_keyword_filter,
    _decimal_to_str,
    _get_keyword,
    _iso,
    _ok,
    dashboard_login_required,
    cloud_assets_list,
    cloud_order_detail,
    cloud_orders_list,
    cloud_plans_list,
    cloud_pricing_list,
    create_cloud_plan,
    delete_cloud_plan,
    servers_list,
    servers_statistics,
    sync_cloud_assets,
    sync_cloud_plans,
    sync_servers,
    update_cloud_asset,
    update_cloud_order_status,
    update_cloud_plan,
)


@dashboard_login_required
@require_GET
def monitors_list(request):
    keyword = _get_keyword(request)
    queryset = AddressMonitor.objects.select_related('user').order_by('-created_at')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['address', 'remark', 'daily_income_currency', 'daily_expense_currency', 'user__tg_user_id', 'user__username'],
    )
    items = list(
        queryset[:100].values(
            'id', 'address', 'remark', 'monitor_transfers', 'monitor_resources',
            'daily_income', 'daily_expense', 'daily_income_currency', 'daily_expense_currency',
            'stats_date', 'is_active', 'created_at', 'resource_checked_at', 'user__tg_user_id', 'user__username'
        )
    )
    return _ok([
        {
            **item,
            'daily_income': _decimal_to_str(item['daily_income']),
            'daily_expense': _decimal_to_str(item['daily_expense']),
            'created_at': _iso(item['created_at']),
            'resource_checked_at': _iso(item['resource_checked_at']),
            'tg_user_id': item.pop('user__tg_user_id', None),
            'username': item.pop('user__username', None),
        }
        for item in items
    ])


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
