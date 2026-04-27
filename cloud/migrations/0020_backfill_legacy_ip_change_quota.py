from django.db import migrations


def backfill_legacy_ip_change_quota(apps, schema_editor):
    CloudServerOrder = apps.get_model('cloud', 'CloudServerOrder')
    CloudServerOrder.objects.filter(
        provider='aws_lightsail',
        status__in=['completed', 'expiring', 'suspended'],
        ip_change_quota__lte=0,
    ).update(ip_change_quota=1)


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0019_cloudserverorder_cloud_reminder_enabled'),
    ]

    operations = [
        migrations.RunPython(backfill_legacy_ip_change_quota, migrations.RunPython.noop),
    ]
