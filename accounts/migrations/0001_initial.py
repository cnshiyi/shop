from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name='TelegramUser',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tg_user_id', models.BigIntegerField(db_index=True, unique=True, verbose_name='Telegram 用户ID')),
                ('username', models.CharField(blank=True, max_length=191, null=True, verbose_name='主用户名')),
                ('first_name', models.CharField(blank=True, max_length=191, null=True, verbose_name='昵称')),
                ('balance', models.DecimalField(decimal_places=6, default=0, max_digits=18, verbose_name='USDT余额')),
                ('balance_trx', models.DecimalField(decimal_places=6, default=0, max_digits=18, verbose_name='TRX余额')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': 'Telegram用户',
                'verbose_name_plural': 'Telegram用户',
                'db_table': 'users',
            },
        ),
    ]
