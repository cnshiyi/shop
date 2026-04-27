from django.db import migrations, models
import uuid


def forwards(apps, schema_editor):
    CloudServerPlan = apps.get_model('cloud', 'CloudServerPlan')
    seen = set()
    for plan in CloudServerPlan.objects.all().order_by('id').iterator():
        base = (getattr(plan, 'provider_plan_id', '') or '').strip()
        if not base:
            base = f'cfg-{uuid.uuid4().hex[:12]}'
        candidate = base
        suffix = 1
        scope = (plan.provider, plan.region_code)
        while (scope, candidate) in seen:
            candidate = f'{base}-{suffix}'
            suffix += 1
        plan.config_id = candidate
        seen.add((scope, candidate))
        plan.save(update_fields=['config_id'])


class Migration(migrations.Migration):
    dependencies = [
        ('cloud', '0009_serverprice_display_bandwidth_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverplan',
            name='config_id',
            field=models.CharField(blank=True, db_index=True, default='', max_length=64, verbose_name='配置ID'),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.AlterUniqueTogether(
            name='cloudserverplan',
            unique_together={('provider', 'region_code', 'config_id')},
        ),
    ]
