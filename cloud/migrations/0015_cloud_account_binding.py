# Generated for cloud multi-account binding.

from django.db import migrations, models
import django.db.models.deletion


def backfill_account_labels(apps, schema_editor):
    CloudServerOrder = apps.get_model('cloud', 'CloudServerOrder')
    CloudAsset = apps.get_model('cloud', 'CloudAsset')
    Server = apps.get_model('cloud', 'Server')
    CloudAccountConfig = apps.get_model('core', 'CloudAccountConfig')

    def provider_key(provider):
        if provider == 'aws_lightsail':
            return 'aws'
        if provider == 'aliyun_simple':
            return 'aliyun'
        return provider or ''

    account_map = {}
    for account in CloudAccountConfig.objects.filter(is_active=True).order_by('provider', 'id'):
        account_map.setdefault(account.provider, account)

    for order in CloudServerOrder.objects.all().iterator():
        account = account_map.get(provider_key(order.provider))
        if account:
            order.cloud_account_id = account.id
            order.account_label = f'{account.provider}:{account.id}:{account.name}'[:191]
        elif not order.account_label:
            order.account_label = order.provider
        order.save(update_fields=['cloud_account', 'account_label'])

    for asset in CloudAsset.objects.all().iterator():
        account = account_map.get(provider_key(asset.provider))
        if account:
            asset.cloud_account_id = account.id
            asset.account_label = f'{account.provider}:{account.id}:{account.name}'[:191]
        elif not asset.account_label:
            asset.account_label = asset.provider
        asset.save(update_fields=['cloud_account', 'account_label'])

    for server in Server.objects.filter(account_label__isnull=True).iterator():
        account = account_map.get(provider_key(server.provider))
        server.account_label = f'{account.provider}:{account.id}:{account.name}'[:191] if account else server.provider
        server.save(update_fields=['account_label'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0005_alter_cloudaccountconfig_table_and_more'),
        ('cloud', '0014_resequence_active_config_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='cloudserverorder',
            name='account_label',
            field=models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='账户/来源标识'),
        ),
        migrations.AddField(
            model_name='cloudserverorder',
            name='cloud_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cloud_orders', to='core.cloudaccountconfig', verbose_name='云账号'),
        ),
        migrations.AddField(
            model_name='cloudasset',
            name='account_label',
            field=models.CharField(blank=True, db_index=True, max_length=191, null=True, verbose_name='账户/来源标识'),
        ),
        migrations.AddField(
            model_name='cloudasset',
            name='cloud_account',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='cloud_assets', to='core.cloudaccountconfig', verbose_name='云账号'),
        ),
        migrations.RunPython(backfill_account_labels, migrations.RunPython.noop),
    ]
