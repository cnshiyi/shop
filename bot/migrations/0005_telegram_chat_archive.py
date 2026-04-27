# Generated manually for Telegram chat archives.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0004_telegram_chat_message_source_account'),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramChatArchive',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('chat_id', models.BigIntegerField(db_index=True, unique=True, verbose_name='会话ID')),
                ('title', models.CharField(blank=True, max_length=191, null=True, verbose_name='会话标题')),
                ('note', models.TextField(blank=True, null=True, verbose_name='备注')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='归档时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': 'Telegram归档会话',
                'verbose_name_plural': 'Telegram归档会话',
                'db_table': 'bot_telegram_chat_archive',
                'ordering': ['-updated_at', '-id'],
            },
        ),
    ]
