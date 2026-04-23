from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_cloudaccount_status_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='cloudaccountconfig',
            name='provider',
            field=models.CharField(choices=[('aws', 'AWS'), ('aliyun', '阿里云'), ('trongrid', 'TRONGrid')], db_index=True, max_length=32, verbose_name='云厂商'),
        ),
        migrations.CreateModel(
            name='ExternalSyncLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('source', models.CharField(choices=[('trongrid', 'TRONGrid'), ('aws_lightsail', 'AWS Lightsail'), ('aliyun', '阿里云'), ('dashboard', '后台接口')], db_index=True, max_length=32, verbose_name='来源')),
                ('action', models.CharField(db_index=True, max_length=64, verbose_name='动作')),
                ('target', models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='目标')),
                ('request_payload', models.TextField(blank=True, null=True, verbose_name='请求载荷')),
                ('response_payload', models.TextField(blank=True, null=True, verbose_name='响应载荷')),
                ('is_success', models.BooleanField(db_index=True, default=True, verbose_name='是否成功')),
                ('error_message', models.TextField(blank=True, null=True, verbose_name='错误信息')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True, verbose_name='创建时间')),
                ('account', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sync_logs', to='core.cloudaccountconfig', verbose_name='关联账户')),
            ],
            options={
                'verbose_name': '外部同步日志',
                'verbose_name_plural': '外部同步日志',
                'db_table': 'external_sync_logs',
                'ordering': ['-created_at', '-id'],
            },
        ),
    ]
