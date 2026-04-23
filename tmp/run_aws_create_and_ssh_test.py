import asyncio
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BASE_DIR))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')

import django
from django.db import close_old_connections

django.setup()

from cloud.aws_lightsail import create_instance
from cloud.bootstrap import _wait_ssh_password_ready
from cloud.provisioning import _mark_success, _mark_failed
from mall.models import CloudServerOrder, CloudServerPlan
from django.utils import timezone


def _refresh_order(order_id: int):
    close_old_connections()
    return CloudServerOrder.objects.get(id=order_id)

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
    order_no=f"TESTAWSSSH{timezone.now().strftime('%Y%m%d%H%M%S')}",
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
result = asyncio.run(create_instance(order, f"sshtest-{timezone.now().strftime('%H%M%S')}"))
print(json.dumps({
    'ok': result.ok,
    'instance_id': result.instance_id,
    'public_ip': result.public_ip,
    'login_user': result.login_user,
    'login_password': result.login_password,
    'note': result.note,
}, ensure_ascii=False), flush=True)
if result.ok:
    ssh_ok, ssh_note = asyncio.run(_wait_ssh_password_ready(result.public_ip, 'root', result.login_password, timeout=300, interval=10))
    print(json.dumps({'ssh_ok': ssh_ok, 'ssh_note': ssh_note}, ensure_ascii=False), flush=True)
    save_note = '\n'.join(part for part in [result.note, ssh_note] if part)
    if ssh_ok:
        close_old_connections()
        saved = asyncio.run(_mark_success(order.id, result.instance_id, result.instance_id, result.public_ip, result.login_user or 'admin', result.login_password, save_note))
    else:
        close_old_connections()
        fresh_order = _refresh_order(order.id)
        fresh_order.instance_id = result.instance_id
        fresh_order.provider_resource_id = result.instance_id
        fresh_order.public_ip = result.public_ip
        fresh_order.login_user = result.login_user or 'admin'
        fresh_order.login_password = result.login_password
        fresh_order.server_name = result.instance_id
        fresh_order.provision_note = save_note
        fresh_order.save(update_fields=['instance_id', 'provider_resource_id', 'public_ip', 'login_user', 'login_password', 'server_name', 'provision_note', 'updated_at'])
        close_old_connections()
        saved = asyncio.run(_mark_failed(order.id, save_note))
    print(json.dumps({
        'saved_order_id': saved.id,
        'saved_status': saved.status,
        'saved_public_ip': saved.public_ip,
        'saved_instance_id': saved.instance_id,
        'saved_login_user': saved.login_user,
    }, ensure_ascii=False), flush=True)
else:
    close_old_connections()
    saved = asyncio.run(_mark_failed(order.id, result.note or 'AWS 创建失败'))
    print(json.dumps({
        'saved_order_id': saved.id,
        'saved_status': saved.status,
        'saved_public_ip': saved.public_ip,
        'saved_instance_id': saved.instance_id,
        'saved_login_user': saved.login_user,
    }, ensure_ascii=False), flush=True)
