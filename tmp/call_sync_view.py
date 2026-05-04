from django.test import RequestFactory
from cloud.api import sync_cloud_assets
req = RequestFactory().post('/api/admin/cloud-assets/sync/', data='{}', content_type='application/json')
f = sync_cloud_assets
while hasattr(f, '__wrapped__'):
    print('unwrap', f.__name__)
    f = f.__wrapped__
print('target', f)
response = f(req)
print('status', getattr(response, 'status_code', None))
print((getattr(response, 'content', b'') or b'')[:3000])
