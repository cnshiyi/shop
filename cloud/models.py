"""cloud 域模型。"""

import uuid
from decimal import Decimal

from django.conf import settings
from django.db import models

class CloudServerPlan(models.Model):
    PROVIDER_AWS_LIGHTSAIL = 'aws_lightsail'
    PROVIDER_ALIYUN_ECS = 'aliyun_simple'
    PROVIDER_CHOICES = (
        (PROVIDER_AWS_LIGHTSAIL, 'AWS 光帆服务器'),
        (PROVIDER_ALIYUN_ECS, '阿里云轻量云'),
    )

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    provider = models.CharField('云厂商', max_length=32, choices=PROVIDER_CHOICES, db_index=True, db_comment='云厂商')
    region_code = models.CharField('地区代码', max_length=64, db_index=True, db_comment='地区代码')
    region_name = models.CharField('地区名称', max_length=128, db_comment='地区名称')
    config_id = models.CharField('配置ID', max_length=64, default='', blank=True, db_index=True, db_comment='配置ID')
    provider_plan_id = models.CharField('云厂商套餐ID', max_length=191, blank=True, db_index=True, db_comment='云厂商套餐ID')
    plan_name = models.CharField('套餐名称', max_length=191, db_comment='套餐名称')
    plan_description = models.TextField('套餐描述', blank=True, null=True, db_comment='套餐描述')
    cpu = models.CharField('CPU', max_length=64, blank=True, null=True, db_comment='CPU')
    memory = models.CharField('内存', max_length=64, blank=True, null=True, db_comment='内存')
    storage = models.CharField('存储', max_length=64, blank=True, null=True, db_comment='存储')
    bandwidth = models.CharField('带宽', max_length=64, blank=True, null=True, db_comment='带宽')
    display_plan_name = models.CharField('展示套餐名', max_length=191, blank=True, db_comment='展示套餐名')
    display_cpu = models.CharField('展示CPU', max_length=64, blank=True, db_comment='展示CPU')
    display_memory = models.CharField('展示内存', max_length=64, blank=True, db_comment='展示内存')
    display_storage = models.CharField('展示存储', max_length=64, blank=True, db_comment='展示存储')
    display_bandwidth = models.CharField('展示带宽', max_length=64, blank=True, db_comment='展示带宽')
    display_description = models.TextField('展示说明', blank=True, db_comment='展示说明')
    cost_price = models.DecimalField('进货价', max_digits=18, decimal_places=6, default=0, db_comment='进货价')
    price = models.DecimalField('出售价', max_digits=18, decimal_places=6, db_comment='出售价')
    currency = models.CharField('币种', max_length=32, default='USDT', db_comment='币种')
    is_active = models.BooleanField('启用', default=True, db_comment='启用')
    sort_order = models.IntegerField('排序', default=0, db_comment='排序')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'cloud_plan'
        db_table_comment = '云服务器销售套餐配置表'
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
    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    provider = models.CharField('云厂商', max_length=32, choices=CloudServerPlan.PROVIDER_CHOICES, db_index=True, db_comment='云厂商')
    region_code = models.CharField('地区代码', max_length=64, db_index=True, db_comment='地区代码')
    region_name = models.CharField('地区名称', max_length=128, db_comment='地区名称')
    config_id = models.CharField('配置ID', max_length=64, default='', blank=True, db_index=True, db_comment='配置ID')
    bundle_code = models.CharField('规格代码', max_length=128, db_index=True, db_comment='规格代码')
    server_name = models.CharField('服务器价格名', max_length=191, db_comment='服务器价格名')
    server_description = models.TextField('服务器价格描述', blank=True, null=True, db_comment='服务器价格描述')
    cpu = models.CharField('CPU', max_length=64, blank=True, null=True, db_comment='CPU')
    memory = models.CharField('内存', max_length=64, blank=True, null=True, db_comment='内存')
    storage = models.CharField('存储', max_length=64, blank=True, null=True, db_comment='存储')
    bandwidth = models.CharField('带宽', max_length=64, blank=True, null=True, db_comment='带宽')
    display_plan_name = models.CharField('展示套餐名', max_length=191, blank=True, db_comment='展示套餐名')
    display_cpu = models.CharField('展示CPU', max_length=64, blank=True, db_comment='展示CPU')
    display_memory = models.CharField('展示内存', max_length=64, blank=True, db_comment='展示内存')
    display_storage = models.CharField('展示存储', max_length=64, blank=True, db_comment='展示存储')
    display_bandwidth = models.CharField('展示带宽', max_length=64, blank=True, db_comment='展示带宽')
    display_description = models.TextField('展示说明', blank=True, db_comment='展示说明')
    cost_price = models.DecimalField('进货价', max_digits=18, decimal_places=6, default=0, db_comment='进货价')
    price = models.DecimalField('销售价格', max_digits=18, decimal_places=6, db_comment='销售价格')
    currency = models.CharField('币种', max_length=32, default='USDT', db_comment='币种')
    is_active = models.BooleanField('启用', default=True, db_comment='启用')
    sort_order = models.IntegerField('排序', default=0, db_comment='排序')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'cloud_price'
        db_table_comment = '云厂商服务器规格和销售价格配置表'
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

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    order_no = models.CharField('订单号', max_length=191, unique=True, db_index=True, db_comment='云服务器订单唯一编号')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.CASCADE, db_comment='用户')
    plan = models.ForeignKey('cloud.CloudServerPlan', verbose_name='套餐', on_delete=models.PROTECT, db_comment='套餐')
    provider = models.CharField('云厂商', max_length=32, db_index=True, db_comment='云厂商')
    cloud_account = models.ForeignKey('core.CloudAccountConfig', verbose_name='云账号', on_delete=models.SET_NULL, blank=True, null=True, related_name='cloud_orders', db_comment='云账号')
    account_label = models.CharField('账户/来源标识', max_length=191, blank=True, null=True, db_index=True, db_comment='账户/来源标识')
    region_code = models.CharField('地区代码', max_length=64, db_index=True, db_comment='地区代码')
    region_name = models.CharField('地区名称', max_length=128, db_comment='地区名称')
    plan_name = models.CharField('套餐名称', max_length=191, db_comment='套餐名称')
    quantity = models.IntegerField('购买数量', default=1, db_comment='购买数量')
    currency = models.CharField('币种', max_length=32, choices=CURRENCY_CHOICES, default='USDT', db_index=True, db_comment='币种')
    total_amount = models.DecimalField('总金额', max_digits=18, decimal_places=6, db_comment='总金额')
    pay_amount = models.DecimalField('应付金额', max_digits=18, decimal_places=9, blank=True, null=True, db_comment='余额或链上支付的最终应付金额')
    pay_method = models.CharField('支付方式', max_length=32, choices=PAY_METHOD_CHOICES, default='address', db_comment='支付方式')
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default='pending', db_index=True, db_comment='状态')
    tx_hash = models.CharField('交易哈希', max_length=191, unique=True, blank=True, null=True, db_comment='云服务器订单链上支付交易哈希')
    payer_address = models.CharField('链上付款地址', max_length=191, blank=True, null=True, db_index=True, db_comment='链上付款地址')
    receive_address = models.CharField('链上收款地址', max_length=191, blank=True, null=True, db_index=True, db_comment='链上收款地址')
    image_name = models.CharField('镜像', max_length=128, default='debian', db_comment='镜像')
    server_name = models.CharField('服务器名', max_length=191, blank=True, null=True, db_index=True, db_comment='服务器名')
    lifecycle_days = models.IntegerField('有效期天数', default=31, db_comment='有效期天数')
    service_started_at = models.DateTimeField('服务开始时间', blank=True, null=True, db_comment='订单服务流程开始时间，不作为资产到期事实')
    renew_grace_expires_at = models.DateTimeField('续费宽限到期时间', blank=True, null=True, db_comment='订单续费宽限流程时间，不作为资产到期事实')
    suspend_at = models.DateTimeField('计划关机时间', blank=True, null=True, db_comment='订单派生的计划关机流程时间，不作为资产到期事实')
    delete_at = models.DateTimeField('计划删机时间', blank=True, null=True, db_comment='订单派生的计划删机流程时间，不作为资产到期事实')
    ip_recycle_at = models.DateTimeField('IP保留到期时间', blank=True, null=True, db_comment='订单派生的固定IP回收流程时间，不作为资产到期事实')
    last_renewed_at = models.DateTimeField('最后续费时间', blank=True, null=True, db_comment='最后续费时间')
    renew_notice_sent_at = models.DateTimeField('续费提醒发送时间', blank=True, null=True, db_comment='续费提醒发送时间')
    auto_renew_notice_sent_at = models.DateTimeField('自动续费预提醒发送时间', blank=True, null=True, db_comment='自动续费预提醒发送时间')
    auto_renew_failure_notice_sent_at = models.DateTimeField('自动续费失败通知发送时间', blank=True, null=True, db_comment='自动续费失败通知发送时间')
    delete_notice_sent_at = models.DateTimeField('删机提醒发送时间', blank=True, null=True, db_comment='删机提醒发送时间')
    recycle_notice_sent_at = models.DateTimeField('删IP提醒发送时间', blank=True, null=True, db_comment='删IP提醒发送时间')
    migration_due_at = models.DateTimeField('迁移截止时间', blank=True, null=True, db_comment='订单迁移旧机处理截止时间，不作为资产到期事实')
    replacement_for = models.ForeignKey('self', verbose_name='替换来源订单', on_delete=models.SET_NULL, blank=True, null=True, related_name='replacement_orders', db_comment='替换来源订单')
    renew_extension_days = models.IntegerField('临时延期天数', default=0, db_comment='临时延期天数')
    delay_quota = models.IntegerField('延期次数', default=0, db_comment='延期次数')
    ip_change_quota = models.IntegerField('剩余更换IP次数', default=1, db_comment='剩余更换IP次数')
    cloud_reminder_enabled = models.BooleanField('到期提醒', default=True, db_index=True, db_comment='到期提醒')
    suspend_reminder_enabled = models.BooleanField('停机提醒', default=True, db_index=True, db_comment='停机提醒')
    delete_reminder_enabled = models.BooleanField('删机提醒', default=True, db_index=True, db_comment='删机提醒')
    ip_recycle_reminder_enabled = models.BooleanField('IP保留期提醒', default=True, db_index=True, db_comment='IP保留期提醒')
    auto_renew_enabled = models.BooleanField('自动续费', default=False, db_index=True, db_comment='自动续费')
    last_user_id = models.BigIntegerField('最近绑定TG用户ID', blank=True, null=True, db_index=True, db_comment='最近绑定TG用户ID')
    mtproxy_port = models.IntegerField('MTProxy端口', default=443, db_comment='MTProxy端口')
    mtproxy_link = models.TextField('MTProxy链接', blank=True, null=True, db_comment='MTProxy链接')
    proxy_links = models.JSONField('代理链路', default=list, blank=True, db_comment='代理链路')
    mtproxy_secret = models.CharField('MTProxy密钥', max_length=191, blank=True, null=True, db_comment='MTProxy密钥')
    mtproxy_host = models.CharField('MTProxy主机', max_length=191, blank=True, null=True, db_comment='MTProxy主机')
    instance_id = models.CharField('实例ID', max_length=191, blank=True, null=True, db_comment='实例ID')
    provider_resource_id = models.CharField('云资源ID', max_length=191, blank=True, null=True, db_comment='云资源ID')
    static_ip_name = models.CharField('固定IP名称', max_length=191, blank=True, null=True, db_comment='固定IP名称')
    public_ip = models.CharField('公网IP', max_length=128, blank=True, null=True, db_index=True, db_comment='公网IP')
    previous_public_ip = models.CharField('历史公网IP', max_length=128, blank=True, null=True, db_comment='历史公网IP')
    login_user = models.CharField('登录账号', max_length=64, blank=True, null=True, db_comment='登录账号')
    login_password = models.CharField('登录密码', max_length=191, blank=True, null=True, db_comment='登录密码')
    provision_note = models.TextField('创建说明', blank=True, null=True, db_comment='创建说明')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')
    paid_at = models.DateTimeField('支付时间', blank=True, null=True, db_comment='支付时间')
    expired_at = models.DateTimeField('过期时间', blank=True, null=True, db_comment='过期时间')
    completed_at = models.DateTimeField('完成时间', blank=True, null=True, db_comment='完成时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'cloud_order'
        db_table_comment = '云服务器订单和操作流程上下文表'
        verbose_name = '云服务器订单'
        verbose_name_plural = '云服务器订单'
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        requested_update_fields = kwargs.get('update_fields')
        update_fields = set(requested_update_fields or [])
        if self.completed_at and not self.service_started_at:
            self.service_started_at = self.completed_at
            if requested_update_fields is not None:
                update_fields.add('service_started_at')
        if requested_update_fields is not None:
            if update_fields:
                kwargs['update_fields'] = list(update_fields)
            else:
                kwargs.pop('update_fields', None)
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

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    kind = models.CharField('资产类型', max_length=32, choices=KIND_CHOICES, db_index=True, db_comment='资产类型')
    source = models.CharField('来源', max_length=32, choices=SOURCE_CHOICES, default=SOURCE_ORDER, db_index=True, db_comment='来源')
    provider = models.CharField('云厂商', max_length=32, blank=True, null=True, db_index=True, db_comment='云厂商')
    cloud_account = models.ForeignKey('core.CloudAccountConfig', verbose_name='云账号', on_delete=models.SET_NULL, blank=True, null=True, related_name='cloud_assets', db_comment='云账号')
    account_label = models.CharField('账户/来源标识', max_length=191, blank=True, null=True, db_index=True, db_comment='账户/来源标识')
    region_code = models.CharField('地区代码', max_length=64, blank=True, null=True, db_index=True, db_comment='地区代码')
    region_name = models.CharField('地区名称', max_length=128, blank=True, null=True, db_comment='地区名称')
    asset_name = models.CharField('资产名称', max_length=191, blank=True, null=True, db_index=True, db_comment='资产名称')
    instance_id = models.CharField('实例ID', max_length=191, blank=True, null=True, db_index=True, db_comment='实例ID')
    provider_resource_id = models.CharField('云资源ID', max_length=191, blank=True, null=True, db_index=True, db_comment='云资源ID')
    public_ip = models.CharField('公网IP', max_length=128, blank=True, null=True, db_index=True, db_comment='公网IP')
    previous_public_ip = models.CharField('历史公网IP', max_length=128, blank=True, null=True, db_comment='历史公网IP')
    login_user = models.CharField('登录账号', max_length=64, blank=True, null=True, db_comment='登录账号')
    login_password = models.CharField('登录密码', max_length=191, blank=True, null=True, db_comment='登录密码')
    mtproxy_port = models.IntegerField('MTProxy端口', blank=True, null=True, db_comment='MTProxy端口')
    mtproxy_link = models.TextField('MTProxy链接', blank=True, null=True, db_comment='MTProxy链接')
    proxy_links = models.JSONField('代理链路', default=list, blank=True, db_comment='代理链路')
    mtproxy_secret = models.CharField('MTProxy密钥', max_length=191, blank=True, null=True, db_comment='MTProxy密钥')
    mtproxy_host = models.CharField('MTProxy主机', max_length=191, blank=True, null=True, db_comment='MTProxy主机')
    actual_expires_at = models.DateTimeField('实际到期时间', blank=True, null=True, db_index=True, db_comment='云资产唯一真实到期事实，生命周期计划和续费判断以此字段为准')
    price = models.DecimalField('价格', max_digits=18, decimal_places=6, blank=True, null=True, db_comment='价格')
    currency = models.CharField('币种', max_length=32, default='USDT', db_comment='币种')
    order = models.ForeignKey('cloud.CloudServerOrder', verbose_name='关联订单', on_delete=models.SET_NULL, blank=True, null=True, db_comment='关联订单')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='绑定用户', on_delete=models.SET_NULL, blank=True, null=True, db_comment='绑定用户')
    telegram_group = models.ForeignKey('bot.TelegramGroupFilter', verbose_name='绑定群组', on_delete=models.SET_NULL, blank=True, null=True, db_comment='绑定群组')
    note = models.TextField('备注', blank=True, null=True, db_comment='备注')
    sync_state = models.JSONField('同步状态', default=dict, blank=True, db_comment='同步状态')
    sort_order = models.IntegerField('排序', default=99, db_index=True, db_comment='排序')
    shutdown_enabled = models.BooleanField('关机计划启用', default=True, db_index=True, db_comment='关机计划启用')
    server_delete_enabled = models.BooleanField('服务器删除计划启用', default=True, db_index=True, db_comment='服务器删除计划启用')
    ip_delete_enabled = models.BooleanField('IP删除计划启用', default=True, db_index=True, db_comment='IP删除计划启用')
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default=STATUS_RUNNING, db_index=True, db_comment='状态')
    provider_status = models.CharField('云厂商原始状态', max_length=64, blank=True, null=True, db_index=True, db_comment='云厂商原始状态')
    is_active = models.BooleanField('有效', default=True, db_index=True, db_comment='有效')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'cloud_asset'
        db_table_comment = '云资源事实表'
        verbose_name = '云资产'
        verbose_name_plural = '云资产'
        ordering = ['-updated_at', '-id']
        indexes = [
            models.Index(fields=['kind', 'status', 'is_active'], name='ca_kind_status_active_idx'),
            models.Index(fields=['provider', 'account_label', 'region_code', 'instance_id'], name='ca_provider_acct_inst_idx'),
            models.Index(fields=['provider', 'account_label', 'region_code', 'public_ip'], name='ca_provider_acct_ip_idx'),
            models.Index(fields=['order', 'status'], name='ca_order_status_idx'),
            models.Index(fields=['kind', 'user', 'status'], name='ca_kind_user_status_idx'),
            models.Index(fields=['kind', 'updated_at'], name='ca_kind_updated_idx'),
            models.Index(fields=['kind', '-sort_order', 'actual_expires_at', '-updated_at'], name='ca_kind_sort_due_idx'),
            models.Index(fields=['kind', 'is_active', 'actual_expires_at', 'id'], name='ca_lifecycle_page_idx'),
            models.Index(fields=['kind', 'actual_expires_at', 'id'], name='ca_lifecycle_any_page_idx'),
        ]

    def __str__(self):
        return self.asset_name or self.instance_id or self.public_ip or f'asset-{self.pk}'


class CloudAssetDashboardSnapshot(models.Model):
    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    asset = models.OneToOneField('cloud.CloudAsset', verbose_name='云资产', on_delete=models.CASCADE, related_name='dashboard_snapshot', db_comment='云资产')
    payload = models.JSONField('列表载荷', default=dict, blank=True, db_comment='列表载荷')
    search_text = models.TextField('搜索文本', blank=True, db_comment='搜索文本')
    provider = models.CharField('云厂商', max_length=32, blank=True, null=True, db_index=True, db_comment='云厂商')
    cloud_account = models.ForeignKey('core.CloudAccountConfig', verbose_name='云账号', on_delete=models.SET_NULL, blank=True, null=True, related_name='cloud_asset_dashboard_snapshots', db_comment='云账号')
    account_label = models.CharField('账户/来源标识', max_length=191, blank=True, null=True, db_index=True, db_comment='账户/来源标识')
    region_code = models.CharField('地区代码', max_length=64, blank=True, null=True, db_index=True, db_comment='地区代码')
    public_ip = models.CharField('公网IP', max_length=128, blank=True, null=True, db_index=True, db_comment='公网IP')
    status = models.CharField('状态', max_length=32, blank=True, null=True, db_index=True, db_comment='状态')
    is_active = models.BooleanField('是否活跃', default=True, db_index=True, db_comment='是否活跃')
    is_display_visible = models.BooleanField('后台列表可显示', default=True, db_index=True, db_comment='后台列表 show_deleted=0 时的可见性缓存')
    sort_order = models.IntegerField('排序', default=99, db_index=True, db_comment='排序')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='绑定用户', on_delete=models.SET_NULL, blank=True, null=True, related_name='cloud_asset_dashboard_snapshots', db_comment='绑定用户')
    tg_user_id = models.BigIntegerField('Telegram 用户ID', blank=True, null=True, db_index=True, db_comment='Telegram 用户ID')
    telegram_group = models.ForeignKey('bot.TelegramGroupFilter', verbose_name='绑定群组', on_delete=models.SET_NULL, blank=True, null=True, related_name='cloud_asset_dashboard_snapshots', db_comment='绑定群组')
    group_user_key = models.CharField('用户分组键', max_length=191, db_index=True, db_comment='用户分组键')
    group_user_label = models.CharField('用户分组标签', max_length=191, blank=True, db_comment='用户分组标签')
    group_telegram_key = models.CharField('群组分组键', max_length=191, db_index=True, db_comment='群组分组键')
    group_telegram_label = models.CharField('群组分组标签', max_length=191, blank=True, db_comment='群组分组标签')
    asset_due_sort_at = models.DateTimeField('资产到期排序缓存', blank=True, null=True, db_index=True, db_comment='仅用于后台列表排序缓存，来源 CloudAsset.actual_expires_at，不作为资产到期事实')
    asset_due_sort_null_rank = models.PositiveSmallIntegerField('资产到期空值排序', default=1, db_comment='仅用于后台列表排序，0=有到期时间，1=无到期时间，不作为资产到期事实')
    risk_status = models.CharField('风险状态', max_length=64, default='other', db_index=True, db_comment='风险状态')
    risk_rank = models.IntegerField('风险排序', default=99, db_index=True, db_comment='风险排序')
    risk_statuses = models.JSONField('风险状态集合', default=list, blank=True, db_comment='风险状态集合')
    risk_normal = models.BooleanField('运行中', default=False, db_index=True, db_comment='运行中')
    risk_due_soon = models.BooleanField('即将到期', default=False, db_index=True, db_comment='即将到期')
    risk_expired = models.BooleanField('已过期', default=False, db_index=True, db_comment='已过期')
    risk_unattached_ip = models.BooleanField('未附加固定IP', default=False, db_index=True, db_comment='未附加固定IP')
    risk_abnormal = models.BooleanField('异常/待确认', default=False, db_index=True, db_comment='异常/待确认')
    risk_account_disabled = models.BooleanField('云账号已停用', default=False, db_index=True, db_comment='云账号已停用')
    risk_shutdown_disabled = models.BooleanField('关机计划关闭', default=False, db_index=True, db_comment='关机计划关闭')
    risk_unbound_user = models.BooleanField('未绑定用户', default=False, db_index=True, db_comment='未绑定用户')
    risk_unbound_group = models.BooleanField('未绑定群组', default=False, db_index=True, db_comment='未绑定群组')
    risk_auto_renew_off = models.BooleanField('续费关闭', default=False, db_index=True, db_comment='续费关闭')
    risk_deleted = models.BooleanField('已删除/终止', default=False, db_index=True, db_comment='已删除/终止')
    asset_updated_at = models.DateTimeField('资产更新时间', blank=True, null=True, db_index=True, db_comment='资产更新时间')
    refreshed_at = models.DateTimeField('快照刷新时间', auto_now=True, db_index=True, db_comment='快照刷新时间')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')

    class Meta:
        db_table = 'cloud_asset_dashboard_snapshot'
        db_table_comment = '云资产后台列表快照表，不保存资产到期事实'
        verbose_name = '云资产列表快照'
        verbose_name_plural = '云资产列表快照'
        ordering = ['risk_rank', '-sort_order', '-asset_id']
        indexes = [
            models.Index(fields=['risk_account_disabled', 'risk_rank', '-sort_order'], name='cad_risk_display_idx'),
            models.Index(fields=['group_user_key', 'risk_rank', '-sort_order'], name='cad_group_user_idx'),
            models.Index(fields=['group_telegram_key', 'risk_rank', '-sort_order'], name='cad_group_tg_idx'),
            models.Index(fields=['group_user_key', 'asset_due_sort_at', 'group_user_label'], name='cad_user_due_page_idx'),
            models.Index(fields=['group_telegram_key', 'asset_due_sort_at', 'group_telegram_label'], name='cad_tg_due_page_idx'),
            models.Index(fields=['is_display_visible', 'group_user_key', 'asset_due_sort_at', 'group_user_label'], name='cad_vis_user_due_idx'),
            models.Index(fields=['is_display_visible', 'group_telegram_key', 'asset_due_sort_at', 'group_telegram_label'], name='cad_vis_tg_due_idx'),
            models.Index(fields=['is_display_visible', 'asset_due_sort_null_rank', 'asset_due_sort_at', 'group_user_label', 'group_user_key'], name='cad_vis_user_due_ord_idx'),
            models.Index(fields=['is_display_visible', 'asset_due_sort_null_rank', 'asset_due_sort_at', 'group_telegram_label', 'group_telegram_key'], name='cad_vis_tg_due_ord_idx'),
            models.Index(fields=['risk_normal', 'risk_account_disabled', 'group_user_key'], name='cad_norm_user_group_idx'),
            models.Index(fields=['risk_normal', 'risk_account_disabled', 'asset_due_sort_null_rank', 'asset_due_sort_at', 'group_user_label', 'group_user_key'], name='cad_norm_user_due_ord_idx'),
            models.Index(fields=['risk_due_soon', 'risk_account_disabled', 'asset_due_sort_null_rank', 'asset_due_sort_at', 'group_user_label', 'group_user_key'], name='cad_due_user_due_ord_idx'),
            models.Index(fields=['risk_expired', 'risk_account_disabled', 'asset_due_sort_null_rank', 'asset_due_sort_at', 'group_user_label', 'group_user_key'], name='cad_exp_user_due_ord_idx'),
            models.Index(fields=['risk_unattached_ip', 'risk_account_disabled', 'asset_due_sort_null_rank', 'asset_due_sort_at', 'group_user_label', 'group_user_key'], name='cad_unatt_user_due_ord_idx'),
            models.Index(fields=['risk_abnormal', 'risk_account_disabled', 'asset_due_sort_null_rank', 'asset_due_sort_at', 'group_user_label', 'group_user_key'], name='cad_abn_user_due_ord_idx'),
            models.Index(fields=['risk_unbound_user', 'risk_account_disabled', 'asset_due_sort_null_rank', 'asset_due_sort_at', 'group_user_label', 'group_user_key'], name='cad_nouser_user_due_idx'),
            models.Index(fields=['risk_unbound_group', 'risk_account_disabled', 'asset_due_sort_null_rank', 'asset_due_sort_at', 'group_user_label', 'group_user_key'], name='cad_nogroup_user_due_idx'),
            models.Index(fields=['risk_account_disabled', 'group_user_key'], name='cad_acct_user_group_idx'),
            models.Index(fields=['risk_account_disabled', 'asset_due_sort_null_rank', 'asset_due_sort_at', 'group_user_label', 'group_user_key'], name='cad_acct_user_due_ord_idx'),
            models.Index(fields=['risk_normal', 'risk_account_disabled', 'group_telegram_key'], name='cad_norm_tg_group_idx'),
            models.Index(fields=['risk_due_soon', 'risk_account_disabled', 'group_telegram_key'], name='cad_due_tg_group_idx'),
            models.Index(fields=['risk_expired', 'risk_account_disabled', 'group_telegram_key'], name='cad_exp_tg_group_idx'),
            models.Index(fields=['risk_unattached_ip', 'risk_account_disabled', 'group_telegram_key'], name='cad_unatt_tg_group_idx'),
            models.Index(fields=['risk_abnormal', 'risk_account_disabled', 'group_telegram_key'], name='cad_abn_tg_group_idx'),
            models.Index(fields=['risk_account_disabled', 'group_telegram_key'], name='cad_acct_tg_group_idx'),
            models.Index(fields=['risk_shutdown_disabled', 'risk_account_disabled', 'group_telegram_key'], name='cad_shut_tg_group_idx'),
            models.Index(fields=['risk_unbound_user', 'risk_account_disabled', 'group_telegram_key'], name='cad_nouser_tg_group_idx'),
            models.Index(fields=['risk_unbound_group', 'risk_account_disabled', 'group_telegram_key'], name='cad_nogroup_tg_group_idx'),
            models.Index(fields=['risk_auto_renew_off', 'risk_account_disabled', 'group_telegram_key'], name='cad_renewoff_tg_group_idx'),
            models.Index(fields=['is_display_visible', 'risk_rank', 'asset_due_sort_null_rank', 'asset_due_sort_at', '-sort_order', '-asset_id'], name='cad_vis_list_page_idx'),
            models.Index(fields=['provider', 'cloud_account', 'region_code', 'status'], name='cad_provider_scope_idx'),
            models.Index(fields=['risk_unattached_ip', 'is_active', 'status'], name='idx_cad_display_state'),
        ]

    def __str__(self):
        return f'dashboard-snapshot:{self.asset_id}'


class CloudAssetSyncJob(models.Model):
    STATUS_QUEUED = 'queued'
    STATUS_RUNNING = 'running'
    STATUS_SUCCEEDED = 'succeeded'
    STATUS_PARTIAL = 'partial'
    STATUS_FAILED = 'failed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = (
        (STATUS_QUEUED, '排队中'),
        (STATUS_RUNNING, '运行中'),
        (STATUS_SUCCEEDED, '已完成'),
        (STATUS_PARTIAL, '部分完成'),
        (STATUS_FAILED, '失败'),
        (STATUS_CANCELLED, '已取消'),
    )
    TERMINAL_STATUSES = {STATUS_SUCCEEDED, STATUS_PARTIAL, STATUS_FAILED, STATUS_CANCELLED}

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    run_id = models.CharField('运行ID', max_length=32, unique=True, db_index=True, db_comment='运行ID')
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True, db_comment='状态')
    requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name='发起人', on_delete=models.SET_NULL, blank=True, null=True, related_name='cloud_asset_sync_jobs', db_comment='发起人')
    request_payload = models.JSONField('请求参数', default=dict, blank=True, db_comment='请求参数')
    providers = models.JSONField('云厂商范围', default=list, blank=True, db_comment='云厂商范围')
    account_ids = models.JSONField('账号范围', default=list, blank=True, db_comment='账号范围')
    asset_ids = models.JSONField('资产范围', default=list, blank=True, db_comment='资产范围')
    scope = models.JSONField('同步范围', default=dict, blank=True, db_comment='同步范围')
    progress_current = models.PositiveIntegerField('已完成任务数', default=0, db_comment='已完成任务数')
    progress_total = models.PositiveIntegerField('总任务数', default=0, db_comment='总任务数')
    current_task = models.CharField('当前任务', max_length=255, default='', blank=True, db_comment='当前任务')
    logs = models.JSONField('日志摘要', default=list, blank=True, db_comment='日志摘要')
    warnings = models.JSONField('警告', default=list, blank=True, db_comment='警告')
    errors = models.JSONField('错误', default=list, blank=True, db_comment='错误')
    result_payload = models.JSONField('结果载荷', default=dict, blank=True, db_comment='结果载荷')
    worker_id = models.CharField('Worker ID', max_length=64, default='', blank=True, db_index=True, db_comment='Worker ID')
    worker_heartbeat_at = models.DateTimeField('Worker 心跳时间', blank=True, null=True, db_index=True, db_comment='Worker 心跳时间')
    cancel_requested_at = models.DateTimeField('取消请求时间', blank=True, null=True, db_index=True, db_comment='取消请求时间')
    cancel_requested_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name='取消发起人', on_delete=models.SET_NULL, blank=True, null=True, related_name='cancelled_cloud_asset_sync_jobs', db_comment='取消发起人')
    started_at = models.DateTimeField('开始时间', blank=True, null=True, db_index=True, db_comment='开始时间')
    finished_at = models.DateTimeField('结束时间', blank=True, null=True, db_index=True, db_comment='结束时间')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'cloud_asset_sync_job'
        db_table_comment = '云资产同步任务主表'
        verbose_name = '云资产同步任务'
        verbose_name_plural = '云资产同步任务'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['status', 'created_at'], name='idx_cloud_sync_job_status'),
            models.Index(fields=['requested_by', 'created_at'], name='idx_cloud_sync_job_user'),
            models.Index(fields=['status', 'worker_heartbeat_at'], name='idx_cloud_sync_job_heartbeat'),
        ]

    @property
    def is_terminal(self):
        return self.status in self.TERMINAL_STATUSES

    def __str__(self):
        return f'cloud-asset-sync:{self.run_id}:{self.status}'


class CloudAssetSyncJobEvent(models.Model):
    TYPE_QUEUED = 'queued'
    TYPE_CLAIMED = 'claimed'
    TYPE_STATUS = 'status'
    TYPE_PROGRESS = 'progress'
    TYPE_TASK = 'task'
    TYPE_WARNING = 'warning'
    TYPE_ERROR = 'error'
    TYPE_LOG = 'log'
    TYPE_CANCEL = 'cancel'
    TYPE_RETRY = 'retry'
    TYPE_HEARTBEAT = 'heartbeat'
    TYPE_CHOICES = (
        (TYPE_QUEUED, '入队'),
        (TYPE_CLAIMED, '领取'),
        (TYPE_STATUS, '状态'),
        (TYPE_PROGRESS, '进度'),
        (TYPE_TASK, '任务'),
        (TYPE_WARNING, '警告'),
        (TYPE_ERROR, '错误'),
        (TYPE_LOG, '日志'),
        (TYPE_CANCEL, '取消'),
        (TYPE_RETRY, '重试'),
        (TYPE_HEARTBEAT, '心跳'),
    )

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    job_id = models.BigIntegerField('同步任务ID', db_index=True, db_comment='同步任务ID')
    event_type = models.CharField('事件类型', max_length=32, choices=TYPE_CHOICES, db_index=True, db_comment='事件类型')
    status_from = models.CharField('原状态', max_length=32, default='', blank=True, db_comment='原状态')
    status_to = models.CharField('新状态', max_length=32, default='', blank=True, db_index=True, db_comment='新状态')
    message = models.CharField('事件摘要', max_length=255, default='', blank=True, db_comment='事件摘要')
    payload = models.JSONField('事件载荷', default=dict, blank=True, db_comment='事件载荷')
    worker_id = models.CharField('Worker ID', max_length=64, default='', blank=True, db_index=True, db_comment='Worker ID')
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name='操作人', on_delete=models.SET_NULL, blank=True, null=True, related_name='cloud_asset_sync_job_events', db_comment='操作人')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True, db_comment='创建时间')

    class Meta:
        db_table = 'cloud_asset_sync_job_event'
        db_table_comment = '云资产同步任务事件表'
        verbose_name = '云资产同步任务事件'
        verbose_name_plural = '云资产同步任务事件'
        ordering = ['created_at', 'id']
        indexes = [
            models.Index(fields=['job_id', 'created_at'], name='idx_cloud_sync_event_job_time'),
            models.Index(fields=['event_type', 'created_at'], name='idx_cloud_sync_event_type_time'),
            models.Index(fields=['worker_id', 'created_at'], name='idx_cloud_sync_event_worker'),
        ]

    def __str__(self):
        return f'cloud-asset-sync-event:{self.job_id}:{self.event_type}'


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

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    order = models.ForeignKey('cloud.CloudServerOrder', verbose_name='关联订单', on_delete=models.SET_NULL, blank=True, null=True, related_name='ip_logs', db_comment='关联订单')
    asset = models.ForeignKey('cloud.CloudAsset', verbose_name='关联资产', on_delete=models.SET_NULL, blank=True, null=True, related_name='ip_logs', db_comment='关联资产')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='关联用户', on_delete=models.SET_NULL, blank=True, null=True, related_name='cloud_ip_logs', db_comment='关联用户')
    provider = models.CharField('云厂商', max_length=32, blank=True, null=True, db_index=True, db_comment='云厂商')
    region_code = models.CharField('地区代码', max_length=64, blank=True, null=True, db_index=True, db_comment='地区代码')
    region_name = models.CharField('地区名称', max_length=128, blank=True, null=True, db_comment='地区名称')
    order_no = models.CharField('订单号', max_length=191, blank=True, null=True, db_index=True, db_comment='订单号')
    asset_name = models.CharField('资产名称', max_length=191, blank=True, null=True, db_index=True, db_comment='资产名称')
    instance_id = models.CharField('实例ID', max_length=191, blank=True, null=True, db_index=True, db_comment='实例ID')
    provider_resource_id = models.CharField('云资源ID', max_length=191, blank=True, null=True, db_index=True, db_comment='云资源ID')
    public_ip = models.CharField('当前IP', max_length=128, blank=True, null=True, db_index=True, db_comment='当前IP')
    previous_public_ip = models.CharField('上一个IP', max_length=128, blank=True, null=True, db_index=True, db_comment='上一个IP')
    event_type = models.CharField('事件类型', max_length=32, choices=EVENT_CHOICES, db_index=True, db_comment='事件类型')
    note = models.TextField('说明', blank=True, null=True, db_comment='说明')
    created_at = models.DateTimeField('记录时间', auto_now_add=True, db_index=True, db_comment='记录时间')

    class Meta:
        db_table = 'cloud_ip_log'
        db_table_comment = '云资产公网IP变更和回收日志表'
        verbose_name = '云IP日志'
        verbose_name_plural = '云IP日志'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['event_type', '-id'], name='cil_event_id_desc_idx'),
        ]

    def __str__(self):
        ip = self.public_ip or self.previous_public_ip or '-'
        return f'{self.order_no or self.asset_name or self.instance_id or "ip-log"} {self.event_type} {ip}'


class CloudLifecyclePlanNote(models.Model):
    PLAN_KIND_SHUTDOWN_ORDER = 'shutdown_order'
    PLAN_KIND_ORPHAN_ASSET_DELETE = 'orphan_asset_delete'
    PLAN_KIND_UNATTACHED_IP_DELETE = 'unattached_ip_delete'
    PLAN_KIND_CHOICES = (
        (PLAN_KIND_SHUTDOWN_ORDER, '订单删机计划'),
        (PLAN_KIND_ORPHAN_ASSET_DELETE, '无订单资产删机计划'),
        (PLAN_KIND_UNATTACHED_IP_DELETE, '未附加固定IP删除计划'),
    )

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    plan_kind = models.CharField('计划类型', max_length=64, choices=PLAN_KIND_CHOICES, db_index=True, db_comment='计划类型')
    order = models.ForeignKey('cloud.CloudServerOrder', verbose_name='关联订单', on_delete=models.SET_NULL, blank=True, null=True, related_name='lifecycle_plan_notes', db_comment='关联订单')
    asset = models.ForeignKey('cloud.CloudAsset', verbose_name='关联资产', on_delete=models.SET_NULL, blank=True, null=True, related_name='lifecycle_plan_notes', db_comment='关联资产')
    note = models.TextField('备注', blank=True, null=True, db_comment='备注')
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name='创建人', on_delete=models.SET_NULL, blank=True, null=True, related_name='created_cloud_lifecycle_plan_notes', db_comment='创建人')
    updated_by = models.ForeignKey(settings.AUTH_USER_MODEL, verbose_name='更新人', on_delete=models.SET_NULL, blank=True, null=True, related_name='updated_cloud_lifecycle_plan_notes', db_comment='更新人')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'cloud_lifecycle_plan_note'
        db_table_comment = '生命周期删除计划人工备注表'
        verbose_name = '删除计划备注'
        verbose_name_plural = '删除计划备注'
        ordering = ['-updated_at', '-id']
        indexes = [
            models.Index(fields=['plan_kind', 'order'], name='idx_plan_note_kind_order'),
            models.Index(fields=['plan_kind', 'asset'], name='idx_plan_note_kind_asset'),
        ]

    def __str__(self):
        target = self.order_id or self.asset_id or '-'
        return f'{self.plan_kind}:{target}'


class CloudLifecycleTask(models.Model):
    TASK_SUSPEND = 'suspend'
    TASK_DELETE = 'delete'
    TASK_RECYCLE = 'recycle'
    TASK_MIGRATION_DELETE = 'migration_delete'
    TASK_ORPHAN_ASSET_DELETE = 'orphan_asset_delete'
    TASK_UNATTACHED_IP_DELETE = 'unattached_ip_delete'
    TASK_TYPE_CHOICES = (
        (TASK_SUSPEND, '计划关机'),
        (TASK_DELETE, '计划删机'),
        (TASK_RECYCLE, '固定IP回收'),
        (TASK_MIGRATION_DELETE, '迁移旧机删除'),
        (TASK_ORPHAN_ASSET_DELETE, '无订单资产删除'),
        (TASK_UNATTACHED_IP_DELETE, '未附加固定IP删除'),
    )

    SOURCE_ORDER = 'order'
    SOURCE_ASSET = 'asset'
    SOURCE_KIND_CHOICES = (
        (SOURCE_ORDER, '订单'),
        (SOURCE_ASSET, '资产'),
    )

    STATUS_PENDING = 'pending'
    STATUS_CLAIMED = 'claimed'
    STATUS_DONE = 'done'
    STATUS_SKIPPED = 'skipped'
    STATUS_FAILED = 'failed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = (
        (STATUS_PENDING, '待执行'),
        (STATUS_CLAIMED, '执行中'),
        (STATUS_DONE, '已完成'),
        (STATUS_SKIPPED, '已跳过'),
        (STATUS_FAILED, '执行失败'),
        (STATUS_CANCELLED, '已取消'),
    )

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    source_key = models.CharField('来源唯一键', max_length=191, unique=True, db_comment='生命周期任务幂等来源唯一键')
    task_type = models.CharField('任务类型', max_length=64, choices=TASK_TYPE_CHOICES, db_index=True, db_comment='任务类型')
    source_kind = models.CharField('来源类型', max_length=32, choices=SOURCE_KIND_CHOICES, db_index=True, db_comment='来源类型')
    order = models.ForeignKey('cloud.CloudServerOrder', verbose_name='关联订单', on_delete=models.SET_NULL, blank=True, null=True, related_name='lifecycle_tasks', db_comment='关联订单')
    asset = models.ForeignKey('cloud.CloudAsset', verbose_name='关联资产', on_delete=models.SET_NULL, blank=True, null=True, related_name='lifecycle_tasks', db_comment='关联资产')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='关联用户', on_delete=models.SET_NULL, blank=True, null=True, related_name='cloud_lifecycle_tasks', db_comment='关联用户')
    scheduled_at = models.DateTimeField('计划执行时间', db_index=True, db_comment='生命周期任务计划执行时间')
    basis_actual_expires_at = models.DateTimeField('依据资产到期时间', blank=True, null=True, db_comment='生成任务时引用的CloudAsset.actual_expires_at快照')
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True, db_comment='状态')
    claim_token = models.CharField('认领令牌', max_length=64, blank=True, db_index=True, db_comment='任务执行器认领令牌，用于并发执行隔离')
    claimed_at = models.DateTimeField('认领时间', blank=True, null=True, db_index=True, db_comment='认领时间')
    attempt_count = models.PositiveIntegerField('尝试次数', default=0, db_comment='尝试次数')
    last_error = models.TextField('最后错误', blank=True, db_comment='最后错误')
    last_run_at = models.DateTimeField('最后执行时间', blank=True, null=True, db_comment='最后执行时间')
    completed_at = models.DateTimeField('完成时间', blank=True, null=True, db_comment='完成时间')
    payload = models.JSONField('计划载荷', default=dict, blank=True, db_comment='计划载荷')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'cloud_lifecycle_task'
        db_table_comment = '云资产生命周期执行任务表'
        verbose_name = '生命周期任务'
        verbose_name_plural = '生命周期任务'
        ordering = ['scheduled_at', 'id']
        indexes = [
            models.Index(fields=['status', 'scheduled_at'], name='clt_status_due_idx'),
            models.Index(fields=['task_type', 'status', 'scheduled_at'], name='clt_type_status_due_idx'),
            models.Index(fields=['source_kind', 'order', 'task_type'], name='clt_order_task_idx'),
            models.Index(fields=['source_kind', 'asset', 'task_type'], name='clt_asset_task_idx'),
        ]

    def __str__(self):
        return f'{self.task_type}:{self.source_key}:{self.status}'


class CloudNoticeTask(models.Model):
    NOTICE_RENEW = 'renew_notice'
    NOTICE_AUTO_RENEW = 'auto_renew_notice'
    NOTICE_DELETE = 'delete_notice'
    NOTICE_RECYCLE = 'recycle_notice'
    NOTICE_TYPE_CHOICES = (
        (NOTICE_RENEW, '到期提醒'),
        (NOTICE_AUTO_RENEW, '自动续费预提醒'),
        (NOTICE_DELETE, '删机提醒'),
        (NOTICE_RECYCLE, 'IP回收提醒'),
    )

    STATUS_PENDING = 'pending'
    STATUS_CLAIMED = 'claimed'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_CANCELLED = 'cancelled'
    STATUS_CHOICES = (
        (STATUS_PENDING, '待通知'),
        (STATUS_CLAIMED, '通知中'),
        (STATUS_SENT, '已通知'),
        (STATUS_FAILED, '通知失败'),
        (STATUS_CANCELLED, '已取消'),
    )

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    source_key = models.CharField('来源唯一键', max_length=191, unique=True, db_comment='通知任务幂等来源唯一键')
    notice_type = models.CharField('通知类型', max_length=64, choices=NOTICE_TYPE_CHOICES, db_index=True, db_comment='通知类型')
    order = models.ForeignKey('cloud.CloudServerOrder', verbose_name='关联订单', on_delete=models.SET_NULL, blank=True, null=True, related_name='notice_tasks', db_comment='关联订单')
    asset = models.ForeignKey('cloud.CloudAsset', verbose_name='关联资产', on_delete=models.SET_NULL, blank=True, null=True, related_name='notice_tasks', db_comment='关联资产')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='关联用户', on_delete=models.SET_NULL, blank=True, null=True, related_name='cloud_notice_tasks', db_comment='关联用户')
    target_chat_id = models.BigIntegerField('目标群组/会话ID', blank=True, null=True, db_index=True, db_comment='目标群组/会话ID')
    notice_at = models.DateTimeField('计划通知时间', db_index=True, db_comment='通知任务计划发送时间')
    basis_actual_expires_at = models.DateTimeField('依据资产到期时间', blank=True, null=True, db_comment='生成通知时引用的CloudAsset.actual_expires_at快照')
    batch_id = models.CharField('通知批次', max_length=191, blank=True, db_index=True, db_comment='通知批次')
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True, db_comment='状态')
    claim_token = models.CharField('认领令牌', max_length=64, blank=True, db_index=True, db_comment='通知执行器认领令牌，用于并发发送隔离')
    claimed_at = models.DateTimeField('认领时间', blank=True, null=True, db_index=True, db_comment='认领时间')
    attempt_count = models.PositiveIntegerField('尝试次数', default=0, db_comment='尝试次数')
    last_error = models.TextField('最后错误', blank=True, db_comment='最后错误')
    last_run_at = models.DateTimeField('最后执行时间', blank=True, null=True, db_comment='最后执行时间')
    sent_at = models.DateTimeField('通知成功时间', blank=True, null=True, db_comment='通知成功时间')
    payload = models.JSONField('计划载荷', default=dict, blank=True, db_comment='计划载荷')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'cloud_notice_task'
        db_table_comment = '云资产用户通知任务表'
        verbose_name = '通知任务'
        verbose_name_plural = '通知任务'
        ordering = ['notice_at', 'id']
        indexes = [
            models.Index(fields=['status', 'notice_at'], name='cnt_status_due_idx'),
            models.Index(fields=['notice_type', 'status', 'notice_at'], name='cnt_type_status_due_idx'),
            models.Index(fields=['order', 'notice_type'], name='cnt_order_type_idx'),
            models.Index(fields=['user', 'notice_type', 'status'], name='cnt_user_type_status_idx'),
        ]

    def __str__(self):
        return f'{self.notice_type}:{self.source_key}:{self.status}'


class CloudAutoRenewRetryTask(models.Model):
    STATUS_PENDING = 'pending'
    STATUS_SUCCEEDED = 'succeeded'
    STATUS_CANCELLED = 'cancelled'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, '待重试'),
        (STATUS_SUCCEEDED, '已成功'),
        (STATUS_CANCELLED, '已取消'),
        (STATUS_FAILED, '重试失败'),
    ]

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    order = models.ForeignKey('cloud.CloudServerOrder', verbose_name='云服务器订单', on_delete=models.CASCADE, related_name='auto_renew_retry_tasks', db_comment='云服务器订单')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.SET_NULL, blank=True, null=True, related_name='auto_renew_retry_tasks', db_comment='用户')
    order_no = models.CharField('订单号', max_length=191, db_index=True, db_comment='订单号')
    ip = models.CharField('公网IP', max_length=128, blank=True, null=True, db_index=True, db_comment='公网IP')
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True, db_comment='状态')
    failure_reason = models.TextField('失败原因', blank=True, null=True, db_comment='失败原因')
    last_error = models.TextField('最后错误', blank=True, null=True, db_comment='最后错误')
    attempts = models.PositiveIntegerField('重试次数', default=0, db_comment='重试次数')
    next_check_at = models.DateTimeField('下次检查时间', db_index=True, db_comment='自动续费失败后的下一次重试检查时间')
    last_checked_at = models.DateTimeField('最后检查时间', blank=True, null=True, db_comment='最后检查时间')
    succeeded_at = models.DateTimeField('成功时间', blank=True, null=True, db_comment='成功时间')
    cancelled_at = models.DateTimeField('取消时间', blank=True, null=True, db_comment='取消时间')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_index=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'cloud_auto_renew_retry_task'
        db_table_comment = '云服务器自动续费失败重试任务表'
        verbose_name = '自动续费重试任务'
        verbose_name_plural = '自动续费重试任务'
        ordering = ['next_check_at', 'id']
        indexes = [
            models.Index(fields=['status', 'next_check_at'], name='idx_auto_renew_retry_due'),
            models.Index(fields=['order', 'status'], name='idx_auto_renew_retry_order'),
        ]

    def __str__(self):
        return f'{self.order_no} {self.ip or "-"} {self.status}'


class CloudAutoRenewPatrolLog(models.Model):
    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    order = models.ForeignKey('cloud.CloudServerOrder', verbose_name='云服务器订单', on_delete=models.SET_NULL, blank=True, null=True, related_name='auto_renew_patrol_logs', db_comment='云服务器订单')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.SET_NULL, blank=True, null=True, related_name='auto_renew_patrol_logs', db_comment='用户')
    batch_id = models.CharField('巡检批次', max_length=64, db_index=True, db_comment='巡检批次')
    order_no = models.CharField('订单号', max_length=191, db_index=True, db_comment='订单号')
    ip = models.CharField('公网IP', max_length=128, db_index=True, db_comment='公网IP')
    provider = models.CharField('云厂商', max_length=32, blank=True, null=True, db_index=True, db_comment='云厂商')
    user_display_name = models.CharField('用户显示名', max_length=191, blank=True, null=True, db_comment='用户显示名')
    username_label = models.CharField('用户名', max_length=191, blank=True, null=True, db_comment='用户名')
    tg_user_id = models.BigIntegerField('Telegram ID', blank=True, null=True, db_index=True, db_comment='Telegram ID')
    is_success = models.BooleanField('是否成功', default=False, db_index=True, db_comment='是否成功')
    failure_reason = models.TextField('失败原因', blank=True, null=True, db_comment='失败原因')
    currency = models.CharField('币种', max_length=32, default='USDT', db_comment='币种')
    balance_before = models.DecimalField('余额变更前', max_digits=18, decimal_places=6, blank=True, null=True, db_comment='余额变更前')
    balance_after = models.DecimalField('余额变更后', max_digits=18, decimal_places=6, blank=True, null=True, db_comment='余额变更后')
    balance_change = models.DecimalField('余额变化', max_digits=18, decimal_places=6, blank=True, null=True, db_comment='余额变化')
    completed_order_id = models.BigIntegerField('续费后订单ID', blank=True, null=True, db_index=True, db_comment='续费后订单ID')
    completed_order_no = models.CharField('续费后订单号', max_length=191, blank=True, null=True, db_index=True, db_comment='续费后订单号')
    executed_at = models.DateTimeField('执行时间', auto_now_add=True, db_index=True, db_comment='执行时间')

    class Meta:
        db_table = 'cloud_auto_renew_patrol_log'
        db_table_comment = '云服务器自动续费巡检执行日志表'
        verbose_name = '自动续费巡检日志'
        verbose_name_plural = '自动续费巡检日志'
        ordering = ['-executed_at', '-id']
        indexes = [
            models.Index(fields=['batch_id', '-executed_at'], name='idx_auto_renew_batch'),
            models.Index(fields=['order_no', '-executed_at'], name='idx_auto_renew_order'),
            models.Index(fields=['tg_user_id', '-executed_at'], name='idx_auto_renew_user'),
        ]

    def __str__(self):
        return f'{self.order_no} {self.ip} {self.executed_at}'




class CloudUserNoticeLog(models.Model):
    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.SET_NULL, blank=True, null=True, related_name='cloud_notice_logs', db_comment='用户')
    order = models.ForeignKey('cloud.CloudServerOrder', verbose_name='云服务器订单', on_delete=models.SET_NULL, blank=True, null=True, related_name='notice_logs', db_comment='云服务器订单')
    batch_id = models.CharField('通知批次', max_length=64, blank=True, default='', db_index=True, db_comment='通知批次')
    event_type = models.CharField('通知类型', max_length=64, db_index=True, db_comment='通知类型')
    target_chat_id = models.BigIntegerField('目标聊天ID', blank=True, null=True, db_index=True, db_comment='目标聊天ID')
    order_no = models.CharField('订单号', max_length=191, blank=True, null=True, db_index=True, db_comment='订单号')
    ip = models.CharField('IP', max_length=128, blank=True, null=True, db_index=True, db_comment='IP')
    is_batch = models.BooleanField('是否批量', default=False, db_index=True, db_comment='是否批量')
    delivered = models.BooleanField('是否送达', default=False, db_index=True, db_comment='是否送达')
    text_preview = models.TextField('通知预览', blank=True, null=True, db_comment='通知预览')
    extra = models.JSONField('额外信息', default=dict, blank=True, db_comment='额外信息')
    created_at = models.DateTimeField('记录时间', auto_now_add=True, db_index=True, db_comment='记录时间')

    class Meta:
        db_table = 'cloud_user_notice_log'
        db_table_comment = '云服务器用户通知发送日志表'
        verbose_name = '云通知日志'
        verbose_name_plural = '云通知日志'
        ordering = ['-created_at', '-id']
        indexes = [
            models.Index(fields=['user', '-created_at'], name='idx_cloud_notice_user'),
            models.Index(fields=['event_type', '-created_at'], name='idx_cloud_notice_event'),
            models.Index(fields=['batch_id', '-created_at'], name='idx_cloud_notice_batch'),
        ]

    def __str__(self):
        return f'{self.event_type} {self.order_no or "-"} {self.created_at}'




class AddressMonitor(models.Model):
    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.CASCADE, db_comment='用户')
    address = models.CharField('监控地址', max_length=191, db_index=True, db_comment='监控地址')
    remark = models.TextField('备注', blank=True, null=True, db_comment='备注')
    monitor_transfers = models.BooleanField('监控转账', default=True, db_comment='监控转账')
    monitor_resources = models.BooleanField('监控资源', default=False, db_comment='监控资源')
    last_energy = models.BigIntegerField('上次可用能量', default=0, db_comment='上次可用能量')
    last_bandwidth = models.BigIntegerField('上次可用带宽', default=0, db_comment='上次可用带宽')
    resource_checked_at = models.DateTimeField('资源检查时间', blank=True, null=True, db_comment='资源检查时间')
    usdt_threshold = models.DecimalField('USDT阈值', max_digits=18, decimal_places=6, default=1, db_comment='USDT阈值')
    trx_threshold = models.DecimalField('TRX阈值', max_digits=18, decimal_places=6, default=1, db_comment='TRX阈值')
    energy_threshold = models.BigIntegerField('能量增加阈值', default=1, db_comment='能量增加阈值')
    bandwidth_threshold = models.BigIntegerField('带宽增加阈值', default=1, db_comment='带宽增加阈值')
    daily_income = models.DecimalField('今日收入', max_digits=18, decimal_places=6, default=0, db_comment='今日收入')
    daily_expense = models.DecimalField('今日支出', max_digits=18, decimal_places=6, default=0, db_comment='今日支出')
    daily_income_currency = models.CharField('收入币种', max_length=32, default='USDT', db_comment='收入币种')
    daily_expense_currency = models.CharField('支出币种', max_length=32, default='USDT', db_comment='支出币种')
    stats_date = models.CharField('统计日期', max_length=32, blank=True, null=True, db_comment='统计日期')
    is_active = models.BooleanField('启用', default=True, db_comment='启用')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')

    class Meta:
        db_table = 'cloud_address_monitor'
        db_table_comment = '链上地址监控配置表'
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

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.CASCADE, related_name='daily_address_stats', db_comment='用户')
    monitor = models.ForeignKey('cloud.AddressMonitor', verbose_name='监控地址', on_delete=models.SET_NULL, blank=True, null=True, related_name='daily_stats', db_comment='监控地址')
    account_scope = models.CharField('账户归属类型', max_length=32, choices=ACCOUNT_SCOPE_CHOICES, default=ACCOUNT_SCOPE_PLATFORM, db_index=True, db_comment='账户归属类型')
    account_key = models.CharField('账户标识', max_length=191, blank=True, null=True, db_index=True, db_comment='账户标识')
    address = models.CharField('地址', max_length=191, db_index=True, db_comment='地址')
    currency = models.CharField('币种', max_length=32, db_index=True, db_comment='币种')
    stats_date = models.DateField('统计日期', db_index=True, db_comment='统计日期')
    income = models.DecimalField('收入', max_digits=18, decimal_places=6, default=Decimal('0'), db_comment='收入')
    expense = models.DecimalField('支出', max_digits=18, decimal_places=6, default=Decimal('0'), db_comment='支出')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'cloud_address_stat_daily'
        db_table_comment = '链上地址每日收支统计表'
        verbose_name = '每日地址统计'
        verbose_name_plural = '每日地址统计'
        ordering = ['-stats_date', '-updated_at', '-id']
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'address', 'currency', 'stats_date', 'account_scope', 'account_key'],
                name='uniq_daily_address_stat_scope',
            ),
        ]

    @property
    def profit(self):
        return (self.income or Decimal('0')) - (self.expense or Decimal('0'))

    def __str__(self):
        return f'{self.address} {self.currency} {self.stats_date}'


class ResourceSnapshot(models.Model):
    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    monitor = models.ForeignKey('cloud.AddressMonitor', verbose_name='监控地址', on_delete=models.CASCADE, related_name='resource_snapshots', db_comment='监控地址')
    account_scope = models.CharField('账户归属类型', max_length=32, choices=DailyAddressStat.ACCOUNT_SCOPE_CHOICES, default=DailyAddressStat.ACCOUNT_SCOPE_PLATFORM, db_index=True, db_comment='账户归属类型')
    account_key = models.CharField('账户标识', max_length=191, blank=True, null=True, db_index=True, db_comment='账户标识')
    address = models.CharField('地址', max_length=191, db_index=True, db_comment='地址')
    energy = models.BigIntegerField('可用能量', default=0, db_comment='可用能量')
    bandwidth = models.BigIntegerField('可用带宽', default=0, db_comment='可用带宽')
    delta_energy = models.BigIntegerField('能量变化', default=0, db_comment='能量变化')
    delta_bandwidth = models.BigIntegerField('带宽变化', default=0, db_comment='带宽变化')
    captured_at = models.DateTimeField('采集时间', auto_now_add=True, db_index=True, db_comment='采集时间')

    class Meta:
        db_table = 'cloud_resource_snapshot'
        db_table_comment = '链上地址资源快照表'
        verbose_name = '资源快照'
        verbose_name_plural = '资源快照'
        ordering = ['-captured_at', '-id']

    def __str__(self):
        return f'{self.address} {self.captured_at}'


__all__ = [
    'AddressMonitor',
    'CloudAsset',
    'CloudIpLog',
    'CloudLifecyclePlanNote',
    'CloudLifecycleTask',
    'CloudNoticeTask',
    'CloudUserNoticeLog',
    'CloudServerOrder',
    'CloudAutoRenewPatrolLog',
    'CloudAutoRenewRetryTask',
    'CloudServerPlan',
    'DailyAddressStat',
    'ResourceSnapshot',
    'ServerPrice',
]
