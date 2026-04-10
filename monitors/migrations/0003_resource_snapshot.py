from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('monitors', '0002_monitor_flags'),
    ]

    operations = [
        migrations.AddField(
            model_name='addressmonitor',
            name='last_bandwidth',
            field=models.BigIntegerField(default=0, verbose_name='上次可用带宽'),
        ),
        migrations.AddField(
            model_name='addressmonitor',
            name='last_energy',
            field=models.BigIntegerField(default=0, verbose_name='上次可用能量'),
        ),
        migrations.AddField(
            model_name='addressmonitor',
            name='resource_checked_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='资源检查时间'),
        ),
    ]
