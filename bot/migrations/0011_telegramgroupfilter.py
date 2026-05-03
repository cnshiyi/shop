# Generated for Telegram group notification filters.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0010_telegramuser_admin_forward_muted_until'),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramGroupFilter',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('chat_id', models.BigIntegerField(db_index=True, unique=True, verbose_name='群组会话ID')),
                ('title', models.CharField(blank=True, max_length=191, null=True, verbose_name='群组名称')),
                ('username', models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='群组用户名')),
                ('enabled', models.BooleanField(db_index=True, default=False, verbose_name='允许转发')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': 'Telegram群组过滤',
                'verbose_name_plural': 'Telegram群组过滤',
                'db_table': 'bot_telegram_group_filter',
                'ordering': ['-updated_at', '-id'],
            },
        ),
    ]
