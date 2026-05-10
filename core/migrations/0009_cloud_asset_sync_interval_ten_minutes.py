from django.db import migrations


def update_cloud_asset_sync_interval(apps, schema_editor):
    SiteConfig = apps.get_model('core', 'SiteConfig')
    SiteConfig.objects.filter(
        key='cloud_asset_sync_interval_seconds',
        value__in=['', '1800', '1800.0'],
    ).update(value='600', is_sensitive=False)


def restore_cloud_asset_sync_interval(apps, schema_editor):
    SiteConfig = apps.get_model('core', 'SiteConfig')
    SiteConfig.objects.filter(
        key='cloud_asset_sync_interval_seconds',
        value='600',
    ).update(value='1800', is_sensitive=False)


class Migration(migrations.Migration):
    dependencies = [
        ('core', '0008_cloudaccount_shutdown_enabled'),
    ]

    operations = [
        migrations.RunPython(update_cloud_asset_sync_interval, restore_cloud_asset_sync_interval),
    ]
