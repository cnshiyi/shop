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
