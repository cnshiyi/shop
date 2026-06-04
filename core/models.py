import logging
from threading import RLock

from django.db import connection, models
from django.db import close_old_connections
from django.db.utils import OperationalError, ProgrammingError
from django.utils import timezone

from .crypto import decrypt_text, encrypt_text

logger = logging.getLogger(__name__)


def _is_site_config_table_not_ready(exc: Exception) -> bool:
    message = str(exc).lower()
    return 'core_site_config' in message and any(
        marker in message
        for marker in ['no such table', 'does not exist', 'undefined table']
    )


class SiteConfig(models.Model):
    _CACHE_MISSING = object()
    _CACHE_TTL_SECONDS = 30
    _plain_value_cache = {}
    _cache_lock = RLock()

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    key = models.CharField('键', max_length=191, unique=True, db_index=True, db_comment='键')
    value = models.TextField('值', blank=True, null=True, db_comment='配置值；敏感配置会加密存储')
    is_sensitive = models.BooleanField('敏感配置', default=False, db_comment='是否按敏感配置加密存储')
    sort_order = models.IntegerField('排序', default=0, db_index=True, db_comment='排序')

    class Meta:
        db_table = 'core_site_config'
        db_table_comment = '系统运行配置表'
        verbose_name = '系统配置'
        verbose_name_plural = '系统配置'

    @classmethod
    def _cache_get(cls, key: str):
        with cls._cache_lock:
            cached = cls._plain_value_cache.get(key, cls._CACHE_MISSING)
            if cached is cls._CACHE_MISSING:
                return cls._CACHE_MISSING
            value, cached_at = cached
            if (timezone.now() - cached_at).total_seconds() > cls._CACHE_TTL_SECONDS:
                cls._plain_value_cache.pop(key, None)
                return cls._CACHE_MISSING
            return value

    @classmethod
    def _cache_set(cls, key: str, value):
        with cls._cache_lock:
            cls._plain_value_cache[key] = (value, timezone.now())

    @classmethod
    def clear_cache(cls, key: str | None = None):
        with cls._cache_lock:
            if key is None:
                cls._plain_value_cache.clear()
            else:
                cls._plain_value_cache.pop(key, None)
        try:
            from core.cache import invalidate_config_cache
            invalidate_config_cache(key)
        except Exception:
            pass

    @classmethod
    def get(cls, key: str, default: str = '') -> str:
        cached = cls._cache_get(key)
        if cached is not cls._CACHE_MISSING:
            return default if cached is None else (cached or default)
        try:
            if not connection.in_atomic_block:
                close_old_connections()
            obj = cls.objects.filter(key=key).first()
            if not obj:
                cls._cache_set(key, None)
                return default
            value = obj.value or ''
            plain_value = decrypt_text(value) if obj.is_sensitive else value
            cls._cache_set(key, plain_value)
            return plain_value or default
        except (OperationalError, ProgrammingError) as exc:
            if connection.in_atomic_block:
                raise
            if _is_site_config_table_not_ready(exc):
                logger.debug('SiteConfig.get 跳过：配置表未就绪 key=%s', key)
                return default
            logger.exception('SiteConfig.get 读取失败 key=%s', key)
            return default
        except Exception:
            if connection.in_atomic_block:
                raise
            logger.exception('SiteConfig.get 读取失败 key=%s', key)
            return default

    @classmethod
    def set(cls, key: str, value: str, *, sensitive: bool = False):
        stored_value = encrypt_text(value) if sensitive and value else value
        obj, _ = cls.objects.update_or_create(
            key=key,
            defaults={'value': stored_value, 'is_sensitive': sensitive},
        )
        cls.clear_cache(key)
        return obj

    def save(self, *args, **kwargs):
        if self.is_sensitive and self.value:
            current = self.value or ''
            if not current.startswith('gAAAA'):
                self.value = encrypt_text(current)
        super().save(*args, **kwargs)
        self.clear_cache(self.key)

    def masked_value(self) -> str:
        plain = self.get(self.key, '')
        if not plain:
            return ''
        if len(plain) <= 8:
            return '*' * len(plain)
        return f'{plain[:4]}***{plain[-4:]}'

    def delete(self, *args, **kwargs):
        key = self.key
        result = super().delete(*args, **kwargs)
        self.clear_cache(key)
        return result

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

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    provider = models.CharField('云厂商', max_length=32, choices=PROVIDER_CHOICES, db_index=True, db_comment='云厂商')
    name = models.CharField('账户名称', max_length=128, db_comment='账户名称')
    external_account_id = models.CharField('云厂商账号ID', max_length=128, blank=True, null=True, db_index=True, db_comment='云厂商账号ID')
    access_key = models.TextField('Access Key', db_comment='加密存储的云账号访问密钥')
    secret_key = models.TextField('Secret Key', db_comment='加密存储的云账号私密密钥')
    region_hint = models.CharField('默认地区', max_length=128, blank=True, null=True, db_comment='默认地区')
    is_active = models.BooleanField('启用', default=True, db_comment='启用')
    shutdown_enabled = models.BooleanField('关机计划启用', default=True, db_index=True, db_comment='关机计划启用')
    status = models.CharField('巡检状态', max_length=32, choices=STATUS_CHOICES, default=STATUS_UNKNOWN, db_index=True, db_comment='巡检状态')
    status_note = models.TextField('巡检说明', blank=True, null=True, db_comment='巡检说明')
    last_checked_at = models.DateTimeField('最近巡检时间', blank=True, null=True, db_comment='最近巡检时间')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'core_cloud_account'
        db_table_comment = '云厂商和外部服务账号配置表'
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

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    account = models.ForeignKey('core.CloudAccountConfig', verbose_name='关联账户', on_delete=models.SET_NULL, blank=True, null=True, related_name='sync_logs', db_comment='关联账户')
    source = models.CharField('来源', max_length=32, choices=SOURCE_CHOICES, db_index=True, db_comment='来源')
    action = models.CharField('动作', max_length=64, db_index=True, db_comment='动作')
    target = models.CharField('目标', max_length=191, blank=True, null=True, db_index=True, db_comment='目标')
    request_payload = models.TextField('请求载荷', blank=True, null=True, db_comment='请求载荷')
    response_payload = models.TextField('响应载荷', blank=True, null=True, db_comment='响应载荷')
    is_success = models.BooleanField('是否成功', default=True, db_index=True, db_comment='是否成功')
    error_message = models.TextField('错误信息', blank=True, null=True, db_comment='错误信息')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True, db_comment='创建时间')

    class Meta:
        db_table = 'core_sync_log'
        db_table_comment = '外部系统同步调用日志表'
        verbose_name = '外部同步日志'
        verbose_name_plural = '外部同步日志'
        ordering = ['-created_at', '-id']

    def __str__(self):
        return f'{self.source}:{self.action}:{self.target or "-"}'
