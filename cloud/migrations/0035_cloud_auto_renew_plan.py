from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0014_telegramloginaccount_tg_user_id'),
        ('cloud', '0034_cloud_notice_plan'),
    ]

    operations = [
        migrations.CreateModel(
            name='CloudAutoRenewPlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_key', models.CharField(db_index=True, max_length=191, unique=True, verbose_name='来源键')),
                ('data_group', models.CharField(db_index=True, default='active', max_length=64, verbose_name='数据分组')),
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
                ('balance', models.CharField(blank=True, max_length=64, null=True, verbose_name='余额')),
                ('service_expires_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='服务到期时间')),
                ('auto_renew_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='自动续费时间')),
                ('next_run_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='下次巡检时间')),
                ('suspend_at', models.DateTimeField(blank=True, null=True, verbose_name='关机时间')),
                ('delete_at', models.DateTimeField(blank=True, null=True, verbose_name='删机时间')),
                ('ip_recycle_at', models.DateTimeField(blank=True, null=True, verbose_name='IP回收时间')),
                ('last_failure_reason', models.TextField(blank=True, null=True, verbose_name='最近失败原因')),
                ('related_path', models.CharField(blank=True, max_length=255, null=True, verbose_name='关联路径')),
                ('detail_path', models.CharField(blank=True, max_length=255, null=True, verbose_name='详情路径')),
                ('order_detail_path', models.CharField(blank=True, max_length=255, null=True, verbose_name='订单详情路径')),
                ('order_link_path', models.CharField(blank=True, max_length=255, null=True, verbose_name='订单链接路径')),
                ('source_snapshot', models.JSONField(blank=True, default=dict, verbose_name='来源快照')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='auto_renew_plans', to='cloud.cloudserverorder', verbose_name='关联订单')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cloud_auto_renew_plans', to='bot.telegramuser', verbose_name='关联用户')),
            ],
            options={
                'verbose_name': '自动续费计划',
                'verbose_name_plural': '自动续费计划',
                'db_table': 'cloud_auto_renew_plan',
                'ordering': ['auto_renew_at', 'next_run_at', '-updated_at', '-id'],
            },
        ),
        migrations.AddIndex(
            model_name='cloudautorenewplan',
            index=models.Index(fields=['data_group', 'queue_status'], name='idx_auto_renew_plan_queue'),
        ),
        migrations.AddIndex(
            model_name='cloudautorenewplan',
            index=models.Index(fields=['data_group', 'auto_renew_at'], name='idx_auto_renew_plan_time'),
        ),
        migrations.AddIndex(
            model_name='cloudautorenewplan',
            index=models.Index(fields=['order', 'queue_status'], name='idx_auto_renew_plan_order'),
        ),
    ]
