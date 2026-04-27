# Generated manually for Telegram account message capture metadata.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0003_telegram_login_session_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='telegramchatmessage',
            name='login_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='chat_messages', to='bot.telegramloginaccount', verbose_name='登录账号'),
        ),
        migrations.AddField(
            model_name='telegramchatmessage',
            name='chat_title',
            field=models.CharField(blank=True, max_length=191, null=True, verbose_name='会话标题'),
        ),
        migrations.AddField(
            model_name='telegramchatmessage',
            name='source',
            field=models.CharField(db_index=True, default='bot', max_length=32, verbose_name='来源'),
        ),
    ]
