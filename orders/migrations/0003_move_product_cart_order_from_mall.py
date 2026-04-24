from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0002_move_balanceledger_state_from_accounts'),
        ('mall', '0028_switch_user_fk_to_bot'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='Product',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('name', models.TextField(verbose_name='商品名称')),
                        ('description', models.TextField(blank=True, null=True, verbose_name='商品描述')),
                        ('price', models.DecimalField(decimal_places=6, max_digits=18, verbose_name='商品单价(USDT)')),
                        ('content_type', models.CharField(choices=[('text', '文本'), ('image', '图片'), ('video', '视频')], default='text', max_length=32, verbose_name='内容类型')),
                        ('content_text', models.TextField(blank=True, null=True, verbose_name='文本内容')),
                        ('content_image', models.TextField(blank=True, null=True, verbose_name='图片File ID/URL')),
                        ('content_video', models.TextField(blank=True, null=True, verbose_name='视频File ID/URL')),
                        ('stock', models.IntegerField(default=-1, help_text='-1 表示无限库存', verbose_name='库存')),
                        ('is_active', models.BooleanField(default=True, verbose_name='上架')),
                        ('sort_order', models.IntegerField(default=0, verbose_name='排序')),
                        ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                        ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                    ],
                    options={'db_table': 'order_product', 'verbose_name': '商品', 'verbose_name_plural': '商品', 'ordering': ['-sort_order', '-id']},
                ),
                migrations.CreateModel(
                    name='CartItem',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('item_type', models.CharField(choices=[('product', '商品'), ('cloud_plan', '云套餐')], default='product', max_length=32, verbose_name='项目类型')),
                        ('quantity', models.IntegerField(default=1, verbose_name='数量')),
                        ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                        ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                        ('cloud_plan', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='cart_items', to='mall.cloudserverplan', verbose_name='云套餐')),
                        ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='cart_items', to='orders.product', verbose_name='商品')),
                        ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cart_items', to='bot.telegramuser', verbose_name='用户')),
                    ],
                    options={'db_table': 'order_cart_item', 'verbose_name': '购物车项', 'verbose_name_plural': '购物车项', 'ordering': ['-updated_at', '-id']},
                ),
                migrations.CreateModel(
                    name='Order',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('order_no', models.CharField(db_index=True, max_length=191, unique=True, verbose_name='订单号')),
                        ('product_name', models.TextField(verbose_name='商品名称')),
                        ('quantity', models.IntegerField(default=1, verbose_name='数量')),
                        ('currency', models.CharField(choices=[('USDT', 'USDT'), ('TRX', 'TRX')], db_index=True, default='USDT', max_length=32, verbose_name='币种')),
                        ('total_amount', models.DecimalField(decimal_places=6, max_digits=18, verbose_name='总金额')),
                        ('pay_amount', models.DecimalField(blank=True, decimal_places=9, max_digits=18, null=True, verbose_name='应付金额')),
                        ('pay_method', models.CharField(choices=[('balance', '余额支付'), ('address', '地址支付')], max_length=32, verbose_name='支付方式')),
                        ('status', models.CharField(choices=[('pending', '待支付'), ('paid', '已支付'), ('delivered', '已发货'), ('cancelled', '已取消'), ('expired', '已过期')], db_index=True, default='pending', max_length=32, verbose_name='状态')),
                        ('tx_hash', models.CharField(blank=True, max_length=191, null=True, unique=True, verbose_name='交易哈希')),
                        ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                        ('paid_at', models.DateTimeField(blank=True, null=True, verbose_name='支付时间')),
                        ('expired_at', models.DateTimeField(blank=True, null=True, verbose_name='过期时间')),
                        ('product', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='orders.product', verbose_name='商品')),
                        ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='bot.telegramuser', verbose_name='用户')),
                    ],
                    options={'db_table': 'order_order', 'verbose_name': '订单', 'verbose_name_plural': '订单', 'ordering': ['-created_at']},
                ),
            ],
        ),
    ]
