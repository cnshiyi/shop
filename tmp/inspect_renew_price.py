from cloud.models import CloudAsset, CloudServerOrder
from cloud.services import _renewal_price

for oid in [57]:
    order = CloudServerOrder.objects.select_related('user').filter(id=oid).first()
    print('ORDER', oid, order and {
        'id': order.id,
        'order_no': order.order_no,
        'status': order.status,
        'user_id': order.user_id,
        'public_ip': order.public_ip,
        'total_amount': str(order.total_amount),
        'pay_amount': str(order.pay_amount),
        'currency': order.currency,
        'replacement_for_id': order.replacement_for_id,
        'cloud_reminder_enabled': order.cloud_reminder_enabled,
    })
    print('renewal_price', _renewal_price(order, order.user) if order else None)

for aid in [89]:
    asset = CloudAsset.objects.select_related('order', 'user').filter(id=aid).first()
    print('ASSET', aid, asset and {
        'id': asset.id,
        'public_ip': asset.public_ip,
        'price': str(asset.price),
        'user_id': asset.user_id,
        'order_id': asset.order_id,
        'order_no': getattr(asset.order, 'order_no', None),
        'order_total': str(getattr(asset.order, 'total_amount', None)),
        'status': asset.status,
        'actual_expires_at': str(asset.actual_expires_at),
    })
    print('asset_renewal_price', _renewal_price(asset.order, asset.user) if asset and asset.order else None)

ip = CloudAsset.objects.filter(id=89).values_list('public_ip', flat=True).first()
print('orders same ip:', ip)
for order in CloudServerOrder.objects.filter(public_ip=ip).order_by('-id')[:20]:
    print(order.id, order.order_no, order.status, order.user_id, order.public_ip, order.total_amount, order.pay_amount, order.replacement_for_id)
