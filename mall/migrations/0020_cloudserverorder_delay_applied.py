from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0019_cloudserverorder_auto_renew_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverorder',
            name='delay_applied',
            field=models.BooleanField(db_index=True, default=False, verbose_name='已延期'),
        ),
    ]
