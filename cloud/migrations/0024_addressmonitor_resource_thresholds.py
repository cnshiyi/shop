from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0023_chain_payment_trace'),
    ]

    operations = [
        migrations.AddField(
            model_name='addressmonitor',
            name='energy_threshold',
            field=models.BigIntegerField(default=1, verbose_name='能量增加阈值'),
        ),
        migrations.AddField(
            model_name='addressmonitor',
            name='bandwidth_threshold',
            field=models.BigIntegerField(default=1, verbose_name='带宽增加阈值'),
        ),
    ]
