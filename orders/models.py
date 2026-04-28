"""orders 域模型。"""

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
        db_table = 'order_product'
        verbose_name = '商品'
        verbose_name_plural = '商品'
        ordering = ['-sort_order', '-id']

    def __str__(self):
        return self.name


class CartItem(models.Model):
    ITEM_PRODUCT = 'product'
    ITEM_CLOUD_PLAN = 'cloud_plan'
    ITEM_TYPE_CHOICES = (
        (ITEM_PRODUCT, '商品'),
        (ITEM_CLOUD_PLAN, '云套餐'),
    )

    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.CASCADE, related_name='cart_items')
    item_type = models.CharField('项目类型', max_length=32, choices=ITEM_TYPE_CHOICES, default=ITEM_PRODUCT)
    product = models.ForeignKey('orders.Product', verbose_name='商品', on_delete=models.CASCADE, related_name='cart_items', blank=True, null=True)
    cloud_plan = models.ForeignKey('cloud.CloudServerPlan', verbose_name='云套餐', on_delete=models.CASCADE, related_name='cart_items', blank=True, null=True)
    quantity = models.IntegerField('数量', default=1)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    updated_at = models.DateTimeField('更新时间', auto_now=True)

    class Meta:
        db_table = 'order_cart_item'
        verbose_name = '购物车项'
        verbose_name_plural = '购物车项'
        ordering = ['-updated_at', '-id']

    def __str__(self):
        target_id = self.product_id or self.cloud_plan_id
        return f'{self.user_id}:{self.item_type}:{target_id} x {self.quantity}'


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
    payer_address = models.CharField('链上付款地址', max_length=191, blank=True, null=True, db_index=True)
    receive_address = models.CharField('链上收款地址', max_length=191, blank=True, null=True, db_index=True)
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
    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.CASCADE)
    product = models.ForeignKey('orders.Product', verbose_name='商品', on_delete=models.PROTECT)
    product_name = models.TextField('商品名称')
    quantity = models.IntegerField('数量', default=1)
    currency = models.CharField('币种', max_length=32, choices=CURRENCY_CHOICES, default='USDT', db_index=True)
    total_amount = models.DecimalField('总金额', max_digits=18, decimal_places=6)
    pay_amount = models.DecimalField('应付金额', max_digits=18, decimal_places=9, blank=True, null=True)
    pay_method = models.CharField('支付方式', max_length=32, choices=PAY_METHOD_CHOICES)
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default='pending', db_index=True)
    tx_hash = models.CharField('交易哈希', max_length=191, unique=True, blank=True, null=True)
    payer_address = models.CharField('链上付款地址', max_length=191, blank=True, null=True, db_index=True)
    receive_address = models.CharField('链上收款地址', max_length=191, blank=True, null=True, db_index=True)
    created_at = models.DateTimeField('创建时间', auto_now_add=True)
    paid_at = models.DateTimeField('支付时间', blank=True, null=True)
    expired_at = models.DateTimeField('过期时间', blank=True, null=True)

    class Meta:
        db_table = 'order_order'
        verbose_name = '订单'
        verbose_name_plural = '订单'
        ordering = ['-created_at']

    def __str__(self):
        return self.order_no


__all__ = [
    'BalanceLedger',
    'CartItem',
    'Order',
    'Product',
    'Recharge',
]
