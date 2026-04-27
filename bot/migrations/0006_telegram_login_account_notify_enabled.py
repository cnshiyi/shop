# Generated manually for Telegram account notification switch.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0005_telegram_chat_archive'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramloginaccount',
            name='notify_enabled',
            field=models.BooleanField(db_index=True, default=True, verbose_name='允许通知'),
        ),
    ]
