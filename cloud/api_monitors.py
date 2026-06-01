"""监控地址与云资产 IP 日志后台 API。"""

import json
import logging
import re
from decimal import Decimal

import httpx
from asgiref.sync import async_to_sync
from django.db.models import Q
from django.views.decorators.http import require_GET

from cloud.models import AddressMonitor, CloudAsset, CloudIpLog
from core.cache import get_redis
from core.dashboard_api import (
    _apply_keyword_filter,
    _decimal_to_str,
    _get_keyword,
    _iso,
    _ok,
    _provider_label,
    _region_label,
    _status_label,
    _user_payload,
    dashboard_login_required,
)
from core.runtime_config import get_runtime_config
from core.trongrid import build_trongrid_headers

logger = logging.getLogger(__name__)

ADDRESS_BALANCE_CACHE_TTL = 60
ADDRESS_BALANCE_CACHE_PREFIX = 'address_balance:'


def _cloud_api_override(name: str, fallback):
    try:
        from cloud import api as cloud_api
    except Exception:
        return fallback
    return getattr(cloud_api, name, fallback)


def _trongrid_headers_without_key(headers: dict | None) -> dict:
    return {key: value for key, value in dict(headers or {}).items() if key.lower() != 'tron-pro-api-key'}


def _trongrid_sync_get_with_key_fallback(client: httpx.Client, url: str, headers: dict):
    resp = client.get(url, headers=headers)
    if resp.status_code == 401 and headers.get('TRON-PRO-API-KEY'):
        resp = client.get(url, headers=_trongrid_headers_without_key(headers))
    return resp


def _fetch_address_chain_balances(address: str):
    cache_key = f'{ADDRESS_BALANCE_CACHE_PREFIX}{address}'
    redis_client = async_to_sync(_cloud_api_override('get_redis', get_redis))()
    if redis_client is not None:
        try:
            cached = async_to_sync(redis_client.get)(cache_key)
            if cached:
                payload = json.loads(cached)
                return Decimal(str(payload.get('usdt', '0'))), Decimal(str(payload.get('trx', '0'))), None
        except Exception:
            logger.debug('ADDRESS_BALANCE_CACHE_READ_FAILED address=%s', address, exc_info=True)
    usdt_contract = get_runtime_config('usdt_contract', 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t')
    trongrid_base_url = get_runtime_config('trongrid_base_url', 'https://api.trongrid.io')
    try:
        headers = async_to_sync(_cloud_api_override('build_trongrid_headers', build_trongrid_headers))()
        httpx_module = _cloud_api_override('httpx', httpx)
        with httpx_module.Client(timeout=8) as client:
            resp = _trongrid_sync_get_with_key_fallback(client, f'{trongrid_base_url}/v1/accounts/{address}', headers)
            resp.raise_for_status()
            data = resp.json() or {}
        account_items = data.get('data') or []
        account = account_items[0] if account_items else {}
        trx_balance = Decimal(str(account.get('balance', 0) or 0)) / Decimal('1000000')
        usdt_balance = Decimal('0')
        for item in account.get('trc20') or []:
            if not isinstance(item, dict):
                continue
            for contract, value in item.items():
                if str(contract).lower() == str(usdt_contract).lower():
                    usdt_balance = Decimal(str(value or '0')) / Decimal('1000000')
                    break
        if redis_client is not None:
            try:
                async_to_sync(redis_client.setex)(
                    cache_key,
                    ADDRESS_BALANCE_CACHE_TTL,
                    json.dumps({'usdt': str(usdt_balance), 'trx': str(trx_balance)}),
                )
            except Exception:
                logger.debug('ADDRESS_BALANCE_CACHE_WRITE_FAILED address=%s', address, exc_info=True)
        return usdt_balance, trx_balance, None
    except Exception as exc:
        logger.warning('地址监控链上余额查询失败 address=%s error=%s', address, exc)
        return None, None, str(exc)


def _cloud_ip_log_note_newest_first(note):
    text = str(note or '').strip()
    if not text:
        return ''
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) <= 1:
        return text

    def _line_time(line):
        match = re.search(r'执行时间：(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})', line)
        return match.group(1) if match else ''

    first_time = _line_time(lines[0])
    last_time = _line_time(lines[-1])
    if first_time and last_time and first_time < last_time:
        lines = list(reversed(lines))
    return '\n'.join(lines)


def _cloud_ip_log_payload(item):
    user = item.user
    usernames = user.usernames if user else []
    user_payload = _user_payload({
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'username': user.username,
        'first_name': user.first_name,
        'usernames': usernames,
        'primary_username': usernames[0] if usernames else '',
    }) if user else None
    return {
        'id': item.id,
        'event_type': item.event_type,
        'event_label': _status_label(item.event_type, CloudIpLog.EVENT_CHOICES),
        'provider': item.provider,
        'provider_label': _provider_label(item.provider),
        'region_code': item.region_code,
        'region_name': item.region_name,
        'region_label': _region_label(item.region_code, item.region_name),
        'order_id': item.order_id,
        'order_no': item.order_no,
        'order_detail_path': f'/admin/cloud-orders/{item.order_id}' if item.order_id else '',
        'asset_id': item.asset_id,
        'asset_detail_path': f'/admin/cloud-assets/{item.asset_id}' if item.asset_id else '',
        'detail_path': f'/admin/cloud-orders/{item.order_id}' if item.order_id else (f'/admin/cloud-assets/{item.asset_id}' if item.asset_id else ''),
        'server_id': None,
        'asset_name': item.asset_name,
        'instance_id': item.instance_id,
        'provider_resource_id': item.provider_resource_id,
        'public_ip': item.public_ip,
        'previous_public_ip': item.previous_public_ip,
        'note': _cloud_ip_log_note_newest_first(item.note),
        'created_at': _iso(item.created_at),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
    }


@dashboard_login_required
@require_GET
def cloud_ip_logs_list(request):
    keyword = _get_keyword(request)
    log_type = (request.GET.get('log_type') or 'ip').strip()
    queryset = CloudIpLog.objects.select_related('user', 'order', 'asset').order_by('-created_at', '-id')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['order_no', 'asset_name', 'instance_id', 'provider_resource_id', 'public_ip', 'previous_public_ip', 'note', 'user__tg_user_id', 'user__username'],
    )
    if log_type == 'server':
        queryset = queryset.filter(
            Q(order__isnull=False)
            | Q(asset__kind=CloudAsset.KIND_SERVER)
        )
    elif log_type == 'operation':
        queryset = queryset.filter(note__isnull=False).exclude(note='')
    return _ok([_cloud_ip_log_payload(item) for item in queryset[:200]])


@dashboard_login_required
@require_GET
def monitors_list(request):
    keyword = _get_keyword(request)
    queryset = AddressMonitor.objects.select_related('user').order_by('-created_at')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['address', 'remark', 'daily_income_currency', 'daily_expense_currency', 'user__tg_user_id', 'user__username'],
    )
    items = list(
        queryset[:100].values(
            'id', 'address', 'remark', 'monitor_transfers', 'monitor_resources',
            'usdt_threshold', 'trx_threshold', 'energy_threshold', 'bandwidth_threshold',
            'daily_income', 'daily_expense', 'daily_income_currency', 'daily_expense_currency',
            'stats_date', 'is_active', 'created_at', 'resource_checked_at', 'user__tg_user_id', 'user__username'
        )
    )
    payload = []
    for item in items:
        usdt_balance, trx_balance, balance_error = _fetch_address_chain_balances(item['address'])
        payload.append({
            **item,
            'usdt_threshold': _decimal_to_str(item['usdt_threshold']),
            'trx_threshold': _decimal_to_str(item['trx_threshold']),
            'daily_income': _decimal_to_str(item['daily_income']),
            'daily_expense': _decimal_to_str(item['daily_expense']),
            'chain_usdt_balance': _decimal_to_str(usdt_balance) if usdt_balance is not None else None,
            'chain_trx_balance': _decimal_to_str(trx_balance) if trx_balance is not None else None,
            'chain_balance_error': balance_error,
            'created_at': _iso(item['created_at']),
            'resource_checked_at': _iso(item['resource_checked_at']),
            'tg_user_id': item.pop('user__tg_user_id', None),
            'username': item.pop('user__username', None),
        })
    return _ok(payload)
