"""bot 域模型。"""

from django.db import models


class TelegramUser(models.Model):
    tg_user_id = models.BigIntegerField('Telegram 用户ID', unique=True, db_index=True)
    username = models.TextField('用户名集合', blank=True, null=True)
    first_name = models.CharField('昵称', max_length=191, blank=True, null=True)
    balance = models.DecimalField('USDT余额', max_digits=18, decimal_places=6, default=0)
    balance_trx = models.DecimalField('TRX余额', max_digits=18, decimal_places=6, default=0)
    cloud_discount_rate = models.DecimalField('云服务器专属折扣', max_digits=5, decimal_places=2, default=100, help_text='百分比，100 表示无折扣，90 表示 9 折')
    cloud_reminder_muted_until = models.DateTimeField('云服务器提醒静默到', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'bot_user'
        verbose_name = 'Telegram用户'
        verbose_name_plural = 'Telegram用户'

    def __str__(self):
        return f'{self.tg_user_id} {self.primary_username or ""}'

    @staticmethod
    def normalize_usernames(value):
        if not value:
            return []
        if isinstance(value, (list, tuple)):
            merged = []
            for item in value:
                merged.extend(TelegramUser.normalize_usernames(item))
            value = ','.join(merged)
        raw = str(value).replace('，', ',').replace(' / ', ',').replace('/', ',')
        result = []
        seen = set()
        for item in raw.split(','):
            username = item.strip().lstrip('@')
            key = username.lower()
            if username and key not in seen:
                result.append(username)
                seen.add(key)
        return result

    @staticmethod
    def serialize_usernames(usernames):
        return ','.join(TelegramUser.normalize_usernames(usernames))

    def set_usernames(self, usernames):
        self.username = self.serialize_usernames(usernames)

    @property
    def usernames(self):
        return self.normalize_usernames(self.username)

    @property
    def primary_username(self):
        names = self.usernames
        return names[0] if names else ''


BotUser = TelegramUser

__all__ = [
    'BotUser',
    'TelegramUser',
]
