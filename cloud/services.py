"""cloud 域服务主入口：当前真实云业务实现已收口到这里，旧 `biz.services.*` 仅保留兼容壳。"""

import json
import logging
import os
import re
import secrets
import string
import time
from decimal import Decimal, ROUND_CEILING
from types import SimpleNamespace

from asgiref.sync import async_to_sync, sync_to_async
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from bot.models import TelegramUser
from cloud.models import CloudAsset, CloudIpLog, CloudServerOrder, CloudServerPlan, Server, ServerPrice
from cloud.bootstrap import install_bbr, install_mtproxy
from cloud.ports import get_mtproxy_public_ports
from core.cache import get_redis
from core.cloud_accounts import choose_cloud_account_for_order, cloud_account_label, get_active_cloud_account, get_cloud_account_from_label
from core.order_numbers import unique_timestamp_order_no
from orders.ledger import record_balance_ledger
from orders.models import BalanceLedger, CartItem
from orders.services import _generate_unique_pay_amount, usdt_to_trx

logger = logging.getLogger(__name__)
CUSTOM_CACHE_TTL = 600


def _renew_aliyun_instance(order: CloudServerOrder, days: int = 31):
    if order.provider != 'aliyun_simple':
        return True, ''
    if not str(order.instance_id or '').strip():
        return False, '阿里云实例ID缺失，无法执行真实续费'
    account = getattr(order, 'cloud_account', None) or get_active_cloud_account('aliyun', order.region_code)
    if not account:
        return False, '未配置阿里云账号，无法执行真实续费'
    try:
        from alibabacloud_swas_open20200601 import models as swas_models
        from cloud.aliyun_simple import _build_client, _region_endpoint, _runtime_options

        region_code = (order.region_code or account.region_hint or 'cn-hongkong').strip() or 'cn-hongkong'
        client = _build_client(_region_endpoint(region_code), account=account)
        if not client:
            return False, '无法创建阿里云客户端'
        period = max(1, int(round((int(days or 31)) / 31)))
        request = swas_models.RenewInstanceRequest(
            instance_id=order.instance_id,
            region_id=region_code,
            period=period,
            client_token=f'renew-{order.id}-{int(timezone.now().timestamp())}',
        )
        response = client.renew_instance_with_options(request, _runtime_options())
        logger.info('阿里云真实续费成功 order=%s instance=%s region=%s period=%s response=%s', order.id, order.instance_id, region_code, period, response.body.to_map() if getattr(response, 'body', None) else {})
        return True, f'阿里云实例已真实续费 {period} 个月。'
    except Exception as exc:
        logger.warning('阿里云真实续费失败 order=%s instance=%s err=%s', order.id, order.instance_id, exc)
        return False, f'阿里云真实续费失败: {exc}'


def _aws_lightsail_client_for_order(order: CloudServerOrder):
    import boto3

    account = getattr(order, 'cloud_account', None) or get_active_cloud_account('aws', order.region_code)
    access_key = (getattr(account, 'access_key_plain', '') or '').strip() if account else ''
    secret_key = (getattr(account, 'secret_key_plain', '') or '').strip() if account else ''
    if not access_key or not secret_key:
        access_key = os.getenv('AWS_ACCESS_KEY_ID', '')
        secret_key = os.getenv('AWS_SECRET_ACCESS_KEY', '')
    return boto3.client(
        'lightsail',
        region_name=order.region_code or getattr(account, 'region_hint', None) or 'ap-southeast-1',
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _ensure_aws_instance_running(order: CloudServerOrder) -> tuple[bool, str]:
    if order.provider != 'aws_lightsail' or not order.server_name:
        return True, ''
    try:
        client = _aws_lightsail_client_for_order(order)
        instance = client.get_instance(instanceName=order.server_name).get('instance') or {}
        state = ((instance.get('state') or {}).get('name') or '').lower()
        if state == 'running':
            return True, 'AWS 实例续费后检查：运行中。'
        if state in {'stopped', 'stopping'}:
            client.start_instance(instanceName=order.server_name)
            logger.info('CLOUD_RENEW_START_INSTANCE order=%s server_name=%s previous_state=%s', order.order_no, order.server_name, state)
            return True, f'AWS 实例续费后检查：原状态 {state or "未知"}，已发起启动。'
        return True, f'AWS 实例续费后检查：当前状态 {state or "未知"}。'
    except Exception as exc:
        logger.warning('CLOUD_RENEW_START_INSTANCE_FAILED order=%s server_name=%s error=%s', order.order_no, order.server_name, exc)
        return False, f'AWS 实例启动检查失败: {exc}'


def _probe_mtproxy_ports(ip: str, username: str, password: str, main_port: int) -> tuple[bool, str]:
    if not ip or not password:
        return False, '缺少 SSH 参数，无法检查 MTProxy。'
    try:
        import paramiko
    except ImportError:
        return False, '缺少 paramiko，无法检查 MTProxy。'
    ports = [str(port) for port in get_mtproxy_public_ports(main_port or 9528)]
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(
            hostname=ip,
            port=22,
            username=(username or 'root').strip() or 'root',
            password=password,
            timeout=30,
            banner_timeout=30,
            auth_timeout=30,
            look_for_keys=False,
            allow_agent=False,
        )
        ports_text = ' '.join(ports)
        script = f"""bash -s <<'EOF'
PORTS='{ports_text}'
LISTENING="$(ss -lntup 2>/dev/null || netstat -lntup 2>/dev/null || true)"
missing=''
for port in $PORTS; do
  if ! printf '%s\n' "$LISTENING" | grep -E "[:.]${{port}}\\b" >/dev/null 2>&1; then
    missing="$missing $port"
  fi
done
if [ -n "$missing" ]; then
  echo "MISSING=$missing"
  exit 2
fi
echo 'OK=1'
EOF"""
        stdin, stdout, stderr = client.exec_command(script, timeout=120)
        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode('utf-8', errors='ignore').strip()
        if exit_code == 0:
            return True, f'MTProxy 端口检查正常: {", ".join(ports)}。'
        return False, f'MTProxy 端口检查异常: {output or stderr.read().decode("utf-8", errors="ignore")[:200]}'
    except Exception as exc:
        return False, f'MTProxy 端口检查异常: {exc}'
    finally:
        client.close()


def _ensure_mtproxy_after_renewal(order: CloudServerOrder) -> tuple[bool, str]:
    if not order.public_ip or not order.login_password:
        return True, '缺少登录信息，跳过 MTProxy 运行检查。'
    probe_notes = []
    ok = False
    note = ''
    for attempt in range(1, 7):
        ok, note = _probe_mtproxy_ports(order.public_ip, order.login_user or 'root', order.login_password, order.mtproxy_port or 9528)
        probe_notes.append(f'第 {attempt} 次检查: {note}')
        if ok:
            return True, '\n'.join(probe_notes)
        time.sleep(10)
    logger.warning('CLOUD_RENEW_MTPROXY_REINSTALL order=%s ip=%s reason=%s', order.order_no, order.public_ip, note)
    install_ok, install_note = async_to_sync(install_mtproxy)(
        order.public_ip,
        order.login_user or 'root',
        order.login_password,
        order.mtproxy_port or 9528,
        order.mtproxy_secret or '',
        order.mtproxy_secret or '',
    )
    return install_ok, '\n'.join(filter(None, [note, '续费后 MTProxy 异常，已重新安装。' if install_ok else '续费后 MTProxy 异常，重新安装失败。', install_note]))

CUSTOM_REGIONS_CACHE_KEY = 'custom:regions:v1'
CUSTOM_PLANS_CACHE_PREFIX = 'custom:plans:v1:'

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


def _build_aliyun_client(endpoint: str | None = None):
    account = get_active_cloud_account('aliyun')
    key = account.access_key_plain if account else os.getenv('ALIBABA_CLOUD_ACCESS_KEY_ID', '')
    secret = account.secret_key_plain if account else os.getenv('ALIBABA_CLOUD_ACCESS_KEY_SECRET', '')
    if not key or not secret:
        return None
    from alibabacloud_tea_openapi import models as open_api_models
    from alibabacloud_swas_open20200601.client import Client

    endpoint = (endpoint or 'swas.cn-hongkong.aliyuncs.com').strip() or 'swas.cn-hongkong.aliyuncs.com'
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


def _fetch_aliyun_plan_templates(region_code: str):
    client = _build_aliyun_client(f'swas.{region_code}.aliyuncs.com')
    if not client:
        return [
            (f'{region_code}-{idx}', plan_name, cpu, memory, storage, bandwidth, price)
            for idx, (plan_name, cpu, memory, storage, bandwidth, price) in enumerate(DEFAULT_ALIYUN_PLAN_TEMPLATES, start=1)
        ]
    try:
        from alibabacloud_swas_open20200601 import models as swas_models

        response = client.list_plans(swas_models.ListPlansRequest(region_id=region_code))
        plans = response.body.to_map().get('Plans', [])
        linux_plans = [
            item for item in plans
            if 'Linux' in str(item.get('SupportPlatform', ''))
            and str(item.get('PlanType') or '').upper() == 'NORMAL'
        ]
        linux_plans.sort(key=lambda item: (_parse_aliyun_price(item.get('OriginPrice')), item.get('Core') or 0, item.get('Memory') or 0, str(item.get('PlanId') or '')))
        templates = []
        for item in linux_plans:
            plan_id = str(item.get('PlanId') or '').strip()
            if not plan_id:
                continue
            cpu = f"{item.get('Core') or '-'}核"
            memory = f"{item.get('Memory') or '-'}GB"
            storage = f"{item.get('DiskSize') or '-'}GB {item.get('DiskType') or 'SSD'}"
            bandwidth = f"{item.get('Bandwidth') or '-'}Mbps"
            plan_name = f"{cpu} {memory} {storage}"
            base_price = _parse_aliyun_price(item.get('OriginPrice')).quantize(Decimal('0.01'))
            templates.append((plan_id, plan_name, cpu, memory, storage, bandwidth, base_price))
        if templates:
            return templates
    except Exception:
        pass
    return [
        (f'{region_code}-{idx}', plan_name, cpu, memory, storage, bandwidth, price)
        for idx, (plan_name, cpu, memory, storage, bandwidth, price) in enumerate(DEFAULT_ALIYUN_PLAN_TEMPLATES, start=1)
    ]


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
    key = ''
    secret = ''
    if account:
        ak = (account.access_key_plain or '').strip()
        sk = (account.secret_key_plain or '').strip()
        if ak and sk and len(ak) >= 16 and len(sk) >= 36:
            key, secret = ak, sk
    if not key or not secret:
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
            base_price = Decimal(str(item.get('price') or '0')).quantize(Decimal('0.001'))
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
    key = ''
    secret = ''
    if account:
        ak = (account.access_key_plain or '').strip()
        sk = (account.secret_key_plain or '').strip()
        if ak and sk and len(ak) >= 16 and len(sk) >= 36:
            key, secret = ak, sk
    if not key or not secret:
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


def _build_server_price_config_id(provider: str, region_code: str, sequence: int) -> str:
    provider_key = str(provider or '').strip().replace('_', '-')
    region_key = str(region_code or '').strip().replace('_', '-')
    return f'{provider_key}-{region_key}-{int(sequence)}'[:64]


def sync_server_prices(provider: str, regions: list[tuple[str, str]], templates, deactivate_missing_regions: bool = True):
    regions = _normalize_server_price_regions(provider, regions)
    region_codes = {code for code, _ in regions}
    bundle_codes = {template[0] for template in templates}
    if deactivate_missing_regions:
        ServerPrice.objects.filter(provider=provider).exclude(region_code__in=region_codes).update(is_active=False)
    ServerPrice.objects.filter(provider=provider, region_code__in=region_codes).exclude(bundle_code__in=bundle_codes).update(is_active=False)
    for region_code, region_name in regions:
        ordered_templates = sorted(
            templates,
            key=lambda item: (
                Decimal(str(item[6] or '0')),
                str(item[1] or ''),
                str(item[0] or ''),
            ),
        )
        for index, template in enumerate(ordered_templates, start=1):
            bundle_code, server_name, cpu, memory, storage, bandwidth, price = template
            ServerPrice.objects.update_or_create(
                provider=provider,
                region_code=region_code,
                bundle_code=bundle_code,
                defaults={
                    'region_name': region_name,
                    'config_id': _build_server_price_config_id(provider, region_code, index),
                    'server_name': server_name,
                    'server_description': f'{cpu} / {memory} / {storage} / {bandwidth}',
                    'cpu': cpu,
                    'memory': memory,
                    'storage': storage,
                    'bandwidth': bandwidth,
                    'cost_price': price,
                    'price': price,
                    'currency': 'USDT',
                    'is_active': True,
                    'sort_order': 100 - index,
                },
            )


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
            pricing_templates = _fetch_aliyun_plan_templates(region_code)
            sync_server_prices('aliyun_simple', [(region_code, region_name)], pricing_templates, deactivate_missing_regions=False)
    elif not ServerPrice.objects.filter(provider='aliyun_simple').exists():
        sync_server_prices('aliyun_simple', [('cn-hongkong', '香港')], DEFAULT_ALIYUN_PRICING_TEMPLATES)


def _format_amount_tag(amount: Decimal) -> str:
    normalized = amount.normalize() if isinstance(amount, Decimal) else Decimal(str(amount)).normalize()
    text = format(normalized, 'f')
    return text.replace('.', '_')


def build_cloud_server_name(tg_user_id: int | None, amount: Decimal, unique_tag: str | None = None) -> str:
    timestamp = timezone.now().strftime('%Y%m%d')
    user_tag = str(tg_user_id or 0)
    tag = f'-{unique_tag}' if unique_tag else ''
    return f"{timestamp}-{user_tag}-{_format_amount_tag(amount)}{tag}"[:255]


def ensure_unique_cloud_server_name(base_name: str) -> str:
    candidate = (base_name or '')[:255]
    index = 0
    while Server.objects.filter(instance_id=candidate).exists() or CloudServerOrder.objects.filter(server_name=candidate).exists():
        index += 1
        suffix = f'-{index}'
        candidate = f'{base_name[: max(0, 255 - len(suffix))]}{suffix}'
    return candidate

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
    return sorted(regions, key=lambda item: (item[0] != 'cn-hongkong', item[1], item[0]))


async def list_custom_regions():
    cached = await _cache_get_json(CUSTOM_REGIONS_CACHE_KEY)
    if cached:
        logger.info('定制缓存命中: 地区列表 %s 项', len(cached))
        return [tuple(item) for item in cached]
    regions = await _list_custom_regions_db()
    await _cache_set_json(CUSTOM_REGIONS_CACHE_KEY, regions)
    logger.info('定制缓存回源: 地区列表 %s 项', len(regions))
    return regions


@sync_to_async
def _list_region_plans_db(region_code: str):
    provider = 'aliyun_simple' if region_code == 'cn-hongkong' else 'aws_lightsail'
    queryset = CloudServerPlan.objects.filter(region_code=region_code, provider=provider, is_active=True)
    return list(queryset.order_by('provider', '-sort_order', 'id'))


async def list_region_plans(region_code: str):
    cached = await _cache_get_json(CUSTOM_PLANS_CACHE_PREFIX + region_code)
    if cached:
        ids = [int(item['id']) for item in cached]
        plans = await sync_to_async(lambda: list(CloudServerPlan.objects.filter(id__in=ids)))()
        plan_map = {plan.id: plan for plan in plans}
        ordered = [plan_map[plan_id] for plan_id in ids if plan_id in plan_map]
        if ordered:
            logger.info('定制缓存命中: %s 套餐 %s 个', region_code, len(ordered))
            return ordered
    plans = await _list_region_plans_db(region_code)
    await _cache_set_json(CUSTOM_PLANS_CACHE_PREFIX + region_code, [{'id': plan.id} for plan in plans])
    logger.info('定制缓存回源: %s 套餐 %s 个', region_code, len(plans))
    return plans


async def refresh_custom_plan_cache():
    regions = await _list_custom_regions_db()
    await _cache_set_json(CUSTOM_REGIONS_CACHE_KEY, regions)
    total_plans = 0
    for region_code, _ in regions:
        plans = await _list_region_plans_db(region_code)
        total_plans += len(plans)
        await _cache_set_json(CUSTOM_PLANS_CACHE_PREFIX + region_code, [{'id': plan.id} for plan in plans])
    logger.info('定制缓存刷新完成: 地区 %s 个, 套餐 %s 个', len(regions), total_plans)
    return len(regions)


@sync_to_async
def get_cloud_plan(plan_id: int):
    return CloudServerPlan.objects.filter(id=plan_id, is_active=True).first()


@sync_to_async
def set_cloud_server_port(order_id: int, user_id: int, port: int):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    order.mtproxy_port = port
    order.provision_note = f'用户已确认端口 {port}，开始创建服务器。'
    order.save(update_fields=['mtproxy_port', 'provision_note', 'updated_at'])
    logger.info('云服务器端口确认: order=%s user=%s port=%s', order.order_no, user_id, port)
    return order


@sync_to_async
def prepare_cloud_server_order_instances(order_id: int, user_id: int, port: int):
    with transaction.atomic():
        order = CloudServerOrder.objects.select_for_update().filter(id=order_id, user_id=user_id).first()
        if not order:
            return []
        if order.status not in ['paid']:
            logger.warning('云服务器端口确认被拒绝: order=%s user=%s port=%s status=%s', order.order_no, user_id, port, order.status)
            return []
        quantity = max(1, int(order.quantity or 1))
        order.mtproxy_port = port
        if quantity <= 1:
            order.provision_note = f'用户已确认端口 {port}，开始创建服务器。'
            order.save(update_fields=['mtproxy_port', 'provision_note', 'updated_at'])
            logger.info('云服务器端口确认: order=%s user=%s port=%s quantity=1', order.order_no, user_id, port)
            return [order]

        per_total = (Decimal(order.total_amount or 0) / Decimal(quantity)).quantize(Decimal('0.000001'))
        per_pay = (Decimal(order.pay_amount or 0) / Decimal(quantity)).quantize(Decimal('0.000000001')) if order.pay_amount is not None else None
        original_order_no = order.order_no
        created_orders = [order]
        order.quantity = 1
        order.total_amount = per_total
        order.pay_amount = per_pay
        order.provision_note = f'批量订单 {original_order_no} 已拆分：第 1/{quantity} 台，端口 {port}，开始创建服务器。'
        order.save(update_fields=['quantity', 'total_amount', 'pay_amount', 'mtproxy_port', 'provision_note', 'updated_at'])
        for index in range(2, quantity + 1):
            clone = CloudServerOrder.objects.create(
                order_no=_generate_cloud_order_no(),
                user=order.user,
                plan=order.plan,
                provider=order.provider,
                cloud_account=order.cloud_account,
                account_label=order.account_label,
                region_code=order.region_code,
                region_name=order.region_name,
                plan_name=order.plan_name,
                provider_resource_id=order.provider_resource_id,
                quantity=1,
                currency=order.currency,
                total_amount=per_total,
                pay_amount=per_pay,
                pay_method=order.pay_method,
                status=order.status,
                payer_address=order.payer_address,
                receive_address=order.receive_address,
                image_name=order.image_name,
                lifecycle_days=order.lifecycle_days,
                paid_at=order.paid_at,
                expired_at=order.expired_at,
                mtproxy_port=port,
                last_user_id=order.last_user_id,
                provision_note=f'批量订单 {original_order_no} 已拆分：第 {index}/{quantity} 台，端口 {port}，开始创建服务器。',
            )
            created_orders.append(clone)
        logger.info('云服务器批量订单拆分完成: original_order=%s user=%s quantity=%s port=%s child_orders=%s', original_order_no, user_id, quantity, port, [item.order_no for item in created_orders])
        return created_orders


def _generate_cloud_order_no(prefix: str = 'SRV', tag: str | None = None) -> str:
    return unique_timestamp_order_no(prefix, lambda value: CloudServerOrder.objects.filter(order_no=value).exists(), tag=tag)


def _apply_cloud_discount(plan_price: Decimal, discount_rate) -> Decimal:
    rate = Decimal(str(discount_rate or 100))
    if rate <= 0:
        rate = Decimal('100')
    return (Decimal(plan_price) * rate / Decimal('100')).quantize(Decimal('0.01'))


@sync_to_async
def create_cloud_server_order(user_id: int, plan_id: int, currency: str = 'USDT', quantity: int = 1):
    plan = CloudServerPlan.objects.get(id=plan_id, is_active=True)
    user = TelegramUser.objects.get(id=user_id)
    quantity = max(1, int(quantity or 1))
    original_total_usdt = (Decimal(plan.price) * quantity).quantize(Decimal('0.01'))
    discounted_total_usdt = (_apply_cloud_discount(Decimal(plan.price), user.cloud_discount_rate) * quantity).quantize(Decimal('0.01'))
    payable_base = async_to_sync(usdt_to_trx)(discounted_total_usdt) if currency == 'TRX' else discounted_total_usdt
    pay_amount = _generate_unique_pay_amount(payable_base, currency)
    expired_at = timezone.now() + timezone.timedelta(minutes=5)
    account = choose_cloud_account_for_order(plan.provider, plan.region_code)
    order = CloudServerOrder.objects.create(
        order_no=_generate_cloud_order_no(),
        user_id=user_id,
        plan=plan,
        provider=plan.provider,
        cloud_account=account,
        account_label=cloud_account_label(account) or plan.provider,
        region_code=plan.region_code,
        region_name=plan.region_name,
        plan_name=plan.plan_name,
        provider_resource_id=(plan.provider_plan_id or None),
        quantity=quantity,
        currency=currency,
        total_amount=original_total_usdt,
        pay_amount=pay_amount,
        pay_method='address',
        status='pending',
        mtproxy_port=9528,
        expired_at=expired_at,
    )
    CartItem.objects.filter(user_id=user_id, item_type='cloud_plan', cloud_plan_id=plan_id).delete()
    logger.info('云服务器订单创建: order=%s user=%s region=%s plan=%s qty=%s pay=address amount=%s', order.order_no, user_id, plan.region_code, plan.plan_name, quantity, pay_amount)
    return order


@sync_to_async
def buy_cloud_server_with_balance(user_id: int, plan_id: int, currency: str = 'USDT', quantity: int = 1):
    plan = CloudServerPlan.objects.get(id=plan_id, is_active=True)
    quantity = max(1, int(quantity or 1))
    with transaction.atomic():
        user = TelegramUser.objects.select_for_update().get(id=user_id)
        original_total_usdt = (Decimal(plan.price) * quantity).quantize(Decimal('0.01'))
        discounted_total_usdt = (_apply_cloud_discount(Decimal(plan.price), user.cloud_discount_rate) * quantity).quantize(Decimal('0.01'))
        total = async_to_sync(usdt_to_trx)(discounted_total_usdt) if currency == 'TRX' else discounted_total_usdt
        balance_field = 'balance_trx' if currency == 'TRX' else 'balance'
        current_balance = Decimal(str(getattr(user, balance_field, 0) or 0))
        if current_balance < total:
            return None, f'{currency} 余额不足'
        old_balance = current_balance
        setattr(user, balance_field, current_balance - total)
        user.save(update_fields=[balance_field, 'updated_at'])
        account = choose_cloud_account_for_order(plan.provider, plan.region_code)
        order = CloudServerOrder.objects.create(
            order_no=_generate_cloud_order_no(),
            user_id=user_id,
            plan=plan,
            provider=plan.provider,
            cloud_account=account,
            account_label=cloud_account_label(account) or plan.provider,
            region_code=plan.region_code,
            region_name=plan.region_name,
            plan_name=plan.plan_name,
            provider_resource_id=(plan.provider_plan_id or None),
            quantity=quantity,
            currency=currency,
            total_amount=original_total_usdt,
            pay_amount=total,
            pay_method='balance',
            status='paid',
            mtproxy_port=9528,
            paid_at=timezone.now(),
        )
        record_balance_ledger(
            user,
            ledger_type='cloud_order_balance_pay',
            currency=currency,
            old_balance=old_balance,
            new_balance=getattr(user, balance_field),
            related_type='cloud_order',
            related_id=order.id,
            description=f'云服务器订单 #{order.order_no} 余额支付',
        )
    CartItem.objects.filter(user_id=user_id, item_type='cloud_plan', cloud_plan_id=plan_id).delete()
    logger.info('云服务器钱包下单: order=%s user=%s region=%s plan=%s qty=%s currency=%s amount=%s', order.order_no, user_id, plan.region_code, plan.plan_name, quantity, currency, total)
    return order, None


@sync_to_async
def pay_cloud_server_order_with_balance(order_id: int, user_id: int, currency: str = 'USDT'):
    order = CloudServerOrder.objects.select_related('plan').filter(id=order_id, user_id=user_id, status='pending').first()
    if not order:
        return None, '订单不存在或状态不可支付'
    payable_usdt = Decimal(str(order.pay_amount or order.total_amount or 0))
    total = async_to_sync(usdt_to_trx)(payable_usdt) if currency == 'TRX' else payable_usdt
    with transaction.atomic():
        user = TelegramUser.objects.select_for_update().get(id=user_id)
        balance_field = 'balance_trx' if currency == 'TRX' else 'balance'
        current_balance = Decimal(str(getattr(user, balance_field, 0) or 0))
        if current_balance < total:
            unit = 'TRX' if currency == 'TRX' else 'USDT'
            return None, f'钱包余额不足，请先充值 {unit}'
        old_balance = current_balance
        setattr(user, balance_field, current_balance - total)
        user.save(update_fields=[balance_field, 'updated_at'])
        order.currency = currency
        order.pay_amount = total
        order.pay_method = 'balance'
        order.status = 'paid'
        order.paid_at = timezone.now()
        order.save(update_fields=['currency', 'pay_amount', 'pay_method', 'status', 'paid_at', 'updated_at'])
        record_balance_ledger(
            user,
            ledger_type='cloud_order_balance_pay',
            currency=currency,
            old_balance=old_balance,
            new_balance=getattr(user, balance_field),
            related_type='cloud_order',
            related_id=order.id,
            description=f'云服务器订单 #{order.order_no} 余额补付',
        )
    if order.plan_id:
        CartItem.objects.filter(user_id=user_id, item_type='cloud_plan', cloud_plan_id=order.plan_id).delete()
    logger.info('云服务器钱包补付: order=%s user=%s currency=%s amount=%s', order.order_no, user_id, currency, total)
    return order, None


_ACTIVE_ORDER_STATUSES = {'completed', 'expiring', 'suspended', 'renew_pending', 'paid', 'provisioning'}
_VISIBLE_USER_SERVER_STATUSES = {'completed', 'expiring', 'suspended', 'renew_pending', 'provisioning', 'paid', 'failed'}
_INACTIVE_ASSET_STATUSES = {'deleted', 'deleting', 'terminated', 'terminating', 'expired'}


def _first_nonblank(*values) -> str:
    for value in values:
        text = str(value or '').strip()
        if text:
            return text
    return ''


def _hydrate_order_from_proxy_asset(order: CloudServerOrder | None, asset: CloudAsset | None = None, server: Server | None = None):
    if not order:
        return order
    if asset is None:
        asset = (
            CloudAsset.objects.filter(order=order, kind=CloudAsset.KIND_SERVER)
            .exclude(status__in=_INACTIVE_ASSET_STATUSES)
            .order_by('-updated_at', '-id')
            .first()
        )
    if server is None:
        server = (
            Server.objects.filter(order=order)
            .exclude(status__in=_INACTIVE_ASSET_STATUSES)
            .order_by('-updated_at', '-id')
            .first()
        )
    public_ip = _first_nonblank(getattr(asset, 'public_ip', None), getattr(server, 'public_ip', None), order.public_ip)
    previous_ip = _first_nonblank(getattr(asset, 'previous_public_ip', None), getattr(server, 'previous_public_ip', None), order.previous_public_ip)
    expiry_candidates = [
        item for item in (
            getattr(asset, 'actual_expires_at', None),
            getattr(server, 'expires_at', None),
            order.service_expires_at,
        ) if item
    ]
    expires_at = max(expiry_candidates) if expiry_candidates else None
    if public_ip:
        order.public_ip = public_ip
    if previous_ip:
        order.previous_public_ip = previous_ip
    if expires_at:
        order.service_expires_at = expires_at
    if asset:
        order.mtproxy_link = getattr(asset, 'mtproxy_link', None) or order.mtproxy_link
        order.proxy_links = getattr(asset, 'proxy_links', None) or order.proxy_links
        order.mtproxy_secret = getattr(asset, 'mtproxy_secret', None) or order.mtproxy_secret
        order.mtproxy_host = getattr(asset, 'mtproxy_host', None) or order.mtproxy_host
        order.mtproxy_port = getattr(asset, 'mtproxy_port', None) or order.mtproxy_port
        order.login_password = getattr(asset, 'login_password', None) or order.login_password
        order.instance_id = getattr(asset, 'instance_id', None) or order.instance_id
        if not order.cloud_account_id:
            order.cloud_account = getattr(asset, 'cloud_account', None) or get_cloud_account_from_label(getattr(asset, 'account_label', ''), order.provider)
        order.account_label = order.account_label or getattr(asset, 'account_label', None) or cloud_account_label(getattr(order, 'cloud_account', None))
    if server:
        order.login_password = getattr(server, 'login_password', None) or order.login_password
        order.instance_id = getattr(server, 'instance_id', None) or order.instance_id
        if not order.cloud_account_id:
            order.cloud_account = get_cloud_account_from_label(getattr(server, 'account_label', ''), order.provider)
        order.account_label = order.account_label or getattr(server, 'account_label', None) or cloud_account_label(getattr(order, 'cloud_account', None))
    return order


def _proxy_asset_view(asset: CloudAsset):
    order = asset.order
    return SimpleNamespace(
        id=asset.id,
        _proxy_item_kind='asset',
        asset_id=asset.id,
        order_id=asset.order_id,
        order_user_id=getattr(order, 'user_id', None),
        order_no=asset.asset_name or getattr(order, 'server_name', None) or getattr(order, 'order_no', None) or f'ASSET-{asset.id}',
        public_ip=asset.public_ip,
        previous_public_ip=asset.previous_public_ip,
        service_expires_at=asset.actual_expires_at,
        region_name=asset.region_name or getattr(order, 'region_name', None) or '-',
        region_code=asset.region_code or getattr(order, 'region_code', None) or '',
        plan_name=asset.asset_name or getattr(order, 'plan_name', None) or '人工代理',
        quantity=1,
        status=asset.status,
        provider=asset.provider or getattr(order, 'provider', None),
        pay_method='manual',
        pay_amount=asset.price if asset.price is not None else getattr(order, 'pay_amount', None),
        total_amount=asset.price if asset.price is not None else getattr(order, 'total_amount', None),
        currency=asset.currency or (getattr(order, 'currency', None) if order else 'USDT'),
        mtproxy_port=asset.mtproxy_port,
        mtproxy_link=asset.mtproxy_link,
        proxy_links=asset.proxy_links,
        mtproxy_secret=asset.mtproxy_secret,
        mtproxy_host=asset.mtproxy_host,
        login_user=asset.login_user,
        login_password=asset.login_password,
        instance_id=asset.instance_id,
        ip_recycle_at=getattr(order, 'ip_recycle_at', None),
        auto_renew_enabled=bool(getattr(order, 'auto_renew_enabled', False)),
        cloud_reminder_enabled=bool(getattr(order, 'cloud_reminder_enabled', True)),
        created_at=asset.created_at,
        note=asset.note,
        get_status_display=lambda: asset.get_status_display(),
    )


def _proxy_server_view(server: Server):
    return SimpleNamespace(
        id=server.id,
        _proxy_item_kind='server',
        server_id=server.id,
        order_no=server.server_name or f'SERVER-{server.id}',
        public_ip=server.public_ip,
        previous_public_ip=server.previous_public_ip,
        service_expires_at=server.expires_at,
        region_name=server.region_name or '-',
        region_code=server.region_code or '',
        plan_name=server.server_name or '人工代理',
        quantity=1,
        status=server.status,
        provider=server.provider,
        pay_method='manual',
        pay_amount=None,
        total_amount=None,
        currency='USDT',
        mtproxy_port=None,
        mtproxy_link=None,
        proxy_links=[],
        mtproxy_secret=None,
        mtproxy_host=None,
        login_user=server.login_user,
        login_password=server.login_password,
        instance_id=server.instance_id,
        ip_recycle_at=None,
        auto_renew_enabled=False,
        cloud_reminder_enabled=True,
        created_at=server.created_at,
        note=server.note,
        get_status_display=lambda: server.get_status_display(),
    )


@sync_to_async
def list_user_cloud_servers(user_id: int):
    ip_filter = Q(public_ip__isnull=False) & ~Q(public_ip='')
    assets = (
        CloudAsset.objects.select_related('order')
        .filter(kind=CloudAsset.KIND_SERVER, user_id=user_id)
        .filter(ip_filter)
        .exclude(status__in=_INACTIVE_ASSET_STATUSES)
        .order_by('-sort_order', 'actual_expires_at', '-updated_at', '-id')
    )
    return [_proxy_asset_view(asset) for asset in assets]


@sync_to_async
def list_user_auto_renew_cloud_servers(user_id: int):
    ip_filter = Q(public_ip__isnull=False) & ~Q(public_ip='')
    assets = (
        CloudAsset.objects.select_related('order')
        .filter(kind=CloudAsset.KIND_SERVER, user_id=user_id, order__auto_renew_enabled=True)
        .filter(ip_filter)
        .exclude(status__in=_INACTIVE_ASSET_STATUSES)
        .order_by('-sort_order', 'actual_expires_at', '-updated_at', '-id')
    )
    return [_proxy_asset_view(asset) for asset in assets]


@sync_to_async
def get_user_cloud_server(order_id: int, user_id: int):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    return _hydrate_order_from_proxy_asset(order)


@sync_to_async
def get_user_proxy_asset_detail(item_id: int, user_id: int, kind: str):
    if kind == 'server':
        server = Server.objects.filter(id=item_id, user_id=user_id).first()
        return _proxy_server_view(server) if server else None
    asset = CloudAsset.objects.filter(id=item_id, user_id=user_id).first()
    return _proxy_asset_view(asset) if asset else None


def _extract_asset_mtproxy_fields(note: str) -> tuple[str, str, str]:
    for raw_link in re.findall(r'tg://proxy\?[^"\'\s<>]+', note or ''):
        link = raw_link.rstrip(',.，。')
        if not link:
            continue
        secret = link.split('secret=', 1)[1].split('&', 1)[0].strip() if 'secret=' in link else ''
        host = link.split('server=', 1)[1].split('&', 1)[0].strip() if 'server=' in link else ''
        return link, secret, host
    return '', '', ''


def _generate_asset_login_password(length: int = 18) -> str:
    alphabet = string.ascii_letters + string.digits + '@#%_-'
    return ''.join(secrets.choice(alphabet) for _ in range(length))


async def initialize_proxy_asset(asset_id: int, user_id: int):
    asset = await sync_to_async(
        lambda: CloudAsset.objects.filter(id=asset_id, user_id=user_id).first()
    )()
    if not asset:
        return None, '代理记录不存在'
    public_ip = str(asset.public_ip or '').strip()
    if not public_ip:
        return None, '当前资产缺少公网 IP，无法初始化代理'
    username = str(asset.login_user or '').strip() or ('admin' if asset.provider == 'aws_lightsail' else 'root')
    password = _generate_asset_login_password() if asset.provider == 'aws_lightsail' else (str(asset.login_password or '').strip() or _generate_asset_login_password())
    port = int(asset.mtproxy_port or 9528)
    bbr_ok, bbr_note = await install_bbr(public_ip, username, password, use_key_setup=asset.provider == 'aws_lightsail')
    mtproxy_ok, mtproxy_note = await install_mtproxy(public_ip, username, password, port, asset.mtproxy_secret or '', asset.mtproxy_secret or '')
    note = '\n'.join(part for part in [asset.note, '已执行同步资产代理初始化。', '' if bbr_ok else 'BBR 初始化失败，但继续检查 MTProxy 安装结果。', bbr_note, mtproxy_note] if part)
    if not mtproxy_ok:
        asset.note = note
        await sync_to_async(asset.save)(update_fields=['note', 'updated_at'])
        return asset, 'MTProxy 安装失败，请查看后台日志'
    mtproxy_link, mtproxy_secret, mtproxy_host = _extract_asset_mtproxy_fields(mtproxy_note)
    links = []
    if mtproxy_link:
        links.append({'label': '主链路', 'url': mtproxy_link, 'port': port, 'secret': mtproxy_secret})
    for index, link in enumerate(re.findall(r'tg://proxy\?[^"\'\s<>]+', mtproxy_note or ''), start=1):
        if mtproxy_link and link == mtproxy_link:
            continue
        links.append({'label': f'备用链路 {index}', 'url': link})
    asset.login_user = username
    asset.login_password = password
    asset.mtproxy_port = port
    asset.mtproxy_link = mtproxy_link or asset.mtproxy_link
    asset.mtproxy_secret = mtproxy_secret or asset.mtproxy_secret
    asset.mtproxy_host = mtproxy_host or public_ip
    asset.proxy_links = links or asset.proxy_links
    asset.note = note
    await sync_to_async(asset.save)(update_fields=['login_user', 'login_password', 'mtproxy_port', 'mtproxy_link', 'mtproxy_secret', 'mtproxy_host', 'proxy_links', 'note', 'updated_at'])
    return asset, None


@sync_to_async
def get_cloud_server_by_ip(ip: str):
    normalized_ip = (ip or '').strip()
    if not normalized_ip:
        return None
    ip_q = Q(public_ip=normalized_ip) | Q(previous_public_ip=normalized_ip)
    asset = (
        CloudAsset.objects.filter(ip_q)
        .exclude(status__in=_INACTIVE_ASSET_STATUSES)
        .select_related('order')
        .order_by('-updated_at', '-id')
        .first()
    )
    if asset and asset.order_id and asset.order and asset.order.status not in {'deleted', 'deleting', 'expired', 'cancelled'}:
        return _hydrate_order_from_proxy_asset(asset.order, asset=asset)
    server = (
        Server.objects.filter(ip_q)
        .exclude(status__in=_INACTIVE_ASSET_STATUSES)
        .select_related('order')
        .order_by('-updated_at', '-id')
        .first()
    )
    if server and server.order_id and server.order and server.order.status not in {'deleted', 'deleting', 'expired', 'cancelled'}:
        return _hydrate_order_from_proxy_asset(server.order, server=server)
    order = CloudServerOrder.objects.filter(ip_q, status__in=_ACTIVE_ORDER_STATUSES).order_by('-created_at').first()
    return _hydrate_order_from_proxy_asset(order)


def _can_order_be_renewed(order: CloudServerOrder) -> bool:
    order = _hydrate_order_from_proxy_asset(order)
    has_ip = bool(str(order.public_ip or order.previous_public_ip or '').strip())
    if not has_ip:
        return False
    if order.status in {'completed', 'expiring', 'suspended', 'renew_pending', 'paid', 'provisioning'}:
        return True
    if order.status == 'deleted' and order.ip_recycle_at and order.ip_recycle_at > timezone.now():
        return True
    return False


def _manual_proxy_price(order: CloudServerOrder) -> Decimal | None:
    asset = (
        CloudAsset.objects.filter(order=order, price__isnull=False)
        .exclude(status__in=_INACTIVE_ASSET_STATUSES)
        .order_by('-updated_at', '-id')
        .first()
    )
    if asset and asset.price is not None:
        return Decimal(str(asset.price))
    return None


def _renewal_price(order: CloudServerOrder, user: TelegramUser | None = None) -> Decimal:
    discount_rate = Decimal(str(getattr(user, 'cloud_discount_rate', 100) or 100)) if user else Decimal('100')
    manual_price = _manual_proxy_price(order)
    total_amount = manual_price if manual_price is not None else Decimal(str(order.total_amount or 0))
    if discount_rate <= 0:
        discount_rate = Decimal('100')
    return (total_amount * discount_rate / Decimal('100')).quantize(Decimal('0.01'))


def record_cloud_ip_log(*, event_type, order=None, asset=None, server=None, public_ip=None, previous_public_ip=None, note=''):
    asset_obj = asset
    server_obj = server
    order_obj = order or getattr(asset_obj, 'order', None) or getattr(server_obj, 'order', None)
    user_obj = (
        getattr(order_obj, 'user', None)
        or getattr(asset_obj, 'user', None)
        or getattr(server_obj, 'user', None)
    )
    provider = (
        getattr(order_obj, 'provider', None)
        or getattr(asset_obj, 'provider', None)
        or getattr(server_obj, 'provider', None)
    )
    region_code = (
        getattr(order_obj, 'region_code', None)
        or getattr(asset_obj, 'region_code', None)
        or getattr(server_obj, 'region_code', None)
    )
    region_name = (
        getattr(order_obj, 'region_name', None)
        or getattr(asset_obj, 'region_name', None)
        or getattr(server_obj, 'region_name', None)
    )
    asset_name = (
        getattr(asset_obj, 'asset_name', None)
        or getattr(server_obj, 'server_name', None)
        or getattr(order_obj, 'server_name', None)
    )
    instance_id = (
        getattr(asset_obj, 'instance_id', None)
        or getattr(server_obj, 'instance_id', None)
        or getattr(order_obj, 'instance_id', None)
    )
    provider_resource_id = (
        getattr(asset_obj, 'provider_resource_id', None)
        or getattr(server_obj, 'provider_resource_id', None)
        or getattr(order_obj, 'provider_resource_id', None)
    )
    current_ip = public_ip
    if current_ip is None:
        current_ip = (
            getattr(asset_obj, 'public_ip', None)
            or getattr(server_obj, 'public_ip', None)
            or getattr(order_obj, 'public_ip', None)
        )
    previous_ip = previous_public_ip
    if previous_ip is None:
        previous_ip = (
            getattr(asset_obj, 'previous_public_ip', None)
            or getattr(server_obj, 'previous_public_ip', None)
            or getattr(order_obj, 'previous_public_ip', None)
        )
    if event_type == CloudIpLog.EVENT_DELETED:
        lookup = CloudIpLog.objects.filter(event_type=event_type)
        if asset_obj:
            lookup = lookup.filter(asset=asset_obj)
        elif server_obj:
            lookup = lookup.filter(server=server_obj)
        elif order_obj:
            lookup = lookup.filter(order=order_obj)
        if previous_ip:
            lookup = lookup.filter(previous_public_ip=previous_ip)
        existing = lookup.order_by('-created_at', '-id').first()
        if existing:
            return existing

    return CloudIpLog.objects.create(
        order=order_obj,
        asset=asset_obj,
        server=server_obj,
        user=user_obj,
        provider=provider,
        region_code=region_code,
        region_name=region_name,
        order_no=getattr(order_obj, 'order_no', None),
        asset_name=asset_name,
        instance_id=instance_id,
        provider_resource_id=provider_resource_id,
        public_ip=current_ip,
        previous_public_ip=previous_ip,
        event_type=event_type,
        note=note or '',
    )


def _fmt_dt(value):
    return value.isoformat() if value else '-'


def _trim_operation_order_no(source_order: CloudServerOrder | None, operation: str, suffix: str | None = None) -> str:
    source_id = getattr(source_order, 'id', None) or 0
    tag = suffix or (f'O{source_id}' if source_id else None)
    return _generate_cloud_order_no(f'SRV{operation}', tag=tag)


def _set_source_migration_expiry(order: CloudServerOrder, migration_due_at, reason: str, note: str = ''):
    before = {
        'service_expires_at': order.service_expires_at,
        'renew_grace_expires_at': order.renew_grace_expires_at,
        'suspend_at': order.suspend_at,
        'delete_at': order.delete_at,
        'ip_recycle_at': order.ip_recycle_at,
        'migration_due_at': order.migration_due_at,
    }
    order.migration_due_at = migration_due_at
    order.service_expires_at = migration_due_at
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    order.save()
    asset_count = CloudAsset.objects.filter(order=order).update(actual_expires_at=migration_due_at, updated_at=timezone.now())
    server_count = Server.objects.filter(order=order).update(expires_at=migration_due_at, updated_at=timezone.now())
    after = {
        'service_expires_at': order.service_expires_at,
        'renew_grace_expires_at': order.renew_grace_expires_at,
        'suspend_at': order.suspend_at,
        'delete_at': order.delete_at,
        'ip_recycle_at': order.ip_recycle_at,
        'migration_due_at': order.migration_due_at,
    }
    logger.info(
        'CLOUD_SOURCE_MIGRATION_EXPIRY_CHANGE reason=%s order_id=%s order_no=%s status=%s public_ip=%s previous_public_ip=%s service_expires_at=%s->%s renew_grace_expires_at=%s->%s suspend_at=%s->%s delete_at=%s->%s ip_recycle_at=%s->%s migration_due_at=%s->%s asset_count=%s server_count=%s',
        reason,
        order.id,
        order.order_no,
        order.status,
        order.public_ip,
        order.previous_public_ip,
        _fmt_dt(before['service_expires_at']),
        _fmt_dt(after['service_expires_at']),
        _fmt_dt(before['renew_grace_expires_at']),
        _fmt_dt(after['renew_grace_expires_at']),
        _fmt_dt(before['suspend_at']),
        _fmt_dt(after['suspend_at']),
        _fmt_dt(before['delete_at']),
        _fmt_dt(after['delete_at']),
        _fmt_dt(before['ip_recycle_at']),
        _fmt_dt(after['ip_recycle_at']),
        _fmt_dt(before['migration_due_at']),
        _fmt_dt(after['migration_due_at']),
        asset_count,
        server_count,
    )
    record_cloud_ip_log(
        event_type='changed',
        order=order,
        public_ip=order.public_ip or None,
        previous_public_ip=order.previous_public_ip or None,
        note=(
            f'{reason}: 旧机日期已调整；'
            f'服务到期 {_fmt_dt(before["service_expires_at"])} -> {_fmt_dt(after["service_expires_at"])}；'
            f'宽限到期 {_fmt_dt(before["renew_grace_expires_at"])} -> {_fmt_dt(after["renew_grace_expires_at"])}；'
            f'删机时间 {_fmt_dt(before["delete_at"])} -> {_fmt_dt(after["delete_at"])}；'
            f'IP保留到期 {_fmt_dt(before["ip_recycle_at"])} -> {_fmt_dt(after["ip_recycle_at"])}；'
            f'同步资产 {asset_count} 条、Server {server_count} 条。'
        ),
    )
    return order


def _create_manual_asset_operation_order(asset: CloudAsset, user: TelegramUser, operation: str, service_expires_at=None) -> CloudServerOrder | None:
    provider = asset.provider or CloudServerPlan.PROVIDER_AWS_LIGHTSAIL
    plan = CloudServerPlan.objects.filter(
        provider=provider,
        region_code=asset.region_code or '',
        is_active=True,
    ).order_by('-sort_order', 'id').first() or CloudServerPlan.objects.filter(provider=provider, is_active=True).order_by('-sort_order', 'id').first()
    if not plan:
        return None
    now = timezone.now()
    base_order = asset.order
    base_order_id = getattr(base_order, 'id', None)
    account = asset.cloud_account or get_cloud_account_from_label(asset.account_label, provider)
    order = CloudServerOrder.objects.create(
        order_no=_generate_cloud_order_no('SRVMANUAL', f'{operation}{asset.id}'),
        user=user,
        plan=plan,
        provider=provider,
        cloud_account=account,
        account_label=asset.account_label or cloud_account_label(account),
        region_code=asset.region_code or plan.region_code,
        region_name=asset.region_name or plan.region_name,
        plan_name=asset.asset_name or plan.plan_name,
        quantity=1,
        currency=asset.currency or plan.currency,
        total_amount=Decimal('0'),
        pay_amount=Decimal('0'),
        pay_method='balance',
        status='completed',
        lifecycle_days=getattr(base_order, 'lifecycle_days', 31) or 31,
        service_started_at=getattr(base_order, 'service_started_at', None) or asset.created_at or now,
        service_expires_at=service_expires_at or asset.actual_expires_at,
        server_name=asset.asset_name,
        mtproxy_port=asset.mtproxy_port or getattr(base_order, 'mtproxy_port', None) or 9528,
        mtproxy_link=asset.mtproxy_link or getattr(base_order, 'mtproxy_link', None),
        proxy_links=asset.proxy_links or getattr(base_order, 'proxy_links', None) or [],
        mtproxy_secret=asset.mtproxy_secret or getattr(base_order, 'mtproxy_secret', None),
        mtproxy_host=asset.mtproxy_host or getattr(base_order, 'mtproxy_host', None),
        instance_id=asset.instance_id,
        provider_resource_id=asset.provider_resource_id,
        public_ip=asset.public_ip,
        previous_public_ip=asset.previous_public_ip,
        login_user=asset.login_user or getattr(base_order, 'login_user', None),
        login_password=asset.login_password or getattr(base_order, 'login_password', None),
        ip_change_quota=getattr(base_order, 'ip_change_quota', 1) or 1,
        last_user_id=getattr(user, 'tg_user_id', None),
        completed_at=now,
        replacement_for=base_order if base_order_id else None,
        provision_note=f'{operation}: 后台人工编辑生成的操作订单；来源资产 #{asset.id}；来源订单 #{base_order_id or "-"}。',
    )
    return order


def ensure_manual_owner_operation_order(asset: CloudAsset, new_user: TelegramUser | None, actor: str = '后台人工编辑', previous_user: TelegramUser | None = None) -> tuple[CloudServerOrder | None, str | None]:
    old_user = previous_user if previous_user is not None else asset.user
    if not new_user:
        old_label = getattr(old_user, 'tg_user_id', None) or getattr(old_user, 'username', None) or '-'
        asset.user = None
        asset.order = None
        asset.save(update_fields=['user', 'order', 'updated_at'])
        server_count = Server.objects.filter(
            Q(instance_id=asset.instance_id) | Q(provider_resource_id=asset.provider_resource_id)
        ).update(user=None, updated_at=timezone.now())
        logger.info('CLOUD_MANUAL_OWNER_UNBIND asset_id=%s public_ip=%s old_user=%s server_count=%s actor=%s', asset.id, asset.public_ip, old_label, server_count, actor)
        record_cloud_ip_log(event_type='changed', asset=asset, public_ip=asset.public_ip, previous_public_ip=asset.previous_public_ip, note=f'{actor}: 人工清空所属人；原所属人 {old_label}；同步 Server {server_count} 条。')
        return None, None
    old_label = getattr(old_user, 'tg_user_id', None) or getattr(old_user, 'username', None) or '-'
    new_label = getattr(new_user, 'tg_user_id', None) or getattr(new_user, 'username', None) or new_user.id
    asset.user = new_user
    asset.save(update_fields=['user', 'updated_at'])
    order = _create_manual_asset_operation_order(asset, new_user, 'OWNER', asset.actual_expires_at)
    if not order:
        logger.warning('CLOUD_MANUAL_OWNER_ORDER_SKIPPED asset_id=%s public_ip=%s reason=no_available_plan actor=%s', asset.id, asset.public_ip, actor)
        record_cloud_ip_log(event_type='changed', asset=asset, public_ip=asset.public_ip, previous_public_ip=asset.previous_public_ip, note=f'{actor}: 人工编辑所属人 {old_label} -> {new_label}；未生成操作订单：该地区没有可用套餐。')
        return None, None
    order.provision_note = '\n'.join(filter(None, [order.provision_note, f'{actor}: 人工编辑所属人 {old_label} -> {new_label}，生成独立操作订单支撑同步识别。']))
    order.save(update_fields=['provision_note', 'updated_at'])
    asset.order = order
    asset.save(update_fields=['order', 'updated_at'])
    server_match = Q(order=order)
    if asset.instance_id:
        server_match |= Q(instance_id=asset.instance_id)
    if asset.provider_resource_id:
        server_match |= Q(provider_resource_id=asset.provider_resource_id)
    server_count = Server.objects.filter(server_match).update(order=order, user=new_user, updated_at=timezone.now())
    logger.info('CLOUD_MANUAL_OWNER_ORDER order_id=%s order_no=%s asset_id=%s public_ip=%s old_user=%s new_user=%s server_count=%s actor=%s', order.id, order.order_no, asset.id, asset.public_ip, old_label, new_label, server_count, actor)
    record_cloud_ip_log(event_type='changed', order=order, asset=asset, public_ip=asset.public_ip, previous_public_ip=asset.previous_public_ip, note=f'{actor}: 人工编辑所属人 {old_label} -> {new_label}；操作订单 {order.order_no}；同步 Server {server_count} 条。')
    return order, None


def ensure_manual_expiry_operation_order(asset: CloudAsset, new_expires_at, actor: str = '后台人工编辑', previous_expires_at=None) -> tuple[CloudServerOrder | None, str | None]:
    if not asset.user_id:
        logger.info('CLOUD_MANUAL_EXPIRY_ORDER_SKIPPED asset_id=%s public_ip=%s reason=unbound_user actor=%s', asset.id, asset.public_ip, actor)
        record_cloud_ip_log(event_type='changed', asset=asset, public_ip=asset.public_ip, previous_public_ip=asset.previous_public_ip, note=f'{actor}: 人工编辑未绑定资产到期时间为 {_fmt_dt(new_expires_at)}；未生成操作订单。')
        return None, None
    old_expires_at = previous_expires_at if previous_expires_at is not None else asset.actual_expires_at
    order = _create_manual_asset_operation_order(asset, asset.user, 'EXPIRY', new_expires_at)
    if not order:
        logger.warning('CLOUD_MANUAL_EXPIRY_ORDER_SKIPPED asset_id=%s public_ip=%s reason=no_available_plan actor=%s', asset.id, asset.public_ip, actor)
        record_cloud_ip_log(event_type='changed', asset=asset, public_ip=asset.public_ip, previous_public_ip=asset.previous_public_ip, note=f'{actor}: 人工编辑到期时间 {_fmt_dt(old_expires_at)} -> {_fmt_dt(new_expires_at)}；未生成操作订单：该地区没有可用套餐。')
        return None, None
    order.provision_note = '\n'.join(filter(None, [
        order.provision_note,
        f'{actor}: 人工编辑到期时间 {_fmt_dt(old_expires_at)} -> {_fmt_dt(new_expires_at)}，生成独立操作订单支撑同步识别。',
    ]))
    order.save(update_fields=['provision_note', 'updated_at'])
    asset.order = order
    asset.actual_expires_at = new_expires_at
    asset.save(update_fields=['order', 'actual_expires_at', 'updated_at'])
    server_match = Q(order=order)
    if asset.instance_id:
        server_match |= Q(instance_id=asset.instance_id)
    if asset.provider_resource_id:
        server_match |= Q(provider_resource_id=asset.provider_resource_id)
    server_count = Server.objects.filter(server_match).update(order=order, expires_at=new_expires_at, updated_at=timezone.now())
    logger.info(
        'CLOUD_MANUAL_EXPIRY_ORDER order_id=%s order_no=%s asset_id=%s public_ip=%s old_expires_at=%s new_expires_at=%s server_count=%s actor=%s',
        order.id,
        order.order_no,
        asset.id,
        asset.public_ip,
        _fmt_dt(old_expires_at),
        _fmt_dt(new_expires_at),
        server_count,
        actor,
    )
    record_cloud_ip_log(
        event_type='changed',
        order=order,
        asset=asset,
        public_ip=asset.public_ip,
        previous_public_ip=asset.previous_public_ip,
        note=f'{actor}: 人工编辑到期时间 {_fmt_dt(old_expires_at)} -> {_fmt_dt(new_expires_at)}；操作订单 {order.order_no}；同步 Server {server_count} 条。',
    )
    return order, None


def _create_asset_operation_order(asset: CloudAsset, user_id: int) -> CloudServerOrder | None:
    provider = asset.provider or CloudServerPlan.PROVIDER_AWS_LIGHTSAIL
    region_code = asset.region_code or ''
    plan = CloudServerPlan.objects.filter(
        provider=provider,
        region_code=region_code,
        is_active=True,
    ).order_by('-sort_order', 'id').first()
    if not plan:
        plan = CloudServerPlan.objects.filter(provider=provider, is_active=True).order_by('-sort_order', 'id').first()
    if not plan:
        return None
    now = timezone.now()
    total_amount = Decimal(str(asset.price if asset.price is not None else plan.price))
    account = asset.cloud_account or get_cloud_account_from_label(asset.account_label, provider)
    account_label = asset.account_label or cloud_account_label(account)
    order = CloudServerOrder.objects.create(
        order_no=_generate_cloud_order_no('SRVASSET', str(asset.id)),
        user_id=user_id,
        plan=plan,
        provider=provider,
        cloud_account=account,
        account_label=account_label,
        region_code=asset.region_code or plan.region_code,
        region_name=asset.region_name or plan.region_name,
        plan_name=asset.asset_name or plan.plan_name,
        quantity=1,
        currency=asset.currency or plan.currency,
        total_amount=total_amount,
        pay_amount=total_amount,
        pay_method='address',
        status='completed',
        lifecycle_days=31,
        service_started_at=asset.created_at or now,
        service_expires_at=asset.actual_expires_at,
        server_name=asset.asset_name,
        mtproxy_port=asset.mtproxy_port or 9528,
        mtproxy_link=asset.mtproxy_link,
        proxy_links=asset.proxy_links or [],
        mtproxy_secret=asset.mtproxy_secret,
        mtproxy_host=asset.mtproxy_host,
        instance_id=asset.instance_id,
        provider_resource_id=asset.provider_resource_id,
        public_ip=asset.public_ip,
        previous_public_ip=asset.previous_public_ip,
        login_user=asset.login_user,
        login_password=asset.login_password,
        ip_change_quota=1,
        last_user_id=getattr(asset.user, 'tg_user_id', None),
        completed_at=now,
        provision_note=f'由绑定代理资产 #{asset.id} 自动生成的操作订单。',
    )
    asset.order = order
    asset.save(update_fields=['order', 'updated_at'])
    record_cloud_ip_log(
        event_type=CloudIpLog.EVENT_CREATED,
        order=order,
        asset=asset,
        public_ip=asset.public_ip,
        previous_public_ip=asset.previous_public_ip,
        note=f'代理资产 #{asset.id} 生成操作订单 {order.order_no}',
    )
    return order


@sync_to_async
def ensure_cloud_asset_operation_order(asset_id: int, user_id: int):
    asset = CloudAsset.objects.select_related('order', 'user', 'cloud_account').filter(
        id=asset_id,
        user_id=user_id,
        kind=CloudAsset.KIND_SERVER,
    ).exclude(status__in=_INACTIVE_ASSET_STATUSES).first()
    if not asset:
        return None, '代理记录不存在'
    if not str(asset.public_ip or '').strip():
        return None, '代理缺少公网 IP，暂时无法操作'
    order = asset.order if asset.order_id and getattr(asset.order, 'user_id', None) == user_id else None
    if not order:
        order = _create_asset_operation_order(asset, user_id)
        if not order:
            return None, '该地区没有可用套餐，无法创建操作订单'
    order = _hydrate_order_from_proxy_asset(order, asset=asset)
    order.user_id = user_id
    if order.status not in {'completed', 'expiring', 'suspended', 'renew_pending', 'paid', 'provisioning'}:
        order.status = 'completed'
    order.provider = order.provider or asset.provider or CloudServerPlan.PROVIDER_AWS_LIGHTSAIL
    order.region_code = order.region_code or asset.region_code or ''
    order.region_name = order.region_name or asset.region_name or ''
    order.public_ip = order.public_ip or asset.public_ip
    order.previous_public_ip = order.previous_public_ip or asset.previous_public_ip
    order.instance_id = order.instance_id or asset.instance_id
    order.provider_resource_id = order.provider_resource_id or asset.provider_resource_id
    order.mtproxy_port = order.mtproxy_port or asset.mtproxy_port or 9528
    order.mtproxy_link = order.mtproxy_link or asset.mtproxy_link
    order.proxy_links = order.proxy_links or asset.proxy_links or []
    order.mtproxy_secret = order.mtproxy_secret or asset.mtproxy_secret
    order.mtproxy_host = order.mtproxy_host or asset.mtproxy_host
    order.login_user = order.login_user or asset.login_user
    order.login_password = order.login_password or asset.login_password
    order.service_expires_at = order.service_expires_at or asset.actual_expires_at
    account = asset.cloud_account or get_cloud_account_from_label(asset.account_label, order.provider)
    order.cloud_account = order.cloud_account or account
    order.account_label = order.account_label or asset.account_label or cloud_account_label(account)
    order.save(update_fields=[
        'user', 'status', 'provider', 'region_code', 'region_name', 'public_ip', 'previous_public_ip',
        'instance_id', 'provider_resource_id', 'mtproxy_port', 'mtproxy_link', 'proxy_links',
        'mtproxy_secret', 'mtproxy_host', 'login_user', 'login_password', 'service_expires_at',
        'cloud_account', 'account_label', 'updated_at',
    ])
    return order, None


def _create_retained_ip_recovery_order(order: CloudServerOrder, days: int = 31):
    if not (order.provider == 'aws_lightsail' and order.ip_recycle_at and (order.public_ip or order.previous_public_ip) and not order.instance_id):
        return None, ''
    if not order.static_ip_name:
        static_ip_name = _resolve_aws_static_ip_name_for_order(order)
        if static_ip_name:
            order.static_ip_name = static_ip_name
            order.save(update_fields=['static_ip_name', 'updated_at'])
        else:
            return None, '固定 IP 保留中，但缺少固定 IP 名称，无法自动恢复。'
    if not order.mtproxy_secret:
        return None, '固定 IP 保留中，但缺少旧 MTProxy 密钥，无法保持旧链接不变。'
    existing = CloudServerOrder.objects.filter(replacement_for=order, status__in={'paid', 'provisioning', 'failed'}).order_by('-created_at', '-id').first()
    if existing:
        return existing, ''
    fallback_plan = CloudServerPlan.objects.filter(
        provider=order.provider,
        region_code=order.region_code,
        is_active=True,
        plan_name=order.plan_name,
    ).order_by('-sort_order', 'id').first() or order.plan
    if not fallback_plan:
        return None, '未找到可用同配置套餐，无法自动恢复服务器。'
    now = timezone.now()
    old_public_ip = order.public_ip or order.previous_public_ip or ''
    suffix = now.strftime('%m%d%H%M%S')
    new_order = CloudServerOrder.objects.create(
        user_id=order.user_id,
        order_no=_trim_operation_order_no(order, 'RECOVER', suffix),
        plan_id=fallback_plan.id,
        provider=fallback_plan.provider,
        cloud_account=order.cloud_account,
        account_label=order.account_label,
        region_code=fallback_plan.region_code,
        region_name=fallback_plan.region_name,
        plan_name=fallback_plan.plan_name,
        quantity=1,
        currency=order.currency,
        total_amount=order.total_amount,
        pay_amount=order.pay_amount,
        pay_method=order.pay_method,
        status='paid',
        lifecycle_days=days,
        mtproxy_port=order.mtproxy_port or 9528,
        mtproxy_secret=order.mtproxy_secret,
        mtproxy_link=order.mtproxy_link,
        proxy_links=order.proxy_links or [],
        static_ip_name=order.static_ip_name,
        replacement_for=order,
        renew_extension_days=order.renew_extension_days,
        last_user_id=order.last_user_id,
        previous_public_ip=old_public_ip,
        service_started_at=now,
        service_expires_at=order.service_expires_at,
        image_name=order.image_name,
        provision_note='\n'.join(filter(None, [
            f'固定 IP 保留期续费后自动恢复：来源订单 {order.order_no}；旧IP={old_public_ip or "-"}；旧端口={order.mtproxy_port or "-"}；旧secret={order.mtproxy_secret or "-"}；固定IP={order.static_ip_name or "-"}；新实例必须继承旧链接/旧 secret。',
        ])),
    )
    order.provision_note = '\n'.join(filter(None, [
        order.provision_note,
        f'固定 IP 保留期续费成功，已创建自动恢复订单 {new_order.order_no}；新实例会绑定原固定 IP 并保持旧链接/旧 secret。',
    ]))
    order.save(update_fields=['provision_note', 'updated_at'])
    return new_order, ''


@sync_to_async
def create_cloud_server_renewal(order_id: int, user_id: int, days: int = 31):
    order = CloudServerOrder.objects.select_related('user').filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    order = _hydrate_order_from_proxy_asset(order)
    if not _can_order_be_renewed(order):
        return False
    retained_ip = bool(order.status == 'deleted' and order.ip_recycle_at and order.ip_recycle_at > timezone.now())
    order.status = 'renew_pending'
    order.lifecycle_days = days
    renewal_user = TelegramUser.objects.filter(id=user_id).first()
    order.pay_amount = _generate_unique_pay_amount(_renewal_price(order, renewal_user), order.currency)
    order.expired_at = timezone.now() + timezone.timedelta(minutes=30)
    if retained_ip:
        order.provision_note = '\n'.join(filter(None, [
            order.provision_note,
            f'未附加固定 IP 保留期内发起续费；IP={order.public_ip or order.previous_public_ip or "-"}；端口={order.mtproxy_port or "-"}；旧secret={order.mtproxy_secret or "-"}；IP回收时间={order.ip_recycle_at.isoformat()}。',
        ]))
        order.save(update_fields=['status', 'lifecycle_days', 'pay_amount', 'expired_at', 'provision_note', 'updated_at'])
    else:
        order.save(update_fields=['status', 'lifecycle_days', 'pay_amount', 'expired_at', 'updated_at'])
    return order


@sync_to_async
def apply_cloud_server_renewal(order_id: int, days: int = 31, run_post_checks: bool = True):
    order = _hydrate_order_from_proxy_asset(CloudServerOrder.objects.get(id=order_id))
    ok, renew_note = _renew_aliyun_instance(order, days)
    if not ok:
        order.provision_note = renew_note
        order.save(update_fields=['provision_note', 'updated_at'])
        raise ValueError(renew_note)
    now = timezone.now()
    current_expires_at = order.service_expires_at
    renew_base_at = current_expires_at if current_expires_at and current_expires_at > now else now
    order.service_started_at = now
    order.service_expires_at = renew_base_at + timezone.timedelta(days=days)
    order.last_renewed_at = now
    if hasattr(order, 'auto_renew_notice_sent_at'):
        order.auto_renew_notice_sent_at = None
    if hasattr(order, 'auto_renew_failure_notice_sent_at'):
        order.auto_renew_failure_notice_sent_at = None
    order.delay_quota = max(int(order.delay_quota or 0), 0) + 1
    order.ip_change_quota = max(int(getattr(order, 'ip_change_quota', 0) or 0), 0) + 1
    retained_ip = bool(order.status in {'deleted', 'renew_pending'} and order.ip_recycle_at and (order.public_ip or order.previous_public_ip) and not order.instance_id)
    order.status = 'completed'
    retention_note = ''
    post_notes = []
    recovery_order = None
    if retained_ip:
        retention_note = f'未附加固定 IP 续费成功；保留IP={order.public_ip or order.previous_public_ip or "-"}；端口={order.mtproxy_port or "-"}；旧secret={order.mtproxy_secret or "-"}；系统将自动新建同配置服务器、绑定原固定 IP 并保持旧链接不变。'
    elif run_post_checks:
        start_ok, start_note = _ensure_aws_instance_running(order)
        post_notes.append(start_note)
        if start_ok:
            mtproxy_ok, mtproxy_note = _ensure_mtproxy_after_renewal(order)
            post_notes.append(mtproxy_note)
        else:
            post_notes.append('实例未确认运行，暂未执行 MTProxy 检查。')
    else:
        post_notes.append('续费后运行状态与 MTProxy 巡检已提交后台执行。')
    base_note = '原到期时间未过期，已在原到期时间基础上顺延' if current_expires_at and current_expires_at > now else '原到期时间已过期或缺失，已从当前时间重新计算'
    order.provision_note = '\n'.join(filter(None, [renew_note or f'续费成功，{base_note} {days} 天。', retention_note, *post_notes]))
    order.save(update_fields=['service_started_at', 'service_expires_at', 'renew_grace_expires_at', 'suspend_at', 'delete_at', 'ip_recycle_at', 'last_renewed_at', 'auto_renew_notice_sent_at', 'auto_renew_failure_notice_sent_at', 'delay_quota', 'ip_change_quota', 'status', 'provision_note', 'updated_at'])
    if retained_ip:
        recovery_order, recovery_err = _create_retained_ip_recovery_order(order, days)
        if recovery_err:
            order.provision_note = '\n'.join(filter(None, [order.provision_note, recovery_err]))
            order.save(update_fields=['provision_note', 'updated_at'])
            raise ValueError(recovery_err)
    asset_update = {'actual_expires_at': order.service_expires_at, 'updated_at': timezone.now()}
    server_update = {'expires_at': order.service_expires_at, 'updated_at': timezone.now()}
    if retained_ip:
        asset_update.update({'public_ip': order.public_ip or order.previous_public_ip, 'previous_public_ip': order.public_ip or order.previous_public_ip, 'provider_status': '未附加固定IP-续费保留中', 'is_active': False, 'note': order.provision_note})
        server_update.update({'public_ip': order.public_ip or order.previous_public_ip, 'previous_public_ip': order.public_ip or order.previous_public_ip, 'provider_status': '未附加固定IP-续费保留中', 'is_active': False, 'note': order.provision_note})
    CloudAsset.objects.filter(order=order).update(**asset_update)
    Server.objects.filter(order=order).update(**server_update)
    record_cloud_ip_log(
        event_type=CloudIpLog.EVENT_RENEWED,
        order=order,
        public_ip=order.public_ip,
        previous_public_ip=order.previous_public_ip,
        note=f'服务器续费 {days} 天，新的服务到期时间：{order.service_expires_at:%Y-%m-%d %H:%M}；{renew_note}',
    )
    return recovery_order or order


@sync_to_async
def pay_cloud_server_renewal_with_balance(order_id: int, user_id: int, currency: str = 'USDT', days: int = 31):
    try:
        already_paid = False
        with transaction.atomic():
            order = CloudServerOrder.objects.select_related('user').select_for_update().filter(id=order_id, user_id=user_id).first()
            if not order:
                return None, '订单不存在'
            order = _hydrate_order_from_proxy_asset(order)
            if order.status not in {'renew_pending', 'pending'}:
                return None, '当前订单状态不可钱包支付'
            if not _can_order_be_renewed(order):
                return None, '该服务器IP已删除，禁止续费'
            total = Decimal(str(order.pay_amount or order.total_amount or 0))
            if order.paid_at and order.pay_method == 'balance':
                already_paid = True
            else:
                balance_field = 'balance_trx' if currency == 'TRX' else 'balance'
                user = TelegramUser.objects.select_for_update().get(id=user_id)
                current_balance = Decimal(str(getattr(user, balance_field, 0) or 0))
                if current_balance < total:
                    return None, f'{currency} 余额不足'
                old_balance = current_balance
                setattr(user, balance_field, current_balance - total)
                user.save(update_fields=[balance_field, 'updated_at'])
                paid_at = timezone.now()
                CloudServerOrder.objects.filter(id=order.id).update(
                    currency=currency,
                    pay_method='balance',
                    pay_amount=total,
                    paid_at=paid_at,
                    expired_at=None,
                    updated_at=paid_at,
                )
                order.currency = currency
                order.pay_method = 'balance'
                order.pay_amount = total
                order.paid_at = paid_at
                order.expired_at = None
                ledger = record_balance_ledger(
                    user,
                    ledger_type='cloud_order_balance_pay',
                    currency=currency,
                    old_balance=old_balance,
                    new_balance=getattr(user, balance_field),
                    related_type='cloud_order',
                    related_id=order.id,
                    description=f'云服务器续费订单 #{order.order_no} 钱包支付',
                )
        order = apply_cloud_server_renewal.__wrapped__(order_id, days, False)
        if not already_paid:
            order.renew_balance_change = {
                'currency': currency,
                'amount': getattr(ledger, 'amount', total) if ledger else total,
                'before': old_balance,
                'after': getattr(user, balance_field),
            }
        if already_paid:
            logger.info('云服务器钱包续费订单已支付，跳过重复扣款并继续续期: order_id=%s user_id=%s', order_id, user_id)
        return order, None
    except Exception as exc:
        logger.exception('云服务器钱包续费失败: order_id=%s user_id=%s currency=%s error=%s', order_id, user_id, currency, exc)
        return None, str(exc)


@sync_to_async
def run_cloud_server_renewal_postcheck(order_id: int):
    order = CloudServerOrder.objects.filter(id=order_id).first()
    if not order:
        return None, '订单不存在'
    order = _hydrate_order_from_proxy_asset(order)
    if order.status not in {'completed', 'expiring', 'renew_pending'}:
        return order, '订单当前状态不需要续费后巡检'
    if order.ip_recycle_at and (order.public_ip or order.previous_public_ip) and not order.instance_id:
        recovery_order, recovery_err = _create_retained_ip_recovery_order(order, int(order.lifecycle_days or 31))
        if recovery_err:
            return order, recovery_err
        return recovery_order or order, '固定 IP 保留期续费，已进入自动恢复流程。'
    notes = []
    start_ok, start_note = _ensure_aws_instance_running(order)
    notes.append(start_note)
    if start_ok:
        mtproxy_ok, mtproxy_note = _ensure_mtproxy_after_renewal(order)
        notes.append(mtproxy_note)
    else:
        mtproxy_ok = False
        notes.append('实例未确认运行，暂未执行 MTProxy 检查。')
    order.provision_note = '\n'.join(filter(None, [order.provision_note, '续费后自动巡检：', *notes]))
    order.save(update_fields=['provision_note', 'updated_at'])
    return order, None if start_ok and mtproxy_ok else '\n'.join(filter(None, notes))


@sync_to_async
def rebind_cloud_server_user(order_id: int, new_user_id: int):
    order = CloudServerOrder.objects.select_related('user').get(id=order_id)
    order.user_id = new_user_id
    order.last_user_id = order.user.tg_user_id if hasattr(order.user, 'tg_user_id') else order.last_user_id
    order.save(update_fields=['user', 'last_user_id', 'updated_at'])
    return order


@sync_to_async
def mark_cloud_server_ip_change_requested(order_id: int, user_id: int, region_code: str | None = None, port: int | None = None):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    if order.status not in {'completed', 'expiring', 'suspended'}:
        return False
    if order.provider != 'aws_lightsail':
        return False
    remaining_ip_changes = max(int(getattr(order, 'ip_change_quota', 0) or 0), 0)
    if remaining_ip_changes <= 0:
        return False
    target_region_code = region_code or order.region_code
    if target_region_code == 'cn-hongkong':
        return False
    provider = 'aws_lightsail'
    fallback_plan = CloudServerPlan.objects.filter(
        provider=provider,
        region_code=target_region_code,
        is_active=True,
    ).order_by('-sort_order', 'id').first()
    if not fallback_plan:
        fallback_plan = CloudServerPlan.objects.filter(
            provider=provider,
            region_code=target_region_code,
            plan_name=order.plan_name,
            is_active=True,
        ).order_by('-sort_order', 'id').first()
    if not fallback_plan:
        return False
    if not order.mtproxy_secret:
        return False
    target_port = port or order.mtproxy_port or 9528
    original_service_expires_at = order.service_expires_at
    remaining_ip_changes -= 1
    now = timezone.now()
    migration_due_at = now + timezone.timedelta(days=5)
    ip_recycle_at = migration_due_at + timezone.timedelta(days=15)
    old_public_ip = order.public_ip or order.previous_public_ip or ''
    old_port = order.mtproxy_port or target_port
    old_secret = order.mtproxy_secret or ''
    old_trace_note = (
        f'更换 IP 追溯：来源订单 {order.order_no}；旧IP={old_public_ip or "-"}；旧端口={old_port or "-"}；'
        f'旧secret={old_secret or "-"}；新订单为同配置服务器，会申请并绑定新的固定 IP；新订单必须继承旧 secret，用户提供链接时必须逐项对照 IP/端口/secret。'
    )
    new_order = CloudServerOrder.objects.create(
        user_id=order.user_id,
        order_no=_trim_operation_order_no(order, 'IP'),
        plan_id=fallback_plan.id,
        provider=fallback_plan.provider,
        region_code=fallback_plan.region_code,
        region_name=fallback_plan.region_name,
        plan_name=fallback_plan.plan_name,
        quantity=1,
        currency=order.currency,
        total_amount=order.total_amount,
        pay_amount=order.pay_amount,
        pay_method=order.pay_method,
        status='paid',
        lifecycle_days=order.lifecycle_days,
        mtproxy_port=target_port,
        mtproxy_secret=order.mtproxy_secret,
        mtproxy_link=order.mtproxy_link,
        proxy_links=order.proxy_links or [],
        static_ip_name='',
        service_started_at=order.service_started_at or now,
        service_expires_at=original_service_expires_at,
        migration_due_at=migration_due_at,
        replacement_for=order,
        renew_extension_days=order.renew_extension_days,
        ip_change_quota=remaining_ip_changes,
        last_user_id=order.last_user_id,
        previous_public_ip=old_public_ip,
        server_name=order.server_name,
        image_name=order.image_name,
        provision_note='\n'.join(filter(None, [order.provision_note, f'由订单 {order.order_no} 发起更换 IP，新建同配置服务器，地区: {fallback_plan.region_name}，端口: {target_port}，会申请并绑定新的固定 IP，需在 5 天内切换使用。', old_trace_note])),
    )
    order.ip_change_quota = remaining_ip_changes
    order.save(update_fields=['ip_change_quota', 'updated_at'])
    _set_source_migration_expiry(
        order,
        migration_due_at,
        '更换 IP 新建同配置服务器，旧机到期时间调整',
        f'已发起更换 IP，新实例订单: {new_order.order_no}。旧机追溯：旧IP={old_public_ip or "-"}；旧端口={old_port or "-"}；旧secret={old_secret or "-"}。新服务器为同配置服务器，会申请并绑定新的固定 IP，同时继承旧服务器原到期时间和旧 secret；旧服务器服务到期时间调整为 {migration_due_at:%Y-%m-%d %H:%M}，宽限 3 天后删除，删除后旧 IP 继续保留至 {ip_recycle_at:%Y-%m-%d %H:%M}。'
    )
    return new_order


def _resolve_aws_static_ip_name_for_order(order: CloudServerOrder) -> str:
    public_ip = str(order.public_ip or '').strip()
    if order.provider != 'aws_lightsail' or not public_ip:
        return ''
    account = getattr(order, 'cloud_account', None) or get_active_cloud_account('aws', order.region_code)
    access_key = ''
    secret_key = ''
    if account:
        ak = (account.access_key_plain or '').strip()
        sk = (account.secret_key_plain or '').strip()
        if ak and sk and len(ak) >= 16 and len(sk) >= 36:
            access_key, secret_key = ak, sk
    if not access_key or not secret_key:
        access_key = os.getenv('AWS_ACCESS_KEY_ID', '')
        secret_key = os.getenv('AWS_SECRET_ACCESS_KEY', '')
    if not access_key or not secret_key:
        logger.warning('AWS 固定 IP 名称反查失败: order=%s ip=%s reason=missing_credentials', order.order_no, public_ip)
        return ''
    try:
        import boto3
        client = boto3.client('lightsail', region_name=order.region_code or 'ap-southeast-1', aws_access_key_id=access_key, aws_secret_access_key=secret_key)
        token = None
        while True:
            kwargs = {'pageToken': token} if token else {}
            response = client.get_static_ips(**kwargs)
            for item in response.get('staticIps') or []:
                if str(item.get('ipAddress') or '').strip() == public_ip:
                    name = str(item.get('name') or '').strip()
                    logger.info('AWS 固定 IP 名称反查成功: order=%s ip=%s static_ip_name=%s attached_to=%s', order.order_no, public_ip, name, item.get('attachedTo') or '')
                    return name
            token = response.get('nextPageToken')
            if not token:
                break
    except Exception as exc:
        logger.warning('AWS 固定 IP 名称反查失败: order=%s ip=%s error=%s', order.order_no, public_ip, exc)
    return ''


def create_cloud_server_rebuild_order(order_id: int):
    order = CloudServerOrder.objects.select_related('cloud_account').filter(id=order_id).first()
    if not order:
        return None, '服务器记录不存在'
    if order.provider != 'aws_lightsail':
        return None, '当前仅支持 AWS Lightsail 重装迁移'
    if order.status not in {'completed', 'expiring', 'renew_pending', 'suspended', 'failed'}:
        return None, '当前状态不允许发起重装'
    if not order.static_ip_name:
        static_ip_name = _resolve_aws_static_ip_name_for_order(order)
        if static_ip_name:
            order.static_ip_name = static_ip_name
            order.save(update_fields=['static_ip_name', 'updated_at'])
        else:
            return None, '当前服务器没有固定 IP 名称，无法保证链接不变'
    if not order.mtproxy_secret:
        return None, '当前服务器缺少 MTProxy 密钥，无法保证链接不变'
    existing_rebuild = CloudServerOrder.objects.filter(
        replacement_for=order,
        status__in={'paid', 'provisioning', 'failed'},
    ).order_by('-created_at', '-id').first()
    if existing_rebuild:
        if existing_rebuild.cloud_account_id != order.cloud_account_id or existing_rebuild.account_label != order.account_label:
            existing_rebuild.cloud_account = order.cloud_account
            existing_rebuild.account_label = order.account_label
            existing_rebuild.save(update_fields=['cloud_account', 'account_label', 'updated_at'])
        return existing_rebuild, None
    fallback_plan = CloudServerPlan.objects.filter(
        provider=order.provider,
        region_code=order.region_code,
        is_active=True,
        plan_name=order.plan_name,
    ).order_by('-sort_order', 'id').first() or order.plan
    if not fallback_plan:
        return None, '未找到可用同配置套餐'
    now = timezone.now()
    suffix = now.strftime('%m%d%H%M%S')
    migration_due_at = now + timezone.timedelta(days=3)
    old_public_ip = order.public_ip or order.previous_public_ip or ''
    old_port = order.mtproxy_port or 9528
    old_secret = order.mtproxy_secret or ''
    old_trace_note = (
        f'重装迁移追溯：来源订单 {order.order_no}；旧IP={old_public_ip or "-"}；旧端口={old_port or "-"}；'
        f'旧secret={old_secret or "-"}；固定IP={order.static_ip_name or "-"}；新订单必须继承旧 secret，用户提供链接时必须逐项对照 IP/端口/secret。'
    )
    new_order = CloudServerOrder.objects.create(
        user_id=order.user_id,
        order_no=_trim_operation_order_no(order, 'REBUILD', suffix),
        plan_id=fallback_plan.id,
        provider=fallback_plan.provider,
        cloud_account=order.cloud_account,
        account_label=order.account_label,
        region_code=fallback_plan.region_code,
        region_name=fallback_plan.region_name,
        plan_name=fallback_plan.plan_name,
        quantity=1,
        currency=order.currency,
        total_amount=order.total_amount,
        pay_amount=order.pay_amount,
        pay_method=order.pay_method,
        status='paid',
        lifecycle_days=order.lifecycle_days,
        mtproxy_port=order.mtproxy_port or 9528,
        mtproxy_secret=order.mtproxy_secret,
        mtproxy_link=order.mtproxy_link,
        proxy_links=order.proxy_links or [],
        static_ip_name=order.static_ip_name,
        replacement_for=order,
        renew_extension_days=order.renew_extension_days,
        last_user_id=order.last_user_id,
        previous_public_ip=old_public_ip,
        service_started_at=order.service_started_at,
        service_expires_at=order.service_expires_at,
        server_name=order.server_name,
        image_name=order.image_name,
        provision_note='\n'.join(filter(None, [order.provision_note, f'后台发起重装迁移：新实例创建时不申请临时固定 IP，创建成功后直接迁移原固定 IP {order.static_ip_name}，再安装代理。', old_trace_note])),
    )
    _set_source_migration_expiry(
        order,
        migration_due_at,
        '重装/重建迁移旧机到期时间调整',
        f'已发起重装迁移，新实例订单: {new_order.order_no}。旧机追溯：旧IP={old_public_ip or "-"}；旧端口={old_port or "-"}；旧secret={old_secret or "-"}；固定IP={order.static_ip_name or "-"}。旧实例服务到期时间调整为 {migration_due_at:%Y-%m-%d %H:%M}，宽限 3 天后删除，删除后固定 IP 保留 15 天；新机创建后会先迁移固定 IP {order.static_ip_name}，并强制沿用旧 secret 安装代理。'
    )
    return new_order, None


@sync_to_async
def mark_cloud_server_reinit_requested(order_id: int, user_id: int):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    is_unfinished = order.status in {'paid', 'provisioning', 'failed'} and not order.replacement_for_id
    if is_unfinished:
        note = '用户发起继续初始化请求，允许重新生成代理链接。'
        order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
        order.save(update_fields=['provision_note', 'updated_at'])
        return order
    if not _has_main_proxy_link(order):
        return 'missing_main_link'
    rebuild_order, err = create_cloud_server_rebuild_order(order.id)
    if err:
        return err
    return rebuild_order


def _has_main_proxy_link(order: CloudServerOrder) -> bool:
    if getattr(order, 'mtproxy_link', None):
        return True
    for item in getattr(order, 'proxy_links', None) or []:
        if isinstance(item, dict) and item.get('url') and str(item.get('port') or '') == str(order.mtproxy_port or 9528):
            return True
    return False


def _upgrade_blocks_and_expiry(expires_at):
    now = timezone.now()
    if not expires_at or expires_at <= now:
        remaining_days = Decimal('0')
    else:
        seconds = Decimal(str((expires_at - now).total_seconds()))
        remaining_days = seconds / Decimal('86400')
    blocks = max(1, int((remaining_days / Decimal('31')).to_integral_value(rounding=ROUND_CEILING)))
    target_days = blocks * 31
    return blocks, now + timezone.timedelta(days=target_days), remaining_days


@sync_to_async
def list_cloud_server_upgrade_plans(order_id: int, user_id: int):
    order = _hydrate_order_from_proxy_asset(CloudServerOrder.objects.select_related('plan', 'user').filter(id=order_id, user_id=user_id).first())
    if not order:
        return [], '服务器记录不存在'
    if order.provider != 'aws_lightsail':
        return [], '当前服务器不支持升级配置'
    if order.status not in {'completed', 'expiring', 'suspended'}:
        return [], '当前状态不允许升级'
    if not _has_main_proxy_link(order):
        return [], '当前服务器没有主代理链接，请先在后台添加主链接后再升级'
    current_price = Decimal(getattr(order.plan, 'price', None) or order.total_amount or 0)
    blocks, target_expiry, _ = _upgrade_blocks_and_expiry(order.service_expires_at)
    plans = list(CloudServerPlan.objects.filter(provider=order.provider, region_code=order.region_code, is_active=True, price__gt=current_price).order_by('price', 'sort_order', 'id'))
    result = []
    for plan in plans:
        diff = ((Decimal(plan.price) - current_price) * Decimal(blocks)).quantize(Decimal('0.01'))
        result.append({'id': plan.id, 'name': plan.plan_name, 'price': str(plan.price.quantize(Decimal('0.001'))), 'diff': str(diff.quantize(Decimal('0.001'))), 'target_days': blocks * 31, 'target_expiry': target_expiry})
    return result, None


@sync_to_async
def create_cloud_server_upgrade_order(order_id: int, user_id: int, target_plan_id: int):
    order = _hydrate_order_from_proxy_asset(CloudServerOrder.objects.select_related('plan', 'user').filter(id=order_id, user_id=user_id).first())
    if not order:
        return None, '服务器记录不存在'
    if order.provider != 'aws_lightsail':
        return None, '当前仅支持 AWS Lightsail 升级迁移'
    if order.status not in {'completed', 'expiring', 'suspended'}:
        return None, '当前状态不允许升级'
    if not order.static_ip_name:
        return None, '当前服务器没有固定 IP，无法保证链接不变'
    if not _has_main_proxy_link(order):
        return None, '当前服务器没有主代理链接，请先在后台添加主链接后再升级'
    if not order.mtproxy_secret:
        return None, '当前服务器缺少 MTProxy 密钥，无法保证链接不变'
    target_plan = CloudServerPlan.objects.filter(id=target_plan_id, provider=order.provider, region_code=order.region_code, is_active=True).first()
    if not target_plan:
        return None, '目标套餐不存在'
    current_price = Decimal(getattr(order.plan, 'price', None) or order.total_amount or 0)
    if Decimal(target_plan.price) <= current_price:
        return None, '只能升级到更高价格的配置'
    blocks, target_expiry, _ = _upgrade_blocks_and_expiry(order.service_expires_at)
    diff = ((Decimal(target_plan.price) - current_price) * Decimal(blocks)).quantize(Decimal('0.01'))
    with transaction.atomic():
        user = TelegramUser.objects.select_for_update().get(id=user_id)
        current_balance = Decimal(str(user.balance or 0))
        if current_balance < diff:
            return None, f'USDT 余额不足，需要补差价 {diff} U'
        old_balance = current_balance
        user.balance = current_balance - diff
        user.save(update_fields=['balance', 'updated_at'])
        suffix = timezone.now().strftime('%m%d%H%M%S')
        old_public_ip = order.public_ip or order.previous_public_ip or ''
        old_port = order.mtproxy_port or 9528
        old_secret = order.mtproxy_secret or ''
        old_trace_note = (
            f'升级配置追溯：来源订单 {order.order_no}；旧IP={old_public_ip or "-"}；旧端口={old_port or "-"}；'
            f'旧secret={old_secret or "-"}；固定IP={order.static_ip_name or "-"}；'
            f'配置 {order.plan_name} -> {target_plan.plan_name}；补差价 {diff} USDT；新订单必须继承旧 secret，用户提供链接时必须逐项对照 IP/端口/secret。'
        )
        new_order = CloudServerOrder.objects.create(
            user_id=order.user_id,
            order_no=_trim_operation_order_no(order, 'UPGRADE', suffix),
            plan=target_plan,
            provider=target_plan.provider,
            cloud_account=order.cloud_account,
            account_label=order.account_label or order.provider,
            region_code=target_plan.region_code,
            region_name=target_plan.region_name,
            plan_name=target_plan.plan_name,
            provider_resource_id=(target_plan.provider_plan_id or None),
            quantity=1,
            currency='USDT',
            total_amount=diff,
            pay_amount=diff,
            pay_method='balance',
            status='paid',
            lifecycle_days=blocks * 31,
            mtproxy_port=order.mtproxy_port or 9528,
            mtproxy_secret=order.mtproxy_secret,
            mtproxy_link=order.mtproxy_link,
            proxy_links=order.proxy_links or [],
            static_ip_name=order.static_ip_name,
            replacement_for=order,
            last_user_id=order.last_user_id,
            previous_public_ip=old_public_ip,
            service_started_at=order.service_started_at or timezone.now(),
            service_expires_at=target_expiry,
            server_name=order.server_name,
            image_name=order.image_name,
            paid_at=timezone.now(),
            provision_note='\n'.join(filter(None, [order.provision_note, f'用户发起升级配置：{order.plan_name} -> {target_plan.plan_name}，补差价 {diff} USDT，目标到期 {target_expiry:%Y-%m-%d %H:%M}，保持主/备用代理链路不变。', old_trace_note])),
        )
        record_balance_ledger(user, ledger_type='cloud_order_balance_pay', currency='USDT', old_balance=old_balance, new_balance=user.balance, related_type='cloud_order', related_id=new_order.id, description=f'云服务器升级补差价 #{new_order.order_no}')
        order.provision_note = '\n'.join(filter(None, [order.provision_note, f'已发起升级配置，新实例订单: {new_order.order_no}，补差价 {diff} USDT。旧机追溯：旧IP={old_public_ip or "-"}；旧端口={old_port or "-"}；旧secret={old_secret or "-"}；固定IP={order.static_ip_name or "-"}；新配置={target_plan.plan_name}；新服务器必须继承旧 secret。']))
        order.save(update_fields=['provision_note', 'updated_at'])
    return new_order, None


@sync_to_async
def refund_cloud_server_to_balance(order_id: int, user_id: int):
    order = _hydrate_order_from_proxy_asset(CloudServerOrder.objects.select_related('plan', 'user').filter(id=order_id, user_id=user_id).first())
    if not order:
        logger.warning('云服务器退款拒绝: order_id=%s user_id=%s reason=not_found', order_id, user_id)
        return None, '服务器记录不存在'
    logger.info('云服务器退款检查: order=%s user_id=%s status=%s public_ip=%s expires_at=%s pay_amount=%s total_amount=%s currency=%s', order.order_no, user_id, order.status, order.public_ip, order.service_expires_at, order.pay_amount, order.total_amount, order.currency)
    if order.status not in {'paid', 'provisioning', 'failed', 'completed', 'expiring', 'suspended'}:
        logger.warning('云服务器退款拒绝: order=%s user_id=%s status=%s reason=status_not_allowed', order.order_no, user_id, order.status)
        return None, '当前状态不允许退款'
    if BalanceLedger.objects.filter(type='manual_adjust', direction='in', related_type='cloud_order', related_id=order.id, description__startswith='云服务器剩余价值退款 #').exists():
        logger.warning('云服务器退款拒绝: order=%s user_id=%s reason=already_refunded', order.order_no, user_id)
        return None, '该订单已退款，不能重复退款'
    now = timezone.now()
    min_refund_expires_at = now + timezone.timedelta(days=10)
    if order.service_expires_at and order.service_expires_at < min_refund_expires_at:
        logger.warning('云服务器退款拒绝: order=%s user_id=%s expires_at=%s reason=less_than_10_days', order.order_no, user_id, order.service_expires_at)
        return None, '到期时间少于 10 天，禁止退款'
    currency = order.currency or 'USDT'
    monthly = Decimal(str(order.pay_amount or order.total_amount or 0))
    is_delivered = order.status in {'completed', 'expiring', 'suspended'}
    if is_delivered:
        if not str(order.public_ip or '').strip():
            logger.warning('云服务器退款拒绝: order=%s user_id=%s reason=missing_public_ip', order.order_no, user_id)
            return None, '当前服务器缺少有效 IP，不允许退款'
        if not order.service_expires_at or order.service_expires_at <= now:
            logger.warning('云服务器退款拒绝: order=%s user_id=%s expires_at=%s reason=no_remaining_time', order.order_no, user_id, order.service_expires_at)
            return None, '当前服务器没有剩余有效期可退款'
        seconds_left = Decimal(str((order.service_expires_at - now).total_seconds()))
        refund = (monthly * seconds_left / Decimal(str(31 * 86400))).quantize(Decimal('0.001'))
    else:
        refund = monthly.quantize(Decimal('0.001'))
    if refund <= 0:
        logger.warning('云服务器退款拒绝: order=%s user_id=%s refund=%s reason=zero_amount', order.order_no, user_id, refund)
        return None, '退款金额为 0'
    logger.info('云服务器退款准备入账: order=%s user_id=%s refund=%s currency=%s delivered=%s', order.order_no, user_id, refund, currency, is_delivered)
    with transaction.atomic():
        user = TelegramUser.objects.select_for_update().get(id=user_id)
        balance_field = 'balance_trx' if currency == 'TRX' else 'balance'
        old_balance = Decimal(str(getattr(user, balance_field, 0) or 0))
        setattr(user, balance_field, old_balance + refund)
        user.save(update_fields=[balance_field, 'updated_at'])
        record_balance_ledger(user, ledger_type='manual_adjust', currency=currency, old_balance=old_balance, new_balance=getattr(user, balance_field), related_type='cloud_order', related_id=order.id, description=f'云服务器剩余价值退款 #{order.order_no}')
        refund_expires_at = now + timezone.timedelta(days=3)
        order.service_expires_at = refund_expires_at
        order.provision_note = '\n'.join(filter(None, [order.provision_note, f'用户申请退款：退回 {refund} {currency} 至余额，服务将在 3 天后到期，订单状态保持为 {order.status}。']))
        order.save(update_fields=['service_expires_at', 'renew_grace_expires_at', 'suspend_at', 'delete_at', 'ip_recycle_at', 'provision_note', 'updated_at'])
        asset_count = CloudAsset.objects.filter(order=order).update(actual_expires_at=refund_expires_at, updated_at=now)
        server_count = Server.objects.filter(order=order).update(expires_at=refund_expires_at, updated_at=now)
        logger.info('云服务器退款完成: order=%s user_id=%s refund=%s currency=%s new_balance=%s refund_expires_at=%s asset_updated=%s server_updated=%s', order.order_no, user_id, refund, currency, getattr(user, balance_field), refund_expires_at, asset_count, server_count)
    return {'amount': refund, 'currency': currency, 'order': order}, None


@sync_to_async
def mute_cloud_reminders(user_id: int, days: int = 3):
    user = TelegramUser.objects.filter(id=user_id).first()
    if not user:
        return None
    user.cloud_reminder_muted_until = timezone.now() + timezone.timedelta(days=days)
    user.save(update_fields=['cloud_reminder_muted_until', 'updated_at'])
    return user


@sync_to_async
def mute_cloud_order_reminders(order_id: int, user_id: int):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    order.cloud_reminder_enabled = False
    order.suspend_reminder_enabled = False
    order.delete_reminder_enabled = False
    order.ip_recycle_reminder_enabled = False
    order.save(update_fields=['cloud_reminder_enabled', 'suspend_reminder_enabled', 'delete_reminder_enabled', 'ip_recycle_reminder_enabled', 'updated_at'])
    return order


@sync_to_async
def unmute_cloud_reminders(user_id: int):
    user = TelegramUser.objects.filter(id=user_id).first()
    if not user:
        return None
    user.cloud_reminder_muted_until = None
    user.save(update_fields=['cloud_reminder_muted_until', 'updated_at'])
    return user


@sync_to_async
def get_user_reminder_summary(user_id: int):
    user = TelegramUser.objects.filter(id=user_id).first()
    if not user:
        return None
    muted_until = user.cloud_reminder_muted_until
    cloud_orders = list(
        CloudServerOrder.objects.filter(user_id=user_id, service_expires_at__isnull=False)
        .exclude(status__in=['cancelled', 'refunded', 'deleted'])
        .order_by('service_expires_at', '-id')
    )
    auto_renew_count = sum(1 for order in cloud_orders if order.auto_renew_enabled)
    cloud_reminder_count = sum(1 for order in cloud_orders if getattr(order, 'cloud_reminder_enabled', True))
    suspend_reminder_count = sum(1 for order in cloud_orders if getattr(order, 'suspend_reminder_enabled', True))
    delete_reminder_count = sum(1 for order in cloud_orders if getattr(order, 'delete_reminder_enabled', True))
    ip_recycle_reminder_count = sum(1 for order in cloud_orders if getattr(order, 'ip_recycle_reminder_enabled', True))
    cloud_reminder_enabled = any(
        getattr(order, 'cloud_reminder_enabled', True)
        or getattr(order, 'suspend_reminder_enabled', True)
        or getattr(order, 'delete_reminder_enabled', True)
        or getattr(order, 'ip_recycle_reminder_enabled', True)
        or getattr(order, 'auto_renew_enabled', False)
        for order in cloud_orders
    )
    return {
        'user': user,
        'cloud_orders': cloud_orders,
        'cloud_reminder_enabled': cloud_reminder_enabled,
        'muted_until': muted_until,
        'auto_renew_count': auto_renew_count,
        'cloud_reminder_count': cloud_reminder_count,
        'suspend_reminder_count': suspend_reminder_count,
        'delete_reminder_count': delete_reminder_count,
        'ip_recycle_reminder_count': ip_recycle_reminder_count,
    }


@sync_to_async
def mute_all_user_reminders(user_id: int, days: int = 3650):
    user = TelegramUser.objects.filter(id=user_id).first()
    if not user:
        return None
    user.cloud_reminder_muted_until = None
    user.save(update_fields=['cloud_reminder_muted_until', 'updated_at'])
    cloud_updated = CloudServerOrder.objects.filter(user_id=user_id).update(
        cloud_reminder_enabled=False,
        suspend_reminder_enabled=False,
        delete_reminder_enabled=False,
        ip_recycle_reminder_enabled=False,
        auto_renew_enabled=False,
        updated_at=timezone.now(),
    )
    return {'user': user, 'cloud_reminders_closed': cloud_updated, 'cloud_auto_renew_closed': cloud_updated}


@sync_to_async
def unmute_all_user_reminders(user_id: int):
    user = TelegramUser.objects.filter(id=user_id).first()
    if not user:
        return None
    user.cloud_reminder_muted_until = None
    user.save(update_fields=['cloud_reminder_muted_until', 'updated_at'])
    cloud_orders = CloudServerOrder.objects.filter(user_id=user_id).exclude(status__in=['cancelled', 'refunded', 'deleted'])
    cloud_reminder_updated = cloud_orders.update(
        cloud_reminder_enabled=True,
        suspend_reminder_enabled=True,
        delete_reminder_enabled=True,
        ip_recycle_reminder_enabled=True,
        updated_at=timezone.now(),
    )
    renewable_ids = [order.id for order in cloud_orders if _can_order_be_renewed(order)]
    auto_renew_updated = CloudServerOrder.objects.filter(id__in=renewable_ids).update(auto_renew_enabled=True, updated_at=timezone.now()) if renewable_ids else 0
    return {'user': user, 'cloud_reminders_opened': cloud_reminder_updated, 'cloud_auto_renew_opened': auto_renew_updated}


@sync_to_async
def set_cloud_order_reminder(order_id: int, user_id: int, enabled: bool, reminder_type: str = 'expiry'):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    field_map = {
        'expiry': 'cloud_reminder_enabled',
        'suspend': 'suspend_reminder_enabled',
        'delete': 'delete_reminder_enabled',
        'ip_recycle': 'ip_recycle_reminder_enabled',
    }
    field_name = field_map.get(reminder_type or 'expiry')
    if not field_name:
        return None
    if enabled:
        user = TelegramUser.objects.filter(id=user_id).first()
        if user and user.cloud_reminder_muted_until:
            user.cloud_reminder_muted_until = None
            user.save(update_fields=['cloud_reminder_muted_until', 'updated_at'])
    setattr(order, field_name, enabled)
    order.save(update_fields=[field_name, 'updated_at'])
    return order


@sync_to_async
def set_cloud_server_auto_renew(order_id: int, user_id: int, enabled: bool):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    if enabled and not _can_order_be_renewed(order):
        return False
    order.auto_renew_enabled = enabled
    order.save(update_fields=['auto_renew_enabled', 'updated_at'])
    return order


@sync_to_async
def get_cloud_server_auto_renew(order_id: int, user_id: int):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    return bool(order.auto_renew_enabled)


@sync_to_async
def delay_cloud_server_expiry(order_id: int, user_id: int, days: int = 5):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    expires_at = order.service_expires_at
    if not expires_at:
        return False, '当前订单未设置到期时间'
    now = timezone.now()
    if expires_at < now:
        return False, '服务器已到期，不能延期'
    if expires_at > now + timezone.timedelta(days=5):
        return False, '仅允许在到期前5天内使用延期'
    delay_quota = max(int(order.delay_quota or 0), 0)
    if delay_quota <= 0:
        return False, '暂无可用延期次数'
    order.renew_extension_days = max(int(order.renew_extension_days or 0), days)
    order.delay_quota = delay_quota - 1
    order.save(update_fields=['renew_extension_days', 'delay_quota', 'updated_at'])
    return order, None


__all__ = [
    'apply_cloud_server_renewal',
    'build_cloud_server_name',
    'buy_cloud_server_with_balance',
    'create_cloud_server_order',
    'create_cloud_server_renewal',
    'delay_cloud_server_expiry',
    'ensure_cloud_server_pricing',
    'ensure_unique_cloud_server_name',
    'get_cloud_plan',
    'get_cloud_server_auto_renew',
    'get_user_reminder_summary',
    'get_cloud_server_by_ip',
    'get_user_cloud_server',
    'get_user_proxy_asset_detail',
    'ensure_cloud_asset_operation_order',
    'initialize_proxy_asset',
    'list_custom_regions',
    'list_region_plans',
    'list_user_auto_renew_cloud_servers',
    'list_user_cloud_servers',
    'create_cloud_server_rebuild_order',
    'mark_cloud_server_ip_change_requested',
    'mark_cloud_server_reinit_requested',
    'mute_all_user_reminders',
    'mute_cloud_order_reminders',
    'mute_cloud_reminders',
    'pay_cloud_server_order_with_balance',
    'pay_cloud_server_renewal_with_balance',
    'refund_cloud_server_to_balance',
    'prepare_cloud_server_order_instances',
    'create_cloud_server_upgrade_order',
    'list_cloud_server_upgrade_plans',
    'rebind_cloud_server_user',
    'refresh_custom_plan_cache',
    'record_cloud_ip_log',
    'set_cloud_order_reminder',
    'set_cloud_server_auto_renew',
    'set_cloud_server_port',
    'unmute_all_user_reminders',
    'unmute_cloud_reminders',
]
