from decimal import Decimal
import random
import time

from asgiref.sync import sync_to_async
from django.utils import timezone

from biz.models import CloudServerOrder, CloudServerPlan
from .commerce import _generate_unique_pay_amount

AWS_REGIONS = [
    ('ap-southeast-1', '新加坡'),
    ('ap-southeast-2', '悉尼'),
    ('ap-northeast-1', '东京'),
    ('ap-northeast-2', '首尔'),
    ('eu-central-1', '法兰克福'),
    ('eu-west-1', '爱尔兰'),
    ('us-east-1', '弗吉尼亚'),
    ('us-west-2', '俄勒冈'),
]
ALIYUN_REGIONS = [
    ('ap-southeast-1', '新加坡'),
    ('ap-southeast-5', '雅加达'),
    ('ap-southeast-7', '曼谷'),
    ('ap-northeast-1', '东京'),
    ('ap-south-1', '孟买'),
    ('eu-central-1', '法兰克福'),
    ('us-east-1', '弗吉尼亚'),
    ('me-east-1', '迪拜'),
    ('cn-hongkong', '香港'),
]

DEFAULT_PLAN_SEEDS = [
    ('aws_lightsail', 'ap-southeast-1', '新加坡', '1C1G 40G 1TB', '1 vCPU', '1GB', '40GB SSD', '1TB', Decimal('7.00')),
    ('aws_lightsail', 'ap-southeast-1', '新加坡', '2C2G 60G 2TB', '2 vCPU', '2GB', '60GB SSD', '2TB', Decimal('12.00')),
    ('aws_lightsail', 'ap-northeast-1', '东京', '1C1G 40G 1TB', '1 vCPU', '1GB', '40GB SSD', '1TB', Decimal('8.00')),
    ('aws_lightsail', 'eu-central-1', '法兰克福', '2C2G 60G 2TB', '2 vCPU', '2GB', '60GB SSD', '2TB', Decimal('13.00')),
    ('aliyun_simple', 'cn-hongkong', '香港', '1C1G 40G 1TB', '1 vCPU', '1GB', '40GB SSD', '1TB', Decimal('8.50')),
    ('aliyun_simple', 'ap-southeast-5', '雅加达', '1C1G 40G 1TB', '1 vCPU', '1GB', '40GB SSD', '1TB', Decimal('7.50')),
    ('aliyun_simple', 'ap-southeast-7', '曼谷', '2C2G 60G 2TB', '2 vCPU', '2GB', '60GB SSD', '2TB', Decimal('12.50')),
    ('aliyun_simple', 'me-east-1', '迪拜', '2C2G 60G 2TB', '2 vCPU', '2GB', '60GB SSD', '2TB', Decimal('14.00')),
]


def _generate_order_no() -> str:
    return f'SRV{int(time.time() * 1000)}{random.randint(1000, 9999)}'


@sync_to_async
def ensure_cloud_server_plans():
    if CloudServerPlan.objects.exists():
        return
    for provider, region_code, region_name, plan_name, cpu, memory, storage, bandwidth, price in DEFAULT_PLAN_SEEDS:
        CloudServerPlan.objects.create(
            provider=provider,
            region_code=region_code,
            region_name=region_name,
            plan_name=plan_name,
            cpu=cpu,
            memory=memory,
            storage=storage,
            bandwidth=bandwidth,
            price=price,
            currency='USDT',
            is_active=True,
        )


@sync_to_async
def list_custom_regions():
    ensure_cloud_server_plans.__wrapped__()
    aws_regions = {code: name for code, name in AWS_REGIONS}
    aliyun_regions = {code: name for code, name in ALIYUN_REGIONS}
    aws_only = [(code, name) for code, name in aws_regions.items() if code not in aliyun_regions]
    aliyun_only = [(code, name) for code, name in aliyun_regions.items() if code == 'cn-hongkong' or code not in aws_regions]
    merged = aws_only + aliyun_only
    existing = set(CloudServerPlan.objects.filter(is_active=True).values_list('region_code', flat=True))
    return [(code, name) for code, name in merged if code in existing]


@sync_to_async
def list_region_plans(region_code: str):
    ensure_cloud_server_plans.__wrapped__()
    return list(
        CloudServerPlan.objects.filter(region_code=region_code, is_active=True)
        .order_by('provider', '-sort_order', 'id')
    )


@sync_to_async
def get_cloud_plan(plan_id: int):
    ensure_cloud_server_plans.__wrapped__()
    return CloudServerPlan.objects.filter(id=plan_id, is_active=True).first()


@sync_to_async
def create_cloud_server_order(user_id: int, plan_id: int, currency: str = 'USDT'):
    ensure_cloud_server_plans.__wrapped__()
    plan = CloudServerPlan.objects.get(id=plan_id, is_active=True)
    total = plan.price
    pay_amount = _generate_unique_pay_amount(total, currency)
    expired_at = timezone.now() + timezone.timedelta(minutes=15)
    return CloudServerOrder.objects.create(
        order_no=_generate_order_no(),
        user_id=user_id,
        plan=plan,
        provider=plan.provider,
        region_code=plan.region_code,
        region_name=plan.region_name,
        plan_name=plan.plan_name,
        currency=currency,
        total_amount=total,
        pay_amount=pay_amount,
        pay_method='address',
        status='pending',
        mtproxy_port=9528,
        expired_at=expired_at,
    )


@sync_to_async
def set_cloud_server_port(order_id: int, user_id: int, port: int):
    updated = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).update(mtproxy_port=port)
    if not updated:
        return None
    return CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
