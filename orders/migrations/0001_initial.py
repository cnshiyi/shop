from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('bot', '0001_initial'),
        ('finance', '0002_alter_recharge_table'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='Recharge',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('currency', models.CharField(choices=[('USDT', 'USDT'), ('TRX', 'TRX')], db_index=True, default='USDT', max_length=32, verbose_name='币种')),
                        ('amount', models.DecimalField(decimal_places=6, max_digits=18, verbose_name='充值金额')),
                        ('pay_amount', models.DecimalField(decimal_places=9, max_digits=18, verbose_name='支付金额')),
                        ('status', models.CharField(choices=[('pending', '待支付'), ('completed', '已完成'), ('expired', '已过期')], db_index=True, default='pending', max_length=32, verbose_name='状态')),
                        ('tx_hash', models.CharField(blank=True, max_length=191, null=True, unique=True, verbose_name='交易哈希')),
                        ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                        ('completed_at', models.DateTimeField(blank=True, null=True, verbose_name='完成时间')),
                        ('expired_at', models.DateTimeField(blank=True, null=True, verbose_name='过期时间')),
                        ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to='bot.telegramuser', verbose_name='用户')),
                    ],
                    options={
                        'db_table': 'order_recharge',
                        'verbose_name': '充值记录',
                        'verbose_name_plural': '充值记录',
                        'ordering': ['-created_at'],
                    },
                ),
            ],
        ),
    ]
