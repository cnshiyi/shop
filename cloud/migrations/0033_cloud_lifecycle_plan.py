from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot', '0014_telegramloginaccount_tg_user_id'),
        ('cloud', '0032_lifecycle_plan_note'),
    ]

    operations = [
        migrations.CreateModel(
            name='CloudLifecyclePlan',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source_key', models.CharField(db_index=True, max_length=191, verbose_name='来源键')),
                ('plan_kind', models.CharField(choices=[('shutdown_order', '订单删机计划'), ('orphan_asset_delete', '无订单资产删机计划'), ('unattached_ip_delete', '未附加固定IP删除计划')], db_index=True, max_length=64, verbose_name='计划类型')),
                ('data_group', models.CharField(blank=True, db_index=True, max_length=64, null=True, verbose_name='数据分组')),
                ('queue_status', models.CharField(blank=True, db_index=True, max_length=64, null=True, verbose_name='队列状态')),
                ('queue_status_label', models.CharField(blank=True, max_length=128, null=True, verbose_name='队列状态标签')),
                ('user_display_name', models.CharField(blank=True, max_length=191, null=True, verbose_name='用户显示名')),
                ('username_label', models.CharField(blank=True, max_length=191, null=True, verbose_name='用户名标签')),
                ('ip', models.CharField(blank=True, db_index=True, max_length=128, null=True, verbose_name='IP')),
                ('provider', models.CharField(blank=True, db_index=True, max_length=32, null=True, verbose_name='云厂商')),
                ('provider_label', models.CharField(blank=True, max_length=128, null=True, verbose_name='云厂商标签')),
                ('status', models.CharField(blank=True, db_index=True, max_length=64, null=True, verbose_name='状态')),
                ('status_label', models.CharField(blank=True, max_length=128, null=True, verbose_name='状态标签')),
                ('service_expires_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='服务到期时间')),
                ('suspend_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='关机时间')),
                ('delete_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='删机时间')),
                ('ip_recycle_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='IP回收时间')),
                ('next_run_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='下次执行时间')),
                ('logged_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='记录时间')),
                ('last_failure_reason', models.TextField(blank=True, null=True, verbose_name='失败原因')),
                ('execution_status', models.TextField(blank=True, null=True, verbose_name='执行状态')),
                ('execution_plan', models.TextField(blank=True, null=True, verbose_name='执行计划')),
                ('note', models.TextField(blank=True, null=True, verbose_name='备注')),
                ('display_note', models.TextField(blank=True, null=True, verbose_name='备注预览')),
                ('deletion_source_label', models.CharField(blank=True, max_length=128, null=True, verbose_name='删除来源')),
                ('related_path', models.CharField(blank=True, max_length=255, null=True, verbose_name='关联路径')),
                ('detail_path', models.CharField(blank=True, max_length=255, null=True, verbose_name='详情路径')),
                ('order_detail_path', models.CharField(blank=True, max_length=255, null=True, verbose_name='订单详情路径')),
                ('order_link_path', models.CharField(blank=True, max_length=255, null=True, verbose_name='订单链接路径')),
                ('asset_detail_path', models.CharField(blank=True, max_length=255, null=True, verbose_name='资产详情路径')),
                ('source_snapshot', models.JSONField(blank=True, default=dict, verbose_name='来源快照')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('asset', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='lifecycle_plans', to='cloud.cloudasset', verbose_name='关联资产')),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='lifecycle_plans', to='cloud.cloudserverorder', verbose_name='关联订单')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=models.deletion.SET_NULL, related_name='cloud_lifecycle_plans', to='bot.telegramuser', verbose_name='关联用户')),
            ],
            options={
                'verbose_name': '删除计划',
                'verbose_name_plural': '删除计划',
                'db_table': 'cloud_lifecycle_plan',
                'ordering': ['-next_run_at', 'delete_at', '-updated_at', '-id'],
            },
        ),
        migrations.AddConstraint(
            model_name='cloudlifecycleplan',
            constraint=models.UniqueConstraint(fields=('plan_kind', 'source_key'), name='uniq_cloud_lifecycle_plan_source'),
        ),
        migrations.AddIndex(
            model_name='cloudlifecycleplan',
            index=models.Index(fields=['plan_kind', 'data_group'], name='idx_lifecycle_plan_kind_group'),
        ),
        migrations.AddIndex(
            model_name='cloudlifecycleplan',
            index=models.Index(fields=['plan_kind', 'queue_status'], name='idx_lifecycle_plan_kind_queue'),
        ),
        migrations.AddIndex(
            model_name='cloudlifecycleplan',
            index=models.Index(fields=['plan_kind', 'delete_at'], name='idx_lifecycle_plan_kind_delete'),
        ),
        migrations.AddIndex(
            model_name='cloudlifecycleplan',
            index=models.Index(fields=['plan_kind', 'next_run_at'], name='idx_lifecycle_plan_kind_next'),
        ),
    ]
