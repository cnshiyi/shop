from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0028_auto_renew_patrol_log'),
        ('bot', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='CloudUserNoticeLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('batch_id', models.CharField(blank=True, db_index=True, default='', max_length=64, verbose_name='通知批次')),
                ('event_type', models.CharField(db_index=True, max_length=64, verbose_name='通知类型')),
                ('target_chat_id', models.BigIntegerField(blank=True, db_index=True, null=True, verbose_name='目标聊天ID')),
                ('order_no', models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='订单号')),
                ('ip', models.CharField(blank=True, db_index=True, max_length=128, null=True, verbose_name='IP')),
                ('is_batch', models.BooleanField(db_index=True, default=False, verbose_name='是否批量')),
                ('delivered', models.BooleanField(db_index=True, default=False, verbose_name='是否送达')),
                ('text_preview', models.TextField(blank=True, null=True, verbose_name='通知预览')),
                ('extra', models.JSONField(blank=True, default=dict, verbose_name='额外信息')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='记录时间')),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='notice_logs', to='cloud.cloudserverorder', verbose_name='云服务器订单')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cloud_notice_logs', to='bot.telegramuser', verbose_name='用户')),
            ],
            options={
                'db_table': 'cloud_user_notice_log',
                'verbose_name': '云通知日志',
                'verbose_name_plural': '云通知日志',
                'ordering': ['-created_at', '-id'],
                'indexes': [
                    models.Index(fields=['user', '-created_at'], name='idx_cloud_notice_user'),
                    models.Index(fields=['event_type', '-created_at'], name='idx_cloud_notice_event'),
                    models.Index(fields=['batch_id', '-created_at'], name='idx_cloud_notice_batch'),
                ],
            },
        ),
    ]
