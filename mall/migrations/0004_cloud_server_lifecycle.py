from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('mall', '0003_cloud_server_port'),
    ]

    operations = [
        migrations.AddField(model_name='cloudserverorder', name='delete_at', field=models.DateTimeField(blank=True, null=True, verbose_name='计划删机时间')),
        migrations.AddField(model_name='cloudserverorder', name='ip_recycle_at', field=models.DateTimeField(blank=True, null=True, verbose_name='IP保留到期时间')),
        migrations.AddField(model_name='cloudserverorder', name='last_renewed_at', field=models.DateTimeField(blank=True, null=True, verbose_name='最后续费时间')),
        migrations.AddField(model_name='cloudserverorder', name='last_user_id', field=models.BigIntegerField(blank=True, db_index=True, null=True, verbose_name='最近绑定TG用户ID')),
        migrations.AddField(model_name='cloudserverorder', name='lifecycle_days', field=models.IntegerField(default=31, verbose_name='有效期天数')),
        migrations.AddField(model_name='cloudserverorder', name='mtproxy_host', field=models.CharField(blank=True, max_length=191, null=True, verbose_name='MTProxy主机')),
        migrations.AddField(model_name='cloudserverorder', name='mtproxy_link', field=models.TextField(blank=True, null=True, verbose_name='MTProxy链接')),
        migrations.AddField(model_name='cloudserverorder', name='mtproxy_secret', field=models.CharField(blank=True, max_length=64, null=True, verbose_name='MTProxy密钥')),
        migrations.AddField(model_name='cloudserverorder', name='previous_public_ip', field=models.CharField(blank=True, max_length=128, null=True, verbose_name='历史公网IP')),
        migrations.AddField(model_name='cloudserverorder', name='provider_resource_id', field=models.CharField(blank=True, max_length=191, null=True, verbose_name='云资源ID')),
        migrations.AddField(model_name='cloudserverorder', name='renew_grace_expires_at', field=models.DateTimeField(blank=True, null=True, verbose_name='续费宽限到期时间')),
        migrations.AddField(model_name='cloudserverorder', name='server_name', field=models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='服务器名')),
        migrations.AddField(model_name='cloudserverorder', name='service_expires_at', field=models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='服务到期时间')),
        migrations.AddField(model_name='cloudserverorder', name='service_started_at', field=models.DateTimeField(blank=True, null=True, verbose_name='服务开始时间')),
        migrations.AddField(model_name='cloudserverorder', name='static_ip_name', field=models.CharField(blank=True, max_length=191, null=True, verbose_name='固定IP名称')),
        migrations.AddField(model_name='cloudserverorder', name='suspend_at', field=models.DateTimeField(blank=True, null=True, verbose_name='计划关机时间')),
        migrations.AlterField(model_name='cloudserverorder', name='public_ip', field=models.CharField(blank=True, db_index=True, max_length=128, null=True, verbose_name='公网IP')),
        migrations.AlterField(model_name='cloudserverorder', name='status', field=models.CharField(choices=[('pending', '待支付'), ('paid', '已支付'), ('provisioning', '创建中'), ('completed', '已创建'), ('renew_pending', '待续费'), ('expiring', '即将到期'), ('suspended', '已关机'), ('deleting', '删除中'), ('deleted', '已删除'), ('failed', '创建失败'), ('cancelled', '已取消'), ('expired', '已过期')], db_index=True, default='pending', max_length=32, verbose_name='状态')),
    ]
