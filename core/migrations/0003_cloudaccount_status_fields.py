from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_sensitive_config_and_cloud_accounts'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudaccountconfig',
            name='last_checked_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='最近巡检时间'),
        ),
        migrations.AddField(
            model_name='cloudaccountconfig',
            name='status',
            field=models.CharField(choices=[('unknown', '未检查'), ('ok', '正常'), ('error', '异常'), ('unsupported', '暂不支持')], db_index=True, default='unknown', max_length=32, verbose_name='巡检状态'),
        ),
        migrations.AddField(
            model_name='cloudaccountconfig',
            name='status_note',
            field=models.TextField(blank=True, null=True, verbose_name='巡检说明'),
        ),
    ]
