from django.db import models


class TelegramUser(models.Model):
    tg_user_id = models.BigIntegerField('Telegram 用户ID', unique=True, db_index=True)
    username = models.CharField('用户名', max_length=191, blank=True, null=True)
    first_name = models.CharField('昵称', max_length=191, blank=True, null=True)
    balance = models.DecimalField('USDT余额', max_digits=18, decimal_places=6, default=0)
    balance_trx = models.DecimalField('TRX余额', max_digits=18, decimal_places=6, default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'users'
        verbose_name = 'Telegram用户'
        verbose_name_plural = 'Telegram用户'

    def __str__(self):
        return f'{self.tg_user_id} {self.username or ""}'
