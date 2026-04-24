"""兼容层：云查询实现已迁入 `cloud.services`。"""

from cloud.services import get_cloud_server_by_ip, get_user_cloud_server, list_user_cloud_servers

__all__ = [
    'get_cloud_server_by_ip',
    'get_user_cloud_server',
    'list_user_cloud_servers',
]
