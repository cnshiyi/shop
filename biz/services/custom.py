from decimal import Decimal
import os
import random
import time

from asgiref.sync import sync_to_async
from django.utils import timezone

from biz.models import CloudServerOrder, CloudServerPlan
from .commerce import _generate_unique_pay_amount

AWS_REGION_NAMES = {
    'ap-south-1': '孟买',
    'ap-southeast-1': '新加坡',
    'ap-southeast-2': '悉尼',
    'ap-southeast-3': '雅加达',
    'ap-northeast-1': '东京',
    'ap-northeast-2': '首尔',
    'ca-central-1': '加拿大',
    'eu-central-1': '法兰克福',
    'eu-north-1': '斯德哥尔摩',
    'eu-west-1': '爱尔兰',
    'eu-west-2': '伦敦',
    'eu-west-3': '巴黎',
    'us-east-1': '弗吉尼亚',
    'us-east-2': '俄亥俄',
    'us-west-2': '俄勒冈',
}
ALIYUN_REGION_NAMES = {
    'cn-hongkong': '香港',
    'ap-southeast-1': '新加坡',
    'ap-southeast-5': '雅加达',
    'ap-southeast-7': '曼谷',
    'ap-northeast-1': '东京',
    'ap-south-1': '孟买',
    'eu-central-1': '法兰克福',
    'us-east-1': '弗吉尼亚',
    'me-east-1': '迪拜',
}

DEFAULT_AWS_PLAN_TEMPLATES = [
    ('micro_3_0', 'Micro 1G 40G 2TB', '1GB', '40GB SSD', '2TB'),
    ('small_3_0', 'Small 2G 60G 3TB', '2GB', '60GB SSD', '3TB'),
    ('medium_3_0', 'Medium 2G 60G 4TB', '2GB', '60GB SSD', '4TB'),
    ('large_3_0', 'Large 4G 80G 5TB', '4GB', '80GB SSD', '5TB'),
    ('xlarge_3_0', 'Xlarge 8G 160G 6TB', '8GB', '160GB SSD', '6TB'),
    ('2xlarge_3_0', '2Xlarge 16G 320G 7TB', '16GB', '320GB SSD', '7TB'),
]
DEFAULT_ALIYUN_PLAN_TEMPLATES = [
    ('基础型', '1核', '1GB', '40GB SSD', '1TB', Decimal('8.50')),
    ('标准型', '2核', '2GB', '60GB SSD', '2TB', Decimal('12.50')),
    ('增强型', '2核', '4GB', '80GB SSD', '3TB', Decimal('18.50')),
    ('高配型', '4核', '8GB', '120GB SSD', '4TB', Decimal('28.50')),
    ('旗舰型', '8核', '16GB', '200GB SSD', '5TB', Decimal('48.50')),
    ('至尊型', '16核', '32GB', '400GB SSD', '6TB', Decimal('88.50')),
]


def _format_amount_tag(amount: Decimal) -> str:
    normalized = amount.normalize() if isinstance(amount, Decimal) else Decimal(str(amount)).normalize()
    text = format(normalized, 'f')
    return text.replace('.', '_')


def build_cloud_server_name(user_id: int, amount: Decimal) -> str:
    return f"{int(time.time())}-{user_id}-{_format_amount_tag(amount)}"


def _generate_order_no() -> str:
    return f'SRV{int(time.time() * 1000)}{random.randint(1000, 9999)}'


def _build_aliyun_client(endpoint: str = 'swas.cn-hangzhou.aliyuncs.com'):
    key = os.getenv('ALIBABA_CLOUD_ACCESS_KEY_ID', '')
    secret = os.getenv('ALIBABA_CLOUD_ACCESS_KEY_SECRET', '')
    if not key or not secret:
        return None
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_swas_open20200601.client import Client

    config = open_api_models.Config(
        access_key_id=key,
        access_key_secret=secret,
        endpoint=endpoint,
    )
    return Client(config)


def _parse_aliyun_price(value) -> Decimal:
    text = str(value or '').strip().replace('$', '')
    if not text:
        return Decimal('0')
    return Decimal(text)


def _fetch_aliyun_plan_templates(region_code: str):
    client = _build_aliyun_client()
    if not client:
        return DEFAULT_ALIYUN_PLAN_TEMPLATES
    try:
        from alibabacloud_swas_open20200601 import models as swas_models

        response = client.list_plans(swas_models.ListPlansRequest(region_id=region_code))
        plans = response.body.to_map().get('Plans', [])
        linux_plans = [item for item in plans if 'Linux' in str(item.get('SupportPlatform', ''))]
        linux_plans.sort(key=lambda item: (_parse_aliyun_price(item.get('OriginPrice')), item.get('Core') or 0, item.get('Memory') or 0))
        labels = ['基础型', '标准型', '增强型', '高配型', '旗舰型', '至尊型']
        templates = []
        for idx, item in enumerate(linux_plans[:6]):
            base_price = _parse_aliyun_price(item.get('OriginPrice'))
            sell_price = (base_price * Decimal('2')) + Decimal('5')
            templates.append((
                labels[idx],
                f"{item.get('Core') or '-'}核",
                f"{item.get('Memory') or '-'}GB",
                f"{item.get('DiskSize') or '-'}GB {item.get('DiskType') or 'SSD'}",
                f"{item.get('Bandwidth') or '-'}Mbps",
                sell_price.quantize(Decimal('0.01')),
            ))
        return templates or DEFAULT_ALIYUN_PLAN_TEMPLATES
    except Exception:
        return DEFAULT_ALIYUN_PLAN_TEMPLATES


def _fetch_aws_bundle_templates():
    key = os.getenv('AWS_ACCESS_KEY_ID', '')
    secret = os.getenv('AWS_SECRET_ACCESS_KEY', '')
    if not key or not secret:
        return []
    try:
        import boto3
        client = boto3.client(
            'lightsail',
            region_name='ap-southeast-1',
            aws_access_key_id=key,
            aws_secret_access_key=secret,
        )
        response = client.get_bundles(includeInactive=False)
        templates = []
        allowed = {'micro_3_0', 'small_3_0', 'medium_3_0', 'large_3_0', 'xlarge_3_0', '2xlarge_3_0'}
        for item in response.get('bundles', []):
            bundle_id = item.get('bundleId')
            if bundle_id not in allowed:
                continue
            ram = item.get('ramSizeInGb')
            disk = item.get('diskSizeInGb')
            transfer = item.get('transferPerMonthInGb')
            base_price = Decimal(str(item.get('price') or '0'))
            sell_price = (base_price * Decimal('2')) + Decimal('5')
            templates.append((
                bundle_id,
                item.get('name') or bundle_id,
                f"{item.get('cpuCount') or '-'}核",
                f'{ram}GB' if ram is not None else '',
                f'{disk}GB SSD' if disk is not None else '',
                f'{transfer}GB' if transfer is not None else '',
                sell_price.quantize(Decimal('0.01')),
            ))
        return templates or [
            ('micro_3_0', 'Micro 1G 40G 2TB', '2核', '1GB', '40GB SSD', '2TB', Decimal('19.00')),
            ('small_3_0', 'Small 2G 60G 3TB', '2核', '2GB', '60GB SSD', '3TB', Decimal('29.00')),
            ('medium_3_0', 'Medium 2G 60G 4TB', '2核', '2GB', '60GB SSD', '4TB', Decimal('41.00')),
            ('large_3_0', 'Large 4G 80G 5TB', '2核', '4GB', '80GB SSD', '5TB', Decimal('53.00')),
            ('xlarge_3_0', 'Xlarge 8G 160G 6TB', '4核', '8GB', '160GB SSD', '6TB', Decimal('77.00')),
            ('2xlarge_3_0', '2Xlarge 16G 320G 7TB', '8核', '16GB', '320GB SSD', '7TB', Decimal('125.00')),
        ]
    except Exception:
        return [
            ('micro_3_0', 'Micro 1G 40G 2TB', '2核', '1GB', '40GB SSD', '2TB', Decimal('19.00')),
            ('small_3_0', 'Small 2G 60G 3TB', '2核', '2GB', '60GB SSD', '3TB', Decimal('29.00')),
            ('medium_3_0', 'Medium 2G 60G 4TB', '2核', '2GB', '60GB SSD', '4TB', Decimal('41.00')),
            ('large_3_0', 'Large 4G 80G 5TB', '2核', '4GB', '80GB SSD', '5TB', Decimal('53.00')),
            ('xlarge_3_0', 'Xlarge 8G 160G 6TB', '4核', '8GB', '160GB SSD', '6TB', Decimal('77.00')),
            ('2xlarge_3_0', '2Xlarge 16G 320G 7TB', '8核', '16GB', '320GB SSD', '7TB', Decimal('125.00')),
        ]


def _fetch_aws_regions():
    key = os.getenv('AWS_ACCESS_KEY_ID', '')
    secret = os.getenv('AWS_SECRET_ACCESS_KEY', '')
    if not key or not secret:
        return []
    try:
        import boto3
        client = boto3.client(
            'lightsail',
            region_name='ap-southeast-1',
            aws_access_key_id=key,
            aws_secret_access_key=secret,
        )
        response = client.get_regions(includeAvailabilityZones=False, includeRelationalDatabaseAvailabilityZones=False)
        result = []
        for item in response.get('regions', []):
            code = item.get('name')
            if not code:
                continue
            result.append((code, AWS_REGION_NAMES.get(code, code)))
        return result
    except Exception:
        return []


def _fetch_aliyun_regions():
    client = _build_aliyun_client()
    if not client:
        return []
    try:
        from alibabacloud_swas_open20200601 import models as swas_models

        response = client.list_regions(swas_models.ListRegionsRequest())
        regions = response.body.to_map().get('Regions', [])
        result = []
        for item in regions:
            code = item.get('RegionId')
            name = ALIYUN_REGION_NAMES.get(code) or item.get('LocalName') or code
            if not code:
                continue
            if code != 'cn-hongkong' and (code.startswith('cn-') or code in {'ap-southeast-3', 'ap-southeast-5'}):
                continue
            result.append((code, name))
        return result
    except Exception:
        return []


def _sync_provider_plans(provider: str, regions: list[tuple[str, str]], templates):
    region_codes = {code for code, _ in regions}
    active_plan_names = {template[1] if provider == 'aws_lightsail' else template[0] for template in templates}
    CloudServerPlan.objects.filter(provider=provider).exclude(region_code__in=region_codes).update(is_active=False)
    CloudServerPlan.objects.filter(provider=provider, region_code__in=region_codes).exclude(plan_name__in=active_plan_names).update(is_active=False)
    for region_code, region_name in regions:
        for template in templates:
            if provider == 'aws_lightsail':
                bundle_id, plan_name, cpu, memory, storage, bandwidth, price = template
            else:
                plan_name, cpu, memory, storage, bandwidth, price = template
            plan, created = CloudServerPlan.objects.get_or_create(
                provider=provider,
                region_code=region_code,
                plan_name=plan_name,
                defaults={
                    'region_name': region_name,
                    'cpu': cpu,
                    'memory': memory,
                    'storage': storage,
                    'bandwidth': bandwidth,
                    'price': price,
                    'currency': 'USDT',
                    'is_active': True,
                },
            )
            if not created:
                plan.region_name = region_name
                plan.cpu = cpu
                plan.memory = memory
                plan.storage = storage
                plan.bandwidth = bandwidth
                plan.price = price
                plan.is_active = True
                plan.save(update_fields=['region_name', 'cpu', 'memory', 'storage', 'bandwidth', 'price', 'is_active', 'updated_at'])


@sync_to_async
def ensure_cloud_server_plans():
    aws_regions = _fetch_aws_regions()
    aliyun_regions = _fetch_aliyun_regions()
    if aws_regions:
        _sync_provider_plans('aws_lightsail', aws_regions, _fetch_aws_bundle_templates())
    if aliyun_regions:
        for region_code, region_name in aliyun_regions:
            _sync_provider_plans('aliyun_simple', [(region_code, region_name)], _fetch_aliyun_plan_templates(region_code))
    else:
        existing_aliyun_regions = list(
            CloudServerPlan.objects.filter(provider='aliyun_simple', is_active=True)
            .values_list('region_code', 'region_name')
            .distinct()
        )
        if existing_aliyun_regions:
            for region_code, region_name in existing_aliyun_regions:
                _sync_provider_plans('aliyun_simple', [(region_code, region_name)], _fetch_aliyun_plan_templates(region_code))
    if not CloudServerPlan.objects.exists():
        _sync_provider_plans('aws_lightsail', [('ap-southeast-1', '新加坡')], _fetch_aws_bundle_templates())


def _sort_region_pairs(regions: list[tuple[str, str]]) -> list[tuple[str, str]]:
    preferred = ['新加坡', '香港']
    preferred_index = {name: idx for idx, name in enumerate(preferred)}
    return sorted(regions, key=lambda item: (preferred_index.get(item[1], 999), item[1], item[0]))


@sync_to_async
def list_custom_regions():
    ensure_cloud_server_plans.__wrapped__()
    plans = list(CloudServerPlan.objects.filter(is_active=True).values_list('provider', 'region_code', 'region_name').distinct())
    aws_regions = {code: name for provider, code, name in plans if provider == 'aws_lightsail'}
    aliyun_hk = [(code, name) for provider, code, name in plans if provider == 'aliyun_simple' and code == 'cn-hongkong']
    regions = list(aws_regions.items())
    if aliyun_hk and 'cn-hongkong' not in aws_regions:
        regions.extend(aliyun_hk)
    return _sort_region_pairs(regions)


@sync_to_async
def list_region_plans(region_code: str):
    ensure_cloud_server_plans.__wrapped__()
    provider = 'aliyun_simple' if region_code == 'cn-hongkong' else 'aws_lightsail'
    queryset = CloudServerPlan.objects.filter(region_code=region_code, provider=provider, is_active=True)
    queryset = queryset.exclude(provider='aws_lightsail', plan_name__iexact='Nano')
    return list(queryset.order_by('provider', '-sort_order', 'id')[:6])


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
