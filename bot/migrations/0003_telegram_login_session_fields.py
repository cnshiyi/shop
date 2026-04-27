# Generated manually for Telegram login sessions.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0002_telegram_login_account_chat_message'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramloginaccount',
            name='phone_code_hash',
            field=models.CharField(blank=True, max_length=191, null=True, verbose_name='验证码哈希'),
        ),
        migrations.AddField(
            model_name='telegramloginaccount',
            name='session_string',
            field=models.TextField(blank=True, null=True, verbose_name='Telegram会话'),
        ),
    ]
