from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0024_addressmonitor_resource_thresholds'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverorder',
            name='auto_renew_notice_sent_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='自动续费预提醒发送时间'),
        ),
    ]
