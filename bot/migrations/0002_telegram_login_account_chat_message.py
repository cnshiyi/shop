# Generated manually for dashboard Telegram account/chat management.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='TelegramLoginAccount',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('label', models.CharField(max_length=191, verbose_name='账号备注')),
                ('phone', models.CharField(blank=True, max_length=64, null=True, verbose_name='手机号')),
                ('username', models.CharField(blank=True, max_length=191, null=True, verbose_name='用户名')),
                ('status', models.CharField(db_index=True, default='pending', max_length=32, verbose_name='状态')),
                ('note', models.TextField(blank=True, null=True, verbose_name='备注')),
                ('last_synced_at', models.DateTimeField(blank=True, null=True, verbose_name='最近同步时间')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
            ],
            options={
                'verbose_name': 'Telegram登录账号',
                'verbose_name_plural': 'Telegram登录账号',
                'db_table': 'bot_telegram_login_account',
                'ordering': ['-updated_at', '-id'],
            },
        ),
        migrations.CreateModel(
            name='TelegramChatMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('tg_user_id', models.BigIntegerField(db_index=True, verbose_name='Telegram用户ID')),
                ('chat_id', models.BigIntegerField(db_index=True, verbose_name='会话ID')),
                ('message_id', models.BigIntegerField(blank=True, null=True, verbose_name='消息ID')),
                ('direction', models.CharField(choices=[('in', '收到'), ('out', '发出')], db_index=True, default='in', max_length=8, verbose_name='方向')),
                ('content_type', models.CharField(default='text', max_length=32, verbose_name='消息类型')),
                ('text', models.TextField(blank=True, null=True, verbose_name='消息内容')),
                ('username_snapshot', models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='用户名快照')),
                ('first_name_snapshot', models.CharField(blank=True, max_length=191, null=True, verbose_name='昵称快照')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='chat_messages', to='bot.telegramuser', verbose_name='Telegram用户')),
            ],
            options={
                'verbose_name': 'Telegram聊天记录',
                'verbose_name_plural': 'Telegram聊天记录',
                'db_table': 'bot_telegram_chat_message',
                'ordering': ['-created_at', '-id'],
            },
        ),
        migrations.AddIndex(
            model_name='telegramchatmessage',
            index=models.Index(fields=['tg_user_id', '-created_at'], name='idx_tg_msg_user_time'),
        ),
        migrations.AddIndex(
            model_name='telegramchatmessage',
            index=models.Index(fields=['username_snapshot', '-created_at'], name='idx_tg_msg_username_time'),
        ),
    ]
