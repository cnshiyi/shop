"""过渡层：统一暴露 bot 域后台 API。"""

from dashboard_api.views import (
    create_cloud_account,
    create_product,
    csrf,
    delete_cloud_account,
    me,
    site_config_groups,
    site_configs_list,
    update_cloud_account,
    update_site_config,
    update_user_balance,
    update_user_discount,
    user_balance_details,
    user_info,
    users_list,
    verify_cloud_account,
)

__all__ = [
    'create_cloud_account',
    'create_product',
    'csrf',
    'delete_cloud_account',
    'me',
    'site_config_groups',
    'site_configs_list',
    'update_cloud_account',
    'update_site_config',
    'update_user_balance',
    'update_user_discount',
    'user_balance_details',
    'user_info',
    'users_list',
    'verify_cloud_account',
]
