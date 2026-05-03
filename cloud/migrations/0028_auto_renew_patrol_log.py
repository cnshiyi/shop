# Generated for auto renew patrol logs.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0027_disable_manual_audit_lifecycle'),
        ('bot', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='CloudAutoRenewPatrolLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('batch_id', models.CharField(db_index=True, max_length=64, verbose_name='巡检批次')),
                ('order_no', models.CharField(db_index=True, max_length=191, verbose_name='订单号')),
                ('ip', models.CharField(db_index=True, max_length=128, verbose_name='公网IP')),
                ('provider', models.CharField(blank=True, db_index=True, max_length=32, null=True, verbose_name='云厂商')),
                ('user_display_name', models.CharField(blank=True, max_length=191, null=True, verbose_name='用户显示名')),
                ('username_label', models.CharField(blank=True, max_length=191, null=True, verbose_name='用户名')),
                ('tg_user_id', models.BigIntegerField(blank=True, db_index=True, null=True, verbose_name='Telegram ID')),
                ('is_success', models.BooleanField(db_index=True, default=False, verbose_name='是否成功')),
                ('failure_reason', models.TextField(blank=True, null=True, verbose_name='失败原因')),
                ('currency', models.CharField(default='USDT', max_length=32, verbose_name='币种')),
                ('balance_before', models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True, verbose_name='余额变更前')),
                ('balance_after', models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True, verbose_name='余额变更后')),
                ('balance_change', models.DecimalField(blank=True, decimal_places=6, max_digits=18, null=True, verbose_name='余额变化')),
                ('service_expires_at', models.DateTimeField(blank=True, null=True, verbose_name='续费后到期时间')),
                ('completed_order_id', models.BigIntegerField(blank=True, db_index=True, null=True, verbose_name='续费后订单ID')),
                ('completed_order_no', models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='续费后订单号')),
                ('executed_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='执行时间')),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='auto_renew_patrol_logs', to='cloud.cloudserverorder', verbose_name='云服务器订单')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='auto_renew_patrol_logs', to='bot.telegramuser', verbose_name='用户')),
            ],
            options={
                'verbose_name': '自动续费巡检日志',
                'verbose_name_plural': '自动续费巡检日志',
                'db_table': 'cloud_auto_renew_patrol_log',
                'ordering': ['-executed_at', '-id'],
                'indexes': [
                    models.Index(fields=['batch_id', '-executed_at'], name='idx_auto_renew_batch'),
                    models.Index(fields=['order_no', '-executed_at'], name='idx_auto_renew_order'),
                    models.Index(fields=['tg_user_id', '-executed_at'], name='idx_auto_renew_user'),
                ],
            },
        ),
    ]
