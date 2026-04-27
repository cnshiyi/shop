from django.db import migrations, models


def forwards(apps, schema_editor):
    CloudServerPlan = apps.get_model('cloud', 'CloudServerPlan')
    for plan in CloudServerPlan.objects.all().iterator():
        desc = (plan.plan_description or '').strip()
        plan_id = ''
        if desc.startswith('PlanId:'):
            plan_id = desc.split(':', 1)[1].strip()
        elif plan.provider == 'aws_lightsail':
            plan_id = (plan.plan_name or '').strip()
        plan.provider_plan_id = plan_id
        plan.save(update_fields=['provider_plan_id'])


class Migration(migrations.Migration):
    dependencies = [
        ('cloud', '0006_alter_cloudiplog_event_type'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverplan',
            name='provider_plan_id',
            field=models.CharField(blank=True, db_index=True, default='', max_length=191, verbose_name='云厂商套餐ID'),
            preserve_default=False,
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name='cloudserverplan',
            unique_together={('provider', 'region_code', 'provider_plan_id')},
        ),
    ]
