from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('accounts', '0002_telegramusername'),
        ('mall', '0005_cloud_server_quantity'),
    ]

    operations = [
        migrations.CreateModel(
            name='CloudAsset',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('kind', models.CharField(choices=[('server', '云服务器'), ('mtproxy', 'MTProxy代理')], db_index=True, max_length=32, verbose_name='资产类型')),
                ('source', models.CharField(choices=[('aliyun', '阿里云自动同步'), ('aws_manual', 'AWS手工录入'), ('aws_sync', 'AWS脚本同步'), ('order', '订单创建')], db_index=True, default='order', max_length=32, verbose_name='来源')),
                ('provider', models.CharField(blank=True, db_index=True, max_length=32, null=True, verbose_name='云厂商')),
                ('region_code', models.CharField(blank=True, db_index=True, max_length=64, null=True, verbose_name='地区代码')),
                ('region_name', models.CharField(blank=True, max_length=128, null=True, verbose_name='地区名称')),
                ('asset_name', models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='资产名称')),
                ('instance_id', models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='实例ID')),
                ('provider_resource_id', models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='云资源ID')),
                ('public_ip', models.CharField(blank=True, db_index=True, max_length=128, null=True, verbose_name='公网IP')),
                ('previous_public_ip', models.CharField(blank=True, max_length=128, null=True, verbose_name='历史公网IP')),
                ('login_user', models.CharField(blank=True, max_length=64, null=True, verbose_name='登录账号')),
                ('login_password', models.CharField(blank=True, max_length=191, null=True, verbose_name='登录密码')),
                ('mtproxy_port', models.IntegerField(blank=True, null=True, verbose_name='MTProxy端口')),
                ('mtproxy_link', models.TextField(blank=True, null=True, verbose_name='MTProxy链接')),
                ('mtproxy_secret', models.CharField(blank=True, max_length=64, null=True, verbose_name='MTProxy密钥')),
                ('mtproxy_host', models.CharField(blank=True, max_length=191, null=True, verbose_name='MTProxy主机')),
                ('actual_expires_at', models.DateTimeField(blank=True, db_index=True, null=True, verbose_name='实际到期时间')),
                ('note', models.TextField(blank=True, null=True, verbose_name='备注')),
                ('is_active', models.BooleanField(db_index=True, default=True, verbose_name='有效')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='创建时间')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='更新时间')),
                ('order', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='mall.cloudserverorder', verbose_name='关联订单')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='accounts.telegramuser', verbose_name='绑定用户')),
            ],
            options={
                'verbose_name': '云资产',
                'verbose_name_plural': '云资产',
                'db_table': 'cloud_assets',
                'ordering': ['-updated_at', '-id'],
            },
        ),
    ]
