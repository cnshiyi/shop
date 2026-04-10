from django.db import models


class AddressMonitor(models.Model):
    user = models.ForeignKey('users.TelegramUser', verbose_name='用户', on_delete=models.CASCADE)
    address = models.CharField('监控地址', max_length=191, db_index=True)
    remark = models.TextField('备注', blank=True, null=True)
    monitor_transfers = models.BooleanField('监控转账', default=True)
    monitor_resources = models.BooleanField('监控资源', default=False)
    last_energy = models.BigIntegerField('上次可用能量', default=0)
    last_bandwidth = models.BigIntegerField('上次可用带宽', default=0)
    resource_checked_at = models.DateTimeField('资源检查时间', blank=True, null=True)
    usdt_threshold = models.DecimalField('USDT阈值', max_digits=18, decimal_places=6, default=1)
    trx_threshold = models.DecimalField('TRX阈值', max_digits=18, decimal_places=6, default=1)
    daily_income = models.DecimalField('今日收入', max_digits=18, decimal_places=6, default=0)
    daily_expense = models.DecimalField('今日支出', max_digits=18, decimal_places=6, default=0)
    daily_income_currency = models.CharField('收入币种', max_length=32, default='USDT')
    daily_expense_currency = models.CharField('支出币种', max_length=32, default='USDT')
    stats_date = models.CharField('统计日期', max_length=32, blank=True, null=True)
    is_active = models.BooleanField('启用', default=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)

    class Meta:
        db_table = 'address_monitors'
        verbose_name = '地址监控'
        verbose_name_plural = '地址监控'
        ordering = ['-created_at']

    def __str__(self):
        return self.address
