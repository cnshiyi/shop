from django.db import models

from bot.models import TelegramUser


class BalanceLedger(models.Model):
    TYPE_MANUAL_ADJUST = 'manual_adjust'
    TYPE_RECHARGE = 'recharge'
    TYPE_ORDER_BALANCE_PAY = 'order_balance_pay'
    TYPE_CLOUD_ORDER_BALANCE_PAY = 'cloud_order_balance_pay'
    TYPE_CHOICES = (
        (TYPE_MANUAL_ADJUST, '手动调整'),
        (TYPE_RECHARGE, '充值入账'),
        (TYPE_ORDER_BALANCE_PAY, '商品余额支付'),
        (TYPE_CLOUD_ORDER_BALANCE_PAY, '云服务器余额支付'),
    )
    DIRECTION_IN = 'in'
    DIRECTION_OUT = 'out'
    DIRECTION_CHOICES = (
        (DIRECTION_IN, '收入'),
        (DIRECTION_OUT, '支出'),
    )
    CURRENCY_CHOICES = (
        ('USDT', 'USDT'),
        ('TRX', 'TRX'),
    )

    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', related_name='balance_ledgers', on_delete=models.CASCADE)
    type = models.CharField('类型', max_length=64, choices=TYPE_CHOICES, db_index=True)
    direction = models.CharField('方向', max_length=16, choices=DIRECTION_CHOICES, db_index=True)
    currency = models.CharField('币种', max_length=32, choices=CURRENCY_CHOICES, db_index=True)
    amount = models.DecimalField('变动金额', max_digits=18, decimal_places=9)
    before_balance = models.DecimalField('变动前余额', max_digits=18, decimal_places=9)
    after_balance = models.DecimalField('变动后余额', max_digits=18, decimal_places=9)
    related_type = models.CharField('关联类型', max_length=64, blank=True, null=True, db_index=True)
    related_id = models.BigIntegerField('关联ID', blank=True, null=True, db_index=True)
    description = models.TextField('说明', blank=True, null=True)
    operator = models.CharField('操作人', max_length=191, blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'order_balance_ledger'
        verbose_name = '余额流水'
        verbose_name_plural = '余额流水'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.user_id}-{self.currency}-{self.direction}-{self.amount}'


__all__ = [
    'BalanceLedger',
    'TelegramUser',
]

