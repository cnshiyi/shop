from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0021_replace_delay_applied_with_delay_quota'),
    ]

    operations = [
        migrations.CreateModel(
            name='ServerPrice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(choices=[('aws_lightsail', 'AWS 光帆服务器'), ('aliyun_simple', '阿里云轻量云')], db_index=True, max_length=32, verbose_name='云厂商')),
                ('region_code', models.CharField(db_index=True, max_length=64, verbose_name='地区代码')),
                ('region_name', models.CharField(max_length=128, verbose_name='地区名称')),
                ('bundle_code', models.CharField(db_index=True, max_length=128, verbose_name='规格代码')),
                ('server_name', models.CharField(max_length=191, verbose_name='服务器价格名')),
                ('server_description', models.TextField(blank=True, null=True, verbose_name='服务器价格描述')),
                ('cpu', models.CharField(blank=True, max_length=64, null=True, verbose_name='CPU')),
                ('memory', models.CharField(blank=True, max_length=64, null=True, verbose_name='内存')),
                ('storage', models.CharField(blank=True, max_length=64, null=True, verbose_name='存储')),
                ('bandwidth', models.CharField(blank=True, max_length=64, null=True, verbose_name='带宽')),
                ('cost_price', models.DecimalField(decimal_places=6, default=0, max_digits=18, verbose_name='进货价')),
                ('price', models.DecimalField(decimal_places=6, max_digits=18, verbose_name='销售价格')),
                ('currency', models.CharField(default='USDT', max_length=32, verbose_name='币种')),
                ('is_active', models.BooleanField(default=True, verbose_name='启用')),
                ('sort_order', models.IntegerField(default=0, verbose_name='排序')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': '服务器价格',
                'verbose_name_plural': '服务器价格',
                'db_table': 'server_prices',
                'ordering': ['provider', 'region_name', '-sort_order', 'id'],
                'unique_together': {('provider', 'region_code', 'bundle_code')},
            },
        ),
        migrations.RunSQL(
            sql=(
                "INSERT INTO server_prices "
                "(provider, region_code, region_name, bundle_code, server_name, server_description, cpu, memory, storage, bandwidth, cost_price, price, currency, is_active, sort_order, created_at, updated_at) "
                "SELECT provider, region_code, region_name, bundle_code, plan_name, plan_description, cpu, memory, storage, bandwidth, cost_price, price, currency, is_active, sort_order, created_at, updated_at "
                "FROM cloud_server_pricing"
            ),
            reverse_sql="DELETE FROM server_prices",
        ),
    ]
