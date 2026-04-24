from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0001_initial'),
        ('accounts', '0011_move_telegramuser_state_to_bot'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='BalanceLedger',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('type', models.CharField(choices=[('manual_adjust', '手动调整'), ('recharge', '充值入账'), ('order_balance_pay', '商品余额支付'), ('cloud_order_balance_pay', '云服务器余额支付')], db_index=True, max_length=64, verbose_name='类型')),
                        ('direction', models.CharField(choices=[('in', '收入'), ('out', '支出')], db_index=True, max_length=16, verbose_name='方向')),
                        ('currency', models.CharField(choices=[('USDT', 'USDT'), ('TRX', 'TRX')], db_index=True, max_length=32, verbose_name='币种')),
                        ('amount', models.DecimalField(decimal_places=9, max_digits=18, verbose_name='变动金额')),
                        ('before_balance', models.DecimalField(decimal_places=9, max_digits=18, verbose_name='变动前余额')),
                        ('after_balance', models.DecimalField(decimal_places=9, max_digits=18, verbose_name='变动后余额')),
                        ('related_type', models.CharField(blank=True, db_index=True, max_length=64, null=True, verbose_name='关联类型')),
                        ('related_id', models.BigIntegerField(blank=True, db_index=True, null=True, verbose_name='关联ID')),
                        ('description', models.TextField(blank=True, null=True, verbose_name='说明')),
                        ('operator', models.CharField(blank=True, max_length=191, null=True, verbose_name='操作人')),
                        ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                        ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='balance_ledgers', to='bot.telegramuser', verbose_name='用户')),
                    ],
                    options={
                        'db_table': 'order_balance_ledger',
                        'verbose_name': '余额流水',
                        'verbose_name_plural': '余额流水',
                        'ordering': ['-created_at', '-id'],
                    },
                ),
            ],
        ),
    ]
