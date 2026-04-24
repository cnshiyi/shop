from django.urls import path

from bot import api as bot_api
from cloud import api as cloud_api
from orders import api as orders_api
from . import views

app_name = 'dashboard_api'

urlpatterns = [
    path('csrf/', bot_api.csrf, name='csrf'),
    path('auth/login', bot_api.auth_login, name='auth-login'),
    path('auth/logout', bot_api.auth_logout, name='auth-logout'),
    path('auth/refresh', bot_api.auth_refresh, name='auth-refresh'),
    path('auth/codes', bot_api.auth_codes, name='auth-codes'),
    path('user/info', bot_api.user_info, name='user-info'),
    path('dashboard/me/', bot_api.me, name='me'),
    path('dashboard/overview/', bot_api.overview, name='overview'),
    path('users/', bot_api.users_list, name='users-list'),
    path('users/<int:user_id>/balance/', bot_api.update_user_balance, name='user-balance-update'),
    path('users/<int:user_id>/discount/', bot_api.update_user_discount, name='user-discount-update'),
    path('users/<int:user_id>/balance-details/', bot_api.user_balance_details, name='user-balance-details'),
    path('products/', bot_api.products_list, name='products-list'),
    path('orders/', orders_api.orders_list, name='orders-list'),
    path('tasks/', cloud_api.tasks_overview, name='tasks-overview'),
    path('task-list/', cloud_api.tasks_overview, name='task-list-compat'),
    path('products/create/', bot_api.create_product, name='product-create'),
    path('products/<int:product_id>/', bot_api.update_product, name='product-update'),
    path('cloud-assets/', cloud_api.cloud_assets_list, name='cloud-assets-list'),
    path('cloud-assets/<int:asset_id>/', cloud_api.update_cloud_asset, name='cloud-asset-update'),
    path('cloud-assets/sync/', cloud_api.sync_cloud_assets, name='cloud-assets-sync'),
    path('cloud-orders/', cloud_api.cloud_orders_list, name='cloud-orders-list'),
    path('cloud-orders/<int:order_id>/', cloud_api.cloud_order_detail, name='cloud-order-detail'),
    path('cloud-orders/<int:order_id>/status/', cloud_api.update_cloud_order_status, name='cloud-order-status-update'),
    path('servers/', cloud_api.servers_list, name='servers-list'),
    path('servers/<int:server_id>/delete/', cloud_api.delete_server, name='server-delete'),
    path('servers/statistics/', cloud_api.servers_statistics, name='servers-statistics'),
    path('servers/sync/', cloud_api.sync_servers, name='servers-sync'),
    path('settings/site-configs/', bot_api.site_configs_list, name='site-configs-list'),
    path('settings/site-configs/groups/', bot_api.site_config_groups, name='site-config-groups'),
    path('settings/site-configs/init/', bot_api.init_site_configs, name='site-configs-init'),
    path('settings/site-configs/<int:config_id>/', bot_api.update_site_config, name='site-config-update'),
    path('settings/cloud-accounts/', bot_api.cloud_accounts_list, name='cloud-accounts-list'),
    path('settings/cloud-accounts/create/', bot_api.create_cloud_account, name='cloud-account-create'),
    path('settings/cloud-accounts/<int:account_id>/', bot_api.update_cloud_account, name='cloud-account-update'),
    path('settings/cloud-accounts/<int:account_id>/delete/', bot_api.delete_cloud_account, name='cloud-account-delete'),
    path('settings/cloud-accounts/<int:account_id>/verify/', bot_api.verify_cloud_account, name='cloud-account-verify'),
    path('cloud-plans/', cloud_api.cloud_plans_list, name='cloud-plans-list'),
    path('cloud-pricing/', cloud_api.cloud_pricing_list, name='cloud-pricing-list'),
    path('plan-settings/', cloud_api.cloud_plans_list, name='plan-settings-compat'),
    path('cloud-plans/sync/', cloud_api.sync_cloud_plans, name='cloud-plans-sync'),
    path('cloud-plans/create/', cloud_api.create_cloud_plan, name='cloud-plan-create'),
    path('cloud-plans/<int:plan_id>/', cloud_api.update_cloud_plan, name='cloud-plan-update'),
    path('cloud-plans/<int:plan_id>/delete/', cloud_api.delete_cloud_plan, name='cloud-plan-delete'),
    path('recharges/', orders_api.recharges_list, name='recharges-list'),
    path('recharges/<int:recharge_id>/', orders_api.recharge_detail, name='recharge-detail'),
    path('recharges/<int:recharge_id>/status/', orders_api.update_recharge_status, name='recharge-status-update'),
    path('monitors/', cloud_api.monitors_list, name='monitors-list'),
]
