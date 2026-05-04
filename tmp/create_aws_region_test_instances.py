import json
import time
from pathlib import Path
from django.utils import timezone
from core.cloud_accounts import cloud_account_label, list_active_cloud_accounts
from cloud.management.commands.sync_aws_assets import _lightsail_client, _list_regions

account = list_active_cloud_accounts('aws')[0]
account_label = cloud_account_label(account)
regions = _list_regions('all', account)
stamp = timezone.now().strftime('%Y%m%d%H%M%S')
record = {
    'created_at': timezone.now().isoformat(),
    'account_label': account_label,
    'prefix': f'oc-sync-test-{stamp}',
    'instances': [],
    'errors': [],
}

def choose_blueprint(client):
    blueprints = client.get_blueprints().get('blueprints') or []
    ids = {item.get('blueprintId') for item in blueprints if item.get('type') == 'os'}
    for candidate in ('debian_12', 'debian_13', 'ubuntu_22_04', 'ubuntu_24_04', 'amazon_linux_2023'):
        if candidate in ids:
            return candidate
    raise RuntimeError('no supported os blueprint')

def choose_bundle(client):
    bundles = client.get_bundles().get('bundles') or []
    linux = [item for item in bundles if item.get('supportedPlatforms') and 'LINUX_UNIX' in item.get('supportedPlatforms')]
    candidates = linux or bundles
    candidates.sort(key=lambda item: float(item.get('price') or 999999))
    if not candidates:
        raise RuntimeError('no bundle available')
    return candidates[0].get('bundleId')

def choose_az(client, region):
    response = client.get_regions(includeAvailabilityZones=True)
    for item in response.get('regions') or []:
        if item.get('name') == region:
            for zone in item.get('availabilityZones') or []:
                if zone.get('state') == 'available' and zone.get('zoneName'):
                    return zone.get('zoneName')
            zones = [zone.get('zoneName') for zone in item.get('availabilityZones') or [] if zone.get('zoneName')]
            if zones:
                return zones[0]
    return f'{region}a'

for region in regions:
    name = f'oc-sync-test-{region}-{stamp[-6:]}'
    try:
        client = _lightsail_client(region, account)
        blueprint_id = choose_blueprint(client)
        bundle_id = choose_bundle(client)
        az = choose_az(client, region)
        client.create_instances(
            instanceNames=[name],
            availabilityZone=az,
            blueprintId=blueprint_id,
            bundleId=bundle_id,
            tags=[
                {'key': 'purpose', 'value': 'openclaw-sync-test'},
                {'key': 'createdBy', 'value': 'openclaw'},
                {'key': 'createdAt', 'value': stamp},
            ],
        )
        item = {
            'region': region,
            'name': name,
            'availability_zone': az,
            'blueprint_id': blueprint_id,
            'bundle_id': bundle_id,
            'public_ip': None,
            'state': 'creating',
        }
        record['instances'].append(item)
        print('CREATED', region, name, az, blueprint_id, bundle_id)
    except Exception as exc:
        error = {'region': region, 'error': str(exc)}
        record['errors'].append(error)
        print('CREATE_FAILED', region, exc)

# Wait for public IPs, but do not delete anything.
deadline = time.time() + 600
pending = {(item['region'], item['name']) for item in record['instances']}
while pending and time.time() < deadline:
    for item in record['instances']:
        key = (item['region'], item['name'])
        if key not in pending:
            continue
        try:
            client = _lightsail_client(item['region'], account)
            instance = client.get_instance(instanceName=item['name']).get('instance') or {}
            item['state'] = ((instance.get('state') or {}).get('name') or '')
            item['public_ip'] = instance.get('publicIpAddress') or None
            item['arn'] = instance.get('arn') or None
            if item['public_ip'] and item['state'] in {'running', 'pending'}:
                pending.discard(key)
                print('READY', item['region'], item['name'], item['public_ip'], item['state'])
        except Exception as exc:
            item['wait_error'] = str(exc)
            pending.discard(key)
            print('WAIT_FAILED', item['region'], item['name'], exc)
    if pending:
        time.sleep(15)

record['finished_at'] = timezone.now().isoformat()
record['pending'] = [{'region': region, 'name': name} for region, name in sorted(pending)]
path = Path('tmp') / f'aws-region-sync-test-{stamp}.json'
path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
print('RECORD', path)
print('CREATED_COUNT', len(record['instances']))
print('ERROR_COUNT', len(record['errors']))
