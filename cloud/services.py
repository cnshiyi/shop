"""cloud 域服务主入口：当前真实云业务实现已收口到这里，旧 `biz.services.*` 仅保留兼容壳。"""

import json
import logging
import os
import re
import secrets
import socket
import string
import time
from decimal import Decimal, ROUND_CEILING
from types import SimpleNamespace
from urllib.parse import urlparse

from asgiref.sync import async_to_sync, sync_to_async
from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone

from bot.models import TelegramLoginAccount, TelegramUser
from cloud.asset_expiry import apply_order_lifecycle_from_asset_expiry, order_asset_expiry
from cloud.lifecycle_schedule import compute_order_lifecycle_fields, runtime_int_config, with_runtime_time
from cloud.models import CloudAsset, CloudIpLog, CloudServerOrder, CloudServerPlan, ServerPrice
from cloud.note_utils import append_note, prepend_note
from cloud.bootstrap import install_bbr, install_mtproxy
from cloud.dashboard_snapshots import _refresh_dashboard_plan_snapshots
from cloud.ip_guard import validate_server_connection_ip
from cloud.ports import MTPROXY_DEFAULT_PORT, get_mtproxy_public_ports
from core.cache import get_redis
from core.cloud_accounts import choose_cloud_account_for_order, cloud_account_label, cloud_account_label_variants, get_active_cloud_account, get_cloud_account_from_label
from core.models import CloudAccountConfig
from core.order_numbers import unique_timestamp_order_no
from orders.ledger import record_balance_ledger
from orders.models import BalanceLedger, CartItem
from orders.services import _generate_unique_pay_amount, usdt_to_trx

logger = logging.getLogger(__name__)
CUSTOM_CACHE_TTL = 600
CLOUD_ORDER_MAX_QUANTITY = 99


def drop_asset_note_update(updates: dict | None) -> dict:
    next_updates = dict(updates or {})
    next_updates.pop('note', None)
    return next_updates


def scoped_server_match_for_asset(asset: CloudAsset | None, *, include_order: bool = True, include_ip: bool = False):
    if not asset:
        return Q(pk__in=[])
    identity = Q()
    if include_order and getattr(asset, 'order_id', None):
        identity |= Q(order_id=asset.order_id)
    for value, fields in [
        (getattr(asset, 'instance_id', None), ['instance_id']),
        (getattr(asset, 'provider_resource_id', None), ['provider_resource_id']),
    ]:
        value = str(value or '').strip()
        if not value:
            continue
        for field in fields:
            identity |= Q(**{field: value})
    if include_ip:
        for value in [getattr(asset, 'public_ip', None), getattr(asset, 'previous_public_ip', None)]:
            value = str(value or '').strip()
            if value:
                identity |= Q(public_ip=value) | Q(previous_public_ip=value)
    if not identity:
        return Q(pk__in=[])

    scope = Q()
    provider = str(getattr(asset, 'provider', '') or '').strip()
    if provider:
        scope &= Q(provider=provider)
    account = getattr(asset, 'cloud_account', None)
    account_label = str(getattr(asset, 'account_label', '') or cloud_account_label(account) or '').strip()
    account_labels = []
    if account_label:
        account_labels.append(account_label)
    account_labels.extend(cloud_account_label_variants(account))
    account_labels = list(dict.fromkeys(label for label in account_labels if label))
    if account_labels:
        scope &= Q(account_label__in=account_labels)
    else:
        scope &= (Q(account_label='') | Q(account_label__isnull=True))
    region_code = str(getattr(asset, 'region_code', '') or '').strip()
    if region_code:
        scope &= Q(region_code=region_code)
    return scope & identity


def _asset_update_from_server_fields(updates: dict | None) -> dict:
    mapped = drop_asset_note_update(updates)
    if 'server_name' in mapped:
        mapped['asset_name'] = mapped.pop('server_name')
    if 'expires_at' in mapped:
        mapped['actual_expires_at'] = mapped.pop('expires_at')
    return mapped


def _normalize_cloud_order_quantity(quantity: int) -> int:
    try:
        normalized = int(quantity or 1)
    except (TypeError, ValueError):
        normalized = 1
    if normalized < 1 or normalized > CLOUD_ORDER_MAX_QUANTITY:
        raise ValueError(f'购买数量需在 1-{CLOUD_ORDER_MAX_QUANTITY} 之间')
    return normalized


def _refresh_dashboard_plan_snapshots_after_service_change(reason: str = '', *, lifecycle_limit: int = 1000):
    try:
        _refresh_dashboard_plan_snapshots(reason, lifecycle_limit=lifecycle_limit)
        logger.info('CLOUD_SERVICE_DASHBOARD_SNAPSHOTS_REFRESHED reason=%s', reason)
    except Exception:
        logger.exception('CLOUD_SERVICE_DASHBOARD_SNAPSHOTS_REFRESH_FAILED reason=%s', reason)


def _order_primary_asset(order: CloudServerOrder | None):
    if not order:
        return None
    queryset = CloudAsset.objects.filter(order=order)
    public_ip = str(getattr(order, 'public_ip', None) or '').strip()
    previous_public_ip = str(getattr(order, 'previous_public_ip', None) or '').strip()
    instance_id = str(getattr(order, 'instance_id', None) or '').strip()
    provider_resource_id = str(getattr(order, 'provider_resource_id', None) or '').strip()
    server_name = str(getattr(order, 'server_name', None) or '').strip()
    match = Q()
    if public_ip:
        match |= Q(public_ip=public_ip) | Q(previous_public_ip=public_ip)
    if previous_public_ip:
        match |= Q(public_ip=previous_public_ip) | Q(previous_public_ip=previous_public_ip)
    if instance_id:
        match |= Q(instance_id=instance_id)
    if provider_resource_id:
        match |= Q(provider_resource_id=provider_resource_id)
    if server_name:
        match |= Q(asset_name=server_name)
    ip_match = Q()
    if public_ip:
        ip_match |= Q(public_ip=public_ip) | Q(previous_public_ip=public_ip)
    if previous_public_ip:
        ip_match |= Q(public_ip=previous_public_ip) | Q(previous_public_ip=previous_public_ip)
    if ip_match:
        item = queryset.filter(ip_match).order_by(
            Case(
                When(public_ip=public_ip, then=Value(0)) if public_ip else When(pk__isnull=True, then=Value(9)),
                When(public_ip=previous_public_ip, then=Value(1)) if previous_public_ip else When(pk__isnull=True, then=Value(9)),
                When(previous_public_ip=public_ip, then=Value(2)) if public_ip else When(pk__isnull=True, then=Value(9)),
                When(previous_public_ip=previous_public_ip, then=Value(3)) if previous_public_ip else When(pk__isnull=True, then=Value(9)),
                default=Value(9),
                output_field=IntegerField(),
            ),
            '-updated_at',
            '-id',
        ).first()
        if item:
            return item
    fallback_match = Q()
    if instance_id:
        fallback_match |= Q(instance_id=instance_id)
    if provider_resource_id:
        fallback_match |= Q(provider_resource_id=provider_resource_id)
    if server_name:
        fallback_match |= Q(asset_name=server_name)
    if fallback_match:
        item = queryset.filter(fallback_match).order_by('-updated_at', '-id').first()
        if item:
            return item
    return queryset.order_by('-updated_at', '-id').first()


def _order_primary_server(order: CloudServerOrder | None):
    return _order_primary_asset(order)


def _update_order_primary_records(order: CloudServerOrder | None, *, asset_updates: dict | None = None, server_updates: dict | None = None, now=None):
    now = now or timezone.now()
    asset = _order_primary_asset(order)
    updates = {}
    if asset_updates:
        updates.update(drop_asset_note_update(asset_updates))
    if server_updates:
        updates.update(_asset_update_from_server_fields(server_updates))
    if asset and updates:
        updates.setdefault('updated_at', now)
        CloudAsset.objects.filter(id=asset.id).update(**updates)
    return asset, None


def _ensure_order_asset_expiry_record(order: CloudServerOrder | None, expires_at, *, status: str = CloudAsset.STATUS_PENDING):
    if not order or not getattr(order, 'id', None) or not expires_at:
        return None
    asset = _order_primary_asset(order)
    defaults = {
        'kind': CloudAsset.KIND_SERVER,
        'source': CloudAsset.SOURCE_ORDER,
        'provider': getattr(order, 'provider', None),
        'cloud_account': getattr(order, 'cloud_account', None),
        'account_label': getattr(order, 'account_label', None) or getattr(order, 'provider', None),
        'region_code': getattr(order, 'region_code', None),
        'region_name': getattr(order, 'region_name', None),
        'asset_name': getattr(order, 'server_name', None) or getattr(order, 'order_no', None),
        'instance_id': getattr(order, 'instance_id', None),
        'provider_resource_id': getattr(order, 'provider_resource_id', None),
        'public_ip': getattr(order, 'public_ip', None),
        'previous_public_ip': getattr(order, 'previous_public_ip', None),
        'actual_expires_at': expires_at,
        'price': getattr(order, 'total_amount', None),
        'currency': getattr(order, 'currency', None) or 'USDT',
        'order': order,
        'user': getattr(order, 'user', None),
        'status': status,
        'is_active': status not in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED},
    }
    if asset:
        for key, value in defaults.items():
            if value is not None or key == 'actual_expires_at':
                setattr(asset, key, value)
        asset.save()
        return asset
    return CloudAsset.objects.create(**defaults)


def _maybe_tg_user_id(value) -> int | None:
    text = str(value or '').strip()
    if not text or not text.isdigit() or _is_asset_date_token(text):
        return None
    try:
        user_id = int(text)
    except (TypeError, ValueError):
        return None
    return user_id if user_id > 0 else None


def _active_cloud_account_asset_filter():
    active_labels = []
    inactive_labels = []
    for account in CloudAccountConfig.objects.filter(
        provider__in=[CloudAccountConfig.PROVIDER_AWS, CloudAccountConfig.PROVIDER_ALIYUN],
    ):
        labels = cloud_account_label_variants(account)
        if account.is_active:
            active_labels.extend(labels)
        else:
            inactive_labels.extend(labels)
    active_labels = list(dict.fromkeys(active_labels))
    inactive_labels = list(dict.fromkeys(inactive_labels))
    return (
        ~Q(cloud_account__is_active=False)
        & ~Q(account_label__in=inactive_labels)
        & (
            Q(cloud_account__is_active=True)
            | Q(account_label__in=active_labels)
            | Q(account_label__isnull=True)
            | Q(account_label='')
        )
    )


def _renew_aliyun_instance(order: CloudServerOrder, days: int = 31):
    if order.provider != 'aliyun_simple':
        return True, ''
    if not str(order.instance_id or '').strip():
        return False, '阿里云实例ID缺失，无法执行真实续费'
    account = getattr(order, 'cloud_account', None)
    if not account or not getattr(account, 'is_active', False):
        return False, '缺少订单绑定的启用阿里云账号，拒绝回退默认账号执行真实续费'
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
    account = getattr(order, 'cloud_account', None) or get_cloud_account_from_label(getattr(order, 'account_label', ''), 'aws')
    if not account:
        raise ValueError('缺少绑定的 AWS 云账号，拒绝回退默认账号执行实例启动检查。')
    if not getattr(account, 'is_active', False):
        raise ValueError(f'AWS 云账号#{getattr(account, "id", "-")}已停用，拒绝执行实例启动检查。')
    access_key = (getattr(account, 'access_key_plain', '') or '').strip() if account else ''
    secret_key = (getattr(account, 'secret_key_plain', '') or '').strip() if account else ''
    if not access_key or not secret_key or len(access_key) < 16 or len(secret_key) < 36:
        raise ValueError(f'AWS 云账号#{getattr(account, "id", "-")}凭据缺失或疑似截断，拒绝执行实例启动检查。')
    import boto3
    return boto3.client(
        'lightsail',
        region_name=order.region_code or getattr(account, 'region_hint', None) or 'ap-southeast-1',
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
    )


def _cloud_status_from_aws_state(state: str) -> str:
    state = (state or '').lower().strip()
    mapping = {
        'running': CloudAsset.STATUS_RUNNING,
        'pending': CloudAsset.STATUS_STARTING,
        'starting': CloudAsset.STATUS_STARTING,
        'stopping': CloudAsset.STATUS_STOPPING,
        'stopped': CloudAsset.STATUS_STOPPED,
        'shutting-down': CloudAsset.STATUS_STOPPING,
        'terminated': CloudAsset.STATUS_TERMINATED,
    }
    return mapping.get(state, CloudAsset.STATUS_UNKNOWN)


def _sync_order_cloud_runtime_state(order: CloudServerOrder, state: str, public_ip: str = '', note: str = ''):
    if not order:
        return
    status = _cloud_status_from_aws_state(state)
    updates = {
        'status': status,
        'provider_status': state or status,
        'is_active': status not in {CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_DELETED},
    }
    if public_ip and public_ip != '-':
        updates['public_ip'] = public_ip
    _update_order_primary_records(order, asset_updates=updates, server_updates=updates)


AWS_START_WAIT_ATTEMPTS = 10
AWS_START_WAIT_SECONDS = 10


def _aws_lightsail_instance_state(client, server_name: str) -> tuple[str, str]:
    instance = client.get_instance(instanceName=server_name).get('instance') or {}
    state = ((instance.get('state') or {}).get('name') or '').lower()
    public_ip = instance.get('publicIpAddress') or '-'
    return state, public_ip


def _aws_instance_name_by_ip(client, public_ip: str) -> str:
    public_ip = str(public_ip or '').strip()
    if not public_ip:
        return ''
    token = None
    while True:
        kwargs = {'pageToken': token} if token else {}
        response = client.get_instances(**kwargs)
        for item in response.get('instances') or []:
            if str(item.get('publicIpAddress') or '').strip() == public_ip:
                return str(item.get('name') or '').strip()
        token = response.get('nextPageToken')
        if not token:
            return ''


def _aws_instance_name_for_order_runtime(client, order: CloudServerOrder) -> str:
    for public_ip in [getattr(order, 'public_ip', None), getattr(order, 'previous_public_ip', None)]:
        resolved = _aws_instance_name_by_ip(client, public_ip)
        if resolved:
            return resolved
    return str(getattr(order, 'server_name', '') or '').strip()


def _start_aws_instance_after_shutdown(client, order: CloudServerOrder, state: str, public_ip: str, *, log_tag: str, instance_name: str = '') -> tuple[bool, str, str, str]:
    current_state = (state or '').lower()
    current_ip = public_ip or order.public_ip or order.previous_public_ip or '-'
    target_name = instance_name or _aws_instance_name_for_order_runtime(client, order)
    if not target_name:
        return False, f'云端正在关机，但按 IP 未找到实例且缺少实例名，无法开机。IP={current_ip}。', current_state, current_ip
    for attempt in range(1, AWS_START_WAIT_ATTEMPTS + 1):
        time.sleep(AWS_START_WAIT_SECONDS)
        current_state, queried_ip = _aws_lightsail_instance_state(client, target_name)
        current_ip = queried_ip if queried_ip != '-' else current_ip
        if current_state == 'stopped':
            client.start_instance(instanceName=target_name)
            logger.info('%s order=%s server_name=%s previous_state=%s wait_attempt=%s ip=%s', log_tag, order.order_no, target_name, current_state, attempt, current_ip)
            return True, f'云端正在关机，等待第 {attempt}/{AWS_START_WAIT_ATTEMPTS} 次后变为 stopped，已立即发起开机。IP={current_ip}。', 'starting', current_ip
        if current_state == 'running':
            return True, f'云端正在关机，等待第 {attempt}/{AWS_START_WAIT_ATTEMPTS} 次后已恢复 running。IP={current_ip}。', current_state, current_ip
        if current_state in {'pending', 'starting'}:
            return True, f'云端正在关机，等待第 {attempt}/{AWS_START_WAIT_ATTEMPTS} 次后已进入启动中状态 {current_state}。IP={current_ip}。', current_state, current_ip
        if current_state in {'terminated', 'deleted'}:
            return False, f'云端正在关机，等待第 {attempt}/{AWS_START_WAIT_ATTEMPTS} 次后变为 {current_state}，无法开机。IP={current_ip}。', current_state, current_ip
    return False, f'云端仍处于 {current_state or "未知"}，已延时检查 {AWS_START_WAIT_ATTEMPTS} 次仍未关机完成，暂无法开机。IP={current_ip}。', current_state, current_ip


def _ensure_aws_instance_running(order: CloudServerOrder) -> tuple[bool, str]:
    if order.provider != 'aws_lightsail':
        return True, ''
    try:
        client = _aws_lightsail_client_for_order(order)
        instance_name = _aws_instance_name_for_order_runtime(client, order)
        if not instance_name:
            return False, 'AWS 实例启动检查失败：按 IP 未找到实例，且缺少实例名。'
        instance = client.get_instance(instanceName=instance_name).get('instance') or {}
        state = ((instance.get('state') or {}).get('name') or '').lower()
        public_ip = instance.get('publicIpAddress') or order.public_ip or order.previous_public_ip or '-'
        if state == 'running':
            note = f'AWS 实例续费后检查：运行中；IP={public_ip}。'
            _sync_order_cloud_runtime_state(order, state, public_ip, note)
            return True, note
        if state == 'stopped':
            client.start_instance(instanceName=instance_name)
            note = f'AWS 实例续费后检查：检测到关机状态 stopped，已发起开机；IP={public_ip}。'
            _sync_order_cloud_runtime_state(order, 'starting', public_ip, note)
            logger.info('CLOUD_RENEW_START_INSTANCE order=%s server_name=%s previous_state=%s ip=%s', order.order_no, instance_name, state, public_ip)
            return True, note
        if state in {'pending', 'starting'}:
            note = f'AWS 实例续费后检查：实例正在启动中，暂不重复开机；云端状态={state}；IP={public_ip}。'
            _sync_order_cloud_runtime_state(order, state, public_ip, note)
            return False, note
        if state in {'stopping', 'shutting-down'}:
            ok, wait_note, sync_state, public_ip = _start_aws_instance_after_shutdown(client, order, state, public_ip, log_tag='CLOUD_RENEW_WAIT_SHUTDOWN_START_INSTANCE', instance_name=instance_name)
            note = f'AWS 实例续费后检查：初始状态 {state}；{wait_note}'
            _sync_order_cloud_runtime_state(order, sync_state, public_ip, note)
            return ok, note
        note = f'AWS 实例续费后检查：当前状态 {state or "未知"}，未执行开机；IP={public_ip}。'
        _sync_order_cloud_runtime_state(order, state, public_ip, note)
        return False, note
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
    ports = [str(port) for port in get_mtproxy_public_ports(main_port or MTPROXY_DEFAULT_PORT)]
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


def _probe_mtproxy_public_ports(ip: str, main_port: int, timeout: float = 3.0) -> tuple[bool, str]:
    ports = get_mtproxy_public_ports(main_port)
    open_ports = []
    closed_ports = []
    for port in ports:
        try:
            with socket.create_connection((ip, int(port)), timeout=timeout):
                open_ports.append(str(port))
        except OSError:
            closed_ports.append(str(port))
    if open_ports:
        return True, f'MTProxy 公网端口可连接: {", ".join(open_ports)}。'
    return False, f'MTProxy 公网端口暂不可连接: {", ".join(closed_ports)}。'


def _ensure_mtproxy_after_renewal(order: CloudServerOrder) -> tuple[bool, str]:
    if not order.public_ip:
        return True, '缺少公网 IP，跳过 MTProxy 运行检查。'
    ok, note = _probe_mtproxy_public_ports(order.public_ip, order.mtproxy_port or MTPROXY_DEFAULT_PORT)
    if ok:
        return True, note
    logger.warning('CLOUD_MTPROXY_RENEWAL_PROBE_FAILED order=%s ip=%s reason=%s', order.order_no, order.public_ip, note)
    return False, f'{note}\n续费不会自动重装服务器；如代理确实不可用，请手动点击“重新安装”。'

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
            defaults = {
                'region_name': region_name,
                'config_id': _build_server_price_config_id(provider, region_code, index),
                'server_name': server_name,
                'server_description': f'{cpu} / {memory} / {storage} / {bandwidth}',
                'cpu': cpu,
                'memory': memory,
                'storage': storage,
                'bandwidth': bandwidth,
                'cost_price': price,
                'currency': 'USDT',
                'is_active': True,
                'sort_order': 100 - index,
            }
            server_price, created = ServerPrice.objects.get_or_create(
                provider=provider,
                region_code=region_code,
                bundle_code=bundle_code,
                defaults={**defaults, 'price': price},
            )
            if not created:
                for field, value in defaults.items():
                    setattr(server_price, field, value)
                server_price.save(update_fields=[*defaults.keys(), 'updated_at'])


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
    while CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, instance_id=candidate).exists() or CloudServerOrder.objects.filter(server_name=candidate).exists():
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
def prepare_cloud_server_order_instances(order_id: int, user_id: int, port: int):
    with transaction.atomic():
        order = CloudServerOrder.objects.select_for_update().filter(id=order_id, user_id=user_id).first()
        if not order:
            return []
        if order.status not in ['paid']:
            logger.warning('云服务器默认端口创建被拒绝: order=%s user=%s port=%s status=%s', order.order_no, user_id, port, order.status)
            return []
        quantity = max(1, int(order.quantity or 1))
        order.mtproxy_port = port
        if quantity <= 1:
            order.provision_note = append_note(order.provision_note, f'使用默认端口 {port}，开始创建服务器。')
            order.save(update_fields=['mtproxy_port', 'provision_note', 'updated_at'])
            logger.info('云服务器默认端口创建提交: order=%s user=%s port=%s quantity=1', order.order_no, user_id, port)
            return [order]

        per_total = (Decimal(order.total_amount or 0) / Decimal(quantity)).quantize(Decimal('0.000001'))
        per_pay = (Decimal(order.pay_amount or 0) / Decimal(quantity)).quantize(Decimal('0.000000001')) if order.pay_amount is not None else None
        original_order_no = order.order_no
        created_orders = [order]
        order.quantity = 1
        order.total_amount = per_total
        order.pay_amount = per_pay
        order.provision_note = append_note(order.provision_note, f'批量订单 {original_order_no} 已拆分：第 1/{quantity} 台，默认端口 {port}，开始创建服务器。')
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
                provision_note=f'批量订单 {original_order_no} 已拆分：第 {index}/{quantity} 台，默认端口 {port}，开始创建服务器。',
            )
            created_orders.append(clone)
        logger.info('云服务器批量订单默认端口拆分完成: original_order=%s user=%s quantity=%s port=%s child_orders=%s', original_order_no, user_id, quantity, port, [item.order_no for item in created_orders])
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
    requested_currency = str(currency or 'USDT').upper()
    if requested_currency != 'USDT':
        logger.warning('云服务器地址支付仅支持 USDT，已忽略请求币种: user=%s plan=%s requested_currency=%s', user_id, plan_id, requested_currency)
    currency = 'USDT'
    plan = CloudServerPlan.objects.get(id=plan_id, is_active=True)
    user = TelegramUser.objects.get(id=user_id)
    quantity = _normalize_cloud_order_quantity(quantity)
    discounted_total_usdt = (_apply_cloud_discount(Decimal(plan.price), user.cloud_discount_rate) * quantity).quantize(Decimal('0.01'))
    pay_amount = _generate_unique_pay_amount(discounted_total_usdt, currency)
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
        total_amount=discounted_total_usdt,
        pay_amount=pay_amount,
        pay_method='address',
        status='pending',
        mtproxy_port=MTPROXY_DEFAULT_PORT,
        expired_at=expired_at,
    )
    CartItem.objects.filter(user_id=user_id, item_type='cloud_plan', cloud_plan_id=plan_id).delete()
    logger.info('云服务器订单创建: order=%s user=%s region=%s plan=%s qty=%s pay=address amount=%s', order.order_no, user_id, plan.region_code, plan.plan_name, quantity, pay_amount)
    return order


@sync_to_async
def buy_cloud_server_with_balance(user_id: int, plan_id: int, currency: str = 'USDT', quantity: int = 1):
    plan = CloudServerPlan.objects.get(id=plan_id, is_active=True)
    quantity = _normalize_cloud_order_quantity(quantity)
    with transaction.atomic():
        user = TelegramUser.objects.select_for_update().get(id=user_id)
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
            total_amount=discounted_total_usdt,
            pay_amount=total,
            pay_method='balance',
            status='paid',
            mtproxy_port=MTPROXY_DEFAULT_PORT,
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
    with transaction.atomic():
        order = CloudServerOrder.objects.select_for_update().select_related('plan').filter(id=order_id, user_id=user_id).first()
        if not order or order.status != 'pending':
            return None, '订单不存在或状态不可支付'
        payable_usdt = Decimal(str(order.total_amount or order.pay_amount or 0))
        total = async_to_sync(usdt_to_trx)(payable_usdt) if currency == 'TRX' else payable_usdt
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
_INACTIVE_ORDER_STATUSES = {'deleted', 'deleting', 'expired', 'cancelled'}
_ASSET_RENEWAL_MARKER = '未绑定代理资产续费'
_ASSET_TG_USER_ID_RE = re.compile(r'(?<!\d)(\d{5,20})(?!\d)')


def is_cloud_asset_renewal_order(order: CloudServerOrder | None) -> bool:
    if not order:
        return False
    if _ASSET_RENEWAL_MARKER not in str(getattr(order, 'provision_note', '') or ''):
        return False
    if (
        str(getattr(order, 'instance_id', '') or '').strip()
        and getattr(order, 'service_started_at', None)
        and order_asset_expiry(order)
    ):
        return False
    return True


def _first_nonblank(*values) -> str:
    for value in values:
        text = str(value or '').strip()
        if text:
            return text
    return ''


def _is_manual_authoritative_provider(provider: str | None) -> bool:
    return str(provider or '').strip() == CloudServerPlan.PROVIDER_AWS_LIGHTSAIL


def _hydrate_order_from_proxy_asset(order: CloudServerOrder | None, asset: CloudAsset | None = None, server=None):
    if not order:
        return order
    if asset is None:
        asset = _order_primary_asset(order)
        if asset and getattr(asset, 'kind', None) != CloudAsset.KIND_SERVER:
            asset = None
        if asset and getattr(asset, 'status', None) in _INACTIVE_ASSET_STATUSES:
            asset = None
        if asset is None:
            asset = (
                CloudAsset.objects.filter(order=order, kind=CloudAsset.KIND_SERVER)
                .exclude(status__in=_INACTIVE_ASSET_STATUSES)
                .order_by('-updated_at', '-id')
                .first()
            )
    public_ip = _first_nonblank(getattr(asset, 'public_ip', None), order.public_ip)
    previous_ip = _first_nonblank(getattr(asset, 'previous_public_ip', None), order.previous_public_ip)
    current_order_expires_at = order_asset_expiry(order)
    if _is_manual_authoritative_provider(getattr(order, 'provider', None)) and current_order_expires_at:
        expires_at = current_order_expires_at
    else:
        expiry_candidates = [
            item for item in (
                getattr(asset, 'actual_expires_at', None),
                current_order_expires_at,
            ) if item
        ]
        expires_at = max(expiry_candidates) if expiry_candidates else None
    if public_ip:
        order.public_ip = public_ip
    if previous_ip:
        order.previous_public_ip = previous_ip
    if asset:
        if getattr(asset, 'price', None) is not None:
            order.renewal_price = Decimal(str(asset.price)).quantize(Decimal('0.01'))
        order.mtproxy_link = getattr(asset, 'mtproxy_link', None) or order.mtproxy_link
        order.proxy_links = getattr(asset, 'proxy_links', None) or order.proxy_links
        order.mtproxy_secret = getattr(asset, 'mtproxy_secret', None) or order.mtproxy_secret
        order.mtproxy_host = getattr(asset, 'mtproxy_host', None) or order.mtproxy_host
        order.mtproxy_port = getattr(asset, 'mtproxy_port', None) or order.mtproxy_port
        order.login_password = getattr(asset, 'login_password', None) or order.login_password
        order.instance_id = getattr(asset, 'instance_id', None) or order.instance_id
        if _is_unattached_static_ip_asset(asset) and getattr(asset, 'actual_expires_at', None):
            order.ip_recycle_at = order.ip_recycle_at or asset.actual_expires_at
            order.static_ip_name = order.static_ip_name or getattr(asset, 'asset_name', '')
        if not order.cloud_account_id:
            order.cloud_account = getattr(asset, 'cloud_account', None) or get_cloud_account_from_label(getattr(asset, 'account_label', ''), order.provider)
        order.account_label = order.account_label or getattr(asset, 'account_label', None) or cloud_account_label(getattr(order, 'cloud_account', None))
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
        region_name=asset.region_name or getattr(order, 'region_name', None) or '-',
        region_code=asset.region_code or getattr(order, 'region_code', None) or '',
        plan_name=asset.asset_name or getattr(order, 'plan_name', None) or '人工代理',
        quantity=1,
        status=asset.status,
        provider_status=asset.provider_status,
        actual_expires_at=asset.actual_expires_at,
        provider_resource_id=asset.provider_resource_id,
        provider=asset.provider or getattr(order, 'provider', None),
        account_label=asset.account_label or getattr(order, 'account_label', None),
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
        user_id=asset.user_id,
        user_tg_id=getattr(getattr(asset, 'user', None), 'tg_user_id', None),
        username=getattr(getattr(asset, 'user', None), 'primary_username', ''),
        first_name=getattr(getattr(asset, 'user', None), 'first_name', '') or '',
        created_at=asset.created_at,
        note=asset.note,
        get_status_display=lambda: asset.get_status_display(),
    )


def _is_asset_date_token(value: str) -> bool:
    text = str(value or '').strip()
    if len(text) != 8 or not text.isdigit() or not text.startswith(('19', '20')):
        return False
    try:
        year = int(text[:4])
        month = int(text[4:6])
        day = int(text[6:8])
    except ValueError:
        return False
    return 1900 <= year <= 2099 and 1 <= month <= 12 and 1 <= day <= 31


def _asset_identity_texts(asset: CloudAsset | None) -> list[str]:
    if not asset:
        return []
    order = getattr(asset, 'order', None)
    values = [
        getattr(asset, 'asset_name', None),
        getattr(asset, 'instance_id', None),
        getattr(asset, 'provider_resource_id', None),
        getattr(asset, 'mtproxy_host', None),
        getattr(order, 'order_no', None),
        getattr(order, 'server_name', None),
        getattr(order, 'last_user_id', None),
    ]
    result = []
    seen = set()
    for value in values:
        text = str(value or '').strip()
        if not text or text in seen:
            continue
        result.append(text)
        seen.add(text)
    return result


def _asset_identity_tg_user_ids(asset: CloudAsset | None) -> list[int]:
    result = []
    seen = set()
    order = getattr(asset, 'order', None)
    direct_user_id = _maybe_tg_user_id(getattr(order, 'last_user_id', None))
    if direct_user_id:
        result.append(direct_user_id)
        seen.add(direct_user_id)
    for text in _asset_identity_texts(asset):
        for match in _ASSET_TG_USER_ID_RE.findall(text):
            if _is_asset_date_token(match):
                continue
            try:
                value = int(match)
            except (TypeError, ValueError):
                continue
            if value <= 0 or value in seen:
                continue
            result.append(value)
            seen.add(value)
    return result


def _asset_identity_username_tokens(asset: CloudAsset | None) -> list[str]:
    result = []
    seen = set()
    ignored = {'aws', 'aliyun', 'staticip', 'lightsail', 'server'}
    for text in _asset_identity_texts(asset):
        for token in re.findall(r'[A-Za-z][A-Za-z0-9_]{2,63}', text):
            normalized = token.strip().lstrip('@')
            key = normalized.lower()
            if not normalized or key in seen or key in ignored:
                continue
            result.append(normalized)
            seen.add(key)
    return result


def _merge_telegram_user_from_login_account(account: TelegramLoginAccount | None) -> TelegramUser | None:
    if not account or not getattr(account, 'tg_user_id', None):
        return None
    usernames = TelegramUser.normalize_usernames(getattr(account, 'username', None))
    serialized_usernames = TelegramUser.serialize_usernames(usernames)
    first_name = str(getattr(account, 'label', '') or '').strip()
    user, _ = TelegramUser.objects.get_or_create(
        tg_user_id=account.tg_user_id,
        defaults={
            'username': serialized_usernames,
            'first_name': first_name[:191],
        },
    )
    changed = []
    if usernames:
        merged = []
        seen = set()
        for item in [*user.usernames, *usernames]:
            key = item.lower()
            if item and key not in seen:
                merged.append(item)
                seen.add(key)
        next_usernames = TelegramUser.serialize_usernames(merged)
        if user.username != next_usernames:
            user.username = next_usernames
            changed.append('username')
    if first_name and user.first_name != first_name[:191]:
        user.first_name = first_name[:191]
        changed.append('first_name')
    if changed:
        changed.append('updated_at')
        user.save(update_fields=changed)
    return user


def _resolve_cloud_asset_identity_user(asset: CloudAsset | None) -> TelegramUser | None:
    if not asset:
        return None
    if getattr(asset, 'user_id', None):
        return getattr(asset, 'user', None) or TelegramUser.objects.filter(id=asset.user_id).first()
    order = getattr(asset, 'order', None)
    if getattr(order, 'user_id', None):
        return getattr(order, 'user', None) or TelegramUser.objects.filter(id=order.user_id).first()

    ips = [value for value in [asset.public_ip, asset.previous_public_ip] if value]
    trace_query = Q(asset=asset)
    if ips:
        trace_query |= Q(public_ip__in=ips) | Q(previous_public_ip__in=ips)
    trace = (
        CloudIpLog.objects.select_related('user')
        .filter(trace_query)
        .filter(user__isnull=False)
        .order_by('-id')
        .first()
    )
    if trace and trace.user_id:
        return trace.user

    for tg_user_id in _asset_identity_tg_user_ids(asset):
        user = TelegramUser.objects.filter(tg_user_id=tg_user_id).first()
        if user:
            return user
        account = TelegramLoginAccount.objects.filter(tg_user_id=tg_user_id).order_by('-updated_at', '-id').first()
        user = _merge_telegram_user_from_login_account(account)
        if user:
            return user

    for username in _asset_identity_username_tokens(asset):
        username_key = username.lower()
        candidates = list(TelegramUser.objects.filter(username__icontains=username).order_by('-updated_at', '-id')[:20])
        for candidate in candidates:
            if username_key in {item.lower() for item in candidate.usernames}:
                return candidate
        accounts = list(TelegramLoginAccount.objects.filter(username__icontains=username).exclude(tg_user_id__isnull=True).order_by('-updated_at', '-id')[:20])
        for account in accounts:
            if username_key in {item.lower() for item in TelegramUser.normalize_usernames(account.username)}:
                user = _merge_telegram_user_from_login_account(account)
                if user:
                    return user
    return None


def sync_cloud_asset_user_binding(asset: CloudAsset | None, *, persist: bool = True) -> TelegramUser | None:
    user = _resolve_cloud_asset_identity_user(asset)
    if not asset or not user:
        return user
    if getattr(asset, 'user_id', None) != user.id:
        asset.user = user
        asset.user_id = user.id
        if persist:
            now = timezone.now()
            CloudAsset.objects.filter(id=asset.id).update(user=user, updated_at=now)
    return user


def _user_bound_group_ids(user_id: int) -> list[int]:
    return list(
        CloudAsset.objects
        .filter(kind=CloudAsset.KIND_SERVER, user_id=user_id, telegram_group_id__isnull=False)
        .values_list('telegram_group_id', flat=True)
        .distinct()
    )


def _user_asset_visibility_filter(user_id: int):
    group_ids = _user_bound_group_ids(user_id)
    visibility = Q(user_id=user_id)
    if group_ids:
        visibility |= Q(telegram_group_id__in=group_ids)
    return visibility


def _cloud_server_asset_queryset():
    ip_filter = Q(public_ip__isnull=False) & ~Q(public_ip='')
    active_order_filter = Q(order__isnull=True) | ~Q(order__status__in=_INACTIVE_ORDER_STATUSES)
    return (
        CloudAsset.objects.select_related('order', 'user', 'telegram_group')
        .filter(kind=CloudAsset.KIND_SERVER)
        .filter(ip_filter)
        .filter(active_order_filter)
        .filter(_active_cloud_account_asset_filter())
        .exclude(status__in=_INACTIVE_ASSET_STATUSES)
    )


def _group_filter_for_chat_id(chat_id: int):
    from bot.models import TelegramGroupFilter
    return TelegramGroupFilter.objects.filter(chat_id=chat_id, enabled=True).first()


@sync_to_async
def list_user_cloud_servers(user_id: int):
    assets = (
        _cloud_server_asset_queryset()
        .filter(_user_asset_visibility_filter(user_id))
        .order_by('-sort_order', 'actual_expires_at', '-updated_at', '-id')
    )
    return [_proxy_asset_view(asset) for asset in assets]


@sync_to_async
def list_group_cloud_servers(chat_id: int):
    group = _group_filter_for_chat_id(chat_id)
    if not group:
        return []
    assets = (
        _cloud_server_asset_queryset()
        .filter(telegram_group=group)
        .order_by('-sort_order', 'actual_expires_at', '-updated_at', '-id')
    )
    return [_proxy_asset_view(asset) for asset in assets]


@sync_to_async
def is_retained_ip_order_visible_in_group(order_id: int, chat_id: int) -> bool:
    group = _group_filter_for_chat_id(chat_id)
    if not group:
        return False
    asset = (
        CloudAsset.objects.select_related('order', 'order__user', 'order__plan', 'user', 'cloud_account')
        .filter(kind=CloudAsset.KIND_SERVER, order_id=order_id, telegram_group=group)
        .order_by('-updated_at', '-id')
        .first()
    )
    if not asset or not asset.order_id or not _is_retained_static_ip_asset(asset):
        return False
    order = _hydrate_order_from_proxy_asset(asset.order)
    return bool(_is_retained_ip_renewal_candidate(order) and _can_order_be_renewed(order))


@sync_to_async
def list_user_auto_renew_cloud_servers(user_id: int):
    assets = (
        _cloud_server_asset_queryset()
        .filter(_user_asset_visibility_filter(user_id))
        .order_by('-sort_order', 'actual_expires_at', '-updated_at', '-id')
    )
    return [_proxy_asset_view(asset) for asset in assets if not _is_unattached_static_ip_asset(asset)]


@sync_to_async
def list_group_auto_renew_cloud_servers(chat_id: int):
    group = _group_filter_for_chat_id(chat_id)
    if not group:
        return []
    assets = (
        _cloud_server_asset_queryset()
        .filter(telegram_group=group)
        .order_by('-sort_order', 'actual_expires_at', '-updated_at', '-id')
    )
    return [_proxy_asset_view(asset) for asset in assets if not _is_unattached_static_ip_asset(asset)]


@sync_to_async
def list_all_auto_renew_cloud_servers():
    ip_filter = Q(public_ip__isnull=False) & ~Q(public_ip='')
    active_order_filter = Q(order__isnull=True) | ~Q(order__status__in=_INACTIVE_ORDER_STATUSES)
    assets = (
        CloudAsset.objects.select_related('order', 'user')
        .filter(kind=CloudAsset.KIND_SERVER)
        .filter(ip_filter)
        .filter(active_order_filter)
        .filter(_active_cloud_account_asset_filter())
        .exclude(status__in=_INACTIVE_ASSET_STATUSES)
        .order_by('-sort_order', 'user_id', 'actual_expires_at', '-updated_at', '-id')
    )
    return [_proxy_asset_view(asset) for asset in assets if not _is_unattached_static_ip_asset(asset)]


@sync_to_async
def get_user_cloud_server(order_id: int, user_id: int):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    return _hydrate_order_from_proxy_asset(order)


@sync_to_async
def get_cloud_server_for_admin(order_id: int):
    order = CloudServerOrder.objects.filter(id=order_id).first()
    return _hydrate_order_from_proxy_asset(order)


@sync_to_async
def get_user_proxy_asset_detail(item_id: int, user_id: int, kind: str):
    asset = (
        _cloud_server_asset_queryset()
        .filter(id=item_id)
        .filter(_user_asset_visibility_filter(user_id))
        .first()
    )
    return _proxy_asset_view(asset) if asset else None


@sync_to_async
def get_group_proxy_asset_detail(item_id: int, chat_id: int, kind: str):
    if kind == 'server':
        return None
    group = _group_filter_for_chat_id(chat_id)
    if not group:
        return None
    asset = _cloud_server_asset_queryset().filter(id=item_id, telegram_group=group).first()
    return _proxy_asset_view(asset) if asset else None


@sync_to_async
def get_proxy_asset_detail_for_admin(item_id: int, kind: str = 'asset'):
    active_order_filter = Q(order__isnull=True) | ~Q(order__status__in=_INACTIVE_ORDER_STATUSES)
    asset = CloudAsset.objects.filter(id=item_id, kind=CloudAsset.KIND_SERVER).filter(active_order_filter).exclude(status__in=_INACTIVE_ASSET_STATUSES).first()
    return _proxy_asset_view(asset) if asset else None


@sync_to_async
def get_proxy_asset_by_ip_for_admin(ip: str):
    normalized_ip = (ip or '').strip()
    if not normalized_ip:
        return None
    ip_q = Q(public_ip=normalized_ip) | Q(previous_public_ip=normalized_ip)
    assets = (
        CloudAsset.objects.filter(ip_q, kind=CloudAsset.KIND_SERVER)
        .exclude(status__in=_INACTIVE_ASSET_STATUSES)
        .select_related('order', 'user')
        .order_by('-updated_at', '-id')[:10]
    )
    asset = next((item for item in assets if not _cloud_asset_deleted_or_missing(item)), None)
    if not asset:
        return None
    view = _proxy_asset_view(asset)
    view.matched_query_ip = normalized_ip
    return view


@sync_to_async
def get_proxy_asset_by_ip_for_user(ip: str, user_id: int):
    normalized_ip = (ip or '').strip()
    if not normalized_ip:
        return None
    ip_q = Q(public_ip=normalized_ip) | Q(previous_public_ip=normalized_ip)
    assets = (
        CloudAsset.objects.filter(ip_q, kind=CloudAsset.KIND_SERVER, user_id=user_id)
        .filter(_active_cloud_account_asset_filter())
        .exclude(status__in=_INACTIVE_ASSET_STATUSES)
        .select_related('order', 'user')
        .order_by('-updated_at', '-id')[:10]
    )
    asset = next((item for item in assets if not _cloud_asset_deleted_or_missing(item)), None)
    if not asset:
        return None
    view = _proxy_asset_view(asset)
    view.matched_query_ip = normalized_ip
    return view


def _update_cloud_order_expiry(order: CloudServerOrder, expires_at, *, now=None, note: str = ''):
    now = now or timezone.now()
    lifecycle = compute_order_lifecycle_fields(expires_at)
    for field, value in lifecycle.items():
        setattr(order, field, value)
    order.renew_notice_sent_at = None
    order.auto_renew_notice_sent_at = None
    order.auto_renew_failure_notice_sent_at = None
    order.delete_notice_sent_at = None
    order.recycle_notice_sent_at = None
    if note:
        order.provision_note = append_note(order.provision_note, f'{note}：{expires_at:%Y-%m-%d %H:%M:%S}')
    order.save(update_fields=[
        'renew_grace_expires_at', 'suspend_at', 'delete_at', 'ip_recycle_at',
        'renew_notice_sent_at', 'auto_renew_notice_sent_at', 'auto_renew_failure_notice_sent_at',
        'delete_notice_sent_at', 'recycle_notice_sent_at', 'provision_note', 'updated_at',
    ])
    _update_order_primary_records(
        order,
        asset_updates={'actual_expires_at': expires_at, 'updated_at': now},
        server_updates={'expires_at': expires_at, 'updated_at': now},
        now=now,
    )
    record_cloud_ip_log(
        event_type='changed',
        order=order,
        public_ip=order.public_ip,
        previous_public_ip=order.previous_public_ip,
        note=f'{note or "管理员修改到期时间"}：{expires_at:%Y-%m-%d %H:%M:%S}',
    )
    transaction.on_commit(lambda: _refresh_dashboard_plan_snapshots_after_service_change(f'bot_admin_expiry:{order.id}'))
    return order


@sync_to_async
def update_cloud_item_expiry_for_admin(item_id: int, item_kind: str, expires_at):
    now = timezone.now()
    item_kind = str(item_kind or 'order').strip().lower()
    if item_kind == 'asset':
        asset = CloudAsset.objects.select_related('order', 'cloud_account').filter(id=item_id, kind=CloudAsset.KIND_SERVER).exclude(status__in=_INACTIVE_ASSET_STATUSES).first()
        if not asset:
            return None, '代理记录不存在'
        asset.actual_expires_at = expires_at
        asset.save(update_fields=['actual_expires_at', 'updated_at'])
        if asset.order_id:
            _update_cloud_order_expiry(asset.order, expires_at, now=now, note='管理员通过机器人修改到期时间')
        return _proxy_asset_view(CloudAsset.objects.select_related('order', 'user').get(id=asset.id)), None

    order = CloudServerOrder.objects.select_related('user', 'cloud_account').filter(id=item_id).first()
    if not order:
        return None, '订单不存在'
    _update_cloud_order_expiry(order, expires_at, now=now, note='管理员通过机器人修改到期时间')
    return _hydrate_order_from_proxy_asset(CloudServerOrder.objects.get(id=order.id)), None


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


def _strip_raw_proxy_link_lines(note: str | None) -> str:
    lines = []
    for raw_line in str(note or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if 'tg://proxy?' in line or 'socks5://' in line:
            continue
        if line.startswith(('TG链接:', '分享链接:', '扩展链接:', 'SOCKS5链接:')):
            continue
        lines.append(line)
    return '\n'.join(lines)


def _compact_asset_proxy_init_note(note: str, links: list[dict], main_port: int | str | None = None) -> str:
    if 'MTProxy 安装完成' not in str(note or '') and 'tg://proxy?' not in str(note or '') and 'socks5://' not in str(note or ''):
        return note
    mtproxy_ports = []
    socks5_port = ''
    for item in links or []:
        port = str(item.get('port') or '').strip()
        url = str(item.get('url') or '')
        if not port:
            continue
        if url.startswith('socks5://'):
            socks5_port = port
        elif port not in mtproxy_ports:
            mtproxy_ports.append(port)
    lines = [
        'MTProxy/SOCKS5 安装完成',
        f'主代理端口: {main_port or (mtproxy_ports[0] if mtproxy_ports else "-")}',
    ]
    extra_ports = [port for port in mtproxy_ports if str(port) != str(main_port or '')]
    if extra_ports:
        lines.append(f'备用/Telemt端口: {", ".join(extra_ports)}')
    if socks5_port:
        lines.append(f'SOCKS5端口: {socks5_port}')
    lines.append('代理链接已保存到代理链路列表。')
    return '\n'.join(lines)


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
    port = int(asset.mtproxy_port or MTPROXY_DEFAULT_PORT)
    guard_ok, guard_note = validate_server_connection_ip(public_ip, [asset.public_ip, asset.previous_public_ip, asset.mtproxy_host], context=f'initialize_asset:{asset.id}')
    if not guard_ok:
        return asset, guard_note
    bbr_ok, bbr_note = await install_bbr(public_ip, username, password, use_key_setup=asset.provider == 'aws_lightsail')
    mtproxy_ok, mtproxy_note = await install_mtproxy(public_ip, username, password, port, asset.mtproxy_secret or '', asset.mtproxy_secret or '')
    if not mtproxy_ok:
        return asset, 'MTProxy 安装失败，请查看后台日志'
    mtproxy_link, mtproxy_secret, mtproxy_host = _extract_asset_mtproxy_fields(mtproxy_note)
    links = []
    if mtproxy_link:
        links.append({'label': '主链路', 'url': mtproxy_link, 'port': port, 'secret': mtproxy_secret})
    for raw_link in re.findall(r'tg://proxy\?[^"\'\s<>]+', mtproxy_note or ''):
        if mtproxy_link and raw_link == mtproxy_link:
            continue
        links.append({'label': f'备用链路 {len(links)}', 'url': raw_link})
    for raw_link in re.findall(r'socks5://[^"\'\s<>]+', mtproxy_note or ''):
        parsed = urlparse(raw_link)
        links.append({'label': 'SOCKS5', 'url': raw_link, 'port': str(parsed.port or '')})
    asset.login_user = username
    asset.login_password = password
    asset.mtproxy_port = port
    asset.mtproxy_link = mtproxy_link or asset.mtproxy_link
    asset.mtproxy_secret = mtproxy_secret or asset.mtproxy_secret
    asset.mtproxy_host = mtproxy_host or public_ip
    asset.proxy_links = links or asset.proxy_links
    await sync_to_async(asset.save)(update_fields=['login_user', 'login_password', 'mtproxy_port', 'mtproxy_link', 'mtproxy_secret', 'mtproxy_host', 'proxy_links', 'updated_at'])
    return asset, None


@sync_to_async
def get_cloud_server_by_ip(ip: str):
    normalized_ip = (ip or '').strip()
    if not normalized_ip:
        return None
    ip_q = Q(public_ip=normalized_ip) | Q(previous_public_ip=normalized_ip)
    assets = (
        CloudAsset.objects.filter(ip_q)
        .exclude(status__in=_INACTIVE_ASSET_STATUSES)
        .select_related('order')
        .order_by('-updated_at', '-id')[:10]
    )
    asset = next((item for item in assets if not _cloud_asset_deleted_or_missing(item)), None)
    if asset and asset.order_id and asset.order and asset.order.status not in {'deleted', 'deleting', 'expired', 'cancelled'}:
        order = _hydrate_order_from_proxy_asset(asset.order, asset=asset)
        order.matched_query_ip = normalized_ip
        return order
    for order in CloudServerOrder.objects.filter(ip_q, status__in=_ACTIVE_ORDER_STATUSES).order_by('-created_at')[:10]:
        if not _order_primary_asset_unavailable(order):
            hydrated = _hydrate_order_from_proxy_asset(order)
            hydrated.matched_query_ip = normalized_ip
            return hydrated
    retained_order = _valid_retained_order_for_ip(ip_q)
    hydrated = _hydrate_order_from_proxy_asset(retained_order)
    if hydrated:
        hydrated.matched_query_ip = normalized_ip
    return hydrated


@sync_to_async
def get_cloud_server_by_ip_for_user(ip: str, user_id: int):
    normalized_ip = (ip or '').strip()
    if not normalized_ip:
        return None
    ip_q = Q(public_ip=normalized_ip) | Q(previous_public_ip=normalized_ip)
    assets = (
        CloudAsset.objects.filter(ip_q, user_id=user_id)
        .exclude(status__in=_INACTIVE_ASSET_STATUSES)
        .select_related('order')
        .order_by('-updated_at', '-id')[:10]
    )
    asset = next((item for item in assets if not _cloud_asset_deleted_or_missing(item)), None)
    if asset and asset.order_id and asset.order and asset.order.status not in {'deleted', 'deleting', 'expired', 'cancelled'}:
        order = _hydrate_order_from_proxy_asset(asset.order, asset=asset)
        order.matched_query_ip = normalized_ip
        return order
    for order in CloudServerOrder.objects.filter(ip_q, user_id=user_id, status__in=_ACTIVE_ORDER_STATUSES).order_by('-created_at')[:10]:
        if not _order_primary_asset_unavailable(order):
            hydrated = _hydrate_order_from_proxy_asset(order)
            hydrated.matched_query_ip = normalized_ip
            return hydrated
    retained_order = _valid_retained_order_for_ip(ip_q, user_id=user_id)
    hydrated = _hydrate_order_from_proxy_asset(retained_order)
    if hydrated:
        hydrated.matched_query_ip = normalized_ip
    return hydrated


def _order_primary_asset_unavailable(order: CloudServerOrder | None) -> bool:
    if not order:
        return True
    asset = _order_primary_asset(order)
    if asset and _cloud_asset_deleted_or_missing(asset):
        retained_alive = bool(
            order.status in {'completed', 'expiring', 'suspended', 'deleted', 'renew_pending'}
            and order.ip_recycle_at
            and order.ip_recycle_at > timezone.now()
            and _is_retained_static_ip_asset(asset)
        )
        if not retained_alive:
            return True
    active_retained_ip = bool(
        asset
        and order.ip_recycle_at
        and order.ip_recycle_at > timezone.now()
        and _is_retained_static_ip_asset(asset)
    )
    if (
        order.provider == CloudServerPlan.PROVIDER_AWS_LIGHTSAIL
        and str(order.static_ip_name or '').strip()
        and not str(order.instance_id or '').strip()
        and order.status in _ACTIVE_ORDER_STATUSES
        and not active_retained_ip
    ):
        return True
    return False


def _valid_retained_order_for_ip(ip_q, user_id: int | None = None):
    queryset = CloudServerOrder.objects.filter(
        ip_q,
        provider=CloudServerPlan.PROVIDER_AWS_LIGHTSAIL,
        status='deleted',
        ip_recycle_at__gt=timezone.now(),
    ).filter(Q(instance_id__isnull=True) | Q(instance_id=''))
    if user_id is not None:
        queryset = queryset.filter(user_id=user_id)
    for order in queryset.order_by('-ip_recycle_at', '-updated_at', '-id')[:10]:
        retained_asset = (
            CloudAsset.objects.filter(order=order, kind=CloudAsset.KIND_SERVER)
            .order_by('-updated_at', '-id')
            .first()
        )
        if retained_asset and _is_retained_static_ip_asset(retained_asset):
            return order
    return None


def _is_retained_static_ip_asset(asset: CloudAsset | None) -> bool:
    if not asset:
        return False
    provider_status = str(getattr(asset, 'provider_status', '') or '')
    note = str(getattr(asset, 'note', '') or '')
    return bool(
        getattr(asset, 'provider', None) == CloudServerPlan.PROVIDER_AWS_LIGHTSAIL
        and not str(getattr(asset, 'instance_id', '') or '').strip()
        and (
            '固定IP保留中' in provider_status
            or '固定 IP 保留' in provider_status
            or '固定IP保留中' in note
            or '固定 IP 保留' in note
            or _is_unattached_static_ip_asset(asset)
        )
    )


def _is_unattached_static_ip_asset(asset: CloudAsset | None) -> bool:
    if not asset:
        return False
    provider_status = str(getattr(asset, 'provider_status', '') or '')
    note = str(getattr(asset, 'note', '') or '')
    provider_resource_id = str(getattr(asset, 'provider_resource_id', '') or '')
    return bool(
        getattr(asset, 'provider', None) == CloudServerPlan.PROVIDER_AWS_LIGHTSAIL
        and not str(getattr(asset, 'instance_id', '') or '').strip()
        and (
            '未附加固定IP' in provider_status
            or '未附加IP' in provider_status
            or '固定IP保留中' in provider_status
            or '固定 IP 保留' in provider_status
            or '未附加固定IP' in note
            or '未附加IP' in note
            or '固定IP保留中' in note
            or '固定 IP 保留' in note
            or 'StaticIp' in provider_resource_id
        )
    )


def _resolve_asset_original_cloud_account(asset: CloudAsset | None):
    if not asset:
        return None
    account = getattr(asset, 'cloud_account', None)
    if account and getattr(account, 'is_active', False):
        return account
    return get_cloud_account_from_label(getattr(asset, 'account_label', ''), getattr(asset, 'provider', None))


def _resolve_unattached_aws_static_ip_name_for_asset(asset: CloudAsset | None) -> str:
    if not asset or getattr(asset, 'provider', None) != CloudServerPlan.PROVIDER_AWS_LIGHTSAIL:
        return ''
    public_ip = str(getattr(asset, 'public_ip', '') or getattr(asset, 'previous_public_ip', '') or '').strip()
    if not public_ip:
        return ''
    account = _resolve_asset_original_cloud_account(asset)
    access_key = ''
    secret_key = ''
    if account:
        ak = (account.access_key_plain or '').strip()
        sk = (account.secret_key_plain or '').strip()
        if ak and sk and len(ak) >= 16 and len(sk) >= 36:
            access_key, secret_key = ak, sk
    if not access_key or not secret_key:
        return ''
    try:
        import boto3
        client = boto3.client('lightsail', region_name=asset.region_code or 'ap-southeast-1', aws_access_key_id=access_key, aws_secret_access_key=secret_key)
        token = None
        while True:
            kwargs = {'pageToken': token} if token else {}
            response = client.get_static_ips(**kwargs)
            for item in response.get('staticIps') or []:
                if str(item.get('ipAddress') or '').strip() == public_ip and not str(item.get('attachedTo') or '').strip():
                    return str(item.get('name') or '').strip()
            token = response.get('nextPageToken')
            if not token:
                break
    except Exception as exc:
        logger.warning('AWS 未附加固定 IP 名称反查失败: asset=%s ip=%s error=%s', getattr(asset, 'id', None), public_ip, exc)
    return ''


def _cloud_asset_deleted_or_missing(asset: CloudAsset | None) -> bool:
    if not asset:
        return False
    status = getattr(asset, 'status', '')
    provider_status = str(getattr(asset, 'provider_status', '') or '')
    note = str(getattr(asset, 'note', '') or '')
    active_cloud_status = status in CloudAsset.ACTIVE_STATUSES or status in {CloudAsset.STATUS_STOPPED, CloudAsset.STATUS_SUSPENDED, CloudAsset.STATUS_UNKNOWN}
    note_marks_missing = '云上不存在' in note or '已标记删除' in note
    return bool(
        status in _INACTIVE_ASSET_STATUSES
        or '云上未找到' in provider_status
        or '已到期删除' in provider_status
        or '已删除' in provider_status
        or (note_marks_missing and not active_cloud_status)
    )


def _can_order_be_renewed(order: CloudServerOrder) -> bool:
    order = _hydrate_order_from_proxy_asset(order)
    has_ip = bool(str(order.public_ip or order.previous_public_ip or '').strip())
    if not has_ip or _order_primary_asset_unavailable(order):
        return False
    if order.status in {'completed', 'expiring', 'suspended', 'renew_pending', 'paid', 'provisioning'}:
        return True
    if order.status == 'deleted' and order.ip_recycle_at and order.ip_recycle_at > timezone.now():
        return True
    return False


def _is_retained_ip_renewal_candidate(order: CloudServerOrder | None) -> bool:
    if not order:
        return False
    order = _hydrate_order_from_proxy_asset(order)
    has_ip = bool(str(order.public_ip or order.previous_public_ip or '').strip())
    return bool(
        order.provider == CloudServerPlan.PROVIDER_AWS_LIGHTSAIL
        and order.status in {'completed', 'expiring', 'suspended', 'deleted', 'renew_pending'}
        and order.ip_recycle_at
        and order.ip_recycle_at > timezone.now()
        and has_ip
        and not str(order.instance_id or '').strip()
        and not _order_primary_asset_unavailable(order)
    )


def _operation_order_source_asset(order: CloudServerOrder) -> CloudAsset | None:
    note = str(getattr(order, 'provision_note', '') or '')
    match = re.search(r'来源资产\s*#(\d+)', note)
    if match:
        asset = CloudAsset.objects.filter(id=int(match.group(1))).first()
        if asset:
            return asset
    return (
        CloudAsset.objects.filter(
            Q(public_ip=order.public_ip) | Q(previous_public_ip=order.public_ip) | Q(public_ip=order.previous_public_ip) | Q(previous_public_ip=order.previous_public_ip),
            user_id=order.user_id,
            kind=CloudAsset.KIND_SERVER,
        )
        .exclude(status__in=_INACTIVE_ASSET_STATUSES)
        .order_by('-updated_at', '-id')
        .first()
    )


def _source_order_price(order: CloudServerOrder) -> Decimal | None:
    source = getattr(order, 'replacement_for', None)
    if not source:
        note = str(getattr(order, 'provision_note', '') or '')
        match = re.search(r'来源订单\s*#(\d+)', note)
        if match:
            source = CloudServerOrder.objects.filter(id=int(match.group(1))).first()
    if source and source.total_amount is not None:
        amount = Decimal(str(source.total_amount or 0))
        if amount > 0:
            return amount
    return None


def _manual_proxy_price(order: CloudServerOrder) -> Decimal | None:
    asset = None
    if str(getattr(order, 'order_no', '') or '').startswith('SRVMANUAL'):
        asset = _operation_order_source_asset(order)
    if not asset:
        asset = (
            CloudAsset.objects.filter(order=order, price__isnull=False)
            .exclude(status__in=_INACTIVE_ASSET_STATUSES)
            .order_by('-updated_at', '-id')
            .first()
        )
    if asset and asset.price is not None:
        return Decimal(str(asset.price))
    return None


class RenewalPriceMissingError(ValueError):
    pass


def _renewal_price(order: CloudServerOrder, user: TelegramUser | None = None) -> Decimal:
    transient_price = getattr(order, 'renewal_price', None)
    if transient_price is not None:
        return Decimal(str(transient_price)).quantize(Decimal('0.01'))
    manual_price = _manual_proxy_price(order)
    if manual_price is not None:
        return manual_price.quantize(Decimal('0.01'))
    source_price = _source_order_price(order)
    if source_price is not None:
        return source_price.quantize(Decimal('0.01'))
    amount = Decimal(str(order.total_amount or 0)).quantize(Decimal('0.01'))
    if amount <= 0:
        raise RenewalPriceMissingError('该代理缺少续费价格，请先在后台代理列表填写人工价格。')
    return amount


def _renewal_wallet_charge_amount(order: CloudServerOrder, user: TelegramUser | None, currency: str) -> tuple[Decimal, Decimal]:
    latest_usdt = _renewal_price(order, user).quantize(Decimal('0.01'))
    if currency == 'TRX':
        return latest_usdt, async_to_sync(usdt_to_trx)(latest_usdt)
    return latest_usdt, latest_usdt


def _cloud_ip_log_chain_ip(*, order_obj=None, asset_obj=None, server_obj=None, current_ip=None, previous_ip=None):
    return (
        current_ip
        or previous_ip
        or getattr(order_obj, 'public_ip', None)
        or getattr(order_obj, 'previous_public_ip', None)
        or getattr(asset_obj, 'public_ip', None)
        or getattr(asset_obj, 'previous_public_ip', None)
        or getattr(server_obj, 'public_ip', None)
        or getattr(server_obj, 'previous_public_ip', None)
    )


def _cloud_ip_log_chain_candidates(*, order_obj=None, asset_obj=None, server_obj=None, current_ip=None, previous_ip=None):
    return [
        value for value in dict.fromkeys([
            current_ip,
            previous_ip,
            getattr(order_obj, 'public_ip', None),
            getattr(order_obj, 'previous_public_ip', None),
            getattr(asset_obj, 'public_ip', None),
            getattr(asset_obj, 'previous_public_ip', None),
        ]) if value
    ]


def _cloud_ip_log_chain_lookup(*, order_obj=None, asset_obj=None, server_obj=None, chain_ips=None):
    is_unattached_static_asset = bool(
        asset_obj
        and (
            not getattr(asset_obj, 'instance_id', None)
            or '未附加固定IP' in str(getattr(asset_obj, 'provider_status', '') or '')
            or 'StaticIp' in str(getattr(asset_obj, 'provider_resource_id', '') or '')
        )
    )
    relation_match = Q()
    if asset_obj:
        relation_match |= Q(asset=asset_obj)
    if order_obj and not is_unattached_static_asset:
        relation_match |= Q(order=order_obj)
    if relation_match:
        existing = CloudIpLog.objects.filter(relation_match).order_by('-created_at', '-id').first()
        if existing:
            return existing

    asset_name = getattr(asset_obj, 'asset_name', None) or getattr(order_obj, 'server_name', None)
    instance_id = getattr(asset_obj, 'instance_id', None) or getattr(order_obj, 'instance_id', None)
    scoped_match = Q()
    if asset_name:
        scoped_match |= Q(asset_name=asset_name)
    if instance_id:
        scoped_match |= Q(instance_id=instance_id)
    ip_match = Q()
    for chain_ip in chain_ips or []:
        ip_match |= Q(public_ip=chain_ip) | Q(previous_public_ip=chain_ip)
    if not ip_match:
        return None
    if order_obj:
        lineage_ids = _replacement_lineage_ids(order_obj)
        if lineage_ids:
            existing = CloudIpLog.objects.filter(order_id__in=lineage_ids).filter(ip_match).order_by('-created_at', '-id').first()
            if existing:
                return existing
    if not scoped_match:
        return None
    return CloudIpLog.objects.filter(scoped_match & ip_match).order_by('-created_at', '-id').first()


def _replacement_lineage_ids(order_obj):
    if not order_obj:
        return set()
    seen = set()
    queue = [getattr(order_obj, 'id', None)]
    while queue:
        current_id = queue.pop(0)
        if not current_id or current_id in seen:
            continue
        seen.add(current_id)
        parent_id = CloudServerOrder.objects.filter(id=current_id).values_list('replacement_for_id', flat=True).first()
        if parent_id and parent_id not in seen:
            queue.append(parent_id)
        child_ids = list(CloudServerOrder.objects.filter(replacement_for_id=current_id).values_list('id', flat=True))
        for child_id in child_ids:
            if child_id not in seen:
                queue.append(child_id)
    return seen


def _should_rebind_cloud_ip_log(latest_existing, order_obj):
    if not latest_existing or not order_obj or not latest_existing.order_id:
        return False
    if latest_existing.order_id == order_obj.id:
        return False
    lineage_ids = _replacement_lineage_ids(order_obj)
    return bool(lineage_ids and latest_existing.order_id in lineage_ids)


def _merge_cloud_ip_log_duplicates(target):
    if not target:
        return target
    if target.pk and not CloudIpLog.objects.filter(pk=target.pk).exists():
        return target
    candidates = [value for value in dict.fromkeys([target.public_ip, target.previous_public_ip]) if value]
    if not candidates:
        return target
    match = Q()
    if target.asset_id:
        match |= Q(asset_id=target.asset_id)
    if target.order_id:
        match |= Q(order_id=target.order_id)
    scoped_name_match = Q()
    if target.asset_name:
        scoped_name_match |= Q(asset_name=target.asset_name)
    if target.instance_id:
        scoped_name_match |= Q(instance_id=target.instance_id)
    ip_match = Q()
    for value in candidates:
        ip_match |= Q(public_ip=value) | Q(previous_public_ip=value)
    if scoped_name_match and ip_match:
        match |= scoped_name_match & ip_match
    if not match:
        return target
    duplicates = list(CloudIpLog.objects.filter(match).exclude(id=target.id).order_by('created_at', 'id'))
    if not duplicates:
        return target
    for duplicate in duplicates:
        target.order = target.order or duplicate.order
        target.asset = target.asset or duplicate.asset
        target.user = target.user or duplicate.user
        target.provider = target.provider or duplicate.provider
        target.region_code = target.region_code or duplicate.region_code
        target.region_name = target.region_name or duplicate.region_name
        target.order_no = target.order_no or duplicate.order_no
        target.asset_name = target.asset_name or duplicate.asset_name
        target.instance_id = target.instance_id or duplicate.instance_id
        target.provider_resource_id = target.provider_resource_id or duplicate.provider_resource_id
        target.public_ip = target.public_ip or duplicate.public_ip
        if duplicate.previous_public_ip and (
            not target.previous_public_ip
            or (target.public_ip and target.previous_public_ip == target.public_ip and duplicate.previous_public_ip != target.public_ip)
        ):
            target.previous_public_ip = duplicate.previous_public_ip
        target.note = prepend_note(target.note, duplicate.note or '', unique=True)
        duplicate.delete()
    target.save(update_fields=[
        'order', 'asset', 'user', 'provider', 'region_code', 'region_name',
        'order_no', 'asset_name', 'instance_id', 'provider_resource_id', 'public_ip',
        'previous_public_ip', 'note',
    ])
    return target


def record_cloud_ip_log(*, event_type, order=None, asset=None, server=None, public_ip=None, previous_public_ip=None, note='', trigger_label: str | None = None):
    asset_obj = asset
    server_obj = None
    order_obj = order or getattr(asset_obj, 'order', None)
    user_obj = (
        getattr(order_obj, 'user', None)
        or getattr(asset_obj, 'user', None)
    )
    provider = (
        getattr(order_obj, 'provider', None)
        or getattr(asset_obj, 'provider', None)
    )
    region_code = (
        getattr(order_obj, 'region_code', None)
        or getattr(asset_obj, 'region_code', None)
    )
    region_name = (
        getattr(order_obj, 'region_name', None)
        or getattr(asset_obj, 'region_name', None)
    )
    asset_name = (
        getattr(asset_obj, 'asset_name', None)
        or getattr(order_obj, 'server_name', None)
    )
    instance_id = (
        getattr(asset_obj, 'instance_id', None)
        or getattr(order_obj, 'instance_id', None)
    )
    provider_resource_id = (
        getattr(asset_obj, 'provider_resource_id', None)
        or getattr(order_obj, 'provider_resource_id', None)
    )
    current_ip = public_ip
    if current_ip is None:
        current_ip = (
            getattr(asset_obj, 'public_ip', None)
            or getattr(order_obj, 'public_ip', None)
        )
    previous_ip = previous_public_ip
    if previous_ip is None:
        previous_ip = (
            getattr(asset_obj, 'previous_public_ip', None)
            or getattr(order_obj, 'previous_public_ip', None)
        )
    executed_at = timezone.now()
    final_note = _build_cloud_ip_log_note(event_type, order_obj, asset_obj, server_obj, current_ip, previous_ip, note or '', executed_at=executed_at, trigger_label=trigger_label)
    chain_ip = _cloud_ip_log_chain_ip(
        order_obj=order_obj,
        asset_obj=asset_obj,
        server_obj=server_obj,
        current_ip=current_ip,
        previous_ip=previous_ip,
    )
    latest_existing = _cloud_ip_log_chain_lookup(
        order_obj=order_obj,
        asset_obj=asset_obj,
        server_obj=server_obj,
        chain_ips=_cloud_ip_log_chain_candidates(
            order_obj=order_obj,
            asset_obj=asset_obj,
            server_obj=server_obj,
            current_ip=current_ip,
            previous_ip=previous_ip,
        ),
    )
    if latest_existing:
        if _should_rebind_cloud_ip_log(latest_existing, order_obj):
            latest_existing.order = order_obj
            latest_existing.asset = asset_obj or latest_existing.asset
            latest_existing.user = user_obj or latest_existing.user
        else:
            latest_existing.order = latest_existing.order or order_obj
            latest_existing.asset = latest_existing.asset or asset_obj
            latest_existing.user = latest_existing.user or user_obj
        latest_existing.provider = provider or latest_existing.provider
        latest_existing.region_code = region_code or latest_existing.region_code
        latest_existing.region_name = region_name or latest_existing.region_name
        latest_existing.order_no = getattr(order_obj, 'order_no', None) or latest_existing.order_no
        latest_existing.asset_name = asset_name or latest_existing.asset_name
        latest_existing.instance_id = instance_id or latest_existing.instance_id
        latest_existing.provider_resource_id = provider_resource_id or latest_existing.provider_resource_id
        latest_existing.public_ip = current_ip or chain_ip or latest_existing.public_ip
        is_terminal_ip_only_event = event_type in {CloudIpLog.EVENT_DELETED, CloudIpLog.EVENT_RECYCLED} and previous_ip and (not current_ip or current_ip == previous_ip)
        if previous_ip and not (
            is_terminal_ip_only_event
            and latest_existing.previous_public_ip
            and latest_existing.previous_public_ip != previous_ip
        ):
            latest_existing.previous_public_ip = previous_ip
        latest_existing.event_type = event_type
        latest_existing.note = prepend_note(latest_existing.note, final_note, unique=True)
        latest_existing.save(update_fields=[
            'order', 'asset', 'user', 'provider', 'region_code', 'region_name',
            'order_no', 'asset_name', 'instance_id', 'provider_resource_id', 'public_ip',
            'previous_public_ip', 'event_type', 'note',
        ])
        return _merge_cloud_ip_log_duplicates(latest_existing)

    created = CloudIpLog.objects.create(
        order=order_obj,
        asset=asset_obj,
        user=user_obj,
        provider=provider,
        region_code=region_code,
        region_name=region_name,
        order_no=getattr(order_obj, 'order_no', None),
        asset_name=asset_name,
        instance_id=instance_id,
        provider_resource_id=provider_resource_id,
        public_ip=chain_ip or current_ip,
        previous_public_ip=previous_ip,
        event_type=event_type,
        note=final_note,
    )
    return _merge_cloud_ip_log_duplicates(created)


def _fmt_dt(value):
    return value.isoformat() if value else '-'


def _fmt_amount(value):
    if value in (None, ''):
        return '-'
    return str(Decimal(str(value)).quantize(Decimal('0.01')))


def _fmt_log_dt(value):
    if not value:
        return '-'
    try:
        return timezone.localtime(value).strftime('%Y-%m-%d %H:%M:%S')
    except Exception:
        return str(value)


def _cloud_log_user_label(user_obj):
    if not user_obj:
        return '未绑定用户'
    usernames = getattr(user_obj, 'usernames', None) or []
    username = (usernames[0] if usernames else getattr(user_obj, 'username', '') or '').strip()
    name = (getattr(user_obj, 'first_name', '') or '').strip()
    tg_user_id = getattr(user_obj, 'tg_user_id', None)
    username_label = f'@{username}' if username and not username.startswith('@') else username
    parts = [part for part in [name, username_label, str(tg_user_id) if tg_user_id else ''] if part]
    return ' / '.join(parts) or f'用户#{getattr(user_obj, "id", "-")}'


def _cloud_log_trigger_label(order_obj):
    if not order_obj:
        return '生命周期'
    order_no = (getattr(order_obj, 'order_no', None) or '').upper()
    if order_no.startswith('SRVIP'):
        return '更换IP'
    if order_no.startswith('SRVUPGRADE') or order_no.startswith('SRVDOWNGRADE'):
        return '修改配置'
    if order_no.startswith('SRVREBUILD'):
        return '重装'
    return '新购创建'


def _is_unattached_static_ip_asset(asset_obj=None, server_obj=None):
    target = asset_obj or server_obj
    if not target:
        return False
    provider_status = str(getattr(target, 'provider_status', '') or '')
    note = str(getattr(target, 'note', '') or '')
    provider_resource_id = str(getattr(target, 'provider_resource_id', '') or '')
    return bool(
        not str(getattr(target, 'instance_id', '') or '').strip()
        and (
            '未附加固定IP' in provider_status
            or '未附加IP' in provider_status
            or '固定IP保留中' in provider_status
            or '固定 IP 保留' in provider_status
            or '固定IP仍存在但未附加' in provider_status
            or '未附加固定IP' in note
            or '未附加IP' in note
            or '固定IP保留中' in note
            or '固定 IP 保留' in note
            or 'StaticIp' in provider_resource_id
        )
    )


def _cloud_log_execution_plan(order_obj, asset_obj=None):
    if _is_unattached_static_ip_asset(asset_obj):
        release_at = getattr(asset_obj, 'actual_expires_at', None)
        return f'释放固定IP {_fmt_log_dt(release_at)}' if release_at else '-'
    suspend_at = getattr(order_obj, 'suspend_at', None) if order_obj else None
    delete_at = getattr(order_obj, 'delete_at', None) if order_obj else None
    recycle_at = getattr(order_obj, 'ip_recycle_at', None) if order_obj else None
    if not suspend_at and asset_obj and getattr(asset_obj, 'actual_expires_at', None):
        try:
            expires_at = asset_obj.actual_expires_at
            suspend_at = with_runtime_time(expires_at + timezone.timedelta(days=runtime_int_config('cloud_suspend_after_days', 3)), 'cloud_suspend_time')
            delete_at = with_runtime_time(suspend_at + timezone.timedelta(days=runtime_int_config('cloud_delete_after_days', 0)), 'cloud_delete_time')
            if delete_at and delete_at < suspend_at:
                delete_at = suspend_at
        except Exception:
            pass
    plan_parts = []
    if suspend_at:
        plan_parts.append(f'关机 {_fmt_log_dt(suspend_at)}')
    if delete_at:
        plan_parts.append(f'删机 {_fmt_log_dt(delete_at)}')
    if recycle_at:
        plan_parts.append(f'IP回收 {_fmt_log_dt(recycle_at)}')
    return '，'.join(plan_parts) or '-'


def _build_cloud_ip_log_note(event_type, order_obj, asset_obj, server_obj, current_ip, previous_ip, note, executed_at=None, trigger_label: str | None = None):
    user_obj = (
        getattr(order_obj, 'user', None)
        or getattr(asset_obj, 'user', None)
        or getattr(server_obj, 'user', None)
    )
    is_unattached_static_ip = _is_unattached_static_ip_asset(asset_obj, server_obj)
    expires_at = (
        getattr(asset_obj, 'actual_expires_at', None)
        if is_unattached_static_ip else None
    ) or (
        order_asset_expiry(order_obj)
        or getattr(asset_obj, 'actual_expires_at', None)
        or getattr(server_obj, 'expires_at', None)
    )
    ip_value = current_ip or previous_ip or '-'
    base_parts = [
        f'IP：{ip_value}',
        f'触发事件：{trigger_label or _cloud_log_trigger_label(order_obj)}',
        f'订单号：{getattr(order_obj, "order_no", None) or "-"}',
        f'服务器名：{getattr(order_obj, "server_name", None) or getattr(asset_obj, "asset_name", None) or getattr(server_obj, "server_name", None) or "-"}',
        f'用户：{_cloud_log_user_label(user_obj)}',
        f'执行时间：{_fmt_log_dt(executed_at or timezone.now())}',
        f'{"计划释放时间" if is_unattached_static_ip else "到期时间"}：{_fmt_log_dt(expires_at)}',
        f'执行计划：{_cloud_log_execution_plan(order_obj, asset_obj)}',
    ]
    if note:
        base_parts.append(f'执行内容：{note}')
    return '；'.join(base_parts)


def _trim_operation_order_no(source_order: CloudServerOrder | None, operation: str, suffix: str | None = None) -> str:
    source_id = getattr(source_order, 'id', None) or 0
    tag = suffix or (f'O{source_id}' if source_id else None)
    return _generate_cloud_order_no(f'SRV{operation}', tag=tag)


def _set_source_migration_expiry(order: CloudServerOrder, migration_due_at, reason: str, note: str = ''):
    before = {
        'actual_expires_at': order_asset_expiry(order),
        'renew_grace_expires_at': order.renew_grace_expires_at,
        'suspend_at': order.suspend_at,
        'delete_at': order.delete_at,
        'ip_recycle_at': order.ip_recycle_at,
        'migration_due_at': order.migration_due_at,
    }
    delete_at = migration_due_at + timezone.timedelta(days=3)
    order.migration_due_at = migration_due_at
    order.renew_grace_expires_at = delete_at
    order.suspend_at = delete_at
    order.delete_at = delete_at
    order.ip_recycle_at = delete_at + timezone.timedelta(days=15)
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    CloudServerOrder.objects.filter(id=order.id).update(
        migration_due_at=order.migration_due_at,
        renew_grace_expires_at=order.renew_grace_expires_at,
        suspend_at=order.suspend_at,
        delete_at=order.delete_at,
        ip_recycle_at=order.ip_recycle_at,
        provision_note=order.provision_note,
        updated_at=timezone.now(),
    )
    asset, _ = _update_order_primary_records(
        order,
        asset_updates={'actual_expires_at': migration_due_at},
        server_updates={'expires_at': migration_due_at},
    )
    asset_count = 1 if asset else 0
    after = {
        'actual_expires_at': migration_due_at,
        'renew_grace_expires_at': order.renew_grace_expires_at,
        'suspend_at': order.suspend_at,
        'delete_at': order.delete_at,
        'ip_recycle_at': order.ip_recycle_at,
        'migration_due_at': order.migration_due_at,
    }
    logger.info(
        'CLOUD_SOURCE_MIGRATION_EXPIRY_CHANGE reason=%s order_id=%s order_no=%s status=%s public_ip=%s previous_public_ip=%s actual_expires_at=%s->%s renew_grace_expires_at=%s->%s suspend_at=%s->%s delete_at=%s->%s ip_recycle_at=%s->%s migration_due_at=%s->%s asset_count=%s',
        reason,
        order.id,
        order.order_no,
        order.status,
        order.public_ip,
        order.previous_public_ip,
        _fmt_dt(before['actual_expires_at']),
        _fmt_dt(after['actual_expires_at']),
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
    )
    source_trigger_label = '更换IP' if '更换 IP' in reason or '更换IP' in reason else ('重装' if '重装' in reason or '重建' in reason else None)
    record_cloud_ip_log(
        event_type='changed',
        order=order,
        public_ip=order.public_ip or None,
        previous_public_ip=order.previous_public_ip or None,
        trigger_label=source_trigger_label,
        note=(
            f'{reason}: 旧服务器生命周期已更新；'
            f'旧机订单 {order.order_no}；旧机IP {order.public_ip or order.previous_public_ip or "-"}；'
            f'状态：旧服务器继续保留到迁移到期时间，之后进入宽限/删机/IP保留流程；'
            f'服务到期 {_fmt_dt(before["actual_expires_at"])} -> {_fmt_dt(after["actual_expires_at"])}；'
            f'宽限到期 {_fmt_dt(before["renew_grace_expires_at"])} -> {_fmt_dt(after["renew_grace_expires_at"])}；'
            f'删机时间 {_fmt_dt(before["delete_at"])} -> {_fmt_dt(after["delete_at"])}；'
            f'IP保留到期 {_fmt_dt(before["ip_recycle_at"])} -> {_fmt_dt(after["ip_recycle_at"])}；'
            f'同步资产 {asset_count} 条。'
        ),
    )
    return order


def _create_manual_asset_operation_order(asset: CloudAsset, user: TelegramUser, operation: str, asset_expires_at=None) -> CloudServerOrder | None:
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
    lifecycle_fields = compute_order_lifecycle_fields(asset_expires_at) if asset_expires_at else {}
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
        status='cancelled',
        cloud_reminder_enabled=False,
        suspend_reminder_enabled=False,
        delete_reminder_enabled=False,
        ip_recycle_reminder_enabled=False,
        auto_renew_enabled=False,
        lifecycle_days=getattr(base_order, 'lifecycle_days', 31) or 31,
        service_started_at=getattr(base_order, 'service_started_at', None) or asset.created_at or now,
        server_name=asset.asset_name,
        mtproxy_port=asset.mtproxy_port or getattr(base_order, 'mtproxy_port', None) or MTPROXY_DEFAULT_PORT,
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
        provision_note=f'{operation}: 后台人工编辑生成的审计订单，不参与续费/到期生命周期；来源资产 #{asset.id}；来源订单 #{base_order_id or "-"}。',
        **lifecycle_fields,
    )
    _ensure_order_asset_expiry_record(order, asset_expires_at)
    return order


def _mark_manual_replaced_order_inactive(order: CloudServerOrder, actor: str, reason: str) -> None:
    note = '\n'.join(filter(None, [
        str(getattr(order, 'provision_note', '') or '').strip(),
        f'{actor}: {reason}；旧订单已失效。',
    ]))
    now = timezone.now()
    CloudServerOrder.objects.filter(id=order.id).update(
        status='cancelled',
        auto_renew_enabled=False,
        cloud_reminder_enabled=False,
        suspend_reminder_enabled=False,
        delete_reminder_enabled=False,
        ip_recycle_reminder_enabled=False,
        renew_notice_sent_at=None,
        auto_renew_notice_sent_at=None,
        auto_renew_failure_notice_sent_at=None,
        delete_notice_sent_at=None,
        recycle_notice_sent_at=None,
        renew_grace_expires_at=None,
        suspend_at=None,
        delete_at=None,
        ip_recycle_at=None,
        expired_at=now,
        provision_note=note,
        updated_at=now,
    )
    order.status = 'cancelled'
    order.auto_renew_enabled = False
    order.cloud_reminder_enabled = False
    order.suspend_reminder_enabled = False
    order.delete_reminder_enabled = False
    order.ip_recycle_reminder_enabled = False
    order.renew_grace_expires_at = None
    order.suspend_at = None
    order.delete_at = None
    order.ip_recycle_at = None
    order.expired_at = now
    order.provision_note = note


def replace_cloud_asset_order_by_admin(
    asset: CloudAsset,
    *,
    actor: str = '后台人工编辑',
    new_user: TelegramUser | None = None,
    new_expires_at=None,
    new_price=None,
    previous_user: TelegramUser | None = None,
    previous_expires_at=None,
    previous_price=None,
) -> tuple[CloudServerOrder | None, str | None]:
    provider = str(getattr(asset, 'provider', None) or getattr(getattr(asset, 'order', None), 'provider', None) or '').strip()
    if not _is_manual_authoritative_provider(provider):
        return None, None
    base_order = getattr(asset, 'order', None)
    if not base_order:
        return None, '当前资产没有关联订单，无法生成替换订单'
    target_user = new_user if new_user is not None else asset.user
    if not target_user:
        return None, '当前资产缺少归属用户，无法生成替换订单'
    target_expires_at = new_expires_at or asset.actual_expires_at or order_asset_expiry(base_order)
    if not target_expires_at:
        return None, '当前资产缺少到期时间，无法生成替换订单'
    plan = getattr(base_order, 'plan', None)
    plan_id = getattr(base_order, 'plan_id', None)
    if not plan_id:
        plan = CloudServerPlan.objects.filter(
            provider=provider,
            region_code=asset.region_code or getattr(base_order, 'region_code', '') or '',
            is_active=True,
        ).order_by('-sort_order', 'id').first() or CloudServerPlan.objects.filter(provider=provider, is_active=True).order_by('-sort_order', 'id').first()
        plan_id = getattr(plan, 'id', None)
    if not plan_id:
        return None, '当前地区没有可用套餐，无法生成替换订单'
    now = timezone.now()
    requested_price = new_price if new_price is not None else asset.price
    old_price = previous_price if previous_price is not None else getattr(base_order, 'total_amount', None)
    base_amount = requested_price
    if base_amount in (None, ''):
        base_amount = getattr(base_order, 'total_amount', None)
    if base_amount in (None, '') and asset.price is not None:
        base_amount = asset.price
    total_amount = Decimal(str(base_amount)).quantize(Decimal('0.01')) if base_amount not in (None, '') else Decimal('0.00')
    pay_amount = total_amount
    lifecycle = compute_order_lifecycle_fields(target_expires_at)
    old_user = previous_user if previous_user is not None else getattr(base_order, 'user', None)
    old_expires = previous_expires_at if previous_expires_at is not None else order_asset_expiry(base_order)
    old_user_label = getattr(old_user, 'tg_user_id', None) or getattr(old_user, 'username', None) or '-'
    new_user_label = getattr(target_user, 'tg_user_id', None) or getattr(target_user, 'username', None) or target_user.id
    change_bits = []
    if str(old_user_label) != str(new_user_label):
        change_bits.append(f'所属人 {old_user_label} -> {new_user_label}')
    if old_expires != target_expires_at:
        change_bits.append(f'到期时间 {_fmt_dt(old_expires)} -> {_fmt_dt(target_expires_at)}')
    if old_price != requested_price and requested_price is not None:
        change_bits.append(f'价格 {_fmt_amount(old_price)} -> {_fmt_amount(requested_price)}')
    if not change_bits:
        change_bits.append('人工重建当前有效订单')
    note = f"{actor}: {'；'.join(change_bits)}；生成新订单并让旧订单失效。"
    new_order = CloudServerOrder.objects.create(
        order_no=_generate_cloud_order_no('SRVADMIN', f'REPLACE{asset.id}'),
        user=target_user,
        plan_id=plan_id,
        provider=provider,
        cloud_account=asset.cloud_account or getattr(base_order, 'cloud_account', None),
        account_label=asset.account_label or getattr(base_order, 'account_label', None),
        region_code=asset.region_code or getattr(base_order, 'region_code', None) or getattr(plan, 'region_code', ''),
        region_name=asset.region_name or getattr(base_order, 'region_name', None) or getattr(plan, 'region_name', ''),
        plan_name=asset.asset_name or getattr(base_order, 'plan_name', None) or getattr(plan, 'plan_name', '人工代理'),
        quantity=1,
        currency=asset.currency or getattr(base_order, 'currency', None) or getattr(plan, 'currency', 'USDT'),
        total_amount=total_amount,
        pay_amount=pay_amount,
        pay_method=getattr(base_order, 'pay_method', None) or 'balance',
        status='completed',
        lifecycle_days=getattr(base_order, 'lifecycle_days', 31) or 31,
        service_started_at=getattr(base_order, 'service_started_at', None) or asset.created_at or now,
        suspend_at=lifecycle.get('suspend_at'),
        renew_grace_expires_at=lifecycle.get('renew_grace_expires_at'),
        delete_at=lifecycle.get('delete_at'),
        ip_recycle_at=lifecycle.get('ip_recycle_at'),
        cloud_reminder_enabled=getattr(base_order, 'cloud_reminder_enabled', True),
        suspend_reminder_enabled=getattr(base_order, 'suspend_reminder_enabled', True),
        delete_reminder_enabled=getattr(base_order, 'delete_reminder_enabled', True),
        ip_recycle_reminder_enabled=getattr(base_order, 'ip_recycle_reminder_enabled', True),
        auto_renew_enabled=getattr(base_order, 'auto_renew_enabled', False),
        last_user_id=getattr(target_user, 'tg_user_id', None),
        mtproxy_port=asset.mtproxy_port or getattr(base_order, 'mtproxy_port', None) or MTPROXY_DEFAULT_PORT,
        mtproxy_link=asset.mtproxy_link or getattr(base_order, 'mtproxy_link', None),
        proxy_links=asset.proxy_links or getattr(base_order, 'proxy_links', None) or [],
        mtproxy_secret=asset.mtproxy_secret or getattr(base_order, 'mtproxy_secret', None),
        mtproxy_host=asset.mtproxy_host or getattr(base_order, 'mtproxy_host', None),
        instance_id=asset.instance_id or getattr(base_order, 'instance_id', None),
        provider_resource_id=asset.provider_resource_id or getattr(base_order, 'provider_resource_id', None),
        public_ip=asset.public_ip or getattr(base_order, 'public_ip', None),
        previous_public_ip=asset.previous_public_ip or getattr(base_order, 'previous_public_ip', None),
        server_name=asset.asset_name or getattr(base_order, 'server_name', None),
        login_user=asset.login_user or getattr(base_order, 'login_user', None),
        login_password=asset.login_password or getattr(base_order, 'login_password', None),
        ip_change_quota=getattr(base_order, 'ip_change_quota', 1) or 1,
        last_renewed_at=getattr(base_order, 'last_renewed_at', None),
        completed_at=now,
        replacement_for=base_order,
        provision_note='\n'.join(filter(None, [
            str(getattr(base_order, 'provision_note', '') or '').strip(),
            note,
            f'来源资产 #{asset.id}；来源订单 #{base_order.id}。',
        ])),
    )
    asset.order = new_order
    asset.user = target_user
    asset.actual_expires_at = target_expires_at
    asset.save(update_fields=['order', 'user', 'actual_expires_at', 'updated_at'])
    _mark_manual_replaced_order_inactive(base_order, actor, note)
    logger.info('CLOUD_MANUAL_REPLACE_ORDER old_order_id=%s new_order_id=%s asset_id=%s public_ip=%s actor=%s note=%s', base_order.id, new_order.id, asset.id, asset.public_ip, actor, note)
    record_cloud_ip_log(
        event_type='changed',
        order=new_order,
        asset=asset,
        public_ip=asset.public_ip,
        previous_public_ip=asset.previous_public_ip,
        note=note,
    )
    return new_order, None


def ensure_manual_owner_operation_order(asset: CloudAsset, new_user: TelegramUser | None, actor: str = '后台人工编辑', previous_user: TelegramUser | None = None, previous_expires_at=None) -> tuple[CloudServerOrder | None, str | None]:
    old_user = previous_user if previous_user is not None else asset.user
    if not new_user:
        old_label = getattr(old_user, 'tg_user_id', None) or getattr(old_user, 'username', None) or '-'
        asset.user = None
        asset.order = None
        asset.save(update_fields=['user', 'order', 'updated_at'])
        logger.info('CLOUD_MANUAL_OWNER_UNBIND asset_id=%s public_ip=%s old_user=%s actor=%s', asset.id, asset.public_ip, old_label, actor)
        record_cloud_ip_log(event_type='changed', asset=asset, public_ip=asset.public_ip, previous_public_ip=asset.previous_public_ip, note=f'{actor}: 人工清空所属人；原所属人 {old_label}。')
        return None, None
    old_label = getattr(old_user, 'tg_user_id', None) or getattr(old_user, 'username', None) or '-'
    new_label = getattr(new_user, 'tg_user_id', None) or getattr(new_user, 'username', None) or new_user.id
    asset.user = new_user
    asset.save(update_fields=['user', 'updated_at'])
    owner_order_expires_at = previous_expires_at
    if owner_order_expires_at is None:
        owner_order_expires_at = order_asset_expiry(getattr(asset, 'order', None)) or asset.actual_expires_at
    order = _create_manual_asset_operation_order(asset, new_user, 'OWNER', owner_order_expires_at)
    if not order:
        logger.warning('CLOUD_MANUAL_OWNER_ORDER_SKIPPED asset_id=%s public_ip=%s reason=no_available_plan actor=%s', asset.id, asset.public_ip, actor)
        record_cloud_ip_log(event_type='changed', asset=asset, public_ip=asset.public_ip, previous_public_ip=asset.previous_public_ip, note=f'{actor}: 人工编辑所属人 {old_label} -> {new_label}；未生成操作订单：该地区没有可用套餐。')
        return None, None
    order.provision_note = '\n'.join(filter(None, [order.provision_note, f'{actor}: 人工编辑所属人 {old_label} -> {new_label}，生成独立操作订单用于审计，不改写资产原订单绑定。']))
    order.save(update_fields=['provision_note', 'updated_at'])
    logger.info('CLOUD_MANUAL_OWNER_ORDER order_id=%s order_no=%s asset_id=%s public_ip=%s old_user=%s new_user=%s actor=%s', order.id, order.order_no, asset.id, asset.public_ip, old_label, new_label, actor)
    record_cloud_ip_log(event_type='changed', order=order, asset=asset, public_ip=asset.public_ip, previous_public_ip=asset.previous_public_ip, note=f'{actor}: 人工编辑所属人 {old_label} -> {new_label}；操作订单 {order.order_no}。')
    return order, None


def ensure_manual_price_operation_order(asset: CloudAsset, new_price, actor: str = '后台人工编辑', previous_price=None) -> tuple[CloudServerOrder | None, str | None]:
    if not asset.user_id:
        logger.info('CLOUD_MANUAL_PRICE_ORDER_SKIPPED asset_id=%s public_ip=%s reason=unbound_user actor=%s', asset.id, asset.public_ip, actor)
        record_cloud_ip_log(event_type='changed', asset=asset, public_ip=asset.public_ip, previous_public_ip=asset.previous_public_ip, note=f'{actor}: 人工编辑未绑定资产价格为 {new_price}；未生成操作订单。')
        return None, None
    old_price = previous_price if previous_price is not None else getattr(getattr(asset, 'order', None), 'total_amount', None)
    order = _create_manual_asset_operation_order(asset, asset.user, 'PRICE', asset.actual_expires_at)
    if not order:
        logger.warning('CLOUD_MANUAL_PRICE_ORDER_SKIPPED asset_id=%s public_ip=%s reason=no_available_plan actor=%s', asset.id, asset.public_ip, actor)
        record_cloud_ip_log(event_type='changed', asset=asset, public_ip=asset.public_ip, previous_public_ip=asset.previous_public_ip, note=f'{actor}: 人工编辑价格 {old_price} -> {new_price}；未生成操作订单：该地区没有可用套餐。')
        return None, None
    order.provision_note = '\n'.join(filter(None, [
        order.provision_note,
        f'{actor}: 人工编辑价格 {old_price} -> {new_price}，生成独立操作订单用于审计，不改写资产原订单绑定。',
    ]))
    order.save(update_fields=['provision_note', 'updated_at'])
    logger.info('CLOUD_MANUAL_PRICE_ORDER order_id=%s order_no=%s asset_id=%s public_ip=%s old_price=%s new_price=%s actor=%s', order.id, order.order_no, asset.id, asset.public_ip, old_price, new_price, actor)
    record_cloud_ip_log(event_type='changed', order=order, asset=asset, public_ip=asset.public_ip, previous_public_ip=asset.previous_public_ip, note=f'{actor}: 人工编辑价格 {old_price} -> {new_price}；操作订单 {order.order_no}。')
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
        f'{actor}: 人工编辑到期时间 {_fmt_dt(old_expires_at)} -> {_fmt_dt(new_expires_at)}，生成独立操作订单用于审计，不改写资产原订单绑定。',
    ]))
    order.save(update_fields=['provision_note', 'updated_at'])
    asset.actual_expires_at = new_expires_at
    asset.save(update_fields=['actual_expires_at', 'updated_at'])
    logger.info(
        'CLOUD_MANUAL_EXPIRY_ORDER order_id=%s order_no=%s asset_id=%s public_ip=%s old_expires_at=%s new_expires_at=%s actor=%s',
        order.id,
        order.order_no,
        asset.id,
        asset.public_ip,
        _fmt_dt(old_expires_at),
        _fmt_dt(new_expires_at),
        actor,
    )
    record_cloud_ip_log(
        event_type='changed',
        order=order,
        asset=asset,
        public_ip=asset.public_ip,
        previous_public_ip=asset.previous_public_ip,
        note=f'{actor}: 人工编辑到期时间 {_fmt_dt(old_expires_at)} -> {_fmt_dt(new_expires_at)}；操作订单 {order.order_no}。',
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
        server_name=asset.asset_name,
        static_ip_name=asset.asset_name if provider == CloudServerPlan.PROVIDER_AWS_LIGHTSAIL else '',
        mtproxy_port=asset.mtproxy_port or MTPROXY_DEFAULT_PORT,
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
    _ensure_order_asset_expiry_record(order, asset.actual_expires_at, status=CloudAsset.STATUS_RUNNING)
    if _is_unattached_static_ip_asset(asset) and asset.actual_expires_at:
        CloudServerOrder.objects.filter(id=order.id).update(ip_recycle_at=asset.actual_expires_at, updated_at=timezone.now())
        order.ip_recycle_at = asset.actual_expires_at
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
def ensure_cloud_asset_operation_order(asset_id: int, user_id: int, admin: bool = False):
    asset_qs = CloudAsset.objects.select_related('order', 'user', 'cloud_account').filter(
        id=asset_id,
        kind=CloudAsset.KIND_SERVER,
    )
    if not admin:
        asset_qs = asset_qs.filter(_user_asset_visibility_filter(user_id))
    asset = asset_qs.exclude(status__in=_INACTIVE_ASSET_STATUSES).first()
    if not asset:
        return None, '代理记录不存在'
    if not str(asset.public_ip or '').strip():
        return None, '代理缺少公网 IP，暂时无法操作'
    operation_user_id = asset.user_id or user_id
    order = asset.order if asset.order_id and (admin or getattr(asset.order, 'user_id', None) == operation_user_id) else None
    if not order:
        order = _create_asset_operation_order(asset, operation_user_id)
        if not order:
            return None, '该地区没有可用套餐，无法创建操作订单'
    order = _hydrate_order_from_proxy_asset(order, asset=asset)
    order.user_id = order.user_id or operation_user_id
    if order.status not in {'completed', 'expiring', 'suspended', 'renew_pending', 'paid', 'provisioning'}:
        order.status = 'completed'
    order.provider = order.provider or asset.provider or CloudServerPlan.PROVIDER_AWS_LIGHTSAIL
    order.region_code = order.region_code or asset.region_code or ''
    order.region_name = order.region_name or asset.region_name or ''
    order.public_ip = order.public_ip or asset.public_ip
    order.previous_public_ip = order.previous_public_ip or asset.previous_public_ip
    order.instance_id = order.instance_id or asset.instance_id
    order.provider_resource_id = order.provider_resource_id or asset.provider_resource_id
    order.mtproxy_port = order.mtproxy_port or asset.mtproxy_port or MTPROXY_DEFAULT_PORT
    order.mtproxy_link = order.mtproxy_link or asset.mtproxy_link
    order.proxy_links = order.proxy_links or asset.proxy_links or []
    order.mtproxy_secret = order.mtproxy_secret or asset.mtproxy_secret
    order.mtproxy_host = order.mtproxy_host or asset.mtproxy_host
    order.login_user = order.login_user or asset.login_user
    order.login_password = order.login_password or asset.login_password
    if _is_unattached_static_ip_asset(asset) and asset.actual_expires_at:
        order.ip_recycle_at = order.ip_recycle_at or asset.actual_expires_at
    if order.provider == CloudServerPlan.PROVIDER_AWS_LIGHTSAIL and not order.static_ip_name and asset.asset_name:
        order.static_ip_name = asset.asset_name
    account = asset.cloud_account or get_cloud_account_from_label(asset.account_label, order.provider)
    order.cloud_account = order.cloud_account or account
    order.account_label = order.account_label or asset.account_label or cloud_account_label(account)
    order.save(update_fields=[
        'user', 'status', 'provider', 'region_code', 'region_name', 'public_ip', 'previous_public_ip',
        'instance_id', 'provider_resource_id', 'mtproxy_port', 'mtproxy_link', 'proxy_links',
        'mtproxy_secret', 'mtproxy_host', 'login_user', 'login_password',
        'ip_recycle_at', 'static_ip_name', 'cloud_account', 'account_label', 'updated_at',
    ])
    if _is_unattached_static_ip_asset(asset) and asset.actual_expires_at and order.ip_recycle_at != asset.actual_expires_at:
        CloudServerOrder.objects.filter(id=order.id).update(ip_recycle_at=asset.actual_expires_at, updated_at=timezone.now())
        order.ip_recycle_at = asset.actual_expires_at
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
    existing = CloudServerOrder.objects.filter(replacement_for=order, status__in={'paid', 'provisioning', 'failed', 'completed'}).order_by('-created_at', '-id').first()
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
        mtproxy_port=order.mtproxy_port or MTPROXY_DEFAULT_PORT,
        mtproxy_secret=order.mtproxy_secret,
        mtproxy_link=order.mtproxy_link,
        proxy_links=order.proxy_links or [],
        static_ip_name=order.static_ip_name,
        replacement_for=order,
        last_user_id=order.last_user_id,
        previous_public_ip=old_public_ip,
        service_started_at=now,
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


def _prepare_cloud_server_renewal(order: CloudServerOrder, renewal_user: TelegramUser | None, days: int = 31):
    order = _hydrate_order_from_proxy_asset(order)
    if not _can_order_be_renewed(order):
        return False
    retained_ip = bool(order.status == 'deleted' and order.ip_recycle_at and order.ip_recycle_at > timezone.now())
    renewal_price = _renewal_price(order, renewal_user)
    order.status = 'renew_pending'
    order.lifecycle_days = days
    order.currency = 'USDT'
    order.total_amount = renewal_price
    order.pay_method = 'address'
    order.pay_amount = _generate_unique_pay_amount(renewal_price, order.currency)
    order.tx_hash = None
    order.payer_address = ''
    order.receive_address = ''
    order.paid_at = None
    order.expired_at = timezone.now() + timezone.timedelta(minutes=30)
    save_fields = ['status', 'lifecycle_days', 'currency', 'total_amount', 'pay_method', 'pay_amount', 'tx_hash', 'payer_address', 'receive_address', 'paid_at', 'expired_at', 'updated_at']
    if retained_ip:
        order.provision_note = '\n'.join(filter(None, [
            order.provision_note,
            f'未附加固定 IP 保留期内发起续费；IP={order.public_ip or order.previous_public_ip or "-"}；端口={order.mtproxy_port or "-"}；旧secret={order.mtproxy_secret or "-"}；IP回收时间={order.ip_recycle_at.isoformat()}。',
        ]))
        order.save(update_fields=[*save_fields, 'provision_note'])
    else:
        order.save(update_fields=save_fields)
    return order


@sync_to_async
def create_cloud_server_renewal(order_id: int, user_id: int, days: int = 31):
    order = CloudServerOrder.objects.select_related('user').filter(id=order_id, user_id=user_id).first()
    if not order:
        return None
    renewal_user = TelegramUser.objects.filter(id=user_id).first()
    return _prepare_cloud_server_renewal(order, renewal_user, days)


@sync_to_async
def create_cloud_server_renewal_by_public_query(order_id: int, days: int = 31):
    order = CloudServerOrder.objects.select_related('user').filter(id=order_id).first()
    if not order:
        return None
    return _prepare_cloud_server_renewal(order, getattr(order, 'user', None), days)


@sync_to_async
def create_cloud_server_renewal_for_user(order_id: int, user_id: int, days: int = 31):
    order = CloudServerOrder.objects.select_related('user').filter(id=order_id, user_id=user_id).first()
    if not order:
        asset = (
            _cloud_server_asset_queryset()
            .filter(order_id=order_id)
            .filter(_user_asset_visibility_filter(user_id))
            .order_by('-updated_at', '-id')
            .first()
        )
        order = _hydrate_order_from_proxy_asset(asset.order, asset=asset) if asset and asset.order else None
        if not order:
            return None
    renewal_user = TelegramUser.objects.filter(id=user_id).first()
    return _prepare_cloud_server_renewal(order, renewal_user, days)


@sync_to_async
def list_cloud_asset_renewal_plans(asset_id: int, user_id: int, admin: bool = False, public: bool = False):
    asset_qs = CloudAsset.objects.select_related('user', 'order', 'cloud_account').filter(
        id=asset_id,
        kind=CloudAsset.KIND_SERVER,
    ).exclude(status__in=_INACTIVE_ASSET_STATUSES)
    if not admin and not public:
        asset_qs = asset_qs.filter(_user_asset_visibility_filter(user_id))
    asset = asset_qs.first()
    if not asset:
        return None, [], '代理记录不存在'
    if public and (asset.order_id or not _is_unattached_static_ip_asset(asset) or _cloud_asset_deleted_or_missing(asset)):
        return None, [], '代理记录不存在'
    if asset.order_id:
        return asset, [], None
    if not str(asset.public_ip or asset.previous_public_ip or '').strip():
        return asset, [], '代理缺少公网 IP，暂时无法续费'
    if _is_unattached_static_ip_asset(asset) and not _resolve_asset_original_cloud_account(asset):
        return asset, [], '原固定 IP 所属云账号不可用，暂时无法自助续费，请联系人工客服。'
    provider = str(asset.provider or CloudServerPlan.PROVIDER_AWS_LIGHTSAIL).strip()
    region_code = str(asset.region_code or '').strip()
    plans_qs = CloudServerPlan.objects.filter(provider=provider, is_active=True)
    if region_code:
        plans_qs = plans_qs.filter(region_code=region_code)
    plans = list(plans_qs.order_by('price', 'sort_order', 'id'))
    if not plans:
        return asset, [], '当前地区暂无可用套餐，请联系人工客服。'
    return asset, plans, None


def _retained_ip_renewal_plans_for_order(order: CloudServerOrder | None):
    if not order:
        return None, [], None
    order = _hydrate_order_from_proxy_asset(order)
    if not _is_retained_ip_renewal_candidate(order):
        return order, [], None
    if not _can_order_be_renewed(order):
        return order, [], '该服务器IP已删除，禁止续费'
    plans = list(
        CloudServerPlan.objects.filter(
            provider=order.provider,
            region_code=order.region_code,
            is_active=True,
        ).order_by('price', 'sort_order', 'id')
    )
    if not plans:
        return order, [], '当前地区暂无可用套餐，无法恢复未附加固定 IP。'
    return order, plans, None


@sync_to_async
def list_retained_ip_renewal_plans(order_id: int, user_id: int, admin: bool = False):
    order_qs = CloudServerOrder.objects.select_related('user', 'plan').filter(id=order_id)
    if not admin:
        order_qs = order_qs.filter(user_id=user_id)
    return _retained_ip_renewal_plans_for_order(order_qs.first())


@sync_to_async
def list_retained_ip_renewal_plans_by_asset(asset_id: int, user_id: int, admin: bool = False, group_chat_id: int | None = None):
    asset_qs = CloudAsset.objects.select_related('order', 'order__user', 'order__plan', 'user', 'cloud_account').filter(
        id=asset_id,
        kind=CloudAsset.KIND_SERVER,
    )
    if not admin:
        if group_chat_id is not None:
            group = _group_filter_for_chat_id(group_chat_id)
            if not group:
                return None, [], None
            asset_qs = asset_qs.filter(telegram_group=group)
        else:
            asset_qs = asset_qs.filter(_user_asset_visibility_filter(user_id))
    asset = asset_qs.first()
    if not asset or not asset.order_id or not _is_retained_static_ip_asset(asset):
        return None, [], None
    return _retained_ip_renewal_plans_for_order(asset.order)


@sync_to_async
def prepare_cloud_asset_renewal_with_link(asset_id: int, user_id: int, plan_id: int, link_data: dict[str, str], days: int = 31, admin: bool = False, public: bool = False):
    with transaction.atomic():
        asset_qs = CloudAsset.objects.select_related('user', 'order', 'cloud_account').select_for_update().filter(
            id=asset_id,
            kind=CloudAsset.KIND_SERVER,
        ).exclude(status__in=_INACTIVE_ASSET_STATUSES)
        if not admin and not public:
            asset_qs = asset_qs.filter(user_id=user_id)
        asset = asset_qs.first()
        if not asset:
            return None, '代理记录不存在'
        if public and (asset.order_id or not _is_unattached_static_ip_asset(asset) or _cloud_asset_deleted_or_missing(asset)):
            return None, '代理记录不存在'
        if asset.order_id:
            return None, '该代理已绑定订单，请重新进入详情续费'
        public_ip = str(asset.public_ip or asset.previous_public_ip or '').strip()
        if not public_ip:
            return None, '代理缺少公网 IP，暂时无法续费'
        original_account = _resolve_asset_original_cloud_account(asset)
        if _is_unattached_static_ip_asset(asset) and not original_account:
            return None, '原固定 IP 所属云账号不可用，暂时无法自助续费，请联系人工客服。'
        expected_port = int(asset.mtproxy_port or MTPROXY_DEFAULT_PORT)
        if str(link_data.get('port') or '').strip() != str(expected_port):
            return None, f'链接端口不匹配。当前主代理端口是 {expected_port}，你发的是 {link_data.get("port") or "-"}'
        target_plan = CloudServerPlan.objects.filter(
            id=plan_id,
            provider=asset.provider or CloudServerPlan.PROVIDER_AWS_LIGHTSAIL,
            is_active=True,
        ).first()
        if not target_plan:
            return None, '目标套餐不存在或已下架'
        if asset.region_code and target_plan.region_code != asset.region_code:
            return None, '目标套餐不属于当前代理地区'
        renewal_user = asset.user if admin and asset.user_id else TelegramUser.objects.select_for_update().get(id=user_id)
        discounted_total = _apply_cloud_discount(Decimal(target_plan.price), renewal_user.cloud_discount_rate)
        now = timezone.now()
        unattached_static_ip_name = asset.asset_name if _is_unattached_static_ip_asset(asset) else _resolve_unattached_aws_static_ip_name_for_asset(asset)
        payment_currency = 'USDT'
        order = CloudServerOrder.objects.create(
            user=renewal_user,
            order_no=_generate_cloud_order_no('SRVASSET', f'RENEW{asset.id}'),
            plan=target_plan,
            provider=target_plan.provider,
            cloud_account=original_account if _is_unattached_static_ip_asset(asset) else (asset.cloud_account or get_cloud_account_from_label(asset.account_label, target_plan.provider)),
            account_label=cloud_account_label(original_account) if _is_unattached_static_ip_asset(asset) else asset.account_label,
            region_code=target_plan.region_code,
            region_name=target_plan.region_name,
            plan_name=target_plan.plan_name,
            quantity=1,
            currency=payment_currency,
            total_amount=discounted_total,
            pay_amount=_generate_unique_pay_amount(discounted_total, payment_currency),
            pay_method='address',
            status='pending',
            lifecycle_days=days,
            public_ip=asset.public_ip,
            previous_public_ip=asset.previous_public_ip or asset.public_ip,
            instance_id=asset.instance_id,
            provider_resource_id=asset.provider_resource_id,
            server_name=asset.asset_name,
            static_ip_name=unattached_static_ip_name,
            service_started_at=None,
            ip_recycle_at=asset.actual_expires_at if _is_unattached_static_ip_asset(asset) else None,
            mtproxy_link=link_data['url'],
            mtproxy_secret=link_data['secret'],
            mtproxy_host=link_data['server'],
            mtproxy_port=expected_port,
            proxy_links=[{'name': '主代理 mtg', 'server': link_data['server'], 'port': link_data['port'], 'secret': link_data['secret'], 'url': link_data['url']}],
            login_user=asset.login_user,
            login_password=asset.login_password,
            last_user_id=getattr(renewal_user, 'tg_user_id', None),
            expired_at=now + timezone.timedelta(minutes=30),
            provision_note='\n'.join(filter(None, [
                str(asset.note or '').strip(),
                f'{_ASSET_RENEWAL_MARKER}：来源资产 #{asset.id}；已选择套餐 {target_plan.plan_name}；旧IP={public_ip}；旧端口={expected_port}；旧secret={link_data["secret"]}。支付完成后自动创建服务器并绑定该固定 IP，继续使用旧主代理链接。',
                f'灰区续费：AWS 实时确认固定 IP 未附加，固定IP名={unattached_static_ip_name}。' if unattached_static_ip_name and not _is_unattached_static_ip_asset(asset) else '',
            ])),
        )
        if _is_unattached_static_ip_asset(asset) and asset.actual_expires_at:
            CloudServerOrder.objects.filter(id=order.id).update(ip_recycle_at=asset.actual_expires_at, updated_at=timezone.now())
            order.ip_recycle_at = asset.actual_expires_at
        asset.order = order
        asset.price = discounted_total
        asset.mtproxy_link = order.mtproxy_link
        asset.mtproxy_secret = order.mtproxy_secret
        asset.mtproxy_host = order.mtproxy_host
        asset.mtproxy_port = order.mtproxy_port
        asset.proxy_links = order.proxy_links
        asset.save(update_fields=['order', 'price', 'mtproxy_link', 'mtproxy_secret', 'mtproxy_host', 'mtproxy_port', 'proxy_links', 'updated_at'])
        record_cloud_ip_log(
            event_type=CloudIpLog.EVENT_CREATED,
            order=order,
            asset=asset,
            public_ip=asset.public_ip,
            previous_public_ip=asset.previous_public_ip,
            note=f'未绑定代理资产 #{asset.id} 选择套餐续费，生成待支付订单 {order.order_no}',
        )
        return order, None


@sync_to_async
def prepare_retained_ip_renewal_with_link(order_id: int, user_id: int, plan_id: int, link_data: dict[str, str], days: int = 31, admin: bool = False):
    with transaction.atomic():
        order_qs = CloudServerOrder.objects.select_related('user', 'plan').select_for_update().filter(id=order_id)
        if not admin:
            order_qs = order_qs.filter(user_id=user_id)
        order = order_qs.first()
        if not order:
            return None, '服务器记录不存在'
        order = _hydrate_order_from_proxy_asset(order)
        if not _is_retained_ip_renewal_candidate(order):
            return None, '当前服务器不是未附加固定 IP 保留期状态'
        if not _can_order_be_renewed(order):
            return None, '该服务器IP已删除，禁止续费'
        expected_port = int(order.mtproxy_port or MTPROXY_DEFAULT_PORT)
        if str(link_data.get('port') or '').strip() != str(expected_port):
            return None, f'链接端口不匹配。当前主代理端口是 {expected_port}，你发的是 {link_data.get("port") or "-"}'
        target_plan = CloudServerPlan.objects.filter(
            id=plan_id,
            provider=order.provider,
            region_code=order.region_code,
            is_active=True,
        ).first()
        if not target_plan:
            return None, '目标套餐不存在或不属于当前固定 IP 地区'
        renewal_user = order.user if admin and order.user_id else TelegramUser.objects.select_for_update().get(id=user_id)
        discounted_total = _apply_cloud_discount(Decimal(target_plan.price), renewal_user.cloud_discount_rate)
        payment_currency = 'USDT'
        old_ip = order.public_ip or order.previous_public_ip or ''
        order.plan = target_plan
        order.provider = target_plan.provider
        order.region_code = target_plan.region_code
        order.region_name = target_plan.region_name
        order.plan_name = target_plan.plan_name
        order.currency = payment_currency
        order.total_amount = discounted_total
        order.pay_method = 'address'
        order.pay_amount = _generate_unique_pay_amount(discounted_total, payment_currency)
        order.status = 'renew_pending'
        order.lifecycle_days = days
        order.tx_hash = None
        order.payer_address = ''
        order.receive_address = ''
        order.paid_at = None
        order.expired_at = timezone.now() + timezone.timedelta(minutes=30)
        order.mtproxy_link = link_data['url']
        order.mtproxy_secret = link_data['secret']
        order.mtproxy_host = link_data['server']
        order.mtproxy_port = expected_port
        links = list(order.proxy_links or [])
        links = [item for item in links if not (isinstance(item, dict) and str(item.get('port') or '') == str(order.mtproxy_port))]
        links.insert(0, {'name': '主代理 mtg', 'server': link_data['server'], 'port': link_data['port'], 'secret': link_data['secret'], 'url': link_data['url']})
        order.proxy_links = links
        order.provision_note = '\n'.join(filter(None, [
            order.provision_note,
            f'未附加固定 IP 续费已选择套餐 {target_plan.plan_name}，并校验旧主代理链接；IP={old_ip or "-"}；端口={order.mtproxy_port or "-"}；旧secret={order.mtproxy_secret or "-"}。支付完成后会创建恢复服务器并绑定原固定 IP。',
        ]))
        order.save(update_fields=[
            'plan', 'provider', 'region_code', 'region_name', 'plan_name', 'currency', 'total_amount',
            'pay_method', 'pay_amount', 'status', 'lifecycle_days', 'tx_hash', 'payer_address',
            'receive_address', 'paid_at', 'expired_at', 'mtproxy_link', 'mtproxy_secret', 'mtproxy_host',
            'mtproxy_port', 'proxy_links', 'provision_note', 'updated_at',
        ])
        _update_order_primary_records(
            order,
            asset_updates={
                'mtproxy_link': order.mtproxy_link,
                'mtproxy_secret': order.mtproxy_secret,
                'mtproxy_host': order.mtproxy_host,
                'mtproxy_port': order.mtproxy_port,
                'proxy_links': links,
                'price': discounted_total,
            },
            now=timezone.now(),
        )
        return order, None


@sync_to_async
def get_cloud_order_group_balance_lines(order_id: int) -> list[str]:
    asset = CloudAsset.objects.filter(order_id=order_id, telegram_group_id__isnull=False).order_by('-updated_at', '-id').first()
    if not asset:
        return []
    users = []
    seen = set()
    order = CloudServerOrder.objects.select_related('user').filter(id=order_id).first()
    if order and order.user_id:
        users.append(order.user)
        seen.add(order.user_id)
    group_users = (
        TelegramUser.objects
        .filter(cloudasset__telegram_group_id=asset.telegram_group_id, cloudasset__kind=CloudAsset.KIND_SERVER)
        .distinct()
        .order_by('-balance', 'id')
    )
    for user in group_users:
        if user.id in seen:
            continue
        seen.add(user.id)
        users.append(user)
    if len(users) <= 1:
        return []
    lines = []
    for user in users:
        usernames = list(getattr(user, 'usernames', []) or [])
        username = usernames[0] if usernames else (getattr(user, 'username', '') or '')
        label = getattr(user, 'display_name', None) or getattr(user, 'first_name', None) or username or str(getattr(user, 'tg_user_id', '') or user.id)
        if username:
            label = f'{label} (@{username})'
        lines.append(f'- {label}: USDT {Decimal(str(user.balance or 0)):.6f} / TRX {Decimal(str(user.balance_trx or 0)):.6f}')
    return lines


@sync_to_async
def start_cloud_server_from_admin(order_id: int):
    order = CloudServerOrder.objects.filter(id=order_id).first()
    if not order:
        return None, '服务器记录不存在'
    order = _hydrate_order_from_proxy_asset(order)
    if order.provider != 'aws_lightsail':
        return order, '当前云平台暂不支持机器人开机'
    try:
        client = _aws_lightsail_client_for_order(order)
        instance_name = _aws_instance_name_for_order_runtime(client, order)
        if not instance_name:
            return order, '按 IP 未找到实例，且缺少实例名称，无法查询云端状态'
        instance = client.get_instance(instanceName=instance_name).get('instance') or {}
        state = ((instance.get('state') or {}).get('name') or '').lower()
        public_ip = instance.get('publicIpAddress') or order.public_ip or order.previous_public_ip or '-'
        notes = [f'云端初始状态: {state or "未知"}；IP={public_ip}。']
        ok = True
        sync_state = state
        if state == 'running':
            notes.append('服务器已在运行，无需云端开机。')
        elif state == 'stopped':
            client.start_instance(instanceName=instance_name)
            sync_state = 'starting'
            notes.append('检测到关机状态，已发起开机。')
            logger.info('CLOUD_ADMIN_START_INSTANCE order=%s server_name=%s previous_state=%s ip=%s', order.order_no, instance_name, state, public_ip)
        elif state in {'pending', 'starting'}:
            notes.append('实例正在启动中，不重复提交开机。')
        elif state in {'stopping', 'shutting-down'}:
            ok, wait_note, sync_state, public_ip = _start_aws_instance_after_shutdown(client, order, state, public_ip, log_tag='CLOUD_ADMIN_WAIT_SHUTDOWN_START_INSTANCE', instance_name=instance_name)
            notes.append(wait_note)
        else:
            ok = False
            notes.append(f'当前云端状态为 {state or "未知"}，未执行开机。')
        if ok and sync_state != 'running':
            running = False
            for attempt in range(1, AWS_START_WAIT_ATTEMPTS + 1):
                time.sleep(AWS_START_WAIT_SECONDS)
                sync_state, queried_ip = _aws_lightsail_instance_state(client, instance_name)
                public_ip = queried_ip if queried_ip != '-' else public_ip
                if sync_state == 'running':
                    running = True
                    notes.append(f'第 {attempt}/{AWS_START_WAIT_ATTEMPTS} 次检查：服务器已运行。')
                    break
                if sync_state in {'stopped', 'terminated', 'deleted'}:
                    notes.append(f'第 {attempt}/{AWS_START_WAIT_ATTEMPTS} 次检查：状态为 {sync_state}，无法继续检查程序。')
                    break
            if not running:
                ok = False
                notes.append(f'服务器尚未确认运行，当前状态: {sync_state or "未知"}。')
        if ok and sync_state == 'running':
            if order.status == 'suspended':
                order.status = 'completed'
                order.save(update_fields=['status', 'updated_at'])
            _sync_order_cloud_runtime_state(order, sync_state, public_ip, '\n'.join(notes))
            order.refresh_from_db()
            mtproxy_ok, mtproxy_note = _ensure_mtproxy_after_renewal(order)
            notes.append(mtproxy_note)
            ok = mtproxy_ok
        note = '\n'.join(filter(None, notes))
        order.provision_note = '\n'.join(filter(None, [order.provision_note, f'管理员手动开机：{note}']))
        order.save(update_fields=['provision_note', 'updated_at'])
        _sync_order_cloud_runtime_state(order, sync_state, public_ip, order.provision_note)
        return order, None if ok else note
    except Exception as exc:
        note = f'开机状态查询/执行失败: {exc}'
        logger.warning('CLOUD_ADMIN_START_INSTANCE_FAILED order=%s server_name=%s error=%s', order.order_no, order.server_name, exc)
        order.provision_note = '\n'.join(filter(None, [order.provision_note, f'管理员手动开机：{note}']))
        order.save(update_fields=['provision_note', 'updated_at'])
        return order, note


@sync_to_async
def apply_cloud_server_renewal(order_id: int, days: int = 31, run_post_checks: bool = True):
    order = _hydrate_order_from_proxy_asset(CloudServerOrder.objects.get(id=order_id))
    ok, renew_note = _renew_aliyun_instance(order, days)
    if not ok:
        order.provision_note = append_note(order.provision_note, renew_note)
        order.save(update_fields=['provision_note', 'updated_at'])
        raise ValueError(renew_note)
    now = timezone.now()
    current_expires_at = order_asset_expiry(order)
    renew_base_at = current_expires_at if current_expires_at and current_expires_at > now else now
    if not order.service_started_at:
        order.service_started_at = now
    new_expires_at = renew_base_at + timezone.timedelta(days=days)
    apply_order_lifecycle_from_asset_expiry(order, new_expires_at, save=False)
    order.last_renewed_at = now
    order.renew_notice_sent_at = None
    if hasattr(order, 'auto_renew_notice_sent_at'):
        order.auto_renew_notice_sent_at = None
    if hasattr(order, 'auto_renew_failure_notice_sent_at'):
        order.auto_renew_failure_notice_sent_at = None
    order.delete_notice_sent_at = None
    order.recycle_notice_sent_at = None
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
    order.provision_note = append_note(
        order.provision_note,
        '\n'.join(filter(None, [renew_note or f'续费成功，{base_note} {days} 天。', retention_note, *post_notes])),
    )
    order.save(update_fields=[
        'service_started_at', 'renew_grace_expires_at', 'suspend_at', 'delete_at', 'ip_recycle_at',
        'last_renewed_at', 'renew_notice_sent_at', 'auto_renew_notice_sent_at',
        'auto_renew_failure_notice_sent_at', 'delete_notice_sent_at', 'recycle_notice_sent_at',
        'ip_change_quota', 'status', 'provision_note', 'updated_at',
    ])
    if retained_ip:
        recovery_order, recovery_err = _create_retained_ip_recovery_order(order, days)
        if recovery_err:
            order.provision_note = '\n'.join(filter(None, [order.provision_note, recovery_err]))
            order.save(update_fields=['provision_note', 'updated_at'])
            raise ValueError(recovery_err)
    asset_update = {'actual_expires_at': new_expires_at, 'updated_at': timezone.now()}
    server_update = {'expires_at': new_expires_at, 'updated_at': timezone.now()}
    if retained_ip:
        asset_update.update({'public_ip': order.public_ip or order.previous_public_ip, 'previous_public_ip': order.public_ip or order.previous_public_ip, 'provider_status': '未附加固定IP-续费保留中', 'is_active': False})
        server_update.update({'public_ip': order.public_ip or order.previous_public_ip, 'previous_public_ip': order.public_ip or order.previous_public_ip, 'provider_status': '未附加固定IP-续费保留中', 'is_active': False})
    _update_order_primary_records(order, asset_updates=asset_update, server_updates=server_update)
    record_cloud_ip_log(
        event_type=CloudIpLog.EVENT_RENEWED,
        order=order,
        public_ip=order.public_ip,
        previous_public_ip=order.previous_public_ip,
        note=f'服务器续费 {days} 天，新的服务到期时间：{new_expires_at:%Y-%m-%d %H:%M}；{renew_note}',
    )
    transaction.on_commit(lambda: _refresh_dashboard_plan_snapshots_after_service_change(f'cloud_server_renewal:{order.id}'))
    return recovery_order or order


@sync_to_async
def pay_cloud_server_renewal_with_balance(order_id: int, user_id: int, currency: str = 'USDT', days: int = 31):
    try:
        already_paid = False
        balance_field = 'balance_trx' if currency == 'TRX' else 'balance'
        ledger = None
        old_balance = None
        user = None
        total = Decimal('0')
        with transaction.atomic():
            order = CloudServerOrder.objects.select_related('user').select_for_update().filter(id=order_id).first()
            if not order:
                return None, '订单不存在'
            order = _hydrate_order_from_proxy_asset(order)
            asset_recovery_order = is_cloud_asset_renewal_order(order)
            if order.status not in {'renew_pending', 'pending'}:
                if asset_recovery_order and order.status == 'completed' and not order.paid_at and not str(order.instance_id or '').strip():
                    order.status = 'pending'
                else:
                    return None, '当前订单状态不可钱包支付'
            if not asset_recovery_order and not _can_order_be_renewed(order):
                return None, '该服务器IP已删除，禁止续费'
            if order.paid_at and order.pay_method == 'balance':
                already_paid = True
            else:
                user = TelegramUser.objects.select_for_update().get(id=user_id)
                total_amount_usdt, total = _renewal_wallet_charge_amount(order, user, currency)
                current_balance = Decimal(str(getattr(user, balance_field, 0) or 0))
                if current_balance < total:
                    return None, f'{currency} 余额不足'
                old_balance = current_balance
                setattr(user, balance_field, current_balance - total)
                user.save(update_fields=[balance_field, 'updated_at'])
                paid_at = timezone.now()
                order_updates = {
                    'currency': currency,
                    'total_amount': total_amount_usdt,
                    'pay_method': 'balance',
                    'pay_amount': total,
                    'paid_at': paid_at,
                    'expired_at': None,
                    'updated_at': paid_at,
                }
                if asset_recovery_order:
                    order_updates['status'] = 'paid'
                    order.provision_note = append_note(order.provision_note, '已收款，正在恢复未绑定代理资产固定 IP。')
                    order_updates['provision_note'] = order.provision_note
                CloudServerOrder.objects.filter(id=order.id).update(**order_updates)
                order.currency = currency
                order.total_amount = total_amount_usdt
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
            if asset_recovery_order:
                order.status = 'paid'
            else:
                order = apply_cloud_server_renewal.__wrapped__(order_id, days, False)
        if not already_paid:
            order.renew_balance_change = {
                'currency': currency,
                'amount': getattr(ledger, 'amount', total) if ledger else total,
                'before': old_balance,
                'after': getattr(user, balance_field) if user else None,
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
    primary_asset = _order_primary_asset(order)
    def _record_is_running(record) -> bool:
        if not record:
            return False
        if getattr(record, 'is_active', True) is False:
            return False
        status = str(getattr(record, 'status', '') or '').lower()
        provider_status = str(getattr(record, 'provider_status', '') or '').lower()
        return status == CloudAsset.STATUS_RUNNING or provider_status == 'running'

    if _record_is_running(primary_asset):
        note = '续费后自动巡检：本地记录为正在运行，已跳过开机和 MTProxy 巡检。'
        asset_expires_at = order_asset_expiry(order, primary_asset)
        _update_order_primary_records(
            order,
            asset_updates={'actual_expires_at': asset_expires_at},
            server_updates={'expires_at': asset_expires_at},
        )
        order.provision_note = append_note(order.provision_note, note)
        order.save(update_fields=['provision_note', 'updated_at'])
        return order, None
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
    with transaction.atomic():
        order = CloudServerOrder.objects.select_for_update().select_related('user').get(id=order_id)
        old_user_id = order.user_id
        new_user = TelegramUser.objects.get(id=new_user_id)
        order.user = new_user
        order.last_user_id = getattr(new_user, 'tg_user_id', None) or new_user.id
        order.save(update_fields=['user', 'last_user_id', 'updated_at'])
        asset, _ = _update_order_primary_records(
            order,
            asset_updates={'user': new_user},
            server_updates={'user': new_user},
            now=timezone.now(),
        )
        record_cloud_ip_log(
            event_type='changed',
            order=order,
            asset=asset,
            public_ip=order.public_ip,
            previous_public_ip=order.previous_public_ip,
            note=f'服务器所属用户已更新：{old_user_id or "-"} -> {new_user.id}',
        )
        return order


@sync_to_async
def mark_cloud_server_ip_change_requested(order_id: int, user_id: int, region_code: str | None = None, port: int | None = None, admin: bool = False):
    with transaction.atomic():
        order_qs = CloudServerOrder.objects.select_for_update().filter(id=order_id)
        if not admin:
            order_qs = order_qs.filter(user_id=user_id)
        order = order_qs.first()
        if not order:
            return None
        if order.status not in {'completed', 'expiring', 'suspended'}:
            return False
        if order.provider != 'aws_lightsail':
            return False
        existing_replacement = CloudServerOrder.objects.filter(
            replacement_for=order,
            status__in={'paid', 'provisioning', 'completed'},
        ).order_by('-created_at', '-id').first()
        if existing_replacement:
            return existing_replacement
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
        target_port = port or order.mtproxy_port or MTPROXY_DEFAULT_PORT
        original_asset_expires_at = order_asset_expiry(order)
        remaining_ip_changes -= 1
        now = timezone.now()
        migration_due_at = now + timezone.timedelta(days=5)
        delete_at = migration_due_at + timezone.timedelta(days=3)
        ip_recycle_at = delete_at + timezone.timedelta(days=15)
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
            mtproxy_port=target_port,
            mtproxy_secret=order.mtproxy_secret,
            mtproxy_link=order.mtproxy_link,
            proxy_links=order.proxy_links or [],
            static_ip_name='',
            service_started_at=order.service_started_at or now,
            migration_due_at=migration_due_at,
            replacement_for=order,
            ip_change_quota=remaining_ip_changes,
            last_user_id=order.last_user_id,
            previous_public_ip=old_public_ip,
            server_name=order.server_name,
            image_name=order.image_name,
            provision_note='\n'.join(filter(None, [order.provision_note, f'由订单 {order.order_no} 发起更换 IP，新建同配置服务器，地区: {fallback_plan.region_name}，端口: {target_port}，会申请并绑定新的固定 IP，需在 5 天内切换使用。', old_trace_note])),
        )
        _ensure_order_asset_expiry_record(new_order, original_asset_expires_at)
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
    public_ip = str(order.public_ip or order.previous_public_ip or '').strip()
    if order.provider != 'aws_lightsail' or not public_ip:
        return ''
    account = getattr(order, 'cloud_account', None) or get_cloud_account_from_label(getattr(order, 'account_label', ''), 'aws')
    access_key = ''
    secret_key = ''
    if account:
        ak = (account.access_key_plain or '').strip()
        sk = (account.secret_key_plain or '').strip()
        if ak and sk and len(ak) >= 16 and len(sk) >= 36:
            access_key, secret_key = ak, sk
    if not access_key or not secret_key:
        logger.warning('AWS 固定 IP 名称反查失败: order=%s ip=%s reason=missing_bound_account_credentials', order.order_no, public_ip)
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
    old_port = order.mtproxy_port or MTPROXY_DEFAULT_PORT
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
        mtproxy_port=order.mtproxy_port or MTPROXY_DEFAULT_PORT,
        mtproxy_secret=order.mtproxy_secret,
        mtproxy_link=order.mtproxy_link,
        proxy_links=order.proxy_links or [],
        static_ip_name=order.static_ip_name,
        replacement_for=order,
        last_user_id=order.last_user_id,
        previous_public_ip=old_public_ip,
        service_started_at=order.service_started_at,
        server_name=order.server_name,
        image_name=order.image_name,
        provision_note='\n'.join(filter(None, [order.provision_note, f'后台发起重装迁移：新实例创建时不申请临时固定 IP，创建成功后直接迁移原固定 IP {order.static_ip_name}，再安装代理。', old_trace_note])),
    )
    _ensure_order_asset_expiry_record(new_order, order_asset_expiry(order))
    _set_source_migration_expiry(
        order,
        migration_due_at,
        '重装/重建迁移旧机到期时间调整',
        f'已发起重装迁移，新实例订单: {new_order.order_no}。旧机追溯：旧IP={old_public_ip or "-"}；旧端口={old_port or "-"}；旧secret={old_secret or "-"}；固定IP={order.static_ip_name or "-"}。旧实例服务到期时间调整为 {migration_due_at:%Y-%m-%d %H:%M}，宽限 3 天后删除，删除后固定 IP 保留 15 天；新机创建后会先迁移固定 IP {order.static_ip_name}，并强制沿用旧 secret 安装代理。'
    )
    return new_order, None


def run_cloud_server_rebuild_job(new_order_id: int):
    from cloud.provisioning import provision_cloud_server

    max_attempts = 3
    retry_delays = [0, 20, 60]
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            time.sleep(retry_delays[attempt - 1])
        try:
            saved = async_to_sync(provision_cloud_server)(new_order_id)
            if saved and getattr(saved, 'status', '') == 'completed' and getattr(saved, 'replacement_for_id', None):
                logger.info(
                    'AWS 重装迁移后台任务完成，旧实例进入迁移保留期: new_order_id=%s replacement_for_id=%s',
                    saved.id,
                    saved.replacement_for_id,
                )
                return
            logger.warning('AWS 重装迁移后台任务未完成，准备重试: new_order_id=%s attempt=%s/%s status=%s', new_order_id, attempt, max_attempts, getattr(saved, 'status', None) if saved else None)
        except Exception:
            logger.exception('AWS 重装迁移后台任务异常，准备重试: new_order_id=%s attempt=%s/%s', new_order_id, attempt, max_attempts)

    order = CloudServerOrder.objects.filter(id=new_order_id).first()
    if not order:
        return
    failure_note = f'重装迁移自动重试失败：已重试 {max_attempts} 次，继续保留旧机服务，请人工检查后再试。'
    order.provision_note = '\n'.join(filter(None, [order.provision_note, failure_note]))
    order.save(update_fields=['provision_note', 'updated_at'])
    source_order = CloudServerOrder.objects.filter(id=order.replacement_for_id).first()
    if source_order:
        source_order.provision_note = '\n'.join(filter(None, [source_order.provision_note, failure_note]))
        source_order.save(update_fields=['provision_note', 'updated_at'])
        _update_order_primary_records(
            source_order,
            asset_updates=drop_asset_note_update({'note': failure_note}),
            server_updates=drop_asset_note_update({'note': failure_note}),
        )


@sync_to_async
def mark_cloud_server_reinit_requested(order_id: int, user_id: int | None):
    qs = CloudServerOrder.objects.filter(id=order_id)
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    order = qs.first()
    if not order:
        return None
    is_unfinished = order.status in {'paid', 'provisioning', 'failed'} and not order.replacement_for_id
    if is_unfinished:
        note = '用户发起继续初始化请求，允许重新生成代理链接。'
        order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
        order.save(update_fields=['provision_note', 'updated_at'])
        return order
    if order.status not in {'completed', 'expiring', 'renew_pending', 'suspended'}:
        return '当前状态不允许重新安装'
    if not str(order.public_ip or '').strip() or not str(order.login_password or '').strip():
        return False
    if not _has_main_proxy_link(order):
        return 'missing_main_link'
    note = '用户发起当前服务器重新安装请求：仅重新执行 BBR/MTProxy 安装，不创建新实例，不迁移固定 IP。'
    order.provision_note = '\n'.join(filter(None, [order.provision_note, note]))
    order.save(update_fields=['provision_note', 'updated_at'])
    return order


def _has_main_proxy_link(order: CloudServerOrder) -> bool:
    if getattr(order, 'mtproxy_link', None):
        return True
    for item in getattr(order, 'proxy_links', None) or []:
        if isinstance(item, dict) and item.get('url') and str(item.get('port') or '') == str(order.mtproxy_port or MTPROXY_DEFAULT_PORT):
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


def _cloud_config_effective_current_price(current_price: Decimal, plans: list[CloudServerPlan], user: TelegramUser | None = None) -> Decimal:
    current_price = Decimal(current_price).quantize(Decimal('0.01'))
    plan_prices = sorted({
        _apply_cloud_discount(Decimal(plan.price), getattr(user, 'cloud_discount_rate', Decimal('0'))).quantize(Decimal('0.01'))
        for plan in plans
    })
    for plan_price in plan_prices:
        if plan_price >= current_price:
            return plan_price
    return current_price


@sync_to_async
def list_cloud_server_upgrade_plans(order_id: int, user_id: int, admin: bool = False):
    queryset = CloudServerOrder.objects.select_related('plan', 'user').filter(id=order_id)
    if not admin:
        queryset = queryset.filter(user_id=user_id)
    order = _hydrate_order_from_proxy_asset(queryset.first())
    if not order:
        return [], '服务器记录不存在'
    if order.provider != 'aws_lightsail':
        return [], '当前服务器不支持修改配置'
    if order.status not in {'completed', 'expiring', 'suspended'}:
        return [], '当前状态不允许修改配置'
    if not _has_main_proxy_link(order):
        return [], '当前服务器没有主代理链接，请先在后台添加主链接后再修改配置'
    current_price = _renewal_price(order, order.user)
    blocks, target_expiry, _ = _upgrade_blocks_and_expiry(order_asset_expiry(order))
    plans = list(CloudServerPlan.objects.filter(provider=order.provider, region_code=order.region_code, is_active=True).order_by('price', 'sort_order', 'id'))
    if not plans:
        plans = list(CloudServerPlan.objects.filter(provider=order.provider, is_active=True).order_by('price', 'sort_order', 'id'))
    effective_current_price = _cloud_config_effective_current_price(current_price, plans, order.user)
    result = []
    for plan in plans:
        if plan.id == getattr(order, 'plan_id', None):
            continue
        target_price = _apply_cloud_discount(Decimal(plan.price), order.user.cloud_discount_rate)
        price_delta = target_price - effective_current_price
        if price_delta == 0:
            continue
        diff = (max(price_delta, Decimal('0')) * Decimal(blocks)).quantize(Decimal('0.01'))
        action = 'upgrade' if price_delta > 0 else 'downgrade'
        result.append({'id': plan.id, 'name': plan.plan_name, 'price': str(target_price.quantize(Decimal('0.001'))), 'diff': str(diff.quantize(Decimal('0.001'))), 'target_days': blocks * 31, 'target_expiry': target_expiry, 'action': action})
    return result, None


@sync_to_async
def create_cloud_server_upgrade_order(order_id: int, user_id: int, target_plan_id: int, admin: bool = False):
    queryset = CloudServerOrder.objects.select_related('plan', 'user').filter(id=order_id)
    if not admin:
        queryset = queryset.filter(user_id=user_id)
    order = _hydrate_order_from_proxy_asset(queryset.first())
    if not order:
        return None, '服务器记录不存在'
    if order.provider != 'aws_lightsail':
        return None, '当前仅支持 AWS Lightsail 修改配置迁移'
    if order.status not in {'completed', 'expiring', 'suspended'}:
        return None, '当前状态不允许修改配置'
    resolved_static_ip_name = _resolve_aws_static_ip_name_for_order(order)
    if resolved_static_ip_name and resolved_static_ip_name != order.static_ip_name:
        order.static_ip_name = resolved_static_ip_name
        order.save(update_fields=['static_ip_name', 'updated_at'])
    if not order.static_ip_name:
        return None, '当前服务器没有固定 IP，无法保证链接不变'
    if not _has_main_proxy_link(order):
        return None, '当前服务器没有主代理链接，请先在后台添加主链接后再修改配置'
    if not order.mtproxy_secret:
        return None, '当前服务器缺少 MTProxy 密钥，无法保证链接不变'
    target_plan = CloudServerPlan.objects.filter(id=target_plan_id, provider=order.provider, region_code=order.region_code, is_active=True).first()
    if not target_plan:
        target_plan = CloudServerPlan.objects.filter(id=target_plan_id, provider=order.provider, is_active=True).first()
    if not target_plan:
        return None, '目标套餐不存在'
    current_price = _renewal_price(order, order.user)
    candidate_plans = list(CloudServerPlan.objects.filter(provider=order.provider, region_code=order.region_code, is_active=True).order_by('price', 'sort_order', 'id'))
    if not candidate_plans:
        candidate_plans = list(CloudServerPlan.objects.filter(provider=order.provider, is_active=True).order_by('price', 'sort_order', 'id'))
    effective_current_price = _cloud_config_effective_current_price(current_price, candidate_plans, order.user)
    target_price = _apply_cloud_discount(Decimal(target_plan.price), order.user.cloud_discount_rate)
    if target_plan.id == getattr(order, 'plan_id', None) or target_price == effective_current_price:
        return None, '目标套餐与当前配置相同'
    blocks, target_expiry, _ = _upgrade_blocks_and_expiry(order_asset_expiry(order))
    price_delta = target_price - effective_current_price
    diff = (max(price_delta, Decimal('0')) * Decimal(blocks)).quantize(Decimal('0.01'))
    charged_amount = Decimal('0.00') if admin else diff
    action_label = '升级配置' if price_delta > 0 else '降级配置'
    operation_code = 'UPGRADE' if price_delta > 0 else 'DOWNGRADE'
    initiator_label = '管理员' if admin else '用户'
    now = timezone.now()
    suffix = now.strftime('%m%d%H%M%S')
    old_public_ip = order.public_ip or order.previous_public_ip or ''
    old_port = order.mtproxy_port or MTPROXY_DEFAULT_PORT
    old_secret = order.mtproxy_secret or ''
    new_order_no = _trim_operation_order_no(order, operation_code, suffix)
    lifecycle_fields = compute_order_lifecycle_fields(target_expiry)
    old_trace_note = (
        f'{action_label}追溯：来源订单 {order.order_no}；旧IP={old_public_ip or "-"}；旧端口={old_port or "-"}；'
        f'旧secret={old_secret or "-"}；固定IP={order.static_ip_name or "-"}；'
        f'配置 {order.plan_name} -> {target_plan.plan_name}；补差价 {diff} USDT；新订单必须继承旧 secret，用户提供链接时必须逐项对照 IP/端口/secret。'
    )
    new_order = CloudServerOrder(
        user_id=order.user_id,
        order_no=new_order_no,
        plan=target_plan,
        provider=target_plan.provider,
        cloud_account=order.cloud_account,
        account_label=order.account_label or order.provider,
        region_code=order.region_code or target_plan.region_code,
        region_name=order.region_name or target_plan.region_name,
        plan_name=target_plan.plan_name,
        provider_resource_id=(target_plan.provider_plan_id or None),
        quantity=1,
        currency='USDT',
        total_amount=diff,
        pay_amount=charged_amount,
        pay_method='balance',
        status='paid',
        lifecycle_days=blocks * 31,
        mtproxy_port=order.mtproxy_port or MTPROXY_DEFAULT_PORT,
        mtproxy_secret=order.mtproxy_secret,
        mtproxy_link=order.mtproxy_link,
        proxy_links=order.proxy_links or [],
        static_ip_name=order.static_ip_name,
        replacement_for=order,
        last_user_id=order.last_user_id,
        previous_public_ip=old_public_ip,
        service_started_at=order.service_started_at or now,
        server_name=order.server_name,
        image_name=order.image_name,
        paid_at=now,
        created_at=now,
        updated_at=now,
        provision_note='\n'.join(filter(None, [order.provision_note, f'{initiator_label}发起{action_label}：{order.plan_name} -> {target_plan.plan_name}，补差价 {diff} USDT，实扣 {charged_amount} USDT，目标到期 {target_expiry:%Y-%m-%d %H:%M}，保持主/备用代理链路不变。', old_trace_note])),
        **lifecycle_fields,
    )
    with transaction.atomic():
        locked_queryset = CloudServerOrder.objects.select_for_update().filter(id=order.id)
        if not admin:
            locked_queryset = locked_queryset.filter(user_id=user_id)
        locked_order = locked_queryset.first()
        if not locked_order or locked_order.status not in {'completed', 'expiring', 'suspended'}:
            return None, '当前状态不允许修改配置'
        existing_upgrade = CloudServerOrder.objects.filter(replacement_for=locked_order, status__in={'paid', 'provisioning', 'completed'}).order_by('-created_at', '-id').first()
        if existing_upgrade:
            return None, f'已有配置调整任务 {existing_upgrade.order_no}，请勿重复提交'
        billing_user_id = locked_order.user_id
        user = TelegramUser.objects.select_for_update().get(id=billing_user_id)
        current_balance = Decimal(str(user.balance or 0))
        if not admin and current_balance < diff:
            return None, f'USDT 余额不足，需要补差价 {diff} U'
        old_balance = current_balance
        if not admin:
            user.balance = current_balance - diff
            user.save(update_fields=['balance', 'updated_at'])
        CloudServerOrder.objects.bulk_create([new_order])
        new_order = CloudServerOrder.objects.get(order_no=new_order_no)
        _ensure_order_asset_expiry_record(new_order, target_expiry)
        if not admin and diff > 0:
            record_balance_ledger(user, ledger_type='cloud_order_balance_pay', currency='USDT', old_balance=old_balance, new_balance=user.balance, related_type='cloud_order', related_id=new_order.id, description=f'云服务器{action_label}补差价 #{new_order.order_no}')
        source_note = '\n'.join(filter(None, [order.provision_note, f'已由{initiator_label}发起{action_label}，新实例订单: {new_order.order_no}，补差价 {diff} USDT，实扣 {charged_amount} USDT。旧机追溯：旧IP={old_public_ip or "-"}；旧端口={old_port or "-"}；旧secret={old_secret or "-"}；固定IP={order.static_ip_name or "-"}；新配置={target_plan.plan_name}；新服务器必须继承旧 secret。']))
        CloudServerOrder.objects.filter(id=order.id).update(provision_note=source_note, updated_at=timezone.now())
        record_cloud_ip_log(
            event_type='changed',
            order=locked_order,
            public_ip=old_public_ip or locked_order.public_ip,
            previous_public_ip=old_public_ip or locked_order.previous_public_ip,
            trigger_label='修改配置',
            note=(
                f'{action_label}已发起，旧服务器生命周期进入迁移跟踪；'
                f'旧机订单 {locked_order.order_no}；新实例订单 {new_order.order_no}；'
                f'旧机IP {old_public_ip or "-"}；旧端口 {old_port or "-"}；固定IP {order.static_ip_name or "-"}；'
                f'配置 {order.plan_name} -> {target_plan.plan_name}；'
                f'当前处理：旧服务器继续服务，待新配置实例创建成功并迁移固定 IP 后，旧服务器会进入保留期并按计划删除。'
            ),
        )
    return new_order, None


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
    cloud_orders = []
    seen_order_ids = set()
    reminder_assets = (
        CloudAsset.objects.select_related('order')
        .filter(kind=CloudAsset.KIND_SERVER, order__user_id=user_id, actual_expires_at__isnull=False)
        .exclude(order__status__in=['cancelled', 'deleted'])
        .order_by('actual_expires_at', '-order_id', '-id')
    )
    for asset in reminder_assets:
        order = asset.order
        if not order or order.id in seen_order_ids:
            continue
        seen_order_ids.add(order.id)
        cloud_orders.append(order)
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
    cloud_orders = CloudServerOrder.objects.filter(user_id=user_id).exclude(status__in=['cancelled', 'deleted'])
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


def _set_cloud_server_auto_renew(order: CloudServerOrder | None, enabled: bool):
    if not order:
        return None
    if enabled and not _can_order_be_renewed(order):
        return False
    order.auto_renew_enabled = enabled
    order.save(update_fields=['auto_renew_enabled', 'updated_at'])
    return order


@sync_to_async
def set_cloud_server_auto_renew(order_id: int, user_id: int, enabled: bool):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        asset = (
            CloudAsset.objects.select_related('order')
            .filter(kind=CloudAsset.KIND_SERVER, order_id=order_id)
            .filter(_user_asset_visibility_filter(user_id))
            .filter(Q(public_ip__isnull=False) & ~Q(public_ip=''))
            .filter(_active_cloud_account_asset_filter())
            .exclude(status__in=_INACTIVE_ASSET_STATUSES)
            .order_by('-updated_at', '-id')
            .first()
        )
        order = asset.order if asset and asset.order else None
    return _set_cloud_server_auto_renew(order, enabled)


@sync_to_async
def set_cloud_server_auto_renew_admin(order_id: int, enabled: bool):
    order = CloudServerOrder.objects.filter(id=order_id).first()
    return _set_cloud_server_auto_renew(order, enabled)


def _set_all_cloud_server_auto_renew(enabled: bool, user_id: int | None = None):
    assets = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, order__isnull=False)
    if user_id is not None:
        assets = assets.filter(_user_asset_visibility_filter(user_id))
    order_ids = list(
        assets.filter(Q(public_ip__isnull=False) & ~Q(public_ip=''))
        .filter(_active_cloud_account_asset_filter())
        .exclude(status__in=_INACTIVE_ASSET_STATUSES)
        .values_list('order_id', flat=True)
        .distinct()
    )
    updated = 0
    skipped = 0
    for order in CloudServerOrder.objects.filter(id__in=order_ids):
        result = _set_cloud_server_auto_renew(order, enabled)
        if result:
            updated += 1
        else:
            skipped += 1
    return {'updated': updated, 'skipped': skipped, 'total': len(order_ids)}


@sync_to_async
def set_group_cloud_server_auto_renew(chat_id: int, enabled: bool):
    group = _group_filter_for_chat_id(chat_id)
    if not group:
        return {'updated': 0, 'skipped': 0, 'total': 0}
    assets = (
        _cloud_server_asset_queryset()
        .filter(telegram_group=group, order__isnull=False)
        .select_related('order')
        .order_by('order_id', '-updated_at', '-id')
    )
    order_ids = []
    seen = set()
    skipped = 0
    for asset in assets:
        if _is_unattached_static_ip_asset(asset):
            skipped += 1
            continue
        if not asset.order_id or asset.order_id in seen:
            continue
        seen.add(asset.order_id)
        order_ids.append(asset.order_id)
    updated = 0
    for order in CloudServerOrder.objects.filter(id__in=order_ids):
        result = _set_cloud_server_auto_renew(order, enabled)
        if result:
            updated += 1
        else:
            skipped += 1
    return {'updated': updated, 'skipped': skipped, 'total': len(order_ids) + skipped}


@sync_to_async
def enable_all_cloud_server_auto_renew(user_id: int):
    return _set_all_cloud_server_auto_renew(True, user_id)


@sync_to_async
def disable_all_cloud_server_auto_renew(user_id: int):
    return _set_all_cloud_server_auto_renew(False, user_id)


@sync_to_async
def enable_all_cloud_server_auto_renew_admin():
    return _set_all_cloud_server_auto_renew(True, None)


@sync_to_async
def disable_all_cloud_server_auto_renew_admin():
    return _set_all_cloud_server_auto_renew(False, None)


@sync_to_async
def get_cloud_server_auto_renew(order_id: int, user_id: int):
    order = CloudServerOrder.objects.filter(id=order_id, user_id=user_id).first()
    if not order:
        asset = (
            CloudAsset.objects.select_related('order')
            .filter(kind=CloudAsset.KIND_SERVER, order_id=order_id)
            .filter(_user_asset_visibility_filter(user_id))
            .filter(Q(public_ip__isnull=False) & ~Q(public_ip=''))
            .filter(_active_cloud_account_asset_filter())
            .exclude(status__in=_INACTIVE_ASSET_STATUSES)
            .order_by('-updated_at', '-id')
            .first()
        )
        order = asset.order if asset and asset.order else None
    if not order:
        return None
    return bool(order.auto_renew_enabled)


__all__ = [
    'apply_cloud_server_renewal',
    'build_cloud_server_name',
    'buy_cloud_server_with_balance',
    'create_cloud_server_order',
    'create_cloud_server_renewal',
    'create_cloud_server_renewal_by_public_query',
    'create_cloud_server_renewal_for_user',
    'list_cloud_asset_renewal_plans',
    'get_cloud_order_group_balance_lines',
    'disable_all_cloud_server_auto_renew',
    'disable_all_cloud_server_auto_renew_admin',
    'ensure_cloud_server_pricing',
    'ensure_unique_cloud_server_name',
    'enable_all_cloud_server_auto_renew',
    'enable_all_cloud_server_auto_renew_admin',
    'get_cloud_plan',
    'get_cloud_server_auto_renew',
    'get_user_reminder_summary',
    'get_cloud_server_by_ip',
    'get_cloud_server_by_ip_for_user',
    'get_cloud_server_for_admin',
    'get_proxy_asset_by_ip_for_admin',
    'get_proxy_asset_by_ip_for_user',
    'get_proxy_asset_detail_for_admin',
    'get_group_proxy_asset_detail',
    'get_user_cloud_server',
    'get_user_proxy_asset_detail',
    'update_cloud_item_expiry_for_admin',
    'ensure_cloud_asset_operation_order',
    'initialize_proxy_asset',
    'is_cloud_asset_renewal_order',
    'is_retained_ip_order_visible_in_group',
    'list_custom_regions',
    'list_all_auto_renew_cloud_servers',
    'list_group_auto_renew_cloud_servers',
    'list_group_cloud_servers',
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
    'prepare_cloud_server_order_instances',
    'prepare_cloud_asset_renewal_with_link',
    'create_cloud_server_upgrade_order',
    'list_cloud_server_upgrade_plans',
    'rebind_cloud_server_user',
    'refresh_custom_plan_cache',
    'record_cloud_ip_log',
    'run_cloud_server_rebuild_job',
    'set_cloud_order_reminder',
    'set_group_cloud_server_auto_renew',
    'set_cloud_server_auto_renew',
    'set_cloud_server_auto_renew_admin',
    'start_cloud_server_from_admin',
    'sync_cloud_asset_user_binding',
    'unmute_all_user_reminders',
    'unmute_cloud_reminders',
]
