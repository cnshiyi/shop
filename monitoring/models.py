from decimal import Decimal

from django.db import models


class AddressMonitor(models.Model):
    user = models.ForeignKey('accounts.TelegramUser', verbose_name='用户', on_delete=models.CASCADE)
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


class DailyAddressStat(models.Model):
    ACCOUNT_SCOPE_PLATFORM = 'platform'
    ACCOUNT_SCOPE_USER = 'user'
    ACCOUNT_SCOPE_CLOUD = 'cloud'
    ACCOUNT_SCOPE_CHOICES = (
        (ACCOUNT_SCOPE_PLATFORM, '平台账户'),
        (ACCOUNT_SCOPE_USER, '用户账户'),
        (ACCOUNT_SCOPE_CLOUD, '云账户'),
    )

    user = models.ForeignKey('accounts.TelegramUser', verbose_name='用户', on_delete=models.CASCADE, related_name='daily_address_stats')
    monitor = models.ForeignKey('monitoring.AddressMonitor', verbose_name='监控地址', on_delete=models.SET_NULL, blank=True, null=True, related_name='daily_stats')
    account_scope = models.CharField('账户归属类型', max_length=32, choices=ACCOUNT_SCOPE_CHOICES, default=ACCOUNT_SCOPE_PLATFORM, db_index=True)
    account_key = models.CharField('账户标识', max_length=191, blank=True, null=True, db_index=True)
    address = models.CharField('地址', max_length=191, db_index=True)
    currency = models.CharField('币种', max_length=32, db_index=True)
    stats_date = models.DateField('统计日期', db_index=True)
    income = models.DecimalField('收入', max_digits=18, decimal_places=6, default=Decimal('0'))
    expense = models.DecimalField('支出', max_digits=18, decimal_places=6, default=Decimal('0'))
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'daily_address_stats'
        verbose_name = '每日地址统计'
        verbose_name_plural = '每日地址统计'
        ordering = ['-stats_date', '-updated_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'address', 'currency', 'stats_date', 'account_scope'],
                name='uniq_daily_address_stat_scope',
            ),
        ]

    @property
    def profit(self):
        return (self.income or Decimal('0')) - (self.expense or Decimal('0'))

    def __str__(self):
        return f'{self.address} {self.currency} {self.stats_date}'


class ResourceSnapshot(models.Model):
    monitor = models.ForeignKey('monitoring.AddressMonitor', verbose_name='监控地址', on_delete=models.CASCADE, related_name='resource_snapshots')
    account_scope = models.CharField('账户归属类型', max_length=32, choices=DailyAddressStat.ACCOUNT_SCOPE_CHOICES, default=DailyAddressStat.ACCOUNT_SCOPE_PLATFORM, db_index=True)
    account_key = models.CharField('账户标识', max_length=191, blank=True, null=True, db_index=True)
    address = models.CharField('地址', max_length=191, db_index=True)
    energy = models.BigIntegerField('可用能量', default=0)
    bandwidth = models.BigIntegerField('可用带宽', default=0)
    delta_energy = models.BigIntegerField('能量变化', default=0)
    delta_bandwidth = models.BigIntegerField('带宽变化', default=0)
    captured_at = models.DateTimeField('采集时间', auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'resource_snapshots'
        verbose_name = '资源快照'
        verbose_name_plural = '资源快照'
        ordering = ['-captured_at', '-id']

    def __str__(self):
        return f'{self.address} {self.captured_at}'
