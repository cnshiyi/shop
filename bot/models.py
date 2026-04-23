"""过渡层：统一暴露 bot 域模型，后续逐步从 accounts/bot 迁入这里。"""

from accounts.models import BalanceLedger, TelegramUser, TelegramUsername

BotUser = TelegramUser

__all__ = [
    'BalanceLedger',
    'BotUser',
    'TelegramUser',
    'TelegramUsername',
]
