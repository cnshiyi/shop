from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('mall', '0023_drop_legacy_cloud_server_pricing'),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[],
            state_operations=[
                migrations.DeleteModel(
                    name='CloudServerPricing',
                ),
            ],
        ),
    ]
