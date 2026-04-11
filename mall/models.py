from django.db import models


class Product(models.Model):
    CONTENT_TEXT = 'text'
    CONTENT_IMAGE = 'image'
    CONTENT_VIDEO = 'video'
    CONTENT_CHOICES = (
        (CONTENT_TEXT, '文本'),
        (CONTENT_IMAGE, '图片'),
        (CONTENT_VIDEO, '视频'),
    )

    name = models.TextField('商品名称')
    description = models.TextField('商品描述', blank=True, null=True)
    price = models.DecimalField('商品单价(USDT)', max_digits=18, decimal_places=6)
    content_type = models.CharField('内容类型', max_length=32, choices=CONTENT_CHOICES, default=CONTENT_TEXT)
    content_text = models.TextField('文本内容', blank=True, null=True)
    content_image = models.TextField('图片File ID/URL', blank=True, null=True)
    content_video = models.TextField('视频File ID/URL', blank=True, null=True)
    stock = models.IntegerField('库存', default=-1, help_text='-1 表示无限库存')
    is_active = models.BooleanField('上架', default=True)
    sort_order = models.IntegerField('排序', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'products'
        verbose_name = '商品'
        verbose_name_plural = '商品'
        ordering = ['-sort_order', '-id']

    def __str__(self):
        return self.name


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
    plan_name = models.CharField('套餐名称', max_length=191)
    cpu = models.CharField('CPU', max_length=64, blank=True, null=True)
    memory = models.CharField('内存', max_length=64, blank=True, null=True)
    storage = models.CharField('存储', max_length=64, blank=True, null=True)
    bandwidth = models.CharField('带宽', max_length=64, blank=True, null=True)
    price = models.DecimalField('价格', max_digits=18, decimal_places=6)
    currency = models.CharField('币种', max_length=32, default='USDT')
    is_active = models.BooleanField('启用', default=True)
    sort_order = models.IntegerField('排序', default=0)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cloud_server_plans'
        verbose_name = '云服务器套餐'
        verbose_name_plural = '云服务器套餐'
        ordering = ['provider', 'region_name', '-sort_order', 'id']
        unique_together = ('provider', 'region_code', 'plan_name')

    def __str__(self):
        return f'{self.region_name} {self.plan_name}'


class CloudServerOrder(models.Model):
    STATUS_CHOICES = (
        ('pending', '待支付'),
        ('paid', '已支付'),
        ('provisioning', '创建中'),
        ('completed', '已创建'),
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
    user = models.ForeignKey('accounts.TelegramUser', verbose_name='用户', on_delete=models.CASCADE)
    plan = models.ForeignKey('mall.CloudServerPlan', verbose_name='套餐', on_delete=models.PROTECT)
    provider = models.CharField('云厂商', max_length=32, db_index=True)
    region_code = models.CharField('地区代码', max_length=64, db_index=True)
    region_name = models.CharField('地区名称', max_length=128)
    plan_name = models.CharField('套餐名称', max_length=191)
    currency = models.CharField('币种', max_length=32, choices=CURRENCY_CHOICES, default='USDT', db_index=True)
    total_amount = models.DecimalField('总金额', max_digits=18, decimal_places=6)
    pay_amount = models.DecimalField('应付金额', max_digits=18, decimal_places=9, blank=True, null=True)
    pay_method = models.CharField('支付方式', max_length=32, choices=PAY_METHOD_CHOICES, default='address')
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default='pending', db_index=True)
    tx_hash = models.CharField('交易哈希', max_length=191, unique=True, blank=True, null=True)
    image_name = models.CharField('镜像', max_length=128, default='debian')
    mtproxy_port = models.IntegerField('MTProxy端口', default=9528)
    instance_id = models.CharField('实例ID', max_length=191, blank=True, null=True)
    public_ip = models.CharField('公网IP', max_length=128, blank=True, null=True)
    login_user = models.CharField('登录账号', max_length=64, blank=True, null=True)
    login_password = models.CharField('登录密码', max_length=191, blank=True, null=True)
    provision_note = models.TextField('创建说明', blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    paid_at = models.DateTimeField('支付时间', blank=True, null=True)
    expired_at = models.DateTimeField('过期时间', blank=True, null=True)
    completed_at = models.DateTimeField('完成时间', blank=True, null=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'cloud_server_orders'
        verbose_name = '云服务器订单'
        verbose_name_plural = '云服务器订单'
        ordering = ['-created_at']

    def __str__(self):
        return self.order_no


class Order(models.Model):
    STATUS_CHOICES = (
        ('pending', '待支付'),
        ('paid', '已支付'),
        ('delivered', '已发货'),
        ('cancelled', '已取消'),
        ('expired', '已过期'),
    )
    PAY_METHOD_CHOICES = (
        ('balance', '余额支付'),
        ('address', '地址支付'),
    )
    CURRENCY_CHOICES = (
        ('USDT', 'USDT'),
        ('TRX', 'TRX'),
    )

    order_no = models.CharField('订单号', max_length=191, unique=True, db_index=True)
    user = models.ForeignKey('accounts.TelegramUser', verbose_name='用户', on_delete=models.CASCADE)
    product = models.ForeignKey(Product, verbose_name='商品', on_delete=models.PROTECT)
    product_name = models.TextField('商品名称')
    quantity = models.IntegerField('数量', default=1)
    currency = models.CharField('币种', max_length=32, choices=CURRENCY_CHOICES, default='USDT', db_index=True)
    total_amount = models.DecimalField('总金额', max_digits=18, decimal_places=6)
    pay_amount = models.DecimalField('应付金额', max_digits=18, decimal_places=9, blank=True, null=True)
    pay_method = models.CharField('支付方式', max_length=32, choices=PAY_METHOD_CHOICES)
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default='pending', db_index=True)
    tx_hash = models.CharField('交易哈希', max_length=191, unique=True, blank=True, null=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    paid_at = models.DateTimeField('支付时间', blank=True, null=True)
    expired_at = models.DateTimeField('过期时间', blank=True, null=True)

    class Meta:
        db_table = 'orders'
        verbose_name = '订单'
        verbose_name_plural = '订单'
        ordering = ['-created_at']

    def __str__(self):
        return self.order_no
