from decimal import Decimal
import json
import logging
import os
import random
import time

from asgiref.sync import sync_to_async
from django.db import transaction
from django.utils import timezone

from orders.ledger import record_balance_ledger
from bot.models import TelegramUser
from cloud.models import CloudServerOrder, CloudServerPlan, Server, ServerPrice
from core.cache import get_redis
from core.cloud_accounts import get_active_cloud_account
from .commerce import _generate_unique_pay_amount
from .rates import usdt_to_trx

logger = logging.getLogger(__name__)

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

SERVER_PRICE_REGION_RULES = {
    'aws_lightsail': {
        'allowed_regions': set(),
        'fallback_regions': [('ap-southeast-1', '新加坡')],
    },
    'aliyun_simple': {
        'allowed_regions': {'cn-hongkong'},
        'fallback_regions': [('cn-hongkong', '香港')],
    },
}


DEFAULT_AWS_PRICING_TEMPLATES = [
    ('micro_3_0', '入门款', '2核', '1GB', '40GB SSD', '2TB', Decimal('19.00')),
    ('small_3_0', '标准款', '2核', '2GB', '60GB SSD', '3TB', Decimal('29.00')),
    ('medium_3_0', '进阶款', '2核', '2GB', '60GB SSD', '4TB', Decimal('41.00')),
    ('large_3_0', '高配款', '2核', '4GB', '80GB SSD', '5TB', Decimal('53.00')),
    ('xlarge_3_0', '旗舰款', '4核', '8GB', '160GB SSD', '6TB', Decimal('77.00')),
    ('2xlarge_3_0', '至尊款', '8核', '16GB', '320GB SSD', '7TB', Decimal('125.00')),
    ('aws-storage-optimized', '存储型', '8核', '32GB', '640GB SSD', '8TB', Decimal('168.00')),
    ('aws-compute-optimized', '计算型', '16核', '32GB', '640GB SSD', '10TB', Decimal('228.00')),
    ('aws-enterprise', '企业型', '16核', '64GB', '1280GB SSD', '12TB', Decimal('328.00')),
]
DEFAULT_ALIYUN_PRICING_TEMPLATES = [
    ('basic', '基础型', '1核', '1GB', '40GB SSD', '1TB', Decimal('8.50')),
    ('standard', '标准型', '2核', '2GB', '60GB SSD', '2TB', Decimal('12.50')),
    ('enhanced', '增强型', '2核', '4GB', '80GB SSD', '3TB', Decimal('18.50')),
    ('pro', '高配型', '4核', '8GB', '120GB SSD', '4TB', Decimal('28.50')),
    ('flagship', '旗舰型', '8核', '16GB', '200GB SSD', '5TB', Decimal('48.50')),
    ('ultimate', '至尊型', '16核', '32GB', '400GB SSD', '6TB', Decimal('88.50')),
    ('migration', '迁移专用', '2核', '2GB', '40GB SSD', '1TB', Decimal('15.00')),
    ('stable', '稳定型', '2核', '4GB', '80GB SSD', '5TB', Decimal('26.00')),
    ('turbo', '加速型', '4核', '8GB', '100GB SSD', '6TB', Decimal('36.00')),
]

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
    ('迁移专用', '2核', '2GB', '40GB SSD', '1TB', Decimal('15.00')),
    ('稳定型', '2核', '4GB', '80GB SSD', '5TB', Decimal('26.00')),
    ('加速型', '4核', '8GB', '100GB SSD', '6TB', Decimal('36.00')),
]


def build_cloud_server_name(*args, **kwargs):
    from cloud.services import build_cloud_server_name as impl
    return impl(*args, **kwargs)


def ensure_unique_cloud_server_name(*args, **kwargs):
    from cloud.services import ensure_unique_cloud_server_name as impl
    return impl(*args, **kwargs)


def _generate_order_no() -> str:
    return f'SRV{int(time.time() * 1000)}{random.randint(1000, 9999)}'


def _build_aliyun_client(endpoint: str = 'swas.cn-hangzhou.aliyuncs.com'):
    account = get_active_cloud_account('aliyun')
    key = account.access_key_plain if account else os.getenv('ALIBABA_CLOUD_ACCESS_KEY_ID', '')
    secret = account.secret_key_plain if account else os.getenv('ALIBABA_CLOUD_ACCESS_KEY_SECRET', '')
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
        labels = ['基础型', '标准型', '增强型', '高配型', '旗舰型', '至尊型', '迁移专用', '稳定型', '加速型']
        templates = []
        for idx, item in enumerate(linux_plans[:9]):
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
        return _merge_templates(templates, DEFAULT_ALIYUN_PLAN_TEMPLATES)
    except Exception:
        return DEFAULT_ALIYUN_PLAN_TEMPLATES


def _is_primary_aws_bundle(bundle_id: str, bundle_name: str) -> bool:
    normalized_id = str(bundle_id or '').strip().lower()
    normalized_name = str(bundle_name or '').strip().lower()
    if not normalized_id:
        return False
    if 'win' in normalized_id or 'windows' in normalized_name:
        return False
    if 'ipv6' in normalized_id:
        return False
    if normalized_id.startswith(('c_', 'm_', 'g_')):
        return False
    return True


def _fetch_aws_bundle_templates():
    account = get_active_cloud_account('aws')
    key = account.access_key_plain if account else os.getenv('AWS_ACCESS_KEY_ID', '')
    secret = account.secret_key_plain if account else os.getenv('AWS_SECRET_ACCESS_KEY', '')
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
        bundle_candidates = []
        for item in response.get('bundles', []):
            if not item.get('isActive', True):
                continue
            bundle_id = item.get('bundleId')
            bundle_name = item.get('name') or bundle_id
            if not bundle_id or not _is_primary_aws_bundle(bundle_id, bundle_name):
                continue
            ram = item.get('ramSizeInGb')
            disk = item.get('diskSizeInGb')
            transfer = item.get('transferPerMonthInGb')
            base_price = Decimal(str(item.get('price') or '0')).quantize(Decimal('0.01'))
            bundle_candidates.append((
                bundle_id,
                bundle_name,
                f"{item.get('cpuCount') or '-'}核",
                f'{ram}GB' if ram is not None else '',
                f'{disk}GB SSD' if disk is not None else '',
                f'{transfer}GB' if transfer is not None else '',
                base_price,
            ))
        bundle_candidates.sort(key=lambda item: (item[6], item[1], item[0]))
        deduped_templates = []
        seen_names = set()
        for template in bundle_candidates:
            name_key = str(template[1] or '').strip().lower()
            if not name_key or name_key in seen_names:
                continue
            deduped_templates.append(template)
            seen_names.add(name_key)
        return deduped_templates
    except Exception:
        return []


def _fetch_aws_regions():
    account = get_active_cloud_account('aws')
    key = account.access_key_plain if account else os.getenv('AWS_ACCESS_KEY_ID', '')
    secret = account.secret_key_plain if account else os.getenv('AWS_SECRET_ACCESS_KEY', '')
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


def _merge_templates(primary, fallback, key_index: int = 0, limit: int | None = None):
    merged = []
    seen = set()
    for item in list(primary or []) + list(fallback or []):
        if not item:
            continue
        key = item[key_index]
        if key in seen:
            continue
        merged.append(item)
        seen.add(key)
        if limit is not None and len(merged) >= limit:
            break
    return merged


def _normalize_server_price_regions(provider: str, regions: list[tuple[str, str]]):
    rule = SERVER_PRICE_REGION_RULES.get(provider) or {}
    allowed_regions = set(rule.get('allowed_regions') or set())
    fallback_regions = list(rule.get('fallback_regions') or [])
    normalized = []
    seen = set()
    for region_code, region_name in regions or []:
        region_code = (region_code or '').strip()
        if not region_code:
            continue
        if allowed_regions and region_code not in allowed_regions:
            continue
        canonical_name = AWS_REGION_NAMES.get(region_code) or ALIYUN_REGION_NAMES.get(region_code) or (region_name or '').strip() or region_code
        if region_code in seen:
            continue
        normalized.append((region_code, canonical_name))
        seen.add(region_code)
    if normalized:
        return normalized
    return fallback_regions


def sync_server_prices(provider: str, regions: list[tuple[str, str]], templates, deactivate_missing_regions: bool = True):
    regions = _normalize_server_price_regions(provider, regions)
    region_codes = {code for code, _ in regions}
    bundle_codes = {template[0] for template in templates}
    if deactivate_missing_regions:
        ServerPrice.objects.filter(provider=provider).exclude(region_code__in=region_codes).update(is_active=False)
    ServerPrice.objects.filter(provider=provider, region_code__in=region_codes).exclude(bundle_code__in=bundle_codes).update(is_active=False)
    for region_code, region_name in regions:
        for index, template in enumerate(templates, start=1):
            bundle_code, server_name, cpu, memory, storage, bandwidth, price = template
            ServerPrice.objects.update_or_create(
                provider=provider,
                region_code=region_code,
                bundle_code=bundle_code,
                defaults={
                    'region_name': region_name,
                    'server_name': server_name,
                    'server_description': f'{cpu} / {memory} / {storage} / {bandwidth}',
                    'cpu': cpu,
                    'memory': memory,
                    'storage': storage,
                    'bandwidth': bandwidth,
                    'price': price,
                    'currency': 'USDT',
                    'is_active': True,
                    'sort_order': 100 - index,
                },
            )



def _sync_provider_plans(provider: str, regions: list[tuple[str, str]], templates, deactivate_missing_regions: bool = True):
    region_codes = {code for code, _ in regions}
    active_plan_names = {template[1] if provider == 'aws_lightsail' else template[0] for template in templates}
    if deactivate_missing_regions:
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
def ensure_cloud_server_pricing():
    aws_regions = _normalize_server_price_regions('aws_lightsail', _fetch_aws_regions())
    aliyun_regions = _normalize_server_price_regions('aliyun_simple', _fetch_aliyun_regions())
    if aws_regions:
        aws_templates = _fetch_aws_bundle_templates()
        if aws_templates:
            sync_server_prices('aws_lightsail', aws_regions, aws_templates)
        elif not ServerPrice.objects.filter(provider='aws_lightsail').exists():
            sync_server_prices('aws_lightsail', aws_regions, DEFAULT_AWS_PRICING_TEMPLATES)
    elif not ServerPrice.objects.filter(provider='aws_lightsail').exists():
        sync_server_prices('aws_lightsail', [('ap-southeast-1', '新加坡')], DEFAULT_AWS_PRICING_TEMPLATES)
    if aliyun_regions:
        for region_code, region_name in aliyun_regions:
            region_templates = _fetch_aliyun_plan_templates(region_code)
            region_templates = _merge_templates(region_templates, DEFAULT_ALIYUN_PLAN_TEMPLATES)
            pricing_templates = [
                (f'{region_code}-{idx}', plan_name, cpu, memory, storage, bandwidth, price)
                for idx, (plan_name, cpu, memory, storage, bandwidth, price) in enumerate(region_templates, start=1)
            ]
            sync_server_prices('aliyun_simple', [(region_code, region_name)], pricing_templates, deactivate_missing_regions=False)
    elif not ServerPrice.objects.filter(provider='aliyun_simple').exists():
        sync_server_prices('aliyun_simple', [('cn-hongkong', '香港')], DEFAULT_ALIYUN_PRICING_TEMPLATES)


@sync_to_async
def ensure_cloud_server_plans():
    ensure_cloud_server_pricing.__wrapped__()
    if not CloudServerPlan.objects.exists():
        _sync_provider_plans('aws_lightsail', [('ap-southeast-1', '新加坡')], _fetch_aws_bundle_templates())


def _sort_region_pairs(regions: list[tuple[str, str]]) -> list[tuple[str, str]]:
    preferred = ['新加坡', '香港']
    preferred_index = {name: idx for idx, name in enumerate(preferred)}
    return sorted(regions, key=lambda item: (preferred_index.get(item[1], 999), item[1], item[0]))


CUSTOM_CACHE_TTL = 600
CUSTOM_REGIONS_CACHE_KEY = 'custom:regions:v1'
CUSTOM_PLANS_CACHE_PREFIX = 'custom:plans:v1:'


async def _cache_get_json(key: str):
    r = await get_redis()
    if r is None:
        return None
    try:
        raw = await r.get(key)
        return json.loads(raw) if raw else None
    except Exception:
        return None


async def _cache_set_json(key: str, value, ttl: int = CUSTOM_CACHE_TTL):
    r = await get_redis()
    if r is None:
        return
    try:
        await r.set(key, json.dumps(value, ensure_ascii=False), ex=ttl)
    except Exception:
        pass


@sync_to_async
def _list_custom_regions_db():
    plans = list(
        CloudServerPlan.objects.filter(is_active=True)
        .values_list('provider', 'region_code', 'region_name')
        .distinct()
    )
    aws_regions = {code: name for provider, code, name in plans if provider == 'aws_lightsail'}
    aliyun_hk = [(code, name) for provider, code, name in plans if provider == 'aliyun_simple']
    regions = list(aws_regions.items())
    if aliyun_hk:
        regions.extend(aliyun_hk[:1])
    return _sort_region_pairs(regions)


async def list_custom_regions(*args, **kwargs):
    from cloud.services import list_custom_regions as impl
    return await impl(*args, **kwargs)


@sync_to_async
def _list_region_plans_db(region_code: str):
    provider = 'aliyun_simple' if region_code == 'cn-hongkong' else 'aws_lightsail'
    queryset = CloudServerPlan.objects.filter(region_code=region_code, provider=provider, is_active=True)
    queryset = queryset.exclude(provider='aws_lightsail', plan_name__iexact='Nano')
    return list(queryset.order_by('provider', '-sort_order', 'id'))


async def list_region_plans(*args, **kwargs):
    from cloud.services import list_region_plans as impl
    return await impl(*args, **kwargs)


async def refresh_custom_plan_cache(*args, **kwargs):
    from cloud.services import refresh_custom_plan_cache as impl
    return await impl(*args, **kwargs)


@sync_to_async
def get_cloud_plan(*args, **kwargs):
    from cloud.services import get_cloud_plan as impl
    return impl.__wrapped__(*args, **kwargs)


def _apply_cloud_discount(plan_price: Decimal, discount_rate) -> Decimal:
    rate = Decimal(str(discount_rate or 100))
    if rate <= 0:
        rate = Decimal('100')
    return (Decimal(plan_price) * rate / Decimal('100')).quantize(Decimal('0.01'))


@sync_to_async
def create_cloud_server_order(*args, **kwargs):
    from cloud.services import create_cloud_server_order as impl
    return impl.__wrapped__(*args, **kwargs)


@sync_to_async
def buy_cloud_server_with_balance(*args, **kwargs):
    from cloud.services import buy_cloud_server_with_balance as impl
    return impl.__wrapped__(*args, **kwargs)


@sync_to_async
def pay_cloud_server_order_with_balance(*args, **kwargs):
    from cloud.services import pay_cloud_server_order_with_balance as impl
    return impl.__wrapped__(*args, **kwargs)


@sync_to_async
def set_cloud_server_port(*args, **kwargs):
    from cloud.services import set_cloud_server_port as impl
    return impl.__wrapped__(*args, **kwargs)
