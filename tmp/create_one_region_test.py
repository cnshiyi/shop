from django.utils import timezone
from core.cloud_accounts import list_active_cloud_accounts
from cloud.management.commands.sync_aws_assets import _lightsail_client

a = list_active_cloud_accounts('aws')[0]
region = 'eu-west-3'
stamp = timezone.now().strftime('%Y%m%d%H%M%S')
name = f'oc-sync-test-{region}-{stamp[-6:]}'
client = _lightsail_client(region, a)
blueprints = client.get_blueprints().get('blueprints') or []
ids = {item.get('blueprintId') for item in blueprints if item.get('type') == 'os'}
blueprint_id = next(item for item in ('debian_12', 'debian_13', 'ubuntu_22_04', 'ubuntu_24_04', 'amazon_linux_2023') if item in ids)
bundles = client.get_bundles().get('bundles') or []
linux = [item for item in bundles if 'LINUX_UNIX' in (item.get('supportedPlatforms') or [])]
linux.sort(key=lambda item: float(item.get('price') or 999999))
bundle_id = linux[0].get('bundleId')
regions = client.get_regions(includeAvailabilityZones=True).get('regions') or []
az = next(zone.get('zoneName') for item in regions if item.get('name') == region for zone in item.get('availabilityZones') or [] if zone.get('zoneName'))
client.create_instances(instanceNames=[name], availabilityZone=az, blueprintId=blueprint_id, bundleId=bundle_id, tags=[{'key':'purpose','value':'openclaw-sync-test'},{'key':'createdBy','value':'openclaw'},{'key':'createdAt','value':stamp}])
print('CREATED', region, name, az, blueprint_id, bundle_id)
