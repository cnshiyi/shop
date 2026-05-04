from django.test import Client
c = Client(HTTP_HOST='127.0.0.1')
for path in ['/admin/cloud-assets/sync/', '/api/dashboard/admin/cloud-assets/sync/', '/api/admin/cloud-assets/sync/']:
    response = c.post(path, data='{}', content_type='application/json')
    print('PATH', path, 'STATUS', response.status_code, 'BODY', response.content.decode()[:1000])
