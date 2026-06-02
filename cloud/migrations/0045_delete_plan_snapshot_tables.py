from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('cloud', '0044_remove_order_service_expires_at'),
    ]

    operations = [
        migrations.DeleteModel(
            name='CloudAutoRenewPlan',
        ),
        migrations.DeleteModel(
            name='CloudNoticePlan',
        ),
        migrations.DeleteModel(
            name='CloudLifecyclePlan',
        ),
    ]
