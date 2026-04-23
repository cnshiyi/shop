"""过渡层：统一暴露 bot 域服务。"""

from biz.services.users import get_or_create_user

__all__ = [
    'get_or_create_user',
]
