from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0010_alter_balanceledger_table_alter_telegramuser_table'),
        ('mall', '0026_alter_cloudasset_table_alter_cloudserverorder_table_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='CloudIpLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('provider', models.CharField(blank=True, db_index=True, max_length=32, null=True, verbose_name='云厂商')),
                ('region_code', models.CharField(blank=True, db_index=True, max_length=64, null=True, verbose_name='地区代码')),
                ('region_name', models.CharField(blank=True, max_length=128, null=True, verbose_name='地区名称')),
                ('order_no', models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='订单号')),
                ('asset_name', models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='资产名称')),
                ('instance_id', models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='实例ID')),
                ('provider_resource_id', models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='云资源ID')),
                ('public_ip', models.CharField(blank=True, db_index=True, max_length=128, null=True, verbose_name='当前IP')),
                ('previous_public_ip', models.CharField(blank=True, db_index=True, max_length=128, null=True, verbose_name='上一个IP')),
                ('event_type', models.CharField(choices=[('created', '创建分配'), ('changed', 'IP变更'), ('expired', '到期'), ('suspended', '延停'), ('deleted', '删除'), ('recycled', '回收')], db_index=True, max_length=32, verbose_name='事件类型')),
                ('note', models.TextField(blank=True, null=True, verbose_name='说明')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='记录时间')),
                ('asset', models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='ip_logs', to='mall.cloudasset', verbose_name='关联资产')),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='ip_logs', to='mall.cloudserverorder', verbose_name='关联订单')),
                ('server', models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='ip_logs', to='mall.server', verbose_name='关联服务器')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=models.SET_NULL, related_name='cloud_ip_logs', to='accounts.telegramuser', verbose_name='关联用户')),
            ],
            options={
                'verbose_name': '云IP日志',
                'verbose_name_plural': '云IP日志',
                'db_table': 'cloud_ip_log',
                'ordering': ['-created_at', '-id'],
            },
        ),
    ]
