"""兼容层：用户同步实现已迁入 `bot.services`。"""

from bot.services import get_or_create_user

__all__ = [
    'get_or_create_user',
]
