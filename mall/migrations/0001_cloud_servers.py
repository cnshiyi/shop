from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        ('accounts', '__first__'),
    ]

    operations = [
        migrations.CreateModel(
            name='CloudServerPlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(choices=[('aws_lightsail', 'AWS 光帆服务器'), ('aliyun_simple', '阿里云轻量云')], db_index=True, max_length=32, verbose_name='云厂商')),
                ('region_code', models.CharField(db_index=True, max_length=64, verbose_name='地区代码')),
                ('region_name', models.CharField(max_length=128, verbose_name='地区名称')),
                ('plan_name', models.CharField(max_length=191, verbose_name='套餐名称')),
                ('cpu', models.CharField(blank=True, max_length=64, null=True, verbose_name='CPU')),
                ('memory', models.CharField(blank=True, max_length=64, null=True, verbose_name='内存')),
                ('storage', models.CharField(blank=True, max_length=64, null=True, verbose_name='存储')),
                ('bandwidth', models.CharField(blank=True, max_length=64, null=True, verbose_name='带宽')),
                ('price', models.DecimalField(decimal_places=6, max_digits=18, verbose_name='价格')),
                ('currency', models.CharField(default='USDT', max_length=32, verbose_name='币种')),
                ('is_active', models.BooleanField(default=True, verbose_name='启用')),
                ('sort_order', models.IntegerField(default=0, verbose_name='排序')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': '云服务器套餐',
                'verbose_name_plural': '云服务器套餐',
                'db_table': 'cloud_server_plans',
                'ordering': ['provider', 'region_name', '-sort_order', 'id'],
                'unique_together': {('provider', 'region_code', 'plan_name')},
            },
        ),
        migrations.CreateModel(
            name='CloudServerOrder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order_no', models.CharField(db_index=True, max_length=191, unique=True, verbose_name='订单号')),
                ('provider', models.CharField(db_index=True, max_length=32, verbose_name='云厂商')),
                ('region_code', models.CharField(db_index=True, max_length=64, verbose_name='地区代码')),
                ('region_name', models.CharField(max_length=128, verbose_name='地区名称')),
                ('plan_name', models.CharField(max_length=191, verbose_name='套餐名称')),
                ('currency', models.CharField(choices=[('USDT', 'USDT'), ('TRX', 'TRX')], db_index=True, default='USDT', max_length=32, verbose_name='币种')),
                ('total_amount', models.DecimalField(decimal_places=6, max_digits=18, verbose_name='总金额')),
                ('pay_amount', models.DecimalField(blank=True, decimal_places=9, max_digits=18, null=True, verbose_name='应付金额')),
                ('pay_method', models.CharField(choices=[('address', '地址支付'), ('balance', '余额支付')], default='address', max_length=32, verbose_name='支付方式')),
                ('status', models.CharField(choices=[('pending', '待支付'), ('paid', '已支付'), ('provisioning', '创建中'), ('completed', '已创建'), ('failed', '创建失败'), ('cancelled', '已取消'), ('expired', '已过期')], db_index=True, default='pending', max_length=32, verbose_name='状态')),
                ('tx_hash', models.CharField(blank=True, max_length=191, null=True, unique=True, verbose_name='交易哈希')),
                ('provision_note', models.TextField(blank=True, null=True, verbose_name='创建说明')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('paid_at', models.DateTimeField(blank=True, null=True, verbose_name='支付时间')),
                ('expired_at', models.DateTimeField(blank=True, null=True, verbose_name='过期时间')),
                ('completed_at', models.DateTimeField(blank=True, null=True, verbose_name='完成时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('plan', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, to='mall.cloudserverplan', verbose_name='套餐')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='accounts.telegramuser', verbose_name='用户')),
            ],
            options={
                'verbose_name': '云服务器订单',
                'verbose_name_plural': '云服务器订单',
                'db_table': 'cloud_server_orders',
                'ordering': ['-created_at'],
            },
        ),
    ]
