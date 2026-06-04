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

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    name = models.TextField('商品名称', db_comment='商品名称')
    description = models.TextField('商品描述', blank=True, null=True, db_comment='商品描述')
    price = models.DecimalField('商品单价(USDT)', max_digits=18, decimal_places=6, db_comment='商品单价(USDT)')
    content_type = models.CharField('内容类型', max_length=32, choices=CONTENT_CHOICES, default=CONTENT_TEXT, db_comment='内容类型')
    content_text = models.TextField('文本内容', blank=True, null=True, db_comment='文本内容')
    content_image = models.TextField('图片File ID/URL', blank=True, null=True, db_comment='图片File ID/URL')
    content_video = models.TextField('视频File ID/URL', blank=True, null=True, db_comment='视频File ID/URL')
    stock = models.IntegerField('库存', default=-1, help_text='-1 表示无限库存', db_comment='库存')
    is_active = models.BooleanField('上架', default=True, db_comment='上架')
    sort_order = models.IntegerField('排序', default=0, db_comment='排序')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'order_product'
        db_table_comment = '普通商品配置表'
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

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.CASCADE, related_name='cart_items', db_comment='用户')
    item_type = models.CharField('项目类型', max_length=32, choices=ITEM_TYPE_CHOICES, default=ITEM_PRODUCT, db_comment='项目类型')
    product = models.ForeignKey('orders.Product', verbose_name='商品', on_delete=models.CASCADE, related_name='cart_items', blank=True, null=True, db_comment='商品')
    cloud_plan = models.ForeignKey('cloud.CloudServerPlan', verbose_name='云套餐', on_delete=models.CASCADE, related_name='cart_items', blank=True, null=True, db_comment='云套餐')
    quantity = models.IntegerField('数量', default=1, db_comment='数量')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')
    updated_at = models.DateTimeField('更新时间', auto_now=True, db_comment='更新时间')

    class Meta:
        db_table = 'order_cart_item'
        db_table_comment = '用户购物车明细表'
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

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', related_name='balance_ledgers', on_delete=models.CASCADE, db_comment='用户')
    type = models.CharField('类型', max_length=64, choices=TYPE_CHOICES, db_index=True, db_comment='类型')
    direction = models.CharField('方向', max_length=16, choices=DIRECTION_CHOICES, db_index=True, db_comment='方向')
    currency = models.CharField('币种', max_length=32, choices=CURRENCY_CHOICES, db_index=True, db_comment='币种')
    amount = models.DecimalField('变动金额', max_digits=18, decimal_places=9, db_comment='本次余额变动金额')
    before_balance = models.DecimalField('变动前余额', max_digits=18, decimal_places=9, db_comment='变动前该币种可用余额')
    after_balance = models.DecimalField('变动后余额', max_digits=18, decimal_places=9, db_comment='变动后该币种可用余额')
    related_type = models.CharField('关联类型', max_length=64, blank=True, null=True, db_index=True, db_comment='关联类型')
    related_id = models.BigIntegerField('关联ID', blank=True, null=True, db_index=True, db_comment='关联ID')
    description = models.TextField('说明', blank=True, null=True, db_comment='说明')
    operator = models.CharField('操作人', max_length=191, blank=True, null=True, db_comment='操作人')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')

    class Meta:
        db_table = 'order_balance_ledger'
        db_table_comment = '用户余额变动流水表'
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

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.CASCADE, db_comment='用户')
    currency = models.CharField('币种', max_length=32, choices=CURRENCY_CHOICES, default='USDT', db_index=True, db_comment='币种')
    amount = models.DecimalField('充值金额', max_digits=18, decimal_places=6, db_comment='充值金额')
    pay_amount = models.DecimalField('支付金额', max_digits=18, decimal_places=9, db_comment='链上实际应支付金额')
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default='pending', db_index=True, db_comment='状态')
    tx_hash = models.CharField('交易哈希', max_length=191, unique=True, blank=True, null=True, db_comment='链上充值交易哈希，去重入账依据')
    payer_address = models.CharField('链上付款地址', max_length=191, blank=True, null=True, db_index=True, db_comment='链上付款地址')
    receive_address = models.CharField('链上收款地址', max_length=191, blank=True, null=True, db_index=True, db_comment='链上收款地址')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')
    completed_at = models.DateTimeField('完成时间', blank=True, null=True, db_comment='完成时间')
    expired_at = models.DateTimeField('过期时间', blank=True, null=True, db_comment='过期时间')

    class Meta:
        db_table = 'order_recharge'
        db_table_comment = '用户链上充值记录表'
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

    id = models.BigAutoField('ID', primary_key=True, db_comment='主键ID')
    order_no = models.CharField('订单号', max_length=191, unique=True, db_index=True, db_comment='普通商品订单唯一编号')
    user = models.ForeignKey('bot.TelegramUser', verbose_name='用户', on_delete=models.CASCADE, db_comment='用户')
    product = models.ForeignKey('orders.Product', verbose_name='商品', on_delete=models.PROTECT, db_comment='商品')
    product_name = models.TextField('商品名称', db_comment='商品名称')
    quantity = models.IntegerField('数量', default=1, db_comment='数量')
    currency = models.CharField('币种', max_length=32, choices=CURRENCY_CHOICES, default='USDT', db_index=True, db_comment='币种')
    total_amount = models.DecimalField('总金额', max_digits=18, decimal_places=6, db_comment='总金额')
    pay_amount = models.DecimalField('应付金额', max_digits=18, decimal_places=9, blank=True, null=True, db_comment='余额或链上支付的最终应付金额')
    pay_method = models.CharField('支付方式', max_length=32, choices=PAY_METHOD_CHOICES, db_comment='支付方式')
    status = models.CharField('状态', max_length=32, choices=STATUS_CHOICES, default='pending', db_index=True, db_comment='状态')
    tx_hash = models.CharField('交易哈希', max_length=191, unique=True, blank=True, null=True, db_comment='普通商品订单链上支付交易哈希')
    payer_address = models.CharField('链上付款地址', max_length=191, blank=True, null=True, db_index=True, db_comment='链上付款地址')
    receive_address = models.CharField('链上收款地址', max_length=191, blank=True, null=True, db_index=True, db_comment='链上收款地址')
    created_at = models.DateTimeField('创建时间', auto_now_add=True, db_comment='创建时间')
    paid_at = models.DateTimeField('支付时间', blank=True, null=True, db_comment='支付时间')
    expired_at = models.DateTimeField('过期时间', blank=True, null=True, db_comment='过期时间')

    class Meta:
        db_table = 'order_order'
        db_table_comment = '普通商品订单表'
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
