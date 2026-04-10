from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('monitors', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='addressmonitor',
            name='monitor_resources',
            field=models.BooleanField(default=False, verbose_name='监控资源'),
        ),
        migrations.AddField(
            model_name='addressmonitor',
            name='monitor_transfers',
            field=models.BooleanField(default=True, verbose_name='监控转账'),
        ),
    ]
