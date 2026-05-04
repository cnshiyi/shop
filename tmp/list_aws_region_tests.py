from core.cloud_accounts import list_active_cloud_accounts
from cloud.management.commands.sync_aws_assets import _list_regions, _lightsail_client

a = list_active_cloud_accounts('aws')[0]
regions = _list_regions('all', a)
total = 0
rows = []
errors = []
for region in regions:
    try:
        client = _lightsail_client(region, a)
        items = client.get_instances().get('instances') or []
        matches = [item for item in items if (item.get('name') or '').startswith('oc-sync-test-')]
        total += len(matches)
        rows.extend((region, item.get('name'), item.get('publicIpAddress'), (item.get('state') or {}).get('name')) for item in matches)
    except Exception as exc:
        errors.append((region, str(exc)[:160]))
print('total', total)
for row in rows:
    print(*row)
print('errors', errors)
