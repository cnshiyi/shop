from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0008_encrypt_telegram_login_sessions'),
    ]

    operations = [
        migrations.CreateModel(
            name='AdminReplyLink',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('admin_chat_id', models.BigIntegerField(db_index=True, verbose_name='管理员会话ID')),
                ('admin_message_id', models.BigIntegerField(db_index=True, verbose_name='管理员消息ID')),
                ('user_chat_id', models.BigIntegerField(db_index=True, verbose_name='用户会话ID')),
                ('user_message_id', models.BigIntegerField(blank=True, null=True, verbose_name='用户消息ID')),
                ('source_content_type', models.CharField(default='text', max_length=32, verbose_name='原消息类型')),
                ('is_active', models.BooleanField(db_index=True, default=True, verbose_name='启用')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='admin_reply_links', to='bot.telegramuser', verbose_name='Telegram用户')),
            ],
            options={
                'verbose_name': '管理员回复通道',
                'verbose_name_plural': '管理员回复通道',
                'db_table': 'bot_admin_reply_link',
                'ordering': ['-created_at', '-id'],
                'indexes': [
                    models.Index(fields=['admin_chat_id', 'admin_message_id'], name='idx_admin_reply_msg'),
                    models.Index(fields=['user_chat_id', '-created_at'], name='idx_admin_reply_user_time'),
                ],
                'unique_together': {('admin_chat_id', 'admin_message_id')},
            },
        ),
    ]
