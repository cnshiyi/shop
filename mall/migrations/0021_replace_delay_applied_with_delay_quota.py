from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0020_cloudserverorder_delay_applied'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='cloudserverorder',
            name='delay_applied',
        ),
        migrations.AddField(
            model_name='cloudserverorder',
            name='delay_quota',
            field=models.IntegerField(default=0, verbose_name='延期次数'),
        ),
    ]
