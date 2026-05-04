from django.utils import timezone
from core.cloud_accounts import list_active_cloud_accounts
from cloud.management.commands.sync_aws_assets import _lightsail_client

a = list_active_cloud_accounts('aws')[0]
region = 'eu-west-3'
client = _lightsail_client(region, a)
stamp = timezone.now().strftime('%Y%m%d%H%M%S')
blueprints = client.get_blueprints().get('blueprints') or []
ids = {item.get('blueprintId') for item in blueprints if item.get('type') == 'os'}
blueprint_id = next(item for item in ('debian_12', 'ubuntu_22_04', 'amazon_linux_2023') if item in ids)
bundles = [item for item in client.get_bundles().get('bundles') or [] if 'LINUX_UNIX' in (item.get('supportedPlatforms') or [])]
bundles.sort(key=lambda item: float(item.get('price') or 999999))
azs = [zone.get('zoneName') for item in client.get_regions(includeAvailabilityZones=True).get('regions') or [] if item.get('name') == region for zone in item.get('availabilityZones') or [] if zone.get('zoneName')]
errors = []
for az in azs:
    for bundle in bundles[:3]:
        bundle_id = bundle.get('bundleId')
        name = f'oc-sync-test-{region}-{stamp[-6:]}-{az[-1]}-{bundle_id.split("_")[0]}'[:64]
        try:
            client.create_instances(instanceNames=[name], availabilityZone=az, blueprintId=blueprint_id, bundleId=bundle_id, tags=[{'key':'purpose','value':'openclaw-sync-test'},{'key':'createdBy','value':'openclaw'},{'key':'createdAt','value':stamp}])
            print('CREATED', region, name, az, blueprint_id, bundle_id)
            raise SystemExit(0)
        except Exception as exc:
            errors.append((az, bundle_id, str(exc)[:180]))
            print('FAILED', az, bundle_id, str(exc)[:180])
raise SystemExit('all eu-west-3 attempts failed: %s' % errors)
