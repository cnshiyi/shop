"""cloud 域模型。"""

import uuid
from datetime import timezone as dt_timezone
from decimal import Decimal

from django.db import models
from django.utils import timezone


class CloudServerPlan(models.Model):
    PROVIDER_AWS_LIGHTSAIL = 'aws_lightsail'
    PROVIDER_ALIYUN_ECS = 'aliyun_simple'
    PROVIDER_CHOICES = (
        (PROVIDER_AWS_LIGHTSAIL, 'AWS 光帆服务器'),
        (PROVIDER_ALIYUN_ECS, '阿里云轻量云'),
    )

    provider = models.CharField('云厂商', max_length=32, choices=PROVIDER_CHOICES, db_index=True)
    region_code = models.CharField('地区代码', max_length=64, db_index=True)
    region_name = models.CharField('地区名称', max_length=128)
    config_id = models.CharField('配置ID', max_length=64, default='', blank=True, db_index=True)
    provider_plan_id = models.CharField('云厂商套餐ID', max_length=191, blank=True, db_index=True)
    plan_name = models.CharField('套餐名称', max_length=191)
    plan_description = models.TextField('套餐描述', blank=True, null=True)
    cpu = models.CharField('CPU', max_length=64, blank=True, null=True)
    memory = models.CharField('内存', max_length=64, blank=True, null=True)
    storage = models.CharField('存储', max_length=64, blank=True, null=True)
    bandwidth = models.CharField('带宽', max_length=64, blank=True, null=True)
    display_plan_name = models.CharField('展示套餐名', max_length=191, blank=True)
    display_cpu = models.CharField('展示CPU', max_length=64, blank=True)
    display_memory = models.CharField('展示内存', max_length=64, blank=True)
    display_storage = models.CharField('展示存储', max_length=64, blank=True)
    display_bandwidth = models.CharField('展示带宽', max_length=64, blank=True)
    display_description = models.TextField('展示说明', blank=True)
    cost_price = models.DecimalField('进货价', max_digits=18, decimal_places=6, default=0)
    price = models.DecimalField('出售价', max_digits=18, decimal_places=6)
    currency = models.CharField('币种', max_length=32, default='USDT')
    is_active = models.BooleanField('启用', default=True)
    sort_order = models.IntegerField('排序', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cloud_plan'
        verbose_name = '云服务器套餐'
        verbose_name_plural = '云服务器套餐'
        ordering = ['provider', 'region_name', '-sort_order', 'id']
        unique_together = ('provider', 'region_code', 'config_id')

    def save(self, *args, **kwargs):
        if not str(self.config_id or '').strip():
            self.config_id = f'cfg-{uuid.uuid4().hex[:12]}'
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.region_name} {self.plan_name}'


class ServerPrice(models.Model):
    provider = models.CharField('云厂商', max_length=32, choices=CloudServerPlan.PROVIDER_CHOICES, db_index=True)
    region_code = models.CharField('地区代码', max_length=64, db_index=True)
    region_name = models.CharField('地区名称', max_length=128)
    config_id = models.CharField('配置ID', max_length=64, default='', blank=True, db_index=True)
    bundle_code = models.CharField('规格代码', max_length=128, db_index=True)
    server_name = models.CharField('服务器价格名', max_length=191)
    server_description = models.TextField('服务器价格描述', blank=True, null=True)
    cpu = models.CharField('CPU', max_length=64, blank=True, null=True)
    memory = models.CharField('内存', max_length=64, blank=True, null=True)
    storage = models.CharField('存储', max_length=64, blank=True, null=True)
    bandwidth = models.CharField('带宽', max_length=64, blank=True, null=True)
    display_plan_name = models.CharField('展示套餐名', max_length=191, blank=True)
    display_cpu = models.CharField('展示CPU', max_length=64, blank=True)
    display_memory = models.CharField('展示内存', max_length=64, blank=True)
    display_storage = models.CharField('展示存储', max_length=64, blank=True)
    display_bandwidth = models.CharField('展示带宽', max_length=64, blank=True)
    display_description = models.TextField('展示说明', blank=True)
    cost_price = models.DecimalField('进货价', max_digits=18, decimal_places=6, default=0)
    price = models.DecimalField('销售价格', max_digits=18, decimal_places=6)
    currency = models.CharField('币种', max_length=32, default='USDT')
    is_active = models.BooleanField('启用', default=True)
    sort_order = models.IntegerField('排序', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cloud_price'
        verbose_name = '服务器价格'
        verbose_name_plural = '服务器价格'
        ordering = ['provider', 'region_name', '-sort_order', 'id']
        unique_together = ('provider', 'region_code', 'bundle_code')

    def __str__(self):
        return f'{self.region_name} {self.server_name} ({self.bundle_code})'


class CloudServerOrder(models.Model):
    STATUS_CHOICES = (
        ('pending', '待支付'),
        ('paid', '已支付'),
        ('provisioning', '创建中'),
        ('completed', '已创建'),
        ('renew_pending', '待续费'),
        ('expiring', '即将到期'),
        ('suspended', '已关机'),
        ('deleting', '删除中'),
        ('deleted', '已删除'),
        ('failed', '创建失败'),
        ('cancelled', '已取消'),
        ('expired', '已过期'),
    )
    PAY_METHOD_CHOICES = (
        ('address', '地址支付'),
        ('balance', '余额支付'),
    )
    CURRENCY_CHOICES = (
        ('USDT', 'USDT'),
        ('TRX', 'TRX'),
    )

    order_no = models.CharField('订单号', max_length=191, unique=True, db_index=True)
    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.CASCADE)
    plan = models.ForeignKey('cloud.CloudServerPlan', verbose_name='套餐', on_delete=models.PROTECT)
    provider = models.CharField('云厂商', max_length=32, db_index=True)
    cloud_account = models.ForeignKey('core.CloudAccountConfig', verbose_name='云账号', on_delete=models.SET_NULL, blank=True, null=True, related_name='cloud_orders')
    account_label = models.CharField('账户/来源标识', max_length=191, blank=True, null=True, db_index=True)
    region_code = models.CharField('地区代码', max_length=64, db_index=True)
    region_name = models.CharField('地区名称', max_length=128)
    plan_name = models.CharField('套餐名称', max_length=191)
    quantity = models.IntegerField('购买数量', default=1)
    currency = models.CharField('币种', max_length=32, choices=CURRENCY_CHOICES, default='USDT', db_index=True)
    total_amount = models.DecimalField('总金额', max_digits=18, decimal_places=6)
    pay_amount = models.DecimalField('应付金额', max_digits=18, decimal_places=9, blank=True, null=True)
    pay_method = models.CharField('支付方式', max_length=32, choices=PAY_METHOD_CHOICES, default='address')
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default='pending', db_index=True)
    tx_hash = models.CharField('交易哈希', max_length=191, unique=True, blank=True, null=True)
    image_name = models.CharField('镜像', max_length=128, default='debian')
    server_name = models.CharField('服务器名', max_length=191, blank=True, null=True, db_index=True)
    lifecycle_days = models.IntegerField('有效期天数', default=31)
    service_started_at = models.DateTimeField('服务开始时间', blank=True, null=True)
    service_expires_at = models.DateTimeField('服务到期时间', blank=True, null=True, db_index=True)
    renew_grace_expires_at = models.DateTimeField('续费宽限到期时间', blank=True, null=True)
    suspend_at = models.DateTimeField('计划关机时间', blank=True, null=True)
    delete_at = models.DateTimeField('计划删机时间', blank=True, null=True)
    ip_recycle_at = models.DateTimeField('IP保留到期时间', blank=True, null=True)
    last_renewed_at = models.DateTimeField('最后续费时间', blank=True, null=True)
    renew_notice_sent_at = models.DateTimeField('续费提醒发送时间', blank=True, null=True)
    delete_notice_sent_at = models.DateTimeField('删机提醒发送时间', blank=True, null=True)
    recycle_notice_sent_at = models.DateTimeField('删IP提醒发送时间', blank=True, null=True)
    migration_due_at = models.DateTimeField('迁移截止时间', blank=True, null=True)
    replacement_for = models.ForeignKey('self', verbose_name='替换来源订单', on_delete=models.SET_NULL, blank=True, null=True, related_name='replacement_orders')
    renew_extension_days = models.IntegerField('临时延期天数', default=0)
    delay_quota = models.IntegerField('延期次数', default=0)
    ip_change_quota = models.IntegerField('剩余更换IP次数', default=1)
    cloud_reminder_enabled = models.BooleanField('到期提醒', default=True, db_index=True)
    auto_renew_enabled = models.BooleanField('自动续费', default=False, db_index=True)
    last_user_id = models.BigIntegerField('最近绑定TG用户ID', blank=True, null=True, db_index=True)
    mtproxy_port = models.IntegerField('MTProxy端口', default=9528)
    mtproxy_link = models.TextField('MTProxy链接', blank=True, null=True)
    proxy_links = models.JSONField('代理链路', default=list, blank=True)
    mtproxy_secret = models.CharField('MTProxy密钥', max_length=191, blank=True, null=True)
    mtproxy_host = models.CharField('MTProxy主机', max_length=191, blank=True, null=True)
    instance_id = models.CharField('实例ID', max_length=191, blank=True, null=True)
    provider_resource_id = models.CharField('云资源ID', max_length=191, blank=True, null=True)
    static_ip_name = models.CharField('固定IP名称', max_length=191, blank=True, null=True)
    public_ip = models.CharField('公网IP', max_length=128, blank=True, null=True, db_index=True)
    previous_public_ip = models.CharField('历史公网IP', max_length=128, blank=True, null=True)
    login_user = models.CharField('登录账号', max_length=64, blank=True, null=True)
    login_password = models.CharField('登录密码', max_length=191, blank=True, null=True)
    provision_note = models.TextField('创建说明', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    paid_at = models.DateTimeField('支付时间', blank=True, null=True)
    expired_at = models.DateTimeField('过期时间', blank=True, null=True)
    completed_at = models.DateTimeField('完成时间', blank=True, null=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cloud_order'
        verbose_name = '云服务器订单'
        verbose_name_plural = '云服务器订单'
        ordering = ['-created_at']

    @staticmethod
    def normalize_expiry_time(value):
        if not value:
            return value
        local_value = timezone.localtime(value) if timezone.is_aware(value) else value
        if local_value.hour == 0 and local_value.minute == 0 and local_value.second == 0 and local_value.microsecond == 0:
            local_value = local_value.replace(hour=15)
            return local_value.astimezone(dt_timezone.utc) if timezone.is_aware(local_value) else timezone.make_aware(local_value, timezone.get_current_timezone()).astimezone(dt_timezone.utc)
        return value

    def save(self, *args, **kwargs):
        if self.completed_at and not self.service_started_at:
            self.service_started_at = self.completed_at
        if self.service_started_at and not self.service_expires_at:
            self.service_expires_at = self.service_started_at + timezone.timedelta(days=self.lifecycle_days)
        self.service_expires_at = self.normalize_expiry_time(self.service_expires_at)
        if self.service_expires_at:
            grace_days = 5 + max(int(self.renew_extension_days or 0), 0)
            self.renew_grace_expires_at = self.service_expires_at + timezone.timedelta(days=grace_days)
            self.suspend_at = self.service_expires_at + timezone.timedelta(days=grace_days)
            self.delete_at = self.suspend_at + timezone.timedelta(days=3)
            self.ip_recycle_at = self.delete_at + timezone.timedelta(days=15)
        super().save(*args, **kwargs)

    def __str__(self):
        return self.order_no


class CloudAsset(models.Model):
    STATUS_RUNNING = 'running'
    STATUS_PENDING = 'pending'
    STATUS_STARTING = 'starting'
    STATUS_STOPPING = 'stopping'
    STATUS_STOPPED = 'stopped'
    STATUS_SUSPENDED = 'suspended'
    STATUS_TERMINATING = 'terminating'
    STATUS_TERMINATED = 'terminated'
    STATUS_DELETING = 'deleting'
    STATUS_DELETED = 'deleted'
    STATUS_EXPIRED = 'expired'
    STATUS_EXPIRED_GRACE = 'expired_grace'
    STATUS_UNKNOWN = 'unknown'
    STATUS_CHOICES = (
        (STATUS_RUNNING, '运行中'),
        (STATUS_PENDING, '等待中'),
        (STATUS_STARTING, '启动中'),
        (STATUS_STOPPING, '停止中'),
        (STATUS_STOPPED, '已关机'),
        (STATUS_SUSPENDED, '已停机'),
        (STATUS_TERMINATING, '终止中'),
        (STATUS_TERMINATED, '已终止'),
        (STATUS_DELETING, '删除中'),
        (STATUS_DELETED, '已删除'),
        (STATUS_EXPIRED, '已过期'),
        (STATUS_EXPIRED_GRACE, '到期延停'),
        (STATUS_UNKNOWN, '未知状态'),
    )
    ACTIVE_STATUSES = {STATUS_RUNNING, STATUS_PENDING, STATUS_STARTING}

    KIND_SERVER = 'server'
    KIND_MTPROXY = 'mtproxy'
    KIND_CHOICES = (
        (KIND_SERVER, '云服务器'),
        (KIND_MTPROXY, 'MTProxy代理'),
    )

    SOURCE_ALIYUN = 'aliyun'
    SOURCE_AWS_MANUAL = 'aws_manual'
    SOURCE_AWS_SYNC = 'aws_sync'
    SOURCE_ORDER = 'order'
    SOURCE_CHOICES = (
        (SOURCE_ALIYUN, '阿里云自动同步'),
        (SOURCE_AWS_MANUAL, 'AWS手工录入'),
        (SOURCE_AWS_SYNC, 'AWS脚本同步'),
        (SOURCE_ORDER, '订单创建'),
    )

    kind = models.CharField('资产类型', max_length=32, choices=KIND_CHOICES, db_index=True)
    source = models.CharField('来源', max_length=32, choices=SOURCE_CHOICES, default=SOURCE_ORDER, db_index=True)
    provider = models.CharField('云厂商', max_length=32, blank=True, null=True, db_index=True)
    cloud_account = models.ForeignKey('core.CloudAccountConfig', verbose_name='云账号', on_delete=models.SET_NULL, blank=True, null=True, related_name='cloud_assets')
    account_label = models.CharField('账户/来源标识', max_length=191, blank=True, null=True, db_index=True)
    region_code = models.CharField('地区代码', max_length=64, blank=True, null=True, db_index=True)
    region_name = models.CharField('地区名称', max_length=128, blank=True, null=True)
    asset_name = models.CharField('资产名称', max_length=191, blank=True, null=True, db_index=True)
    instance_id = models.CharField('实例ID', max_length=191, blank=True, null=True, db_index=True)
    provider_resource_id = models.CharField('云资源ID', max_length=191, blank=True, null=True, db_index=True)
    public_ip = models.CharField('公网IP', max_length=128, blank=True, null=True, db_index=True)
    previous_public_ip = models.CharField('历史公网IP', max_length=128, blank=True, null=True)
    login_user = models.CharField('登录账号', max_length=64, blank=True, null=True)
    login_password = models.CharField('登录密码', max_length=191, blank=True, null=True)
    mtproxy_port = models.IntegerField('MTProxy端口', blank=True, null=True)
    mtproxy_link = models.TextField('MTProxy链接', blank=True, null=True)
    proxy_links = models.JSONField('代理链路', default=list, blank=True)
    mtproxy_secret = models.CharField('MTProxy密钥', max_length=191, blank=True, null=True)
    mtproxy_host = models.CharField('MTProxy主机', max_length=191, blank=True, null=True)
    actual_expires_at = models.DateTimeField('实际到期时间', blank=True, null=True, db_index=True)
    price = models.DecimalField('价格', max_digits=18, decimal_places=6, blank=True, null=True)
    currency = models.CharField('币种', max_length=32, default='USDT')
    order = models.ForeignKey('cloud.CloudServerOrder', verbose_name='关联订单', on_delete=models.SET_NULL, blank=True, null=True)
    user = models.ForeignKey('bot.TelegramUser', verbose_name='绑定用户', on_delete=models.SET_NULL, blank=True, null=True)
    note = models.TextField('备注', blank=True, null=True)
    sort_order = models.IntegerField('排序', default=99, db_index=True)
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default=STATUS_RUNNING, db_index=True)
    provider_status = models.CharField('云厂商原始状态', max_length=64, blank=True, null=True, db_index=True)
    is_active = models.BooleanField('有效', default=True, db_index=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cloud_asset'
        verbose_name = '云资产'
        verbose_name_plural = '云资产'
        ordering = ['-updated_at', '-id']

    def __str__(self):
        return self.asset_name or self.instance_id or self.public_ip or f'asset-{self.pk}'


class Server(models.Model):
    STATUS_RUNNING = CloudAsset.STATUS_RUNNING
    STATUS_PENDING = CloudAsset.STATUS_PENDING
    STATUS_STARTING = CloudAsset.STATUS_STARTING
    STATUS_STOPPING = CloudAsset.STATUS_STOPPING
    STATUS_STOPPED = CloudAsset.STATUS_STOPPED
    STATUS_SUSPENDED = CloudAsset.STATUS_SUSPENDED
    STATUS_TERMINATING = CloudAsset.STATUS_TERMINATING
    STATUS_TERMINATED = CloudAsset.STATUS_TERMINATED
    STATUS_DELETING = CloudAsset.STATUS_DELETING
    STATUS_DELETED = CloudAsset.STATUS_DELETED
    STATUS_EXPIRED = CloudAsset.STATUS_EXPIRED
    STATUS_EXPIRED_GRACE = CloudAsset.STATUS_EXPIRED_GRACE
    STATUS_UNKNOWN = CloudAsset.STATUS_UNKNOWN
    STATUS_CHOICES = CloudAsset.STATUS_CHOICES
    ACTIVE_STATUSES = CloudAsset.ACTIVE_STATUSES

    SOURCE_ALIYUN = 'aliyun'
    SOURCE_AWS_MANUAL = 'aws_manual'
    SOURCE_AWS_SYNC = 'aws_sync'
    SOURCE_ORDER = 'order'
    SOURCE_CHOICES = (
        (SOURCE_ALIYUN, '阿里云自动同步'),
        (SOURCE_AWS_MANUAL, 'AWS手工录入'),
        (SOURCE_AWS_SYNC, 'AWS脚本同步'),
        (SOURCE_ORDER, '订单创建'),
    )

    source = models.CharField('来源', max_length=32, choices=SOURCE_CHOICES, default=SOURCE_ORDER, db_index=True)
    provider = models.CharField('云厂商', max_length=32, blank=True, null=True, db_index=True)
    account_label = models.CharField('账户/来源标识', max_length=191, blank=True, null=True, db_index=True)
    region_code = models.CharField('地区代码', max_length=64, blank=True, null=True, db_index=True)
    region_name = models.CharField('地区名称', max_length=128, blank=True, null=True)
    server_name = models.CharField('服务器名称', max_length=191, blank=True, null=True, db_index=True)
    instance_id = models.CharField('实例ID', max_length=191, blank=True, null=True, db_index=True)
    provider_resource_id = models.CharField('云资源ID', max_length=191, blank=True, null=True, db_index=True)
    public_ip = models.CharField('公网IP', max_length=128, blank=True, null=True, db_index=True)
    previous_public_ip = models.CharField('历史公网IP', max_length=128, blank=True, null=True)
    login_user = models.CharField('登录账号', max_length=64, blank=True, null=True)
    login_password = models.CharField('登录密码', max_length=191, blank=True, null=True)
    expires_at = models.DateTimeField('到期时间', blank=True, null=True, db_index=True)
    order = models.ForeignKey('cloud.CloudServerOrder', verbose_name='关联订单', on_delete=models.SET_NULL, blank=True, null=True)
    user = models.ForeignKey('bot.TelegramUser', verbose_name='绑定用户', on_delete=models.SET_NULL, blank=True, null=True)
    note = models.TextField('备注', blank=True, null=True)
    sort_order = models.IntegerField('排序', default=99, db_index=True)
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default=CloudAsset.STATUS_RUNNING, db_index=True)
    provider_status = models.CharField('云厂商原始状态', max_length=64, blank=True, null=True, db_index=True)
    is_active = models.BooleanField('有效', default=True, db_index=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cloud_server'
        verbose_name = '服务器'
        verbose_name_plural = '服务器'
        ordering = ['expires_at', '-updated_at', '-id']

    def __str__(self):
        return self.server_name or self.instance_id or self.public_ip or f'server-{self.pk}'


class CloudIpLog(models.Model):
    EVENT_CREATED = 'created'
    EVENT_CHANGED = 'changed'
    EVENT_RENEWED = 'renewed'
    EVENT_EXPIRED = 'expired'
    EVENT_SUSPENDED = 'suspended'
    EVENT_DELETED = 'deleted'
    EVENT_RECYCLED = 'recycled'
    EVENT_CHOICES = (
        (EVENT_CREATED, '创建分配'),
        (EVENT_CHANGED, 'IP变更'),
        (EVENT_RENEWED, '续费'),
        (EVENT_EXPIRED, '到期'),
        (EVENT_SUSPENDED, '延停'),
        (EVENT_DELETED, '删除'),
        (EVENT_RECYCLED, '回收'),
    )

    order = models.ForeignKey('cloud.CloudServerOrder', verbose_name='关联订单', on_delete=models.SET_NULL, blank=True, null=True, related_name='ip_logs')
    asset = models.ForeignKey('cloud.CloudAsset', verbose_name='关联资产', on_delete=models.SET_NULL, blank=True, null=True, related_name='ip_logs')
    server = models.ForeignKey('cloud.Server', verbose_name='关联服务器', on_delete=models.SET_NULL, blank=True, null=True, related_name='ip_logs')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='关联用户', on_delete=models.SET_NULL, blank=True, null=True, related_name='cloud_ip_logs')
    provider = models.CharField('云厂商', max_length=32, blank=True, null=True, db_index=True)
    region_code = models.CharField('地区代码', max_length=64, blank=True, null=True, db_index=True)
    region_name = models.CharField('地区名称', max_length=128, blank=True, null=True)
    order_no = models.CharField('订单号', max_length=191, blank=True, null=True, db_index=True)
    asset_name = models.CharField('资产名称', max_length=191, blank=True, null=True, db_index=True)
    instance_id = models.CharField('实例ID', max_length=191, blank=True, null=True, db_index=True)
    provider_resource_id = models.CharField('云资源ID', max_length=191, blank=True, null=True, db_index=True)
    public_ip = models.CharField('当前IP', max_length=128, blank=True, null=True, db_index=True)
    previous_public_ip = models.CharField('上一个IP', max_length=128, blank=True, null=True, db_index=True)
    event_type = models.CharField('事件类型', max_length=32, choices=EVENT_CHOICES, db_index=True)
    note = models.TextField('说明', blank=True, null=True)
    created_at = models.DateTimeField('记录时间', auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'cloud_ip_log'
        verbose_name = '云IP日志'
        verbose_name_plural = '云IP日志'
        ordering = ['-created_at', '-id']

    def __str__(self):
        ip = self.public_ip or self.previous_public_ip or '-'
        return f'{self.order_no or self.asset_name or self.instance_id or "ip-log"} {self.event_type} {ip}'


class AddressMonitor(models.Model):
    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.CASCADE)
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
        db_table = 'cloud_address_monitor'
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

    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.CASCADE, related_name='daily_address_stats')
    monitor = models.ForeignKey('cloud.AddressMonitor', verbose_name='监控地址', on_delete=models.SET_NULL, blank=True, null=True, related_name='daily_stats')
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
        db_table = 'cloud_address_stat_daily'
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
    monitor = models.ForeignKey('cloud.AddressMonitor', verbose_name='监控地址', on_delete=models.CASCADE, related_name='resource_snapshots')
    account_scope = models.CharField('账户归属类型', max_length=32, choices=DailyAddressStat.ACCOUNT_SCOPE_CHOICES, default=DailyAddressStat.ACCOUNT_SCOPE_PLATFORM, db_index=True)
    account_key = models.CharField('账户标识', max_length=191, blank=True, null=True, db_index=True)
    address = models.CharField('地址', max_length=191, db_index=True)
    energy = models.BigIntegerField('可用能量', default=0)
    bandwidth = models.BigIntegerField('可用带宽', default=0)
    delta_energy = models.BigIntegerField('能量变化', default=0)
    delta_bandwidth = models.BigIntegerField('带宽变化', default=0)
    captured_at = models.DateTimeField('采集时间', auto_now_add=True, db_index=True)

    class Meta:
        db_table = 'cloud_resource_snapshot'
        verbose_name = '资源快照'
        verbose_name_plural = '资源快照'
        ordering = ['-captured_at', '-id']

    def __str__(self):
        return f'{self.address} {self.captured_at}'


__all__ = [
    'AddressMonitor',
    'CloudAsset',
    'CloudIpLog',
    'CloudServerOrder',
    'CloudServerPlan',
    'DailyAddressStat',
    'ResourceSnapshot',
    'Server',
    'ServerPrice',
]
