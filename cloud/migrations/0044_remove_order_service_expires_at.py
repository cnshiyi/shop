from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0043_backfill_cloud_asset_expiry'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='cloudserverorder',
            name='service_expires_at',
        ),
    ]
