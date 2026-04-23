from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0007_telegramuser_cloud_discount_rate'),
        ('mall', '0013_cloudserverplan_plan_description_cloudserverpricing'),
    ]

    operations = [
        migrations.CreateModel(
            name='CartItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('item_type', models.CharField(choices=[('product', '商品'), ('cloud_plan', '云套餐')], default='product', max_length=32, verbose_name='项目类型')),
                ('quantity', models.IntegerField(default=1, verbose_name='数量')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('cloud_plan', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='cart_items', to='mall.cloudserverplan', verbose_name='云套餐')),
                ('product', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='cart_items', to='mall.product', verbose_name='商品')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='cart_items', to='accounts.telegramuser', verbose_name='用户')),
            ],
            options={
                'verbose_name': '购物车项',
                'verbose_name_plural': '购物车项',
                'db_table': 'cart_items',
                'ordering': ['-updated_at', '-id'],
            },
        ),
    ]
