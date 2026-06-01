from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0039_cloud_asset_indexes'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudasset',
            name='sync_state',
            field=models.JSONField(blank=True, default=dict, verbose_name='同步状态'),
        ),
    ]
