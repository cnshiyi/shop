from django.db import migrations
from django.db.models import OuterRef, Subquery


def backfill_cloud_asset_expiry(apps, schema_editor):
    CloudAsset = apps.get_model('cloud', 'CloudAsset')
    CloudServerOrder = apps.get_model('cloud', 'CloudServerOrder')
    order_expiry = CloudServerOrder.objects.filter(pk=OuterRef('order_id')).values('service_expires_at')[:1]
    CloudAsset.objects.filter(
        kind='server',
        actual_expires_at__isnull=True,
        order_id__isnull=False,
        order__service_expires_at__isnull=False,
    ).update(actual_expires_at=Subquery(order_expiry))


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0042_cloudassetsyncjobevent_and_more'),
    ]

    operations = [
        migrations.RunPython(backfill_cloud_asset_expiry, migrations.RunPython.noop),
    ]
