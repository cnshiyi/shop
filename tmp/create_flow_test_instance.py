import asyncio
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')

import django

django.setup()

from cloud.aws_lightsail import create_instance
from mall.models import CloudServerOrder, CloudServerPlan
from django.utils import timezone

plan = CloudServerPlan.objects.filter(
    provider='aws_lightsail',
    region_code='ap-southeast-1',
    is_active=True,
).order_by('price', 'id').first() or CloudServerPlan.objects.filter(
    provider='aws_lightsail',
    is_active=True,
).order_by('price', 'id').first()
assert plan, 'no aws plan'

order = CloudServerOrder.objects.create(
    order_no=f"TESTAWSFLOW{timezone.now().strftime('%Y%m%d%H%M%S')}",
    user_id=1,
    plan=plan,
    provider=plan.provider,
    region_code=plan.region_code,
    region_name=plan.region_name,
    plan_name=plan.plan_name,
    quantity=1,
    currency='USDT',
    total_amount=plan.price,
    pay_amount=plan.price,
    pay_method='balance',
    status='paid',
    paid_at=timezone.now(),
    mtproxy_port=9528,
)
print('created_order', order.id, order.order_no, flush=True)
result = asyncio.run(create_instance(order, f"flowtest-{timezone.now().strftime('%H%M%S')}"))
print(json.dumps({
    'ok': result.ok,
    'instance_id': result.instance_id,
    'public_ip': result.public_ip,
    'login_user': result.login_user,
    'login_password': result.login_password,
    'note': result.note,
}, ensure_ascii=False), flush=True)
