from django.db import models


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

    user = models.ForeignKey('accounts.TelegramUser', verbose_name='用户', on_delete=models.CASCADE)
    currency = models.CharField('币种', max_length=32, choices=CURRENCY_CHOICES, default='USDT', db_index=True)
    amount = models.DecimalField('充值金额', max_digits=18, decimal_places=6)
    pay_amount = models.DecimalField('支付金额', max_digits=18, decimal_places=9)
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default='pending', db_index=True)
    tx_hash = models.CharField('交易哈希', max_length=191, unique=True, blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    completed_at = models.DateTimeField('完成时间', blank=True, null=True)
    expired_at = models.DateTimeField('过期时间', blank=True, null=True)

    class Meta:
        db_table = 'recharges'
        verbose_name = '充值记录'
        verbose_name_plural = '充值记录'
        ordering = ['-created_at']

    def __str__(self):
        return f'{self.user_id}-{self.amount}-{self.currency}'
