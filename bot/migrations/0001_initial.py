from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ('accounts', '0010_alter_balanceledger_table_alter_telegramuser_table'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.CreateModel(
                    name='TelegramUser',
                    fields=[
                        ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                        ('tg_user_id', models.BigIntegerField(db_index=True, unique=True, verbose_name='Telegram 用户ID')),
                        ('username', models.TextField(blank=True, null=True, verbose_name='用户名集合')),
                        ('first_name', models.CharField(blank=True, max_length=191, null=True, verbose_name='昵称')),
                        ('balance', models.DecimalField(decimal_places=6, default=0, max_digits=18, verbose_name='USDT余额')),
                        ('balance_trx', models.DecimalField(decimal_places=6, default=0, max_digits=18, verbose_name='TRX余额')),
                        ('cloud_discount_rate', models.DecimalField(decimal_places=2, default=100, help_text='百分比，100 表示无折扣，90 表示 9 折', max_digits=5, verbose_name='云服务器专属折扣')),
                        ('cloud_reminder_muted_until', models.DateTimeField(blank=True, null=True, verbose_name='云服务器提醒静默到')),
                        ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                        ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                    ],
                    options={
                        'db_table': 'bot_user',
                        'verbose_name': 'Telegram用户',
                        'verbose_name_plural': 'Telegram用户',
                    },
                ),
            ],
        ),
    ]
