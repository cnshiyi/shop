import json

from django.contrib.auth import get_user_model
from django.test import Client

from accounts.models import TelegramUser
from mall.models import CloudAsset

User = get_user_model()
login_user = User.objects.order_by('id').first()
client = Client(HTTP_HOST='127.0.0.1:8001')
client.force_login(login_user)

tg_user = TelegramUser.objects.order_by('id').first()
asset = CloudAsset.objects.order_by('id').first()

if tg_user:
    response = client.post(
        f'/api/admin/users/{tg_user.id}/balance/',
        data=json.dumps({'balance': str(tg_user.balance), 'balance_trx': str(tg_user.balance_trx)}),
        content_type='application/json',
    )
    print('balance', response.status_code, response.content.decode('utf-8', 'ignore')[:300])
else:
    print('balance skipped no TelegramUser')

if asset:
    response = client.post(
        f'/api/admin/cloud-assets/{asset.id}/',
        data=json.dumps({'note': asset.note or ''}),
        content_type='application/json',
    )
    print('asset', response.status_code, response.content.decode('utf-8', 'ignore')[:300])
else:
    print('asset skipped no CloudAsset')
