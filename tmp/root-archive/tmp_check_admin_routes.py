from django.test import Client

URLS = [
    '/admin/',
    '/admin/accounts/telegramuser/',
    '/admin/mall/cloudserverorder/',
    '/admin/finance/recharge/',
    '/admin/monitoring/addressmonitor/',
    '/admin/mall/product/',
    '/admin/mall/cloudserverplan/',
    '/admin/core/siteconfig/',
]

client = Client()
failed = []
for url in URLS:
    response = client.get(url, follow=False, HTTP_HOST='127.0.0.1')
    status = response.status_code
    print(f'{url} {status}')
    if status not in (200, 302):
        failed.append((url, status))

if failed:
    raise SystemExit(f'ROUTE_FAILED {failed}')
print('ADMIN_ROUTES_OK')
