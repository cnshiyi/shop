from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0014_telegramloginaccount_tg_user_id'),
        ('cloud', '0033_cloud_lifecycle_plan'),
    ]

    operations = [
        migrations.CreateModel(
            name='CloudNoticePlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_key', models.CharField(db_index=True, max_length=191, verbose_name='来源键')),
                ('notice_type', models.CharField(db_index=True, max_length=64, verbose_name='通知类型')),
                ('data_group', models.CharField(blank=True, db_index=True, max_length=64, null=True, verbose_name='数据分组')),
                ('queue_status', models.CharField(blank=True, db_index=True, max_length=64, null=True, verbose_name='队列状态')),
                ('queue_status_label', models.CharField(blank=True, max_length=128, null=True, verbose_name='队列状态标签')),
                ('order_no', models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='订单号')),
                ('ip', models.CharField(blank=True, db_index=True, max_length=128, null=True, verbose_name='IP')),
                ('provider', models.CharField(blank=True, db_index=True, max_length=32, null=True, verbose_name='云厂商')),
                ('provider_label', models.CharField(blank=True, max_length=128, null=True, verbose_name='云厂商标签')),
                ('status', models.CharField(blank=True, db_index=True, max_length=64, null=True, verbose_name='状态')),
                ('status_label', models.CharField(blank=True, max_length=128, null=True, verbose_name='状态标签')),
                ('user_display_name', models.CharField(blank=True, max_length=191, null=True, verbose_name='用户显示名')),
                ('username_label', models.CharField(blank=True, max_length=191, null=True, verbose_name='用户名标签')),
                ('notice_channel', models.CharField(blank=True, db_index=True, max_length=64, null=True, verbose_name='通知渠道')),
                ('notice_channel_label', models.CharField(blank=True, max_length=191, null=True, verbose_name='通知渠道标签')),
                ('notice_channel_attempts', models.JSONField(blank=True, default=list, verbose_name='通知渠道尝试')),
                ('notice_status', models.CharField(blank=True, db_index=True, max_length=64, null=True, verbose_name='通知状态')),
                ('notice_status_label', models.CharField(blank=True, max_length=191, null=True, verbose_name='通知状态标签')),
                ('retry_label', models.TextField(blank=True, null=True, verbose_name='重试说明')),
                ('notice_text_preview', models.TextField(blank=True, null=True, verbose_name='通知文案预览')),
                ('notice_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='通知时间')),
                ('next_run_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='下次执行时间')),
                ('sent_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='发送时间')),
                ('logged_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='记录时间')),
                ('delivered', models.BooleanField(db_index=True, default=False, verbose_name='是否送达')),
                ('batch_id', models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='批次ID')),
                ('log_id', models.BigIntegerField(blank=True, db_index=True, null=True, verbose_name='日志ID')),
                ('related_path', models.CharField(blank=True, max_length=255, null=True, verbose_name='关联路径')),
                ('detail_path', models.CharField(blank=True, max_length=255, null=True, verbose_name='详情路径')),
                ('order_detail_path', models.CharField(blank=True, max_length=255, null=True, verbose_name='订单详情路径')),
                ('order_link_path', models.CharField(blank=True, max_length=255, null=True, verbose_name='订单链接路径')),
                ('source_snapshot', models.JSONField(blank=True, default=dict, verbose_name='来源快照')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='notice_plans', to='cloud.cloudserverorder', verbose_name='关联订单')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='cloud_notice_plans', to='bot.telegramuser', verbose_name='关联用户')),
            ],
            options={
                'verbose_name': '通知计划',
                'verbose_name_plural': '通知计划',
                'db_table': 'cloud_notice_plan',
                'ordering': ['-next_run_at', '-logged_at', '-updated_at', '-id'],
            },
        ),
        migrations.AddConstraint(
            model_name='cloudnoticeplan',
            constraint=models.UniqueConstraint(fields=('notice_type', 'source_key'), name='uniq_cloud_notice_plan_source'),
        ),
        migrations.AddIndex(
            model_name='cloudnoticeplan',
            index=models.Index(fields=['notice_type', 'data_group'], name='idx_notice_plan_type_group'),
        ),
        migrations.AddIndex(
            model_name='cloudnoticeplan',
            index=models.Index(fields=['notice_type', 'queue_status'], name='idx_notice_plan_type_queue'),
        ),
        migrations.AddIndex(
            model_name='cloudnoticeplan',
            index=models.Index(fields=['notice_type', 'notice_at'], name='idx_notice_plan_type_notice'),
        ),
        migrations.AddIndex(
            model_name='cloudnoticeplan',
            index=models.Index(fields=['notice_type', 'sent_at'], name='idx_notice_plan_type_sent'),
        ),
    ]
