from django.db import migrations


def disable_manual_audit_lifecycle(apps, schema_editor):
    CloudServerOrder = apps.get_model('cloud', 'CloudServerOrder')
    CloudServerOrder.objects.filter(order_no__startswith='SRVMANUAL').update(
        status='cancelled',
        cloud_reminder_enabled=False,
        suspend_reminder_enabled=False,
        delete_reminder_enabled=False,
        ip_recycle_reminder_enabled=False,
        auto_renew_enabled=False,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0026_cloudserverorder_auto_renew_failure_notice_sent_at'),
    ]

    operations = [
        migrations.RunPython(disable_manual_audit_lifecycle, migrations.RunPython.noop),
    ]
