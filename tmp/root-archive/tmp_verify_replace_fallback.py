from accounts.models import TelegramUser
from mall.models import CloudServerOrder, CloudServerPlan
from biz.services import cloud_servers
import asyncio

user, _ = TelegramUser.objects.get_or_create(
    id=999032,
    defaults={'tg_user_id': 999032, 'username': 'replace_test'},
)
plan = CloudServerPlan.objects.filter(is_active=True).first()
assert plan is not None
order = CloudServerOrder.objects.create(
    order_no='HB-REPLACE-FALLBACK-1',
    user=user,
    plan=plan,
    provider=plan.provider,
    region_code=plan.region_code,
    region_name=plan.region_name,
    plan_name=plan.plan_name,
    quantity=1,
    currency='USDT',
    total_amount='10',
    pay_amount='10',
    status='completed',
    public_ip='1.2.3.4',
)
original_filter = CloudServerOrder.objects.filter


class FakeQs:
    def first(self):
        obj = CloudServerOrder.objects.get(id=order.id)
        obj.plan_id = None
        return obj


CloudServerOrder.objects.filter = lambda *args, **kwargs: FakeQs()
new_order = None
try:
    new_order = asyncio.run(cloud_servers.mark_cloud_server_ip_change_requested(order.id, user.id))
    print({
        'new_order_created': bool(new_order),
        'new_order_plan_filled': bool(getattr(new_order, 'plan_id', None)),
        'replacement_for_ok': getattr(new_order, 'replacement_for_id', None) == order.id,
    })
finally:
    CloudServerOrder.objects.filter = original_filter
    CloudServerOrder.objects.filter(order_no__in=['HB-REPLACE-FALLBACK-1', 'HB-REPLACE-FALLBACK-1-IP']).delete()
