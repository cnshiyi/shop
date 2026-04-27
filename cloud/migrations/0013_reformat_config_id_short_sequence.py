from django.db import migrations


def build_config_id(provider: str, region_code: str, sequence: int) -> str:
    provider_key = str(provider or '').strip().replace('_', '-')
    region_key = str(region_code or '').strip().replace('_', '-')
    return f'{provider_key}-{region_key}-{int(sequence)}'[:64]


def forwards(apps, schema_editor):
    ServerPrice = apps.get_model('cloud', 'ServerPrice')
    CloudServerPlan = apps.get_model('cloud', 'CloudServerPlan')

    keys = (
        ServerPrice.objects
        .values_list('provider', 'region_code')
        .distinct()
        .order_by('provider', 'region_code')
    )

    for provider, region_code in keys:
        prices = list(
            ServerPrice.objects
            .filter(provider=provider, region_code=region_code, is_active=True)
            .order_by('cost_price', 'price', 'id')
        )
        for seq, price in enumerate(prices, start=1):
            price.config_id = build_config_id(provider, region_code, seq)
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
        ('cloud', '0012_reformat_config_id_sequence'),
    ]

    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
    ]
