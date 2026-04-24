"""orders 域模型。"""

from django.db import models

from mall.models import CartItem, Order, Product


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


class Recharge(models.Model):
    STATUS_CHOICES = (
        ('pending', '待支付'),
        ('completed', '已完成'),
        ('expired', '已过期'),
    )
    CURRENCY_CHOICES = (
        ('USDT', 'USDT'),
        ('TRX', 'TRX'),
    )

    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.CASCADE)
    currency = models.CharField('币种', max_length=32, choices=CURRENCY_CHOICES, default='USDT', db_index=True)
    amount = models.DecimalField('充值金额', max_digits=18, decimal_places=6)
    pay_amount = models.DecimalField('支付金额', max_digits=18, decimal_places=9)
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default='pending', db_index=True)
    tx_hash = models.CharField('交易哈希', max_length=191, unique=True, blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    completed_at = models.DateTimeField('完成时间', blank=True, null=True)
    expired_at = models.DateTimeField('过期时间', blank=True, null=True)

    class Meta:
        db_table = 'order_recharge'
        verbose_name = '充值记录'
        verbose_name_plural = '充值记录'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user_id}-{self.amount}-{self.currency}'


__all__ = [
    'BalanceLedger',
    'CartItem',
    'Order',
    'Product',
    'Recharge',
]
