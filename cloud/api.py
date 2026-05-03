"""cloud 域后台 API。"""

import io
import json
import logging
import re
import threading
import time
import uuid
from decimal import Decimal, InvalidOperation

import httpx
from urllib.parse import urlparse

from asgiref.sync import async_to_sync
from django.core.management import get_commands, load_command_class
from django.db import IntegrityError, transaction
from django.db.models import Case, CharField, Count, F, IntegerField, Q, Value, When
from django.db.models.functions import Cast
from django.db.utils import ProgrammingError
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST, require_http_methods

from bot.api import (
    _apply_keyword_filter,
    _countdown_label,
    _days_left,
    _decimal_to_str,
    _error,
    _get_keyword,
    _iso,
    _ok,
    _parse_decimal,
    _provider_label,
    _provider_status_label,
    _read_payload,
    _region_label,
    _server_source_label,
    _split_usernames,
    _status_label,
    _user_payload,
    dashboard_login_required,
)
from bot.models import TelegramGroupFilter, TelegramUser
from cloud.lifecycle import _delete_instance, _get_due_orders, _mark_replaced_order_deleted, _notice_payload_for_order
from cloud.services import AWS_REGION_NAMES, _cloud_order_lifecycle_fields, create_cloud_server_rebuild_order, ensure_cloud_server_pricing, ensure_manual_expiry_operation_order, ensure_manual_owner_operation_order, ensure_manual_price_operation_order, record_cloud_ip_log, refresh_custom_plan_cache, replace_cloud_asset_order_by_admin, set_cloud_server_auto_renew_admin
from cloud.models import AddressMonitor, CloudAsset, CloudAutoRenewPatrolLog, CloudIpLog, CloudServerOrder, CloudServerPlan, Server, ServerPrice
from core.cloud_accounts import cloud_account_label
from core.models import CloudAccountConfig, ExternalSyncLog
from core.cache import get_redis
from core.persistence import record_external_sync_log
from core.runtime_config import get_cloud_asset_sync_interval_seconds, get_runtime_config
from core.trongrid import build_trongrid_headers
from cloud.provisioning import provision_cloud_server

logger = logging.getLogger(__name__)

ADDRESS_BALANCE_CACHE_TTL = 60
ADDRESS_BALANCE_CACHE_PREFIX = 'address_balance:'


def _fetch_address_chain_balances(address: str):
    cache_key = f'{ADDRESS_BALANCE_CACHE_PREFIX}{address}'
    redis_client = async_to_sync(get_redis)()
    if redis_client is not None:
        try:
            cached = async_to_sync(redis_client.get)(cache_key)
            if cached:
                payload = json.loads(cached)
                return Decimal(str(payload.get('usdt', '0'))), Decimal(str(payload.get('trx', '0'))), None
        except Exception:
            pass
    usdt_contract = get_runtime_config('usdt_contract', 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t')
    trongrid_base_url = get_runtime_config('trongrid_base_url', 'https://api.trongrid.io')
    try:
        with httpx.Client(timeout=8) as client:
            resp = client.get(f'{trongrid_base_url}/v1/accounts/{address}', headers=build_trongrid_headers())
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
                pass
        return usdt_balance, trx_balance, None
    except Exception as exc:
        logger.warning('地址监控链上余额查询失败 address=%s error=%s', address, exc)
        return None, None, str(exc)


def _active_sync_accounts(provider: str):
    return list(CloudAccountConfig.objects.filter(provider=provider, is_active=True).order_by('id'))


def _sync_account_payload(account):
    if not account:
        return None
    return {
        'id': account.id,
        'provider': account.provider,
        'name': account.name,
        'label': cloud_account_label(account),
    }


def _sync_log_tail(output: io.StringIO, limit: int = 80) -> list[str]:
    lines = [line.strip() for line in output.getvalue().splitlines() if line.strip()]
    return lines[-limit:]


def _sync_log_text(output: io.StringIO, limit: int = 200) -> str:
    return '\n'.join(_sync_log_tail(output, limit=limit))


def _record_dashboard_sync_log(*, action: str, target: str, request_payload: dict, response_payload: dict, is_success: bool, error_message: str = ''):
    try:
        record_external_sync_log(
            source=ExternalSyncLog.SOURCE_DASHBOARD,
            action=action,
            target=target,
            request_payload=request_payload,
            response_payload=response_payload,
            is_success=is_success,
            error_message=error_message,
        )
    except Exception:
        logger.exception('DASHBOARD_SYNC_LOG_RECORD_FAILED action=%s target=%s', action, target)


def _call_command_capture(command_name: str, *args, **options):
    output = options.pop('stdout', None) or io.StringIO()
    command = load_command_class(get_commands()[command_name], command_name)
    defaults = {
        'force_color': False,
        'no_color': False,
        'pythonpath': None,
        'settings': None,
        'skip_checks': True,
        'stderr': io.StringIO(),
        'traceback': False,
        'verbosity': 1,
    }
    defaults.update(options)
    command.execute(*args, stdout=output, **defaults)
    return command, output.getvalue()


def _is_unattached_ip_asset(asset: CloudAsset) -> bool:
    return '未附加' in str(asset.provider_status or '')


def _ensure_unattached_ip_expiry(asset: CloudAsset, *, now=None) -> bool:
    """未附加固定 IP 必须有回收/到期时间；缺失时按系统配置补齐。"""
    if not _is_unattached_ip_asset(asset) or asset.actual_expires_at:
        return False
    from core.runtime_config import get_runtime_config
    try:
        delete_days = max(int(str(get_runtime_config('cloud_unattached_ip_delete_after_days', '15') or '15').strip()), 0)
    except (TypeError, ValueError):
        delete_days = 15
    now = now or timezone.now()
    asset.actual_expires_at = now + timezone.timedelta(days=delete_days)
    note = asset.note or ''
    addition = f'自动补齐未附加IP到期时间: {asset.actual_expires_at.isoformat()}'
    asset.note = f'{note}\n{addition}'.strip() if note else addition
    asset.save(update_fields=['actual_expires_at', 'note', 'updated_at'])
    return True


def _generate_cloud_plan_config_id():
    return f'cfg-{uuid.uuid4().hex[:12]}'


def _telegram_user_lookup_terms(value):
    raw = str(value or '').strip()
    if not raw:
        return []

    terms = []

    def add(term):
        normalized = str(term or '').strip().strip('`"\'<>，,。；;：:').lstrip('@')
        if normalized and normalized not in terms:
            terms.append(normalized)

    add(raw)
    parsed = urlparse(raw if '://' in raw else f'https://{raw}')
    if parsed.netloc.lower() in {'t.me', 'telegram.me', 'www.t.me', 'www.telegram.me'}:
        path_parts = [part for part in parsed.path.split('/') if part]
        if path_parts:
            add(path_parts[0])
    for match in re.findall(r'@([A-Za-z0-9_]{3,64})', raw):
        add(match)
    for match in re.findall(r'(?:t\.me|telegram\.me)/([A-Za-z0-9_]{3,64})', raw, flags=re.I):
        add(match)
    for match in re.findall(r'\b\d{5,20}\b', raw):
        add(match)
    return terms


def _resolve_telegram_user(value):
    terms = _telegram_user_lookup_terms(value)
    if not terms:
        return None
    queryset = TelegramUser.objects.all()
    for raw in terms:
        if raw.isdigit():
            found = queryset.filter(Q(id=int(raw)) | Q(tg_user_id=int(raw))).first()
        else:
            found = queryset.filter(username__icontains=raw).first()
        if found:
            return found
    return None


def _parse_iso_datetime(value, field_label='时间'):
    raw = str(value or '').strip()
    if not raw:
        return None
    parsed = parse_datetime(raw)
    if parsed is None:
        parsed_date = parse_date(raw)
        if parsed_date is not None:
            parsed = timezone.datetime.combine(parsed_date, timezone.datetime.min.time())
    if parsed is None:
        raise ValueError(f'{field_label}格式不正确，请使用 ISO 时间或 YYYY-MM-DD 日期')
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed


def _sync_telegram_username(user, username=None):
    incoming = _split_usernames(username)
    if not incoming:
        return
    merged = []
    seen = set()
    for item in [*user.usernames, *incoming]:
        key = str(item).lower()
        if item and key not in seen:
            merged.append(item)
            seen.add(key)
    user.username = ','.join(merged)
    user.save(update_fields=['username', 'updated_at'])


def _preserve_link_status_label(*notes):
    text = '\n'.join(str(item or '') for item in notes if item)
    if '继续保留旧机服务' in text:
        return '重装失败，仍使用旧机'
    if '已发起重装迁移' in text:
        return '重装迁移中'
    if '重装迁移完成' in text:
        return '已切换到新机'
    return ''


def _asset_payload(asset):
    user = asset.user
    order = asset.order
    user_payload = None
    if user:
        usernames = user.usernames
        user_payload = _user_payload({
            'id': user.id,
            'tg_user_id': user.tg_user_id,
            'username': user.username,
            'first_name': user.first_name,
            'usernames': usernames,
            'primary_username': usernames[0] if usernames else '',
        })
    _ensure_unattached_ip_expiry(asset)
    expires_at = asset.actual_expires_at
    account_label = asset.account_label or cloud_account_label(getattr(asset, 'cloud_account', None)) or getattr(order, 'account_label', '')
    cloud_account_id = asset.cloud_account_id or getattr(order, 'cloud_account_id', None)
    return {
        'id': asset.id,
        'kind': asset.kind,
        'source': asset.source,
        'source_label': _server_source_label(asset.source),
        'provider': asset.provider,
        'provider_label': _provider_label(asset.provider),
        'cloud_account_id': cloud_account_id,
        'account_label': account_label,
        'region_code': asset.region_code,
        'region_label': _region_label(getattr(asset, 'region_code', None), asset.region_name),
        'region_name': asset.region_name,
        'asset_name': asset.asset_name,
        'instance_id': asset.instance_id,
        'provider_resource_id': asset.provider_resource_id,
        'public_ip': asset.public_ip or getattr(order, 'public_ip', None),
        'mtproxy_link': asset.mtproxy_link or getattr(order, 'mtproxy_link', None),
        'proxy_links': asset.proxy_links or getattr(order, 'proxy_links', None) or [],
        'mtproxy_port': asset.mtproxy_port or getattr(order, 'mtproxy_port', None),
        'mtproxy_secret': _mask_secret(asset.mtproxy_secret or getattr(order, 'mtproxy_secret', None)),
        'has_mtproxy_secret': bool(asset.mtproxy_secret or getattr(order, 'mtproxy_secret', None)),
        'mtproxy_host': asset.mtproxy_host or getattr(order, 'mtproxy_host', None),
        'note': asset.note,
        'sort_order': asset.sort_order,
        'actual_expires_at': _iso(expires_at),
        'days_left': _days_left(expires_at),
        'status_countdown': _countdown_label(expires_at),
        'preserve_link_status': _preserve_link_status_label(asset.note, getattr(order, 'provision_note', None)),
        'ip_change_quota': max(int(getattr(order, 'ip_change_quota', 0) or 0), 0) if order else 0,
        'price': _decimal_to_str(asset.price if asset.price is not None else (order.total_amount if order and order.total_amount is not None else None), 2),
        'currency': asset.currency or (order.currency if order else ''),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'telegram_group_id': asset.telegram_group_id,
        'telegram_group_chat_id': asset.telegram_group.chat_id if asset.telegram_group_id and asset.telegram_group else None,
        'telegram_group_title': asset.telegram_group.title if asset.telegram_group_id and asset.telegram_group else '',
        'telegram_group_username': asset.telegram_group.username if asset.telegram_group_id and asset.telegram_group else '',
        'order_id': order.id if order else None,
        'order_no': order.order_no if order else '',
        'order_detail_path': f'/admin/cloud-orders/{order.id}' if order else '',
        'order_link_path': f'/admin/cloud-orders/{order.id}' if order else '',
        'auto_renew_enabled': bool(getattr(order, 'auto_renew_enabled', False)),
        'status': asset.status,
        'status_label': _status_label(asset.status, CloudAsset.STATUS_CHOICES),
        'provider_status': '已删除' if asset.status == CloudAsset.STATUS_DELETED else _provider_status_label(asset.provider_status),
        'is_active': asset.is_active,
        'updated_at': _iso(asset.updated_at),
    }


@csrf_exempt
@dashboard_login_required
@require_http_methods(['GET', 'POST', 'PUT', 'PATCH'])
def update_cloud_asset(request, asset_id):
    if request.method == 'GET':
        asset = CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').filter(pk=asset_id).first()
        if not asset:
            return _error('云资产不存在', status=404)
        payload = _asset_payload(asset)
        order = asset.order
        logs = CloudIpLog.objects.filter(Q(asset=asset) | Q(order=order) if order else Q(asset=asset)).order_by('-created_at', '-id')[:50]
        payload.update({
            'order_status': getattr(order, 'status', '') or '',
            'order_status_label': _status_label(getattr(order, 'status', ''), CloudServerOrder.STATUS_CHOICES) if order else '',
            'service_started_at': _iso(getattr(order, 'service_started_at', None)),
            'service_expires_at': _iso(getattr(order, 'service_expires_at', None)),
            'renew_grace_expires_at': _iso(getattr(order, 'renew_grace_expires_at', None)),
            'suspend_at': _iso(getattr(order, 'suspend_at', None)),
            'delete_at': _iso(getattr(order, 'delete_at', None)),
            'ip_recycle_at': _iso(getattr(order, 'ip_recycle_at', None)),
            'last_renewed_at': _iso(getattr(order, 'last_renewed_at', None)),
            'provision_note': getattr(order, 'provision_note', '') or '',
            'created_at': _iso(asset.created_at),
            'related_order': _cloud_order_summary_payload(order),
            'history_orders': _related_order_history_payload(order),
            'ip_logs': [
                {
                    'id': item.id,
                    'event_type': item.event_type,
                    'event_label': dict(CloudIpLog.EVENT_CHOICES).get(item.event_type, item.event_type),
                    'public_ip': item.public_ip,
                    'previous_public_ip': item.previous_public_ip,
                    'note': item.note,
                    'created_at': _iso(item.created_at),
                }
                for item in logs
            ],
        })
        return _ok(payload)
    payload = _read_payload(request)
    owner_change_requested = False
    expiry_change_requested = False
    owner_target_after_commit = None
    previous_owner = None
    previous_expires_at = None
    previous_price = None
    price_change_requested = False
    public_ip_changed = False
    changed_public_ip_before = None
    changed_public_ip_after = None
    is_unattached_ip = False
    linked_order_id = None
    pending_order_updates = {}
    try:
        with transaction.atomic():
            asset = CloudAsset.objects.select_for_update().select_related('order', 'user', 'cloud_account', 'telegram_group').get(pk=asset_id)
            is_unattached_ip = _is_unattached_ip_asset(asset)
            previous_owner = asset.user
            previous_expires_at = asset.actual_expires_at
            previous_price = asset.price if asset.price is not None else getattr(asset.order, 'total_amount', None)
            linked_order_id = asset.order_id

            server = None
            server_lookup = Q()
            if asset.instance_id:
                server_lookup |= Q(instance_id=asset.instance_id)
            if asset.provider_resource_id:
                server_lookup |= Q(provider_resource_id=asset.provider_resource_id)
            if asset.order_id:
                server_lookup |= Q(order_id=asset.order_id)
            if server_lookup:
                server = Server.objects.select_for_update().filter(server_lookup).first()

            user_lookup = payload.get('user_query') or payload.get('user_id') or payload.get('tg_user_id') or payload.get('username')
            username_raw = payload.get('user_query') or payload.get('username')
            clear_user = str(payload.get('clear_user') or '').lower() in {'1', 'true', 'yes', 'on'}
            owner_changed = clear_user or user_lookup not in (None, '')
            owner_change_requested = owner_changed and not is_unattached_ip
            owner_target = asset.user
            if clear_user:
                owner_target = None
                asset.user = None
                if asset.order_id and not is_unattached_ip:
                    pending_order_updates['user_id'] = None
                    pending_order_updates['last_user_id'] = None
                if server:
                    server.user = None
            elif user_lookup not in (None, ''):
                owner_target = _resolve_telegram_user(user_lookup)
                if not owner_target:
                    return _error('未找到匹配的 Telegram 用户', status=404)
                asset.user = owner_target
                _sync_telegram_username(owner_target, username_raw)
                if asset.order_id and not is_unattached_ip:
                    pending_order_updates['user_id'] = owner_target.id
                    pending_order_updates['last_user_id'] = getattr(owner_target, 'tg_user_id', None)
                if server:
                    server.user = owner_target

            group_lookup = payload.get('telegram_group_query')
            if group_lookup is None and 'telegram_group_id' in payload:
                group_lookup = payload.get('telegram_group_id')
            if group_lookup is not None:
                if group_lookup in (None, ''):
                    asset.telegram_group = None
                else:
                    group_lookup_text = str(group_lookup).strip().lstrip('@')
                    group_query = Q(username__iexact=group_lookup_text) | Q(title__icontains=group_lookup_text)
                    try:
                        numeric_group_id = int(group_lookup_text)
                        group_query |= Q(id=numeric_group_id) | Q(chat_id=numeric_group_id)
                    except (TypeError, ValueError):
                        pass
                    group = TelegramGroupFilter.objects.filter(group_query).order_by('-updated_at', '-id').first()
                    if not group:
                        return _error('未找到匹配的 Telegram 群组', status=404)
                    asset.telegram_group = group

            if 'price' in payload:
                try:
                    price = _parse_decimal(payload.get('price'), '价格').quantize(Decimal('0.01'))
                except ValueError as exc:
                    return _error(str(exc), status=400)
                asset.price = price
                price_change_requested = previous_price != price
                if asset.order_id and not str(getattr(asset.order, 'order_no', '') or '').startswith('SRVMANUAL'):
                    pending_order_updates['total_amount'] = price

            if 'currency' in payload:
                asset.currency = (payload.get('currency') or 'USDT').strip() or 'USDT'
                if asset.order_id and asset.order.currency != asset.currency:
                    pending_order_updates['currency'] = asset.currency

            if server and 'account_label' in payload:
                server.account_label = payload.get('account_label') or None

            manual_expires_at = None
            if 'actual_expires_at' in payload:
                try:
                    manual_expires_at = _parse_iso_datetime(payload.get('actual_expires_at'), '到期时间')
                    asset.actual_expires_at = manual_expires_at
                except ValueError as exc:
                    return _error(str(exc), status=400)
                if server:
                    server.expires_at = asset.actual_expires_at
                if asset.order_id and not is_unattached_ip:
                    same_order_active_assets = CloudAsset.objects.filter(
                        order_id=asset.order_id,
                        kind=CloudAsset.KIND_SERVER,
                    ).exclude(status__in=[
                        CloudAsset.STATUS_DELETED,
                        CloudAsset.STATUS_DELETING,
                        CloudAsset.STATUS_TERMINATED,
                        CloudAsset.STATUS_TERMINATING,
                    ]).count()
                    if same_order_active_assets <= 1:
                        pending_order_updates.update({
                            'service_expires_at': manual_expires_at,
                            'renew_notice_sent_at': None,
                            'auto_renew_notice_sent_at': None,
                            'auto_renew_failure_notice_sent_at': None,
                            'delete_notice_sent_at': None,
                            'recycle_notice_sent_at': None,
                            **_cloud_order_lifecycle_fields(manual_expires_at),
                        })

            if asset.order_id:
                if 'mtproxy_link' in payload:
                    pending_order_updates['mtproxy_link'] = payload.get('mtproxy_link') or None
                if 'mtproxy_secret' in payload:
                    pending_order_updates['mtproxy_secret'] = payload.get('mtproxy_secret') or None
                if 'mtproxy_host' in payload:
                    pending_order_updates['mtproxy_host'] = payload.get('mtproxy_host') or None
                if 'mtproxy_port' in payload:
                    mtproxy_port = payload.get('mtproxy_port')
                    pending_order_updates['mtproxy_port'] = int(mtproxy_port) if mtproxy_port not in (None, '') else None
                if 'provider_resource_id' in payload:
                    pending_order_updates['provider_resource_id'] = payload.get('provider_resource_id') or None
                if 'public_ip' in payload:
                    pending_order_updates['public_ip'] = payload.get('public_ip') or None
                if 'asset_name' in payload:
                    pending_order_updates['server_name'] = payload.get('asset_name') or None

            old_public_ip = asset.public_ip
            new_public_ip = payload.get('public_ip') or None if 'public_ip' in payload else asset.public_ip
            if 'public_ip' in payload:
                if old_public_ip and old_public_ip != new_public_ip:
                    asset.previous_public_ip = old_public_ip

            for field in ('asset_name', 'public_ip', 'provider_resource_id', 'mtproxy_link', 'mtproxy_secret', 'mtproxy_host', 'note'):
                if field in payload:
                    setattr(asset, field, payload.get(field) or None)
            if 'mtproxy_port' in payload:
                mtproxy_port = payload.get('mtproxy_port')
                asset.mtproxy_port = int(mtproxy_port) if mtproxy_port not in (None, '') else None
            for field in ('provider', 'region_name', 'region_code'):
                if field in payload:
                    setattr(asset, field, payload.get(field) or None)
            if server:
                if 'asset_name' in payload:
                    server.server_name = payload.get('asset_name') or None
                if 'public_ip' in payload:
                    old_public_ip = server.public_ip
                    server.public_ip = payload.get('public_ip') or None
                    if old_public_ip and old_public_ip != server.public_ip:
                        server.previous_public_ip = old_public_ip
                if 'provider_resource_id' in payload:
                    server.provider_resource_id = payload.get('provider_resource_id') or None
                if 'provider' in payload:
                    server.provider = payload.get('provider') or None
                if 'region_name' in payload:
                    server.region_name = payload.get('region_name') or None
                if 'region_code' in payload:
                    server.region_code = payload.get('region_code') or None
                if 'note' in payload:
                    server.note = payload.get('note') or None
            if 'is_active' in payload:
                asset.is_active = str(payload.get('is_active')).lower() in {'1', 'true', 'yes', 'on'}
                if server:
                    server.is_active = asset.is_active

            if 'sort_order' in payload:
                sort_order = payload.get('sort_order')
                try:
                    asset.sort_order = int(sort_order) if sort_order not in (None, '') else 99
                except (TypeError, ValueError):
                    return _error('排序必须是数字', status=400)
                if server:
                    server.sort_order = asset.sort_order

            if user_lookup not in (None, '') and server:
                server.user = asset.user
            if asset.kind == CloudAsset.KIND_SERVER and not server and not is_unattached_ip:
                server = Server(
                    source=(asset.source or Server.SOURCE_ORDER) if asset.source in {choice[0] for choice in Server.SOURCE_CHOICES} else Server.SOURCE_ORDER,
                    instance_id=asset.instance_id or asset.provider_resource_id or asset.public_ip,
                )
            if server:
                if asset.order_id:
                    server.order = asset.order
                if asset.instance_id:
                    server.instance_id = asset.instance_id
                elif not server.instance_id:
                    server.instance_id = asset.provider_resource_id or asset.public_ip
                server.user = asset.user
                server.source = server.source or Server.SOURCE_ORDER
                server.provider = asset.provider
                server.region_name = asset.region_name
                server.region_code = asset.region_code
                server.provider_resource_id = asset.provider_resource_id
                server.public_ip = asset.public_ip
                server.note = asset.note
                server.sort_order = asset.sort_order
                server.expires_at = asset.actual_expires_at
                server.is_active = asset.is_active
                if asset.asset_name:
                    server.server_name = asset.asset_name
                if server.account_label in (None, ''):
                    server.account_label = asset.provider
                server.save()

            asset.save()
            owner_target_after_commit = owner_target
            expiry_change_requested = manual_expires_at is not None and not is_unattached_ip
            public_ip_changed = 'public_ip' in payload and str(old_public_ip or '') != str(new_public_ip or '')
            changed_public_ip_before = old_public_ip
            changed_public_ip_after = new_public_ip
    except CloudAsset.DoesNotExist:
        return _error('云资产不存在', status=404)

    manual_replace_requested = owner_change_requested or expiry_change_requested
    manual_replace_authoritative = bool(
        manual_replace_requested
        and asset.provider == CloudServerPlan.PROVIDER_AWS_LIGHTSAIL
    )
    if linked_order_id and pending_order_updates and not manual_replace_authoritative:
        try:
            CloudServerOrder.objects.filter(pk=linked_order_id).update(**pending_order_updates, updated_at=timezone.now())
        except Exception as exc:
            logger.warning('CLOUD_ASSET_MANUAL_ORDER_SYNC_SKIPPED asset_id=%s order_id=%s fields=%s error=%s', asset_id, linked_order_id, sorted(pending_order_updates), exc)

    asset = CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').get(pk=asset_id)
    if manual_replace_authoritative:
        try:
            order, err = replace_cloud_asset_order_by_admin(
                asset,
                new_user=owner_target_after_commit,
                new_expires_at=asset.actual_expires_at if expiry_change_requested else None,
                new_price=asset.price if price_change_requested else None,
                previous_user=previous_owner,
                previous_expires_at=previous_expires_at,
                previous_price=previous_price,
            )
            if err:
                logger.warning('CLOUD_ASSET_MANUAL_REPLACE_ORDER_SKIPPED asset_id=%s error=%s', asset_id, err)
                if owner_change_requested:
                    fallback_order, fallback_err = ensure_manual_owner_operation_order(asset, owner_target_after_commit, previous_user=previous_owner, previous_expires_at=previous_expires_at)
                    if fallback_err:
                        logger.warning('CLOUD_ASSET_MANUAL_OWNER_ORDER_SKIPPED asset_id=%s error=%s', asset_id, fallback_err)
                if expiry_change_requested:
                    fallback_order, fallback_err = ensure_manual_expiry_operation_order(asset, asset.actual_expires_at, previous_expires_at=previous_expires_at)
                    if fallback_err:
                        logger.warning('CLOUD_ASSET_MANUAL_EXPIRY_ORDER_SKIPPED asset_id=%s error=%s', asset_id, fallback_err)
        except Exception as exc:
            logger.exception('CLOUD_ASSET_MANUAL_REPLACE_ORDER_FAILED asset_id=%s error=%s', asset_id, exc)
    elif owner_change_requested:
        try:
            owner_order, owner_err = ensure_manual_owner_operation_order(
                asset,
                owner_target_after_commit,
                previous_user=previous_owner,
                previous_expires_at=previous_expires_at,
            )
            if owner_err:
                logger.warning('CLOUD_ASSET_MANUAL_OWNER_AUDIT_ORDER_SKIPPED asset_id=%s error=%s', asset_id, owner_err)
        except Exception as exc:
            logger.exception('CLOUD_ASSET_MANUAL_OWNER_AUDIT_ORDER_FAILED asset_id=%s error=%s', asset_id, exc)
    if price_change_requested and asset.price is not None and not manual_replace_authoritative:
        try:
            price_order, price_err = ensure_manual_price_operation_order(
                asset,
                asset.price,
                previous_price=previous_price,
            )
            if price_err:
                logger.warning('CLOUD_ASSET_MANUAL_PRICE_AUDIT_ORDER_SKIPPED asset_id=%s error=%s', asset_id, price_err)
        except Exception as exc:
            logger.exception('CLOUD_ASSET_MANUAL_PRICE_AUDIT_ORDER_FAILED asset_id=%s error=%s', asset_id, exc)
    if public_ip_changed:
        server = None
        server_lookup = Q()
        if asset.instance_id:
            server_lookup |= Q(instance_id=asset.instance_id)
        if asset.provider_resource_id:
            server_lookup |= Q(provider_resource_id=asset.provider_resource_id)
        if asset.order_id:
            server_lookup |= Q(order_id=asset.order_id)
        if server_lookup:
            server = Server.objects.filter(server_lookup).first()
        record_cloud_ip_log(
            event_type='changed',
            order=asset.order,
            asset=asset,
            server=server,
            previous_public_ip=changed_public_ip_before,
            public_ip=changed_public_ip_after,
            note=f'后台手动更新IP：{changed_public_ip_before or "未分配"} → {changed_public_ip_after or "未分配"}',
        )
    asset = CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').get(pk=asset_id)
    return _ok(_asset_payload(asset))


@csrf_exempt
@dashboard_login_required
@require_POST
def toggle_cloud_asset_auto_renew(request, asset_id):
    asset = CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'telegram_group').filter(pk=asset_id).first()
    if not asset:
        return _error('云资产不存在', status=404)
    if not asset.order_id:
        return _error('该代理未绑定订单，无法设置自动续费', status=400)
    payload = _read_payload(request)
    enabled = str(payload.get('enabled')).lower() in {'1', 'true', 'yes', 'on'}
    order = async_to_sync(set_cloud_server_auto_renew_admin)(asset.order_id, enabled)
    if order is False:
        return _error('当前状态不可开启自动续费', status=400)
    if not order:
        return _error('订单不存在', status=404)
    asset = CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').get(pk=asset_id)
    return _ok(_asset_payload(asset))


@dashboard_login_required
@require_GET
def cloud_assets_list(request):
    keyword = _get_keyword(request)
    grouped = (request.GET.get('grouped') or '').lower() in {'1', 'true', 'yes'}
    group_by = (request.GET.get('group_by') or 'telegram_group').strip().lower()
    if group_by not in {'telegram_group', 'user'}:
        group_by = 'telegram_group'
    paginated = (request.GET.get('paginated') or '').lower() in {'1', 'true', 'yes'}
    try:
        active_account_labels = _cloud_account_labels_queryset(True)
        inactive_account_labels = _cloud_account_labels_queryset(False)
        queryset = CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').filter(kind=CloudAsset.KIND_SERVER).exclude(
            status__in=[CloudAsset.STATUS_DELETED, CloudAsset.STATUS_DELETING, CloudAsset.STATUS_TERMINATED, CloudAsset.STATUS_TERMINATING],
        ).exclude(
            Q(cloud_account__is_active=False) | Q(account_label__in=inactive_account_labels),
        ).filter(
            Q(cloud_account__is_active=True)
            | Q(account_label__in=active_account_labels)
            | Q(account_label__isnull=True)
            | Q(account_label='')
        )
        queryset = _apply_keyword_filter(
            queryset,
            keyword,
            [
                'asset_name', 'public_ip', 'mtproxy_link', 'account_label', 'cloud_account__external_account_id', 'cloud_account__name', 'user__tg_user_id',
                'user__username', 'order__order_no',
            ],
        ).distinct().order_by('-sort_order', F('actual_expires_at').asc(nulls_last=True), '-updated_at', '-id')
        if not grouped and paginated:
            try:
                page = max(int(request.GET.get('page') or '1'), 1)
            except (TypeError, ValueError):
                page = 1
            try:
                page_size = int(request.GET.get('page_size') or '50')
            except (TypeError, ValueError):
                page_size = 50
            page_size = min(max(page_size, 10), 200)
            total = queryset.count()
            offset = (page - 1) * page_size
            items = [_asset_payload(asset) for asset in queryset[offset:offset + page_size]]
            return _ok({'items': items, 'total': total, 'page': page, 'page_size': page_size})
        items = [_asset_payload(asset) for asset in queryset]
    except ProgrammingError:
        if grouped:
            return _ok({'groups': [], 'items': []})
        if paginated:
            return _ok({'items': [], 'total': 0, 'page': 1, 'page_size': 50})
        return _ok([])

    if not grouped:
        return _ok(items)

    groups = {}
    for item in items:
        if group_by == 'user':
            user_id = item.get('user_id') or item.get('tg_user_id')
            key = f'user:{user_id}' if user_id else 'user:unbound'
            group = groups.setdefault(key, {
                'user_key': key,
                'tg_user_id': item['tg_user_id'],
                'user_display_name': item['user_display_name'],
                'username_label': item['username_label'],
                'telegram_group_id': None,
                'telegram_group_chat_id': None,
                'telegram_group_title': '',
                'telegram_group_username': '',
                'default_expanded': True,
                'items': [],
            })
        else:
            group_id = item.get('telegram_group_id')
            if group_id:
                key = f'group:{group_id}'
                group_title = item.get('telegram_group_title') or str(item.get('telegram_group_chat_id') or group_id)
                group_username = item.get('telegram_group_username') or ''
                group = groups.setdefault(key, {
                    'user_key': key,
                    'tg_user_id': None,
                    'user_display_name': group_title,
                    'username_label': f'@{group_username}' if group_username else str(item.get('telegram_group_chat_id') or '-'),
                    'telegram_group_id': group_id,
                    'telegram_group_chat_id': item.get('telegram_group_chat_id'),
                    'telegram_group_title': group_title,
                    'telegram_group_username': group_username,
                    'default_expanded': True,
                    'items': [],
                })
            else:
                user_id = item.get('user_id') or item.get('tg_user_id')
                key = f'user:{user_id}' if user_id else 'user:unbound'
                group = groups.setdefault(key, {
                    'user_key': key,
                    'tg_user_id': item['tg_user_id'],
                    'user_display_name': item['user_display_name'],
                    'username_label': item['username_label'] or (str(item.get('tg_user_id') or '-') if user_id else '-'),
                    'telegram_group_id': None,
                    'telegram_group_chat_id': None,
                    'telegram_group_title': '',
                    'telegram_group_username': '',
                    'default_expanded': True,
                    'items': [],
                })
        group['items'].append(item)
    ordered_groups = list(groups.values())
    ordered_groups.sort(key=lambda group: (
        min((row['actual_expires_at'] or '9999-12-31T23:59:59') for row in group['items']),
        str(group.get('user_display_name') or group.get('telegram_group_title') or '未绑定'),
    ))
    return _ok({'groups': ordered_groups, 'items': items})


def _auto_renew_task_status(order, now):
    if not getattr(order, 'auto_renew_enabled', False):
        return None
    last_renewed_at = getattr(order, 'last_renewed_at', None)
    if last_renewed_at and last_renewed_at >= now - timezone.timedelta(days=1):
        return 'auto_renew_success', '自动续费成功'
    expires_at = getattr(order, 'service_expires_at', None)
    suspend_at = getattr(order, 'suspend_at', None)
    in_renew_window = bool(expires_at and expires_at <= now + timezone.timedelta(days=1) and expires_at > now)
    in_shutdown_fallback = bool(expires_at and expires_at <= now and suspend_at and suspend_at > now)
    if order.status == 'renew_pending' and (in_renew_window or in_shutdown_fallback or expires_at and expires_at <= now):
        return 'auto_renew_failed', '自动续费失败/待补余额'
    if order.status in {'completed', 'expiring', 'renew_pending'} and (in_renew_window or in_shutdown_fallback):
        return 'auto_renew_pending', '自动续费待执行'
    return None


def _auto_renew_pinned_task(now):
    orders = list(CloudServerOrder.objects.filter(auto_renew_enabled=True).order_by('-updated_at')[:500])
    statuses = [_auto_renew_task_status(order, now) for order in orders]
    failed_count = sum(1 for status in statuses if status and status[0] == 'auto_renew_failed')
    pending_count = sum(1 for status in statuses if status and status[0] == 'auto_renew_pending')
    success_count = sum(1 for status in statuses if status and status[0] == 'auto_renew_success')
    latest_time = max((order.last_renewed_at or order.updated_at or order.created_at for order in orders), default=now)
    if failed_count:
        execution_status, execution_status_label = 'auto_renew_failed', '自动续费失败/待补余额'
    elif pending_count:
        execution_status, execution_status_label = 'auto_renew_pending', '自动续费待执行'
    elif success_count:
        execution_status, execution_status_label = 'auto_renew_success', '自动续费成功'
    else:
        execution_status, execution_status_label = 'active', '自动续费巡检中'
    return {
        'id': -10001,
        'order_no': 'AUTO_RENEW_PATROL',
        'task_type': 'auto_renew',
        'task_label': '自动续费巡检',
        'status': 'active',
        'status_label': '置顶',
        'execution_status': execution_status,
        'execution_status_label': execution_status_label,
        'provider': 'system',
        'provider_label': '系统任务',
        'plan_name': '多IP自动续费',
        'public_ip': f'{len(orders)} 个IP',
        'note': f'固定置顶任务，不重复新建；每30分钟巡检一次。开启自动续费 {len(orders)} 个，待执行 {pending_count} 个，失败/待补余额 {failed_count} 个，近24小时成功 {success_count} 个。',
        'created_at': None,
        'updated_at': _iso(latest_time),
        'related_path': '/admin/tasks/auto-renew',
        'detail_path': '/admin/tasks/auto-renew',
        'order_detail_path': '/admin/tasks/auto-renew',
        'order_link_path': '/admin/tasks/auto-renew',
    }


@dashboard_login_required
@require_GET
def tasks_overview(request):
    now = timezone.now()
    orders = CloudServerOrder.objects.order_by('-updated_at')[:100]
    items = []
    pinned_auto_renew = _auto_renew_pinned_task(now)
    if pinned_auto_renew:
        items.append(pinned_auto_renew)
    for order in orders:
        is_regular_task = order.status in {'paid', 'provisioning', 'renew_pending', 'expiring', 'suspended', 'deleting', 'failed'}
        if not is_regular_task:
            continue
        execution_status, execution_status_label = (
            order.status,
            dict(CloudServerOrder.STATUS_CHOICES).get(order.status, order.status),
        )
        items.append({
            'id': order.id,
            'order_id': order.id,
            'order_no': order.order_no,
            'task_type': 'cloud_order',
            'task_label': '云服务器任务',
            'status': order.status,
            'status_label': dict(CloudServerOrder.STATUS_CHOICES).get(order.status, order.status),
            'execution_status': execution_status,
            'execution_status_label': execution_status_label,
            'provider': order.provider,
            'provider_label': _provider_label(order.provider),
            'plan_name': order.plan_name,
            'public_ip': order.public_ip,
            'note': order.provision_note,
            'created_at': _iso(order.created_at),
            'updated_at': _iso(order.updated_at),
            'related_path': f'/admin/cloud-orders/{order.id}',
            'detail_path': f'/admin/cloud-orders/{order.id}',
            'order_detail_path': f'/admin/cloud-orders/{order.id}',
            'order_link_path': f'/admin/cloud-orders/{order.id}',
        })
    return _ok(items[:50])


def _auto_renew_due_item_payload(order, *, queue_status: str = 'due_now', queue_status_label: str = '本轮待执行', next_run_at=None, last_failure_reason: str | None = None):
    user = getattr(order, 'user', None)
    usernames = list(getattr(user, 'usernames', []) or []) if user else []
    user_payload = _user_payload({
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'username': user.username,
        'first_name': user.first_name,
        'usernames': usernames,
        'primary_username': usernames[0] if usernames else '',
    }) if user else None
    notice = _notice_payload_for_order(order) or {}
    expires_at = notice.get('expires_at') or getattr(order, 'service_expires_at', None)
    auto_renew_at = expires_at - timezone.timedelta(days=1) if expires_at else None
    return {
        'id': order.id,
        'order_id': order.id,
        'order_no': order.order_no,
        'ip': notice.get('ip') or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '未分配',
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'queue_status': queue_status,
        'queue_status_label': queue_status_label,
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'balance': _decimal_to_str(getattr(user, 'balance', None)) if user and getattr(user, 'balance', None) is not None else None,
        'service_expires_at': _iso(expires_at),
        'auto_renew_at': _iso(auto_renew_at),
        'next_run_at': _iso(next_run_at),
        'last_failure_reason': last_failure_reason,
        'suspend_at': _iso(notice.get('suspend_at') or getattr(order, 'suspend_at', None)),
        'delete_at': _iso(notice.get('delete_at') or getattr(order, 'delete_at', None)),
        'ip_recycle_at': _iso(notice.get('ip_recycle_at') or getattr(order, 'ip_recycle_at', None)),
        'related_path': f'/admin/cloud-orders/{order.id}',
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
    }


def _auto_renew_future_plan_items(now, next_run_at, due_orders: list):
    plan_items = []
    seen = set()
    for order in due_orders:
        seen.add(order.id)
        plan_items.append(_auto_renew_due_item_payload(order, queue_status='due_now', queue_status_label='本轮待执行', next_run_at=next_run_at))
    future_qs = CloudServerOrder.objects.select_related('user').filter(auto_renew_enabled=True, status__in=['completed', 'expiring', 'renew_pending']).exclude(id__in=list(seen)).order_by('service_expires_at', 'id')[:50]
    for order in future_qs:
        expires_at = getattr(order, 'service_expires_at', None)
        if not expires_at:
            continue
        if expires_at <= now:
            queue_status = 'fallback_retry'
            queue_status_label = '过期后兜底重试'
        elif expires_at <= now + timezone.timedelta(days=1):
            queue_status = 'within_window'
            queue_status_label = '24小时内进入执行窗口'
        else:
            queue_status = 'scheduled_future'
            queue_status_label = '未来计划'
        plan_items.append(_auto_renew_due_item_payload(order, queue_status=queue_status, queue_status_label=queue_status_label, next_run_at=next_run_at))
    return plan_items


def _auto_renew_history_item_payload(log):
    order_id = getattr(log, 'completed_order_id', None) or getattr(log, 'order_id', None)
    return {
        'id': log.id,
        'batch_id': log.batch_id,
        'order_id': order_id,
        'order_no': log.order_no,
        'ip': log.ip,
        'provider': log.provider,
        'provider_label': _provider_label(log.provider),
        'user_id': log.user_id,
        'tg_user_id': log.tg_user_id,
        'user_display_name': log.user_display_name or '未绑定用户',
        'username_label': log.username_label or '-',
        'is_success': bool(log.is_success),
        'result_label': '成功' if log.is_success else '失败',
        'failure_reason': log.failure_reason,
        'currency': log.currency,
        'balance_before': _decimal_to_str(log.balance_before) if log.balance_before is not None else None,
        'balance_after': _decimal_to_str(log.balance_after) if log.balance_after is not None else None,
        'balance_change': _decimal_to_str(log.balance_change) if log.balance_change is not None else None,
        'service_expires_at': _iso(log.service_expires_at),
        'executed_at': _iso(log.executed_at),
        'related_path': f'/admin/cloud-orders/{order_id}' if order_id else '',
        'detail_path': f'/admin/cloud-orders/{order_id}' if order_id else '',
        'order_detail_path': f'/admin/cloud-orders/{order_id}' if order_id else '',
        'order_link_path': f'/admin/cloud-orders/{order_id}' if order_id else '',
    }


@dashboard_login_required
@require_GET
def auto_renew_task_detail(request):
    now = timezone.now()
    due = async_to_sync(_get_due_orders)()
    due_orders = list(due.get('auto_renew') or [])
    history_qs = CloudAutoRenewPatrolLog.objects.select_related('order', 'user').order_by('-executed_at', '-id')
    history_items = [_auto_renew_history_item_payload(item) for item in history_qs[:200]]
    latest_log = history_qs.first()
    recent_since = now - timezone.timedelta(days=1)
    recent_logs = history_qs.filter(executed_at__gte=recent_since)
    last_run_at = getattr(latest_log, 'executed_at', None)
    next_run_at = (last_run_at + timezone.timedelta(minutes=30)) if last_run_at else (now + timezone.timedelta(minutes=30))
    latest_batch_id = getattr(latest_log, 'batch_id', '') or ''
    latest_batch_qs = history_qs.filter(batch_id=latest_batch_id) if latest_batch_id else history_qs.none()
    latest_batch_count = latest_batch_qs.count() if latest_batch_id else 0
    latest_batch_success_count = latest_batch_qs.filter(is_success=True).count() if latest_batch_id else 0
    latest_batch_failure_count = latest_batch_qs.filter(is_success=False).count() if latest_batch_id else 0
    latest_failed_ips = list(latest_batch_qs.filter(is_success=False).values_list('ip', flat=True)[:20]) if latest_batch_id else []
    due_items = [_auto_renew_due_item_payload(order, queue_status='due_now', queue_status_label='本轮待执行', next_run_at=next_run_at) for order in due_orders]
    due_ids = {order.id for order in due_orders}
    failed_retry_items = []
    recent_failed_logs = history_qs.filter(is_success=False, executed_at__gte=now - timezone.timedelta(days=7))
    for log in recent_failed_logs:
        order = getattr(log, 'order', None)
        if not order or not getattr(order, 'auto_renew_enabled', False):
            continue
        if order.id in due_ids:
            continue
        if order.status not in {'completed', 'expiring', 'renew_pending'}:
            continue
        due_ids.add(order.id)
        failed_retry_items.append(_auto_renew_due_item_payload(order, queue_status='retry_failed', queue_status_label='失败待重试', next_run_at=next_run_at, last_failure_reason=log.failure_reason))
    due_items.extend(failed_retry_items)
    future_plan_items = _auto_renew_future_plan_items(now, next_run_at, due_orders)
    return _ok({
        'task_key': 'auto_renew_patrol',
        'task_label': '自动续费巡检',
        'status_label': '置顶任务',
        'interval_minutes': 30,
        'last_run_at': _iso(last_run_at),
        'next_run_at': _iso(next_run_at),
        'due_count': len(due_items),
        'recent_success_count': recent_logs.filter(is_success=True).count(),
        'recent_failure_count': recent_logs.filter(is_success=False).count(),
        'latest_batch_id': latest_batch_id,
        'latest_batch_count': latest_batch_count,
        'latest_batch_success_count': latest_batch_success_count,
        'latest_batch_failure_count': latest_batch_failure_count,
        'latest_failed_ips': latest_failed_ips,
        'due_items': due_items,
        'future_plan_items': future_plan_items,
        'history_items': history_items,
    })




def _cloud_execution_status(note: str | None):
    text = str(note or '').strip()
    if not text:
        return '', ''
    if '阿里云真实续费失败' in text:
        return 'aliyun_renew_failed', '阿里云续费失败，待重试'
    if '关机失败' in text:
        return 'suspend_failed', '关机失败，待重试'
    if '删除失败' in text:
        return 'delete_failed', '删机失败，待重试'
    if '旧实例删除失败' in text or '旧服务器删除失败' in text:
        return 'migration_delete_failed', '迁移旧机删除失败，待重试'
    return '', ''


def _mask_secret(value, keep=4):
    text = str(value or '')
    if not text:
        return ''
    if len(text) <= keep * 2:
        return '*' * len(text)
    return f'{text[:keep]}***{text[-keep:]}'


def _cloud_order_source_tags(order):
    note = str(getattr(order, 'provision_note', '') or '')
    order_no = str(getattr(order, 'order_no', '') or '')
    tags: list[tuple[str, str]] = []
    seen = set()

    def add(tag_key: str, tag_label: str):
        if tag_key in seen:
            return
        seen.add(tag_key)
        tags.append((tag_key, tag_label))

    if '人工编辑' in note or order_no.startswith('SRVMANUAL') or order_no.startswith('SRVADMIN'):
        if '所属人' in note or '用户' in note:
            add('manual_owner_change', '人工改用户')
        if '到期时间' in note:
            add('manual_expiry_change', '人工改时间')
        if '价格' in note:
            add('manual_price_change', '人工改价格')
        if ('所属人' in note or '用户' in note) and '时间' in note and not tags:
            add('manual_owner_expiry_change', '人工改用户+时间')
    if not tags:
        if getattr(order, 'replacement_for_id', None):
            add('renewal_rebuild', '续费恢复')
        elif getattr(order, 'last_renewed_at', None) or getattr(order, 'status', '') == 'renew_pending' or '续费' in note:
            add('renewal', '续费')
        else:
            add('new', '新购')
    return tags


def _cloud_order_source_label(order):
    tags = _cloud_order_source_tags(order)
    first_tag = tags[0] if tags else ('new', '新购')
    return first_tag[0], first_tag[1]


def _cloud_order_summary_payload(order):
    if not order:
        return None
    order_source, order_source_label = _cloud_order_source_label(order)
    order_source_tags = _cloud_order_source_tags(order)
    return {
        'id': order.id,
        'order_id': order.id,
        'order_no': order.order_no,
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'order_source': order_source,
        'order_source_label': order_source_label,
        'order_source_tags': [item[0] for item in order_source_tags],
        'order_source_tag_labels': [item[1] for item in order_source_tags],
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'public_ip': order.public_ip,
        'service_started_at': _iso(order.service_started_at),
        'service_expires_at': _iso(order.service_expires_at),
        'created_at': _iso(order.created_at),
        'updated_at': _iso(order.updated_at),
        'replacement_for_id': order.replacement_for_id,
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
    }


def _related_order_history_payload(order):
    if not order:
        return []
    root = order
    seen = set()
    while root.replacement_for_id and root.replacement_for_id not in seen:
        seen.add(root.id)
        parent = CloudServerOrder.objects.select_related('user', 'plan').filter(id=root.replacement_for_id).first()
        if not parent:
            break
        root = parent
    chain = list(
        CloudServerOrder.objects.select_related('user', 'plan')
        .filter(Q(id=root.id) | Q(replacement_for_id=root.id) | Q(replacement_for__replacement_for_id=root.id) | Q(replacement_for__replacement_for__replacement_for_id=root.id))
        .order_by('-created_at', '-id')[:20]
    )
    if order.id not in {item.id for item in chain}:
        chain.insert(0, order)
    deduped = []
    seen_ids = set()
    for item in chain:
        if item.id in seen_ids:
            continue
        seen_ids.add(item.id)
        deduped.append(_cloud_order_summary_payload(item))
    return deduped


def _cloud_order_detail_payload(order):
    user = order.user
    usernames = user.usernames if user else []
    user_payload = _user_payload({
        'id': user.id,
        'tg_user_id': user.tg_user_id,
        'username': user.username,
        'first_name': user.first_name,
        'usernames': usernames,
        'primary_username': usernames[0] if usernames else '',
    }) if user else None
    order_source, order_source_label = _cloud_order_source_label(order)
    payload = {
        'id': order.id,
        'order_no': order.order_no,
        'provider': order.provider,
        'cloud_account_id': order.cloud_account_id,
        'account_label': order.account_label,
        'region_code': order.region_code,
        'region_label': _region_label(order.region_code, order.region_name),
        'region_name': order.region_name,
        'plan_name': order.plan_name,
        'quantity': order.quantity,
        'currency': order.currency,
        'total_amount': _decimal_to_str(order.total_amount),
        'pay_amount': _decimal_to_str(order.pay_amount) if order.pay_amount is not None else None,
        'pay_method': order.pay_method,
        'order_source': order_source,
        'order_source_label': order_source_label,
        'order_source_tags': [item[0] for item in _cloud_order_source_tags(order)],
        'order_source_tag_labels': [item[1] for item in _cloud_order_source_tags(order)],
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'tx_hash': order.tx_hash,
        'payer_address': order.payer_address,
        'receive_address': order.receive_address,
        'tronscan_url': f'https://tronscan.org/#/transaction/{order.tx_hash}' if order.tx_hash else '',
        'image_name': order.image_name,
        'server_name': order.server_name,
        'lifecycle_days': order.lifecycle_days,
        'service_started_at': _iso(order.service_started_at),
        'service_expires_at': _iso(order.service_expires_at),
        'renew_grace_expires_at': _iso(order.renew_grace_expires_at),
        'suspend_at': _iso(order.suspend_at),
        'delete_at': _iso(order.delete_at),
        'ip_recycle_at': _iso(order.ip_recycle_at),
        'last_renewed_at': _iso(order.last_renewed_at),
        'last_user_id': order.last_user_id,
        'mtproxy_port': order.mtproxy_port,
        'mtproxy_link': order.mtproxy_link,
        'proxy_links': order.proxy_links or [],
        'mtproxy_secret': _mask_secret(order.mtproxy_secret),
        'has_mtproxy_secret': bool(order.mtproxy_secret),
        'mtproxy_host': order.mtproxy_host,
        'instance_id': order.instance_id,
        'provider_resource_id': order.provider_resource_id,
        'static_ip_name': order.static_ip_name,
        'public_ip': order.public_ip,
        'previous_public_ip': order.previous_public_ip,
        'login_user': order.login_user,
        'login_password': _mask_secret(order.login_password),
        'has_login_password': bool(order.login_password),
        'provision_note': order.provision_note,
        'created_at': _iso(order.created_at),
        'paid_at': _iso(order.paid_at),
        'expired_at': _iso(order.expired_at),
        'completed_at': _iso(order.completed_at),
        'updated_at': _iso(order.updated_at),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'plan_id': order.plan_id,
        'execution_status': _cloud_execution_status(order.provision_note)[0],
        'execution_status_label': _cloud_execution_status(order.provision_note)[1],
    }
    payload.update({
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
        'replacement_for_detail_path': f'/admin/cloud-orders/{order.replacement_for_id}' if order.replacement_for_id else '',
        'history_orders': _related_order_history_payload(order),
    })
    return payload


@dashboard_login_required
@require_GET
def cloud_orders_list(request):
    keyword = _get_keyword(request)
    queryset = (
        CloudServerOrder.objects.select_related('user', 'plan')
        .exclude(Q(status='deleted') | Q(order_no__startswith='SRVMANUAL'))
        .order_by('-created_at')
    )
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['order_no', 'provider', 'region_name', 'plan_name', 'status', 'public_ip', 'user__tg_user_id', 'user__username'],
    )
    items = [_cloud_order_detail_payload(item) for item in queryset[:100]]
    now = timezone.now()
    for item in items:
        status = item.get('status')
        service_expires_at = item.get('service_expires_at')
        renew_grace_expires_at = item.get('renew_grace_expires_at')
        delete_at = item.get('delete_at')
        auto_renew_enabled = bool(item.get('auto_renew_enabled'))

        service_expires_dt = parse_datetime(service_expires_at) if isinstance(service_expires_at, str) and service_expires_at else None
        renew_grace_dt = parse_datetime(renew_grace_expires_at) if isinstance(renew_grace_expires_at, str) and renew_grace_expires_at else None
        delete_dt = parse_datetime(delete_at) if isinstance(delete_at, str) and delete_at else None
        if service_expires_dt is not None and timezone.is_naive(service_expires_dt):
            service_expires_dt = timezone.make_aware(service_expires_dt, timezone.get_current_timezone())
        if renew_grace_dt is not None and timezone.is_naive(renew_grace_dt):
            renew_grace_dt = timezone.make_aware(renew_grace_dt, timezone.get_current_timezone())
        if delete_dt is not None and timezone.is_naive(delete_dt):
            delete_dt = timezone.make_aware(delete_dt, timezone.get_current_timezone())

        if status in {'pending', 'cancelled', 'failed'}:
            item['renew_status'] = 'unpaid'
            item['renew_status_label'] = '未付款'
        elif status in {'paid', 'provisioning'}:
            item['renew_status'] = 'paid'
            item['renew_status_label'] = '已付款'
        elif status in {'completed', 'renew_pending', 'expiring', 'suspended', 'deleting', 'deleted', 'expired'}:
            item['renew_status'] = 'completed'
            item['renew_status_label'] = '已完成'
        elif service_expires_dt and service_expires_dt <= now:
            item['renew_status'] = 'completed'
            item['renew_status_label'] = '已完成'
        else:
            item['renew_status'] = 'unpaid'
            item['renew_status_label'] = '未付款'

        item['can_renew'] = item['renew_status'] != 'unpaid' and status not in {'cancelled', 'failed'}
        item['auto_renew_enabled'] = auto_renew_enabled
        item['expired_by_time'] = bool(service_expires_dt and service_expires_dt <= now)
        item['grace_expired'] = bool(renew_grace_dt and renew_grace_dt <= now)
        item['delete_scheduled'] = bool(delete_dt and delete_dt > now)
        item['is_expired'] = status in {'deleted', 'expired'} or item['grace_expired']
        item['expires_in_days'] = _days_left(service_expires_dt) if service_expires_dt else None
        item['grace_expires_in_days'] = _days_left(renew_grace_dt) if renew_grace_dt else None
    return _ok(items)


@csrf_exempt
@dashboard_login_required
@require_POST
def delete_cloud_order(request, order_id):
    order = CloudServerOrder.objects.filter(id=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    order_no = order.order_no
    order.delete()
    logger.info('DASHBOARD_CLOUD_ORDER_DELETE order_id=%s order_no=%s user=%s', order_id, order_no, getattr(request.user, 'id', None))
    return _ok(True)


def _server_payload(server):
    user = server.user
    user_payload = None
    if user:
        usernames = user.usernames
        user_payload = _user_payload({
            'id': user.id,
            'tg_user_id': user.tg_user_id,
            'username': user.username,
            'first_name': user.first_name,
            'usernames': usernames,
            'primary_username': usernames[0] if usernames else '',
        })
    order = server.order
    return {
        'id': server.id,
        'status': server.status,
        'status_label': _status_label(server.status, Server.STATUS_CHOICES),
        'source': server.source,
        'source_label': _server_source_label(server.source),
        'provider': server.provider,
        'provider_label': _provider_label(server.provider),
        'account_label': server.account_label,
        'region_label': _region_label(server.region_code, server.region_name),
        'region_name': server.region_name,
        'server_name': server.server_name,
        'instance_id': server.instance_id,
        'provider_resource_id': server.provider_resource_id,
        'public_ip': server.public_ip,
        'login_user': server.login_user,
        'expires_at': _iso(server.expires_at),
        'days_left': _days_left(server.expires_at),
        'status_countdown': _countdown_label(server.expires_at),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'order_id': order.id if order else None,
        'order_no': order.order_no if order else '',
        'order_detail_path': f'/admin/cloud-orders/{order.id}' if order else '',
        'provider_status': '已删除' if server.status == Server.STATUS_DELETED else _provider_status_label(server.provider_status),
        'preserve_link_status': _preserve_link_status_label(server.note, getattr(order, 'provision_note', None)),
        'is_active': server.is_active,
        'updated_at': _iso(server.updated_at),
    }


@dashboard_login_required
@require_GET
def servers_list(request):
    keyword = _get_keyword(request)
    dedup_raw = (request.GET.get('dedup') or '').lower()
    dedup = dedup_raw not in {'0', 'false', 'no', 'off'}
    queryset = Server.objects.select_related('user', 'order').exclude(status=Server.STATUS_DELETED).exclude(public_ip__isnull=True).exclude(public_ip='').order_by('expires_at', '-updated_at', '-id')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['server_name', 'instance_id', 'public_ip', 'account_label', 'provider', 'region_name', 'user__tg_user_id', 'user__username', 'order__order_no'],
    )
    provider = (request.GET.get('provider') or '').strip()
    region_code = (request.GET.get('region_code') or '').strip()
    if provider:
        queryset = queryset.filter(provider=provider)
    if region_code:
        queryset = queryset.filter(region_code=region_code)
    items = [_server_payload(server) for server in queryset[:500]]
    if dedup:
        seen = set()
        deduped = []
        for item in items:
            dedup_key = (item.get('provider') or '', item.get('instance_id') or '', item.get('public_ip') or '', item.get('server_name') or '')
            if dedup_key in seen:
                continue
            seen.add(dedup_key)
            deduped.append(item)
        items = deduped
    return _ok(items)


def _append_provision_note(order, note):
    if not note:
        return order.provision_note
    return '\n'.join(filter(None, [order.provision_note, note]))


@transaction.atomic
def _apply_cloud_order_status(order, new_status):
    now = timezone.now()
    old_status = order.status
    allowed_statuses = {choice[0] for choice in CloudServerOrder.STATUS_CHOICES}
    if new_status not in allowed_statuses:
        raise ValueError('订单状态不正确')
    if new_status == old_status:
        return order

    note = None
    trigger_provision = False
    active_statuses = {'completed', 'renew_pending', 'expiring'}
    inactive_statuses = {'failed', 'cancelled', 'expired', 'deleted', 'suspended', 'deleting', 'pending'}

    if new_status in {'paid', 'provisioning', 'completed'} and not order.paid_at:
        order.paid_at = now

    if new_status == 'completed':
        if not order.completed_at:
            order.completed_at = now
        if not order.last_renewed_at:
            order.last_renewed_at = now
        note = '后台手动改状态为已完成。'
    elif new_status == 'paid':
        order.completed_at = None
        note = '后台手动改状态为已支付。'
        if not (order.instance_id or order.provider_resource_id or order.public_ip):
            trigger_provision = True
    elif new_status == 'provisioning':
        order.completed_at = None
        note = '后台手动改状态为创建中。'
        if not (order.instance_id or order.provider_resource_id or order.public_ip):
            trigger_provision = True
    elif new_status == 'renew_pending':
        order.completed_at = None
        if order.service_expires_at and order.service_expires_at > now:
            order.last_renewed_at = order.last_renewed_at or now
        note = '后台手动改状态为待续费。'
    elif new_status == 'expiring':
        order.completed_at = None
        note = '后台手动改状态为即将到期。'
    elif new_status in inactive_statuses:
        if new_status == 'pending':
            order.paid_at = None
        order.completed_at = None
        note = f"后台手动改状态为{dict(CloudServerOrder.STATUS_CHOICES).get(new_status, new_status)}。"

    order.status = new_status
    order.provision_note = _append_provision_note(order, note)
    order.save()

    if new_status in active_statuses:
        CloudAsset.objects.filter(order=order).update(
            is_active=True,
            note=order.provision_note,
            updated_at=now,
        )
        Server.objects.filter(order=order).update(
            is_active=True,
            status=Server.STATUS_RUNNING if new_status == 'completed' else Server.STATUS_PENDING,
            note=order.provision_note,
            updated_at=now,
        )
    elif new_status in inactive_statuses:
        CloudAsset.objects.filter(order=order).update(
            is_active=False,
            note=order.provision_note,
            updated_at=now,
        )
        Server.objects.filter(order=order).update(
            is_active=False,
            status=Server.STATUS_DELETED if new_status == 'deleted' else Server.STATUS_STOPPED,
            note=order.provision_note,
            updated_at=now,
        )

    if trigger_provision:
        async_to_sync(provision_cloud_server)(order.id)
        order.refresh_from_db()

    return order


@csrf_exempt
@dashboard_login_required
@require_http_methods(['GET', 'POST', 'PUT', 'PATCH'])
def cloud_order_detail(request, order_id):
    order = CloudServerOrder.objects.select_related('user', 'plan').filter(pk=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    if request.method == 'GET':
        return _ok(_cloud_order_detail_payload(order))

    payload = _read_payload(request)
    try:
        with transaction.atomic():
            order = CloudServerOrder.objects.select_for_update().select_related('user', 'plan').get(pk=order_id)
            changed_fields = set()
            user_lookup = payload.get('user_query') or payload.get('user_id') or payload.get('tg_user_id') or payload.get('username')
            clear_user = str(payload.get('clear_user') or '').lower() in {'1', 'true', 'yes', 'on'}
            if clear_user:
                return _error('订单必须绑定用户，不能清空所属用户', status=400)
            elif user_lookup not in (None, ''):
                user = _resolve_telegram_user(user_lookup)
                if not user:
                    return _error('未找到匹配的 Telegram 用户', status=404)
                order.user = user
                order.last_user_id = user.tg_user_id
                changed_fields.update({'user', 'last_user_id'})
                _sync_telegram_username(user, user_lookup)

            for field in ('server_name', 'public_ip', 'previous_public_ip', 'instance_id', 'provider_resource_id', 'static_ip_name', 'mtproxy_host', 'mtproxy_link', 'provision_note'):
                if field in payload:
                    setattr(order, field, payload.get(field) or None)
                    changed_fields.add(field)
            if 'mtproxy_port' in payload:
                mtproxy_port = payload.get('mtproxy_port')
                order.mtproxy_port = int(mtproxy_port) if mtproxy_port not in (None, '') else None
                changed_fields.add('mtproxy_port')
            if 'total_amount' in payload:
                order.total_amount = _parse_decimal(payload.get('total_amount'), '总金额')
                changed_fields.add('total_amount')
            if 'pay_amount' in payload:
                pay_amount = payload.get('pay_amount')
                order.pay_amount = _parse_decimal(pay_amount, '应付金额') if pay_amount not in (None, '') else None
                changed_fields.add('pay_amount')
            if 'status' in payload:
                status = str(payload.get('status') or '').strip()
                if status and status not in {choice[0] for choice in CloudServerOrder.STATUS_CHOICES}:
                    return _error('订单状态不正确', status=400)
                if status:
                    order.status = status
                    changed_fields.add('status')
            for field, label in (
                ('service_started_at', '服务开始时间'),
                ('service_expires_at', '服务到期时间'),
                ('renew_grace_expires_at', '续费宽限到期'),
                ('suspend_at', '计划关机时间'),
                ('delete_at', '计划删机时间'),
                ('ip_recycle_at', 'IP保留到期'),
            ):
                if field in payload:
                    setattr(order, field, _parse_iso_datetime(payload.get(field), label) if payload.get(field) else None)
                    changed_fields.add(field)
            if changed_fields:
                update_values = {field: getattr(order, field) for field in changed_fields}
                update_values['updated_at'] = timezone.now()
                CloudServerOrder.objects.filter(pk=order.pk).update(**update_values)
                order.refresh_from_db()
                asset_updates = {}
                server_updates = {}
                if 'user' in changed_fields:
                    asset_updates['user'] = order.user
                    server_updates['user'] = order.user
                if 'public_ip' in changed_fields:
                    asset_updates['public_ip'] = order.public_ip
                    server_updates['public_ip'] = order.public_ip
                if 'server_name' in changed_fields:
                    asset_updates['asset_name'] = order.server_name
                    server_updates['server_name'] = order.server_name
                if 'service_expires_at' in changed_fields:
                    asset_updates['actual_expires_at'] = order.service_expires_at
                    server_updates['expires_at'] = order.service_expires_at
                if asset_updates:
                    CloudAsset.objects.filter(order=order).update(**asset_updates, updated_at=timezone.now())
                if server_updates:
                    Server.objects.filter(order=order).update(**server_updates, updated_at=timezone.now())
    except ValueError as exc:
        return _error(str(exc), status=400)
    order.refresh_from_db()
    return _ok(_cloud_order_detail_payload(order))


@csrf_exempt
@dashboard_login_required
@require_POST
def update_cloud_order_status(request, order_id):
    order = CloudServerOrder.objects.select_related('user', 'plan').filter(pk=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    payload = _read_payload(request)
    new_status = str(payload.get('status') or '').strip()
    if not new_status:
        return _error('订单状态不能为空')
    try:
        order = _apply_cloud_order_status(order, new_status)
    except ValueError as exc:
        return _error(str(exc))
    except Exception as exc:
        return _error(f'更新订单状态失败: {exc}', status=500)
    return _ok(_cloud_order_detail_payload(order))


def _run_rebuild_job(new_order_id: int):
    max_attempts = 3
    retry_delays = [0, 20, 60]
    for attempt in range(1, max_attempts + 1):
        if attempt > 1:
            time.sleep(retry_delays[attempt - 1])
        try:
            saved = async_to_sync(provision_cloud_server)(new_order_id)
            if saved and getattr(saved, 'status', '') == 'completed' and getattr(saved, 'replacement_for_id', None):
                source_order = CloudServerOrder.objects.filter(id=saved.replacement_for_id).first()
                if not source_order:
                    return
                delete_note = async_to_sync(_delete_instance)(source_order)
                async_to_sync(_mark_replaced_order_deleted)(source_order.id, f'重装迁移完成，新实例订单: {saved.order_no}；{delete_note}')
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
        CloudAsset.objects.filter(order=source_order).update(note=failure_note, updated_at=timezone.now())
        Server.objects.filter(order=source_order).update(note=failure_note, updated_at=timezone.now())


@csrf_exempt
@dashboard_login_required
@require_POST
def rebuild_server_preserve_link(request, server_id: int):
    server = Server.objects.select_related('order').filter(id=server_id).first()
    if not server or not server.order_id:
        return _error('服务器不存在或未关联订单', status=404)
    order, error = create_cloud_server_rebuild_order(server.order_id)
    if error:
        return _error(error, status=400)
    thread = threading.Thread(target=_run_rebuild_job, args=(order.id,), daemon=True)
    thread.start()
    return _ok({
        'accepted': True,
        'message': '已发起 AWS 重装迁移，后台失败会自动重试（最多 3 次），成功后将删除旧实例。',
        'order_id': order.id,
        'order_no': order.order_no,
        'replacement_for_id': order.replacement_for_id,
    })


@csrf_exempt
@dashboard_login_required
@require_http_methods(['POST', 'DELETE'])
def delete_cloud_asset(request, asset_id: int):
    asset = CloudAsset.objects.select_related('order').filter(id=asset_id).first()
    if not asset:
        return _error('代理记录不存在', status=404)
    now = timezone.now()
    before_status = asset.status
    note = f'后台手动删除代理列表记录；时间: {now.isoformat()}'
    previous_public_ip = asset.public_ip or asset.previous_public_ip
    order = asset.order

    residual_statuses = {
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_TERMINATED,
        CloudAsset.STATUS_TERMINATING,
        CloudAsset.STATUS_EXPIRED,
    }
    residual_order_statuses = {'deleted', 'deleting', 'expired', 'cancelled', 'refunded', 'failed'}

    def _looks_like_local_residual(server):
        provider_status = str(getattr(server, 'provider_status', '') or '')
        server_note = str(getattr(server, 'note', '') or '')
        asset_provider_status = str(getattr(asset, 'provider_status', '') or '')
        asset_note = str(getattr(asset, 'note', '') or '')
        return (
            asset.status in residual_statuses
            or server.status in residual_statuses
            or (order and order.status in residual_order_statuses)
            or not getattr(server, 'is_active', True)
            or '云上未找到' in provider_status
            or '云上未找到' in server_note
            or '云上未找到' in asset_provider_status
            or '云上未找到' in asset_note
        )

    related_servers = Server.objects.filter(
        Q(order=order)
        | Q(instance_id=asset.instance_id)
        | Q(provider_resource_id=asset.provider_resource_id)
        | Q(public_ip=asset.public_ip)
        | Q(public_ip=asset.previous_public_ip)
        | Q(previous_public_ip=asset.public_ip)
        | Q(previous_public_ip=asset.previous_public_ip)
    ).distinct()
    removed_server_ids = []
    for server in related_servers:
        if not _looks_like_local_residual(server):
            continue
        record_cloud_ip_log(
            event_type='deleted',
            order=getattr(server, 'order', None),
            server=server,
            previous_public_ip=server.public_ip or server.previous_public_ip,
            public_ip=None,
            note=f'{note}；检测为本地残留服务器记录，已一并清理',
        )
        removed_server_ids.append(server.id)
        server.delete()

    record_cloud_ip_log(event_type='deleted', order=order, asset=asset, previous_public_ip=previous_public_ip, public_ip=None, note=note)
    asset.delete()
    return _ok({
        'target_type': 'cloud_asset',
        'target_id': asset_id,
        'before_status': before_status,
        'after_status': None,
        'hard_deleted': True,
        'exists_after': CloudAsset.objects.filter(id=asset_id).exists(),
        'removed_servers': len(removed_server_ids),
        'removed_server_ids': removed_server_ids,
        'order_status_changed': False,
    })


@csrf_exempt
@dashboard_login_required
@require_http_methods(['POST', 'DELETE'])
def delete_server(request, server_id: int):
    server = Server.objects.select_related('order').filter(id=server_id).first()
    if not server:
        return _error('服务器不存在', status=404)
    now = timezone.now()
    before_status = server.status
    note = f'后台手动删除服务器列表记录；时间: {now.isoformat()}'
    previous_public_ip = server.public_ip or server.previous_public_ip
    order = server.order

    record_cloud_ip_log(event_type='deleted', order=order, server=server, previous_public_ip=previous_public_ip, public_ip=None, note=note)
    server.delete()
    return _ok({
        'target_type': 'server',
        'target_id': server_id,
        'before_status': before_status,
        'after_status': None,
        'hard_deleted': True,
        'exists_after': Server.objects.filter(id=server_id).exists(),
        'removed_assets': 0,
        'order_status_changed': False,
    })


def _statistics_account_label(account) -> str:
    return cloud_account_label(account)


def _cloud_account_labels_queryset(is_active: bool | None = None):
    queryset = CloudAccountConfig.objects.filter(
        provider__in=[CloudAccountConfig.PROVIDER_AWS, CloudAccountConfig.PROVIDER_ALIYUN],
    )
    if is_active is not None:
        queryset = queryset.filter(is_active=is_active)
    labels = []
    for account in queryset:
        label = cloud_account_label(account)
        if label:
            labels.append(label)
    return labels


@dashboard_login_required
@require_GET
def servers_statistics(request):
    keyword = _get_keyword(request)
    aws_regions = [{'region_code': code, 'region_label': label} for code, label in AWS_REGION_NAMES.items()]
    region_pairs = [*aws_regions, {'region_code': 'cn-hongkong', 'region_label': '香港'}]
    region_codes = [item['region_code'] for item in region_pairs]

    active_statuses = [
        Server.STATUS_RUNNING,
        Server.STATUS_PENDING,
        Server.STATUS_STARTING,
        Server.STATUS_STOPPED,
        Server.STATUS_SUSPENDED,
        Server.STATUS_EXPIRED_GRACE,
    ]
    active_account_labels = _cloud_account_labels_queryset(True)
    inactive_account_labels = _cloud_account_labels_queryset(False)
    queryset = Server.objects.select_related('order', 'order__cloud_account').filter(status__in=active_statuses).exclude(
        account_label__in=inactive_account_labels,
    ).filter(
        Q(account_label__in=active_account_labels)
        | Q(account_label__isnull=True)
        | Q(account_label='')
        | Q(order__cloud_account__is_active=True)
    )
    if keyword:
        queryset = _apply_keyword_filter(
            queryset,
            keyword,
            ['region_code', 'region_name', 'provider', 'account_label', 'server_name', 'instance_id', 'public_ip'],
        )
    rows = list(
        queryset
        .values('provider', 'region_code', 'region_name', 'account_label')
        .annotate(total_count=Count('id'))
        .order_by('account_label', 'provider', 'region_name')
    )

    account_map = {}
    active_accounts = list(CloudAccountConfig.objects.filter(provider__in=[CloudAccountConfig.PROVIDER_AWS, CloudAccountConfig.PROVIDER_ALIYUN], is_active=True).order_by('provider', 'id'))
    for account in active_accounts:
        technical_label = cloud_account_label(account)
        display_label = _statistics_account_label(account)
        if keyword and keyword.lower() not in technical_label.lower() and keyword.lower() not in display_label.lower() and keyword.lower() not in account.provider.lower():
            has_server_match = any((row.get('account_label') or '') == technical_label for row in rows)
            if not has_server_match:
                continue
        account_map[technical_label] = {
            'account_id': technical_label,
            'account_label': display_label,
            'provider_label': 'AWS' if account.provider == CloudAccountConfig.PROVIDER_AWS else '阿里云',
            'regions': {},
            'total_count': 0,
            'sort_key': (account.provider, account.id),
        }

    for row in rows:
        technical_label = row['account_label'] or '-'
        entry = account_map.setdefault(
            technical_label,
            {
                'account_id': technical_label,
                'account_label': technical_label,
                'provider_label': _provider_label(row['provider']),
                'regions': {},
                'total_count': 0,
                'sort_key': (row['provider'] or '', 999999, technical_label),
            },
        )
        region_key = row['region_code'] or _region_label(row['region_code'] or '', row['region_name'])
        if region_key not in region_codes:
            continue
        count = row['total_count']
        entry['regions'][region_key] = entry['regions'].get(region_key, 0) + count
        entry['total_count'] += count

    items = []
    totals = {'account_id': '合计', 'account_label': '合计', 'provider_label': '-', 'regions': {}, 'total_count': 0}
    for technical_label, entry in sorted(account_map.items(), key=lambda item: item[1]['sort_key']):
        row_payload = {
            'account_id': entry['account_id'],
            'account_label': entry['account_label'],
            'provider_label': entry['provider_label'],
            'total_count': entry['total_count'],
        }
        for region in region_pairs:
            region_key = region['region_code']
            value = entry['regions'].get(region_key, 0)
            row_payload[region_key] = value
            totals['regions'][region_key] = totals['regions'].get(region_key, 0) + value
        totals['total_count'] += entry['total_count']
        items.append(row_payload)

    total_row = {
        'account_id': totals['account_id'],
        'account_label': totals['account_label'],
        'provider_label': totals['provider_label'],
        'total_count': totals['total_count'],
    }
    for region in region_pairs:
        total_row[region['region_code']] = totals['regions'].get(region['region_code'], 0)

    return _ok({
        'regions': region_pairs,
        'items': items,
        'summary': total_row,
    })


@csrf_exempt
@dashboard_login_required
@require_http_methods(['POST'])
def create_cloud_plan(request):
    data = _read_payload(request)
    provider = (data.get('provider') or '').strip()
    region_code = (data.get('region_code') or '').strip()
    region_name = (data.get('region_name') or '').strip()
    plan_name = (data.get('plan_name') or '').strip()
    if not provider or not region_code or not region_name or not plan_name:
        return _error('云厂商、地区代码、地区名称、套餐名不能为空')
    provider_plan_id = (data.get('provider_plan_id') or '').strip()
    resolved_config_id = _resolve_cloud_plan_config_id(
        provider=provider,
        region_code=region_code,
        provider_plan_id=provider_plan_id,
        config_id=(data.get('config_id') or '').strip(),
    )
    try:
        payload_fields = {
            'provider': provider,
            'region_code': region_code,
            'region_name': region_name,
            'config_id': resolved_config_id,
            'provider_plan_id': provider_plan_id,
            'plan_name': plan_name,
            'plan_description': ((data.get('plan_description') or data.get('display_description') or '').strip()),
            'display_plan_name': (data.get('display_plan_name') or '').strip(),
            'display_cpu': (data.get('display_cpu') or '').strip(),
            'display_memory': (data.get('display_memory') or '').strip(),
            'display_storage': (data.get('display_storage') or '').strip(),
            'display_bandwidth': (data.get('display_bandwidth') or '').strip(),
            'display_description': (data.get('display_description') or '').strip(),
            'cpu': (data.get('cpu') or '').strip(),
            'memory': (data.get('memory') or '').strip(),
            'storage': (data.get('storage') or '').strip(),
            'bandwidth': (data.get('bandwidth') or '').strip(),
            'cost_price': _parse_decimal(data.get('cost_price') or 0, '进货价').quantize(Decimal('0.01')),
            'price': _parse_decimal(data.get('price') or 0, '出售价').quantize(Decimal('0.01')),
            'currency': (data.get('currency') or 'USDT').strip() or 'USDT',
            'sort_order': int(data.get('sort_order') or 0),
            'is_active': str(data.get('is_active', True)).lower() in {'1', 'true', 'yes', 'on'},
        }
        existed = CloudServerPlan.objects.filter(
            provider=provider,
            region_code=region_code,
            config_id=resolved_config_id,
        ).order_by('-id').first()
        if existed:
            for field, value in payload_fields.items():
                setattr(existed, field, value)
            existed.is_active = True
            existed.save()
            plan = existed
        else:
            plan = CloudServerPlan.objects.create(**payload_fields)
    except IntegrityError:
        return _error('同地区下已存在同厂商配置ID', status=400)
    except (InvalidOperation, TypeError, ValueError):
        return _error('提交的套餐数据格式不正确', status=400)
    async_to_sync(refresh_custom_plan_cache)()
    return _ok(_cloud_plan_payload(plan))


@csrf_exempt
@dashboard_login_required
@require_http_methods(['POST'])
def delete_cloud_plan(request, plan_id: int):
    plan = CloudServerPlan.objects.filter(id=plan_id).first()
    if not plan:
        return _error('套餐不存在', status=404)
    if CloudServerOrder.objects.filter(plan_id=plan_id).exists():
        return _error('该套餐已有订单引用，无法删除，请改为停用', status=400)
    plan.delete()
    async_to_sync(refresh_custom_plan_cache)()
    return _ok({'id': plan_id, 'deleted': True})


@csrf_exempt
@dashboard_login_required
@require_http_methods(['POST'])
def update_cloud_plan(request, plan_id: int):
    plan = CloudServerPlan.objects.filter(id=plan_id).first()
    if not plan:
        return _error('套餐不存在', status=404)
    data = _read_payload(request)
    plan_name = (data.get('plan_name') or '').strip()
    display_description = (data.get('display_description') or '').strip()
    plan_description = (data.get('plan_description') or display_description).strip()
    price = data.get('price')
    cost_price = data.get('cost_price')
    sort_order = data.get('sort_order')
    is_active = data.get('is_active')
    try:
        config_id = (data.get('config_id') or '').strip()
        provider_plan_id = (data.get('provider_plan_id') or '').strip()
        next_provider = (data.get('provider') or '').strip() or plan.provider
        next_region_code = (data.get('region_code') or '').strip() or plan.region_code
        next_provider_plan_id = provider_plan_id if 'provider_plan_id' in data else plan.provider_plan_id
        resolved_config_id = _resolve_cloud_plan_config_id(
            provider=next_provider,
            region_code=next_region_code,
            provider_plan_id=next_provider_plan_id,
            config_id=config_id if 'config_id' in data else plan.config_id,
        )
        plan.config_id = resolved_config_id
        if 'provider_plan_id' in data:
            plan.provider_plan_id = provider_plan_id
        if plan_name:
            plan.plan_name = plan_name
        if 'provider' in data:
            plan.provider = (data.get('provider') or '').strip() or plan.provider
        if 'region_code' in data:
            plan.region_code = (data.get('region_code') or '').strip() or plan.region_code
        if 'region_name' in data:
            plan.region_name = (data.get('region_name') or '').strip() or plan.region_name
        if 'display_plan_name' in data:
            plan.display_plan_name = (data.get('display_plan_name') or '').strip()
        if 'display_cpu' in data:
            plan.display_cpu = (data.get('display_cpu') or '').strip()
        if 'display_memory' in data:
            plan.display_memory = (data.get('display_memory') or '').strip()
        if 'display_storage' in data:
            plan.display_storage = (data.get('display_storage') or '').strip()
        if 'display_bandwidth' in data:
            plan.display_bandwidth = (data.get('display_bandwidth') or '').strip()
        if 'display_description' in data:
            plan.display_description = display_description
        if 'cpu' in data:
            plan.cpu = (data.get('cpu') or '').strip()
        if 'memory' in data:
            plan.memory = (data.get('memory') or '').strip()
        if 'storage' in data:
            plan.storage = (data.get('storage') or '').strip()
        if 'bandwidth' in data:
            plan.bandwidth = (data.get('bandwidth') or '').strip()
        if 'currency' in data:
            plan.currency = (data.get('currency') or 'USDT').strip() or 'USDT'
        plan.plan_description = plan_description
        if price not in (None, ''):
            plan.price = Decimal(str(price))
        if cost_price not in (None, ''):
            plan.cost_price = Decimal(str(cost_price))
        if sort_order not in (None, ''):
            plan.sort_order = int(sort_order)
        if is_active not in (None, ''):
            plan.is_active = str(is_active).lower() in {'1', 'true', 'yes', 'on'}
        plan.save()
    except IntegrityError:
        return _error('同地区下已存在同厂商配置ID', status=400)
    except (InvalidOperation, ValueError):
        return _error('提交的套餐数据格式不正确')
    async_to_sync(refresh_custom_plan_cache)()
    return _ok(_cloud_plan_payload(plan))


def _apply_server_missing_state(provider, region, existing_instance_ids, account=None):
    now = timezone.now()
    existing_instance_ids = {str(item) for item in existing_instance_ids if item}
    queryset = Server.objects.filter(provider=provider, region_code=region).exclude(instance_id__isnull=True).exclude(instance_id='')
    if account:
        queryset = queryset.filter(account_label=cloud_account_label(account))
    legacy_queryset = queryset.filter(provider_status='missing')
    legacy_updated = legacy_queryset.update(
        status=Server.STATUS_DELETED,
        provider_status='已删除',
        is_active=False,
        note=Case(
            When(note__isnull=True, then=Value(f'历史状态修正：服务器不存在，已统一标记为已删除；检查时间: {now.isoformat()}')),
            When(note='', then=Value(f'历史状态修正：服务器不存在，已统一标记为已删除；检查时间: {now.isoformat()}')),
            default=Cast('note', output_field=CharField()),
            output_field=CharField(),
        ),
        updated_at=now,
    )
    queryset = queryset.filter(is_active=True)
    if existing_instance_ids:
        queryset = queryset.exclude(instance_id__in=existing_instance_ids)
    missing_servers = list(queryset.select_related('order'))
    missing_note = f'云平台同步未发现该服务器，已标记为已删除；检查时间: {now.isoformat()}'
    updated = queryset.update(
        status=Server.STATUS_DELETED,
        provider_status='已删除',
        is_active=False,
        note=missing_note,
        updated_at=now,
    )
    order_ids = [item.order_id for item in missing_servers if item.order_id]
    instance_ids = [item.instance_id for item in missing_servers if item.instance_id]
    asset_scope = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, provider=provider)
    if region:
        asset_scope = asset_scope.filter(Q(region_code=region) | Q(region_code='') | Q(region_code__isnull=True))
    if account:
        label = cloud_account_label(account)
        asset_scope = asset_scope.filter(Q(cloud_account=account) | Q(account_label=label))
    if order_ids:
        CloudServerOrder.objects.filter(id__in=order_ids).exclude(status='deleted').update(
            status='deleted',
            provision_note=missing_note,
            updated_at=now,
        )
        asset_scope.filter(order_id__in=order_ids).update(
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
            note=missing_note,
            updated_at=now,
        )
    if instance_ids:
        asset_scope.filter(instance_id__in=instance_ids).update(
            status=CloudAsset.STATUS_DELETED,
            provider_status='已删除',
            is_active=False,
            note=missing_note,
            updated_at=now,
        )
    return legacy_updated + updated


@csrf_exempt
@dashboard_login_required
@require_POST
def sync_servers(request):
    payload = _read_payload(request)
    aliyun_region = (payload.get('region') or request.POST.get('region') or request.GET.get('region') or 'cn-hongkong').strip() or 'cn-hongkong'
    aws_region = (payload.get('aws_region') or request.POST.get('aws_region') or request.GET.get('aws_region') or '').strip()
    if aws_region.lower() == 'all':
        aws_region = ''
    errors = []
    synced = {'aliyun': False, 'aws': False}
    missing = {'aliyun': 0, 'aws': 0}
    aws_regions = []
    command_output = io.StringIO()
    aliyun_accounts = _active_sync_accounts(CloudAccountConfig.PROVIDER_ALIYUN)
    aws_accounts = _active_sync_accounts(CloudAccountConfig.PROVIDER_AWS)
    aws_command = None
    warnings = []
    for aliyun_account in aliyun_accounts:
        try:
            aliyun_command, _ = _call_command_capture('sync_aliyun_assets', region=aliyun_region, account_id=str(aliyun_account.id), stdout=command_output)
            synced['aliyun'] = True
            missing['aliyun'] += _apply_server_missing_state('aliyun_simple', aliyun_region, getattr(aliyun_command, 'synced_instance_ids', None) or [], aliyun_account)
        except Exception as exc:
            message = f'阿里云账号#{getattr(aliyun_account, "id", "-")}同步失败: {exc}'
            errors.append(message)
            logger.exception('DASHBOARD_SYNC_SERVERS_ALIYUN_FAILED account_id=%s region=%s', getattr(aliyun_account, 'id', None), aliyun_region)
    for aws_account in aws_accounts:
        try:
            if aws_region:
                aws_command, _ = _call_command_capture('sync_aws_assets', region=aws_region, account_id=str(aws_account.id), stdout=command_output)
                account_regions = [aws_region]
            else:
                aws_command, _ = _call_command_capture('sync_aws_assets', account_id=str(aws_account.id), stdout=command_output)
                account_regions = getattr(aws_command, 'synced_regions', None) or []
            aws_regions.extend(region for region in account_regions if region not in aws_regions)
            synced['aws'] = True
            warnings.extend(getattr(aws_command, 'sync_errors', []) or [])
            synced_map = getattr(aws_command, 'synced_instance_ids_by_region', None) or {}
            missing['aws'] += sum(
                _apply_server_missing_state('aws_lightsail', region, synced_map.get(region, []), aws_account)
                for region in account_regions
            )
        except Exception as exc:
            message = f'AWS账号#{getattr(aws_account, "id", "-")}同步失败: {exc}'
            errors.append(message)
            logger.exception('DASHBOARD_SYNC_SERVERS_AWS_FAILED account_id=%s region=%s', getattr(aws_account, 'id', None), aws_region or 'all')
    ok = not errors or any(synced.values())
    response_payload = {'ok': ok, 'synced': synced, 'missing': missing, 'aliyun_region': aliyun_region, 'aws_region': aws_region or 'all', 'aws_regions': aws_regions, 'errors': errors, 'warnings': warnings[:50], 'logs': _sync_log_tail(command_output), 'accounts': {'aliyun': [_sync_account_payload(account) for account in aliyun_accounts], 'aws': [_sync_account_payload(account) for account in aws_accounts]}}
    _record_dashboard_sync_log(
        action='sync_servers',
        target=f'aliyun:{aliyun_region};aws:{aws_region or "all"}',
        request_payload={'aliyun_region': aliyun_region, 'aws_region': aws_region or 'all'},
        response_payload={**response_payload, 'log_text': _sync_log_text(command_output)},
        is_success=ok,
        error_message='; '.join(errors[:10]),
    )
    return _ok(response_payload)


@csrf_exempt
@dashboard_login_required
@require_POST
def sync_cloud_assets(request):
    payload = _read_payload(request)
    aliyun_region = (payload.get('region') or request.POST.get('region') or request.GET.get('region') or 'cn-hongkong').strip() or 'cn-hongkong'
    aws_region = (payload.get('aws_region') or request.POST.get('aws_region') or request.GET.get('aws_region') or '').strip()
    if aws_region.lower() == 'all':
        aws_region = ''
    errors = []
    synced = {'aliyun': False, 'aws': False, 'reconcile': False}
    aws_regions = []
    command_output = io.StringIO()
    aliyun_accounts = _active_sync_accounts(CloudAccountConfig.PROVIDER_ALIYUN)
    aws_accounts = _active_sync_accounts(CloudAccountConfig.PROVIDER_AWS)
    aws_command = None
    warnings = []
    for aliyun_account in aliyun_accounts:
        try:
            _call_command_capture('sync_aliyun_assets', region=aliyun_region, account_id=str(aliyun_account.id), stdout=command_output)
            synced['aliyun'] = True
        except Exception as exc:
            message = f'阿里云账号#{getattr(aliyun_account, "id", "-")}代理同步失败: {exc}'
            errors.append(message)
            logger.exception('DASHBOARD_SYNC_ASSETS_ALIYUN_FAILED account_id=%s region=%s', getattr(aliyun_account, 'id', None), aliyun_region)
    for aws_account in aws_accounts:
        try:
            aws_command, _ = _call_command_capture('sync_aws_assets', region=aws_region, account_id=str(aws_account.id), stdout=command_output)
            account_regions = getattr(aws_command, 'synced_regions', None) or [aws_region or 'all']
            aws_regions.extend(region for region in account_regions if region not in aws_regions)
            warnings.extend(getattr(aws_command, 'sync_errors', []) or [])
            synced['aws'] = True
        except Exception as exc:
            message = f'AWS账号#{getattr(aws_account, "id", "-")}代理同步失败: {exc}'
            errors.append(message)
            logger.exception('DASHBOARD_SYNC_ASSETS_AWS_FAILED account_id=%s region=%s', getattr(aws_account, 'id', None), aws_region or 'all')
    try:
        _call_command_capture('reconcile_cloud_assets_from_servers', stdout=command_output)
        synced['reconcile'] = True
    except Exception as exc:
        message = f'代理列表补齐失败: {exc}'
        errors.append(message)
        logger.exception('DASHBOARD_SYNC_ASSETS_RECONCILE_FAILED')
    ok = not errors or any(synced.values())
    response_payload = {'ok': ok, 'synced': synced, 'aliyun_region': aliyun_region, 'aws_region': aws_region or 'all', 'aws_regions': aws_regions, 'errors': errors, 'warnings': warnings[:50], 'logs': _sync_log_tail(command_output), 'accounts': {'aliyun': [_sync_account_payload(account) for account in aliyun_accounts], 'aws': [_sync_account_payload(account) for account in aws_accounts]}}
    _record_dashboard_sync_log(
        action='sync_cloud_assets',
        target=f'aliyun:{aliyun_region};aws:{aws_region or "all"}',
        request_payload={'aliyun_region': aliyun_region, 'aws_region': aws_region or 'all'},
        response_payload={**response_payload, 'log_text': _sync_log_text(command_output)},
        is_success=ok,
        error_message='; '.join(errors[:10]),
    )
    return _ok(response_payload)


@csrf_exempt
@dashboard_login_required
@require_POST
def sync_cloud_plans(request):
    before_pricing_count = ServerPrice.objects.filter(is_active=True).count()
    before_regions = list(
        ServerPrice.objects.filter(is_active=True)
        .values('provider', 'region_code', 'region_name')
        .distinct()
        .order_by('provider', 'region_code')
    )
    try:
        async_to_sync(ensure_cloud_server_pricing)()
    except Exception as exc:
        return _error(f'同步价格配置失败: {exc}', status=500)
    active_pricing_queryset = ServerPrice.objects.filter(is_active=True)
    after_pricing_count = active_pricing_queryset.count()
    after_regions = list(
        active_pricing_queryset
        .values('provider', 'region_code', 'region_name')
        .distinct()
        .order_by('provider', 'region_code')
    )
    provider_region_summary = list(
        active_pricing_queryset
        .values('provider', 'region_code', 'region_name')
        .annotate(pricing_count=Count('id'))
        .order_by('provider', 'region_code')
    )
    return _ok({
        'synced': True,
        'refreshed_regions': len(after_regions),
        'summary': {
            'before_plan_count': CloudServerPlan.objects.filter(is_active=True).count(),
            'after_plan_count': CloudServerPlan.objects.filter(is_active=True).count(),
            'before_pricing_count': before_pricing_count,
            'after_pricing_count': after_pricing_count,
            'region_count': len(after_regions),
        },
        'regions': after_regions,
        'before_regions': before_regions,
        'provider_region_summary': provider_region_summary,
    })


def _resolve_cloud_plan_config_id(provider: str, region_code: str, provider_plan_id: str, config_id: str = '') -> str:
    explicit = str(config_id or '').strip()
    if explicit:
        return explicit
    bundle_code = str(provider_plan_id or '').strip()
    if bundle_code:
        matched_price = ServerPrice.objects.filter(
            provider=provider,
            region_code=region_code,
            bundle_code=bundle_code,
            is_active=True,
        ).only('config_id').first()
        if matched_price and str(matched_price.config_id or '').strip():
            return matched_price.config_id.strip()
    return _generate_cloud_plan_config_id()


def _cloud_plan_payload(plan):
    return {
        'id': plan.id,
        'provider': plan.provider,
        'provider_label': _provider_label(plan.provider),
        'region_code': plan.region_code,
        'region_name': plan.region_name,
        'region_label': _region_label(plan.region_code, plan.region_name),
        'config_id': plan.config_id,
        'provider_plan_id': plan.provider_plan_id,
        'plan_name': plan.plan_name,
        'plan_description': plan.plan_description,
        'display_plan_name': plan.display_plan_name,
        'display_cpu': plan.display_cpu,
        'display_memory': plan.display_memory,
        'display_storage': plan.display_storage,
        'display_bandwidth': plan.display_bandwidth,
        'display_description': plan.display_description,
        'cpu': plan.cpu,
        'memory': plan.memory,
        'storage': plan.storage,
        'bandwidth': plan.bandwidth,
        'cost_price': _decimal_to_str(getattr(plan, 'cost_price', 0)),
        'price': _decimal_to_str(plan.price),
        'currency': plan.currency,
        'sort_order': plan.sort_order,
        'is_active': plan.is_active,
        'updated_at': _iso(plan.updated_at),
    }


def _server_price_payload(price):
    return {
        'id': price.id,
        'provider': price.provider,
        'region_code': price.region_code,
        'region_name': price.region_name,
        'config_id': price.config_id,
        'bundle_code': price.bundle_code,
        'plan_name': price.server_name,
        'server_name': price.server_name,
        'plan_description': price.server_description or '',
        'server_description': price.server_description or '',
        'cpu': price.cpu,
        'memory': price.memory,
        'storage': price.storage,
        'bandwidth': price.bandwidth,
        'cost_price': _decimal_to_str(getattr(price, 'cost_price', 0)),
        'price': _decimal_to_str(price.price),
        'currency': price.currency,
        'sort_order': price.sort_order,
        'is_active': price.is_active,
        'updated_at': _iso(price.updated_at),
    }


@dashboard_login_required
@require_GET
def cloud_pricing_list(request):
    keyword = _get_keyword(request)
    queryset = ServerPrice.objects.filter(is_active=True).order_by('provider', 'region_code', '-sort_order', 'id')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['provider', 'region_code', 'region_name', 'bundle_code', 'server_name', 'server_description', 'cpu', 'memory', 'storage', 'bandwidth'],
    )
    provider = (request.GET.get('provider') or '').strip()
    region_code = (request.GET.get('region_code') or '').strip()
    if provider:
        queryset = queryset.filter(provider=provider)
    if region_code:
        queryset = queryset.filter(region_code=region_code)
    return _ok([_server_price_payload(item) for item in queryset])


@dashboard_login_required
@require_GET
def cloud_plans_list(request):
    keyword = _get_keyword(request)
    queryset = CloudServerPlan.objects.filter(is_active=True).order_by('provider', 'region_code', '-sort_order', 'id')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['provider', 'region_code', 'region_name', 'plan_name', 'plan_description', 'cpu', 'memory', 'storage', 'bandwidth'],
    )
    provider = (request.GET.get('provider') or '').strip()
    region_code = (request.GET.get('region_code') or '').strip()
    if provider:
        queryset = queryset.filter(provider=provider)
    if region_code:
        queryset = queryset.filter(region_code=region_code)
    return _ok([_cloud_plan_payload(item) for item in queryset])


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
        'asset_id': item.asset_id,
        'server_id': item.server_id,
        'asset_name': item.asset_name,
        'instance_id': item.instance_id,
        'provider_resource_id': item.provider_resource_id,
        'public_ip': item.public_ip,
        'previous_public_ip': item.previous_public_ip,
        'note': item.note,
        'created_at': _iso(item.created_at),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
    }


@dashboard_login_required
@require_GET
def cloud_assets_sync_status(request):
    latest_log = ExternalSyncLog.objects.filter(
        source__in=[ExternalSyncLog.SOURCE_AWS, ExternalSyncLog.SOURCE_ALIYUN],
        is_success=True,
    ).order_by('-created_at', '-id').first()
    latest_asset = CloudAsset.objects.filter(
        source__in=[CloudAsset.SOURCE_AWS_SYNC, CloudAsset.SOURCE_ALIYUN],
    ).order_by('-updated_at', '-id').first()
    last_synced_at = None
    if latest_log and latest_asset:
        last_synced_at = max(latest_log.created_at, latest_asset.updated_at)
    elif latest_log:
        last_synced_at = latest_log.created_at
    elif latest_asset:
        last_synced_at = latest_asset.updated_at

    since = last_synced_at
    active_account_labels = _cloud_account_labels_queryset(True)
    active_account_filter = (
        Q(cloud_account__is_active=True)
        | Q(cloud_account__isnull=True, account_label__in=active_account_labels)
    )
    aws_existing_count = CloudAsset.objects.filter(
        active_account_filter,
        kind=CloudAsset.KIND_SERVER,
        provider='aws_lightsail',
    ).exclude(status=CloudAsset.STATUS_DELETED).count()
    aliyun_existing_count = CloudAsset.objects.filter(
        active_account_filter,
        kind=CloudAsset.KIND_SERVER,
        provider='aliyun_simple',
    ).exclude(status=CloudAsset.STATUS_DELETED).count()
    unattached_ip_count = CloudAsset.objects.filter(
        active_account_filter,
        kind=CloudAsset.KIND_SERVER,
    ).filter(
        Q(provider_status__icontains='未附加') | Q(note__icontains='未附加IP') | Q(note__icontains='未附加固定IP')
    ).exclude(status__in=[
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_TERMINATED,
        CloudAsset.STATUS_TERMINATING,
    ]).count()
    return _ok({
        'auto_sync_every_seconds': get_cloud_asset_sync_interval_seconds(),
        'last_synced_at': _iso(last_synced_at),
        'aws_existing_count': aws_existing_count,
        'aliyun_existing_count': aliyun_existing_count,
        'unattached_ip_count': unattached_ip_count,
    })


@dashboard_login_required
@require_GET
def cloud_ip_logs_list(request):
    keyword = _get_keyword(request)
    log_type = (request.GET.get('log_type') or 'ip').strip()
    queryset = CloudIpLog.objects.select_related('user', 'order', 'asset', 'server').order_by('-created_at', '-id')
    queryset = _apply_keyword_filter(
        queryset,
        keyword,
        ['order_no', 'asset_name', 'instance_id', 'provider_resource_id', 'public_ip', 'previous_public_ip', 'note', 'user__tg_user_id', 'user__username'],
    )
    if log_type == 'server':
        queryset = queryset.filter(Q(note__icontains='服务器') | Q(note__icontains='续费') | Q(note__icontains='删除') | Q(event_type__in=['created', 'deleted']))
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


__all__ = [
    'cloud_assets_list',
    'cloud_assets_sync_status',
    'cloud_ip_logs_list',
    'cloud_order_detail',
    'cloud_orders_list',
    'delete_cloud_order',
    'tasks_overview',
    'cloud_plans_list',
    'cloud_pricing_list',
    'create_cloud_plan',
    'delete_cloud_plan',
    'delete_server',
    'monitors_list',
    'servers_list',
    'servers_statistics',
    'sync_cloud_assets',
    'sync_cloud_plans',
    'sync_servers',
    'update_cloud_asset',
    'update_cloud_order_status',
    'update_cloud_plan',
]
