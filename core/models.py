from django.db import models
from django.utils import timezone

from .crypto import decrypt_text, encrypt_text


class SiteConfig(models.Model):
    key = models.CharField('键', max_length=191, unique=True, db_index=True)
    value = models.TextField('值', blank=True, null=True)
    is_sensitive = models.BooleanField('敏感配置', default=False)

    class Meta:
        db_table = 'configs'
        verbose_name = '系统配置'
        verbose_name_plural = '系统配置'

    @classmethod
    def get(cls, key: str, default: str = '') -> str:
        try:
            obj = cls.objects.filter(key=key).first()
            if not obj:
                return default
            value = obj.value or ''
            return decrypt_text(value) if obj.is_sensitive else (value or default)
        except Exception:
            return default

    @classmethod
    def set(cls, key: str, value: str, *, sensitive: bool = False):
        stored_value = encrypt_text(value) if sensitive and value else value
        obj, _ = cls.objects.update_or_create(
            key=key,
            defaults={'value': stored_value, 'is_sensitive': sensitive},
        )
        return obj

    def save(self, *args, **kwargs):
        if self.is_sensitive and self.value:
            current = self.value or ''
            if not current.startswith('gAAAA'):
                self.value = encrypt_text(current)
        super().save(*args, **kwargs)

    def masked_value(self) -> str:
        plain = self.get(self.key, '')
        if not plain:
            return ''
        if len(plain) <= 8:
            return '*' * len(plain)
        return f'{plain[:4]}***{plain[-4:]}'

    def __str__(self):
        return self.key


class CloudAccountConfig(models.Model):
    PROVIDER_AWS = 'aws'
    PROVIDER_ALIYUN = 'aliyun'
    PROVIDER_TRONGRID = 'trongrid'
    PROVIDER_CHOICES = (
        (PROVIDER_AWS, 'AWS'),
        (PROVIDER_ALIYUN, '阿里云'),
        (PROVIDER_TRONGRID, 'TRONGrid'),
    )
    STATUS_UNKNOWN = 'unknown'
    STATUS_OK = 'ok'
    STATUS_ERROR = 'error'
    STATUS_UNSUPPORTED = 'unsupported'
    STATUS_CHOICES = (
        (STATUS_UNKNOWN, '未检查'),
        (STATUS_OK, '正常'),
        (STATUS_ERROR, '异常'),
        (STATUS_UNSUPPORTED, '暂不支持'),
    )

    provider = models.CharField('云厂商', max_length=32, choices=PROVIDER_CHOICES, db_index=True)
    name = models.CharField('账户名称', max_length=128)
    access_key = models.TextField('Access Key')
    secret_key = models.TextField('Secret Key')
    region_hint = models.CharField('默认地区', max_length=128, blank=True, null=True)
    is_active = models.BooleanField('启用', default=True)
    status = models.CharField('巡检状态', max_length=32, choices=STATUS_CHOICES, default=STATUS_UNKNOWN, db_index=True)
    status_note = models.TextField('巡检说明', blank=True, null=True)
    last_checked_at = models.DateTimeField('最近巡检时间', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cloud_account_configs'
        verbose_name = '云账户配置'
        verbose_name_plural = '云账户配置'
        ordering = ['provider', 'name', 'id']

    def save(self, *args, **kwargs):
        if self.access_key and not self.access_key.startswith('gAAAA'):
            self.access_key = encrypt_text(self.access_key)
        if self.secret_key and not self.secret_key.startswith('gAAAA'):
            self.secret_key = encrypt_text(self.secret_key)
        super().save(*args, **kwargs)

    def mark_status(self, status: str, note: str = ''):
        self.status = status or self.STATUS_UNKNOWN
        self.status_note = note or ''
        self.last_checked_at = timezone.now()
        self.save(update_fields=['status', 'status_note', 'last_checked_at', 'updated_at'])

    @property
    def status_label(self) -> str:
        return dict(self.STATUS_CHOICES).get(self.status, self.status or '未检查')

    @property
    def access_key_plain(self) -> str:
        return decrypt_text(self.access_key or '')

    @property
    def secret_key_plain(self) -> str:
        return decrypt_text(self.secret_key or '')

    def __str__(self):
        return f'{self.get_provider_display()} - {self.name}'


class ExternalSyncLog(models.Model):
    SOURCE_TRONGRID = 'trongrid'
    SOURCE_AWS = 'aws_lightsail'
    SOURCE_ALIYUN = 'aliyun'
    SOURCE_DASHBOARD = 'dashboard'
    SOURCE_CHOICES = (
        (SOURCE_TRONGRID, 'TRONGrid'),
        (SOURCE_AWS, 'AWS Lightsail'),
        (SOURCE_ALIYUN, '阿里云'),
        (SOURCE_DASHBOARD, '后台接口'),
    )

    account = models.ForeignKey('core.CloudAccountConfig', verbose_name='关联账户', on_delete=models.SET_NULL, blank=True, null=True, related_name='sync_logs')
    source = models.CharField('来源', max_length=32, choices=SOURCE_CHOICES, db_index=True)
    action = models.CharField('动作', max_length=64, db_index=True)
    target = models.CharField('目标', max_length=191, blank=True, null=True, db_index=True)
    request_payload = models.TextField('请求载荷', blank=True, null=True)
    response_payload = models.TextField('响应载荷', blank=True, null=True)
    is_success = models.BooleanField('是否成功', default=True, db_index=True)
    error_message = models.TextField('错误信息', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'external_sync_logs'
        verbose_name = '外部同步日志'
        verbose_name_plural = '外部同步日志'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.source}:{self.action}:{self.target or "-"}'
