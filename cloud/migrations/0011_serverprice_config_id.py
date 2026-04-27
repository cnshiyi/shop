from django.db import migrations, models


def build_config_id(provider: str, region_code: str, bundle_code: str) -> str:
    provider_key = str(provider or '').strip().replace('_', '-')
    region_key = str(region_code or '').strip().replace('_', '-')
    bundle_key = str(bundle_code or '').strip().replace('_', '-')
    return f'{provider_key}:{region_key}:{bundle_key}'[:64]


def forwards(apps, schema_editor):
    ServerPrice = apps.get_model('cloud', 'ServerPrice')
    CloudServerPlan = apps.get_model('cloud', 'CloudServerPlan')

    for price in ServerPrice.objects.all().iterator():
        price.config_id = build_config_id(price.provider, price.region_code, price.bundle_code)
        price.save(update_fields=['config_id'])

    for plan in CloudServerPlan.objects.exclude(provider_plan_id='').iterator():
        matched_price = ServerPrice.objects.filter(
            provider=plan.provider,
            region_code=plan.region_code,
            bundle_code=plan.provider_plan_id,
        ).only('config_id').first()
        if matched_price and str(matched_price.config_id or '').strip():
            plan.config_id = matched_price.config_id
            plan.save(update_fields=['config_id'])


class Migration(migrations.Migration):
    dependencies = [
        ('cloud', '0010_cloudserverplan_config_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='serverprice',
            name='config_id',
            field=models.CharField(blank=True, db_index=True, default='', max_length=64, verbose_name='配置ID'),
        ),
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
