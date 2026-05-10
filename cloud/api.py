"""cloud 域后台 API。"""

import inspect
import io
import json
import logging
import re
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import timezone as dt_timezone
from decimal import Decimal, InvalidOperation

import httpx
from urllib.parse import urlparse

from asgiref.sync import async_to_sync
from django.core.management import get_commands, load_command_class
from django.db import IntegrityError, close_old_connections, transaction
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
from bot.models import TelegramGroupFilter, TelegramLoginAccount, TelegramUser
from cloud.lifecycle import _auto_renew_notice_batch_payload, _delete_instance, _get_due_orders, _get_notice_text_override, _lifecycle_notice_batch_payload, _mark_replaced_order_deleted, _notice_payload_for_order, _notice_override_key, _record_auto_renew_patrol_log, _renew_notice_batch_payload, _run_auto_renew, _set_notice_text_override
from cloud.services import AWS_REGION_NAMES, RenewalPriceMissingError, _cloud_order_lifecycle_fields, _renewal_price, _update_order_primary_records, create_cloud_server_rebuild_order, ensure_cloud_server_pricing, ensure_manual_expiry_operation_order, ensure_manual_owner_operation_order, ensure_manual_price_operation_order, record_cloud_ip_log, refresh_custom_plan_cache, replace_cloud_asset_order_by_admin, set_cloud_server_auto_renew_admin
from cloud.models import AddressMonitor, CloudAsset, CloudAutoRenewPatrolLog, CloudIpLog, CloudServerOrder, CloudServerPlan, CloudUserNoticeLog, Server, ServerPrice
from cloud.note_utils import append_note, prepend_note
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


def _call_command_capture_threaded(command_name: str, **options):
    output = io.StringIO()
    close_old_connections()
    try:
        command, log_text = _call_command_capture(command_name, stdout=output, **options)
        return command, log_text
    finally:
        close_old_connections()


def _is_unattached_ip_asset(asset: CloudAsset) -> bool:
    return '未附加' in str(asset.provider_status or '')


def _unattached_ip_delete_due_at(*, now=None):
    from core.runtime_config import get_runtime_config
    try:
        delete_days = max(int(str(get_runtime_config('cloud_unattached_ip_delete_after_days', '15') or '15').strip()), 0)
    except (TypeError, ValueError):
        delete_days = 15
    now = now or timezone.now()
    return now + timezone.timedelta(days=delete_days)


def _ensure_unattached_ip_expiry(asset: CloudAsset, *, now=None) -> bool:
    """未附加固定 IP 必须有计划删除时间；缺失时按系统配置补齐。"""
    if not _is_unattached_ip_asset(asset) or asset.actual_expires_at:
        return False
    asset.actual_expires_at = _unattached_ip_delete_due_at(now=now)
    addition = f'自动补齐未附加IP删除计划: {asset.actual_expires_at.isoformat()}'
    asset.note = append_note(asset.note, addition)
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


def _username_matches(saved_value, lookup_value) -> bool:
    lookup_names = {item.lower() for item in TelegramUser.normalize_usernames(lookup_value)}
    if not lookup_names:
        return False
    saved_names = {item.lower() for item in TelegramUser.normalize_usernames(saved_value)}
    return bool(saved_names & lookup_names)


def _resolve_telegram_user(value):
    terms = _telegram_user_lookup_terms(value)
    if not terms:
        return None
    queryset = TelegramUser.objects.all()
    for raw in terms:
        if raw.isdigit():
            found = queryset.filter(Q(id=int(raw)) | Q(tg_user_id=int(raw))).first()
            if found:
                return found
            continue
        candidates = list(queryset.filter(username__icontains=raw).order_by('-updated_at', '-id')[:20])
        found = next((item for item in candidates if _username_matches(item.username, raw)), None)
        if found:
            return found
    for raw in terms:
        account_query = Q(tg_user_id=int(raw)) if raw.isdigit() else Q(username__icontains=raw)
        accounts = TelegramLoginAccount.objects.filter(account_query).exclude(tg_user_id__isnull=True).order_by('-updated_at', '-id')[:20]
        account = next((item for item in accounts if raw.isdigit() or _username_matches(item.username, raw)), None)
        if not account or not account.tg_user_id:
            continue
        user, _ = TelegramUser.objects.get_or_create(
            tg_user_id=account.tg_user_id,
            defaults={
                'username': TelegramUser.serialize_usernames(account.username),
                'first_name': account.label or '',
            },
        )
        _sync_telegram_username(user, account.username)
        return user
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


def _preserve_link_status_with_countdown(status_label, countdown_label):
    status_label = str(status_label or '').strip()
    countdown_label = str(countdown_label or '').strip()
    if status_label == '重装迁移中':
        return ''
    if not status_label:
        return ''
    if countdown_label and countdown_label != '-' and '剩余' not in status_label and '已过期' not in status_label:
        return f'{status_label}（{countdown_label}）'
    return status_label


def _infer_asset_order(asset):
    order = getattr(asset, 'order', None)
    if order:
        return order
    names = {str(getattr(asset, 'asset_name', '') or '').strip(), str(getattr(asset, 'instance_id', '') or '').strip()}
    ips = {str(getattr(asset, 'public_ip', '') or '').strip(), str(getattr(asset, 'previous_public_ip', '') or '').strip()}
    names.discard('')
    ips.discard('')
    lookup = Q()
    if names:
        lookup |= Q(server_name__in=names) | Q(instance_id__in=names)
    if ips:
        lookup |= Q(public_ip__in=ips) | Q(previous_public_ip__in=ips)
    if not lookup:
        return None
    return CloudServerOrder.objects.select_related('user', 'plan').filter(lookup).order_by('-created_at', '-id').first()


def _asset_payload(asset):
    order = _infer_asset_order(asset)
    user = asset.user or getattr(order, 'user', None)
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
    countdown_label = _countdown_label(expires_at)
    preserve_link_status = _preserve_link_status_with_countdown(
        _preserve_link_status_label(asset.note, getattr(order, 'provision_note', None)),
        countdown_label,
    )
    account_label = asset.account_label or cloud_account_label(getattr(asset, 'cloud_account', None)) or getattr(order, 'account_label', '')
    cloud_account_id = asset.cloud_account_id or getattr(order, 'cloud_account_id', None)
    display_status = asset.status
    display_status_label = '旧机保留中' if asset.status == CloudAsset.STATUS_DELETING and '旧机保留期' in str(asset.provider_status or '') else _status_label(asset.status, CloudAsset.STATUS_CHOICES)
    provider_status_label = (
        '已删除' if asset.status == CloudAsset.STATUS_DELETED else (
            '已终止' if asset.status == CloudAsset.STATUS_TERMINATED else _provider_status_label(asset.provider_status)
        )
    )
    if asset.status == CloudAsset.STATUS_UNKNOWN and '未附加' in str(asset.provider_status or ''):
        display_status = 'unattached'
        display_status_label = '未附加固定IP'
        provider_status_label = '未附加固定IP'
    elif asset.status == CloudAsset.STATUS_UNKNOWN and '固定IP仍存在但未附加' in str(asset.provider_status or ''):
        display_status = 'unattached'
        display_status_label = '未附加固定IP'
        provider_status_label = '固定IP仍存在但未附加'
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
        'public_ip': asset.public_ip or asset.previous_public_ip or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None),
        'previous_public_ip': asset.previous_public_ip or getattr(order, 'previous_public_ip', None),
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
        'status_countdown': countdown_label,
        'preserve_link_status': preserve_link_status,
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
        'status': display_status,
        'status_label': display_status_label,
        'provider_status': provider_status_label,
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
        order = _infer_asset_order(asset)
        ip_values = {str(asset.public_ip or '').strip(), str(asset.previous_public_ip or '').strip()}
        ip_values.discard('')
        log_lookup = Q(asset=asset)
        name_lookup = Q()
        if asset.asset_name:
            name_lookup |= Q(asset_name=asset.asset_name)
        if asset.instance_id:
            name_lookup |= Q(instance_id=asset.instance_id)
        if name_lookup and ip_values:
            log_lookup |= name_lookup & (Q(public_ip__in=ip_values) | Q(previous_public_ip__in=ip_values))
        logs = list(CloudIpLog.objects.filter(log_lookup).distinct().order_by('-created_at', '-id')[:100])
        lifecycle_order_nos = set()
        for log_item in logs:
            if log_item.order_no:
                lifecycle_order_nos.add(log_item.order_no)
            for matched_order_no in re.findall(r'订单号：([^；\n]+)|旧机订单\s+([^；\n]+)|新实例订单\s+([^；\n]+)', log_item.note or ''):
                for value in matched_order_no:
                    value = str(value or '').strip().rstrip('。')
                    if value and value != '-':
                        lifecycle_order_nos.add(value)
        lifecycle_order_links = {
            item.order_no: f'/admin/cloud-orders/{item.id}'
            for item in CloudServerOrder.objects.filter(order_no__in=lifecycle_order_nos).only('id', 'order_no')
        }
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
                    'order_no': item.order_no,
                    'asset_name': item.asset_name,
                    'public_ip': item.public_ip,
                    'previous_public_ip': item.previous_public_ip,
                    'note': item.note,
                    'created_at': _iso(item.created_at),
                    'order_detail_path': lifecycle_order_links.get(item.order_no, ''),
                    'order_link_path': lifecycle_order_links.get(item.order_no, ''),
                }
                for item in logs
            ],
            'lifecycle_order_links': lifecycle_order_links,
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
                    group = TelegramGroupFilter.objects.filter(group_query, collapsed=False).order_by('-updated_at', '-id').first()
                    if not group:
                        return _error('未找到匹配的 Telegram 群组，或该群组已在绑定页隐藏', status=404)
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
                    if getattr(asset.order, 'auto_renew_enabled', False):
                        pending_order_updates['auto_renew_failure_notice_sent_at'] = None
                        if getattr(asset.order, 'status', '') == 'renew_pending' and not getattr(asset.order, 'paid_at', None):
                            pending_order_updates['pay_amount'] = price

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
            old_provider_status = str(asset.provider_status or '')
            new_public_ip = payload.get('public_ip') or None if 'public_ip' in payload else asset.public_ip
            if 'public_ip' in payload:
                if old_public_ip and old_public_ip != new_public_ip:
                    asset.previous_public_ip = old_public_ip

            for field in ('asset_name', 'public_ip', 'provider_resource_id', 'instance_id', 'mtproxy_link', 'mtproxy_secret', 'mtproxy_host', 'note'):
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
                if 'instance_id' in payload:
                    server.instance_id = payload.get('instance_id') or None
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
            rebound_to_instance = bool(
                old_provider_status and '未附加' in old_provider_status and str(asset.instance_id or '').strip()
            )
            refresh_unattached_delete_due = bool(is_unattached_ip and payload and 'actual_expires_at' not in payload and not rebound_to_instance)
            if rebound_to_instance:
                rebound_now = timezone.now()
                rebound_note = f'未附加IP已重新绑定到实例，已清空临时到期时间：{rebound_now.isoformat()}；等待人工添加真实到期时间。'
                asset.actual_expires_at = None
                asset.provider_status = '已重新绑定实例-待人工添加时间'
                asset.is_active = True
                asset.note = append_note(asset.note, rebound_note)
                if asset.status == CloudAsset.STATUS_UNKNOWN:
                    asset.status = CloudAsset.STATUS_RUNNING

            if refresh_unattached_delete_due:
                refreshed_due_at = _unattached_ip_delete_due_at()
                asset.actual_expires_at = refreshed_due_at
                if server:
                    server.expires_at = refreshed_due_at
                if linked_order_id:
                    pending_order_updates['ip_recycle_at'] = refreshed_due_at
                    pending_order_updates['recycle_notice_sent_at'] = None

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
                if 'note' not in payload:
                    server.note = append_note(server.note, asset.note)
                server.sort_order = asset.sort_order
                server.expires_at = asset.actual_expires_at
                server.is_active = asset.is_active
                if rebound_to_instance:
                    server.provider_status = asset.provider_status
                    if server.status == Server.STATUS_UNKNOWN:
                        server.status = Server.STATUS_RUNNING
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


def _dashboard_sort_direction(request):
    direction = (request.GET.get('sort_order') or request.GET.get('sort_direction') or '').strip().lower()
    return 'desc' if direction in {'desc', 'descending', '降序'} else 'asc'


def _dashboard_expiry_ordering(field_name: str, direction: str):
    field = F(field_name)
    if direction == 'desc':
        return [field.desc(nulls_last=True), '-updated_at', '-id']
    return [field.asc(nulls_last=True), '-updated_at', '-id']


def _asset_display_ip(asset):
    return str(asset.public_ip or asset.previous_public_ip or '').strip()


def _dedupe_cloud_asset_rows(assets):
    best = {}
    for asset in assets:
        ip = _asset_display_ip(asset)
        key = ip or f'id:{asset.id}'
        is_unattached = '未附加' in str(asset.provider_status or '') or '固定IP仍存在但未附加' in str(asset.provider_status or '')
        is_deleted = asset.status in {CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED}
        score = (
            3 if is_unattached else 0,
            2 if asset.status == CloudAsset.STATUS_DELETING else 0,
            1 if not is_deleted else 0,
            1 if asset.order_id else 0,
            1 if asset.user_id else 0,
            asset.updated_at.timestamp() if asset.updated_at else 0,
            asset.id,
        )
        current = best.get(key)
        if not current or score > current[0]:
            best[key] = (score, asset)
    return [item[1] for item in best.values()]


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
        unattached_ip_values = list(
            CloudAsset.objects.filter(
                kind=CloudAsset.KIND_SERVER,
                provider_status__contains='未附加固定IP',
                public_ip__isnull=False,
            ).exclude(public_ip='').values_list('public_ip', flat=True)[:1000]
        )
        queryset = CloudAsset.objects.select_related('user', 'order', 'cloud_account', 'telegram_group').filter(kind=CloudAsset.KIND_SERVER).exclude(
            Q(cloud_account__is_active=False)
            | Q(account_label__in=inactive_account_labels),
        ).exclude(
            Q(status__in=[CloudAsset.STATUS_DELETED, CloudAsset.STATUS_TERMINATED])
            & (Q(public_ip__in=unattached_ip_values) | Q(previous_public_ip__in=unattached_ip_values))
        ).filter(
            Q(cloud_account__is_active=True)
            | Q(account_label__in=active_account_labels)
            | Q(account_label__isnull=True)
            | Q(account_label='')
        )
        sort_by = (request.GET.get('sort_by') or '').strip().lower()
        sort_direction = _dashboard_sort_direction(request)
        ordering = ['-sort_order', F('actual_expires_at').asc(nulls_last=True), '-updated_at', '-id']
        if sort_by in {'actual_expires_at', 'expires_at', 'days_left', 'remaining_days'}:
            ordering = _dashboard_expiry_ordering('actual_expires_at', sort_direction)
        queryset = _apply_keyword_filter(
            queryset,
            keyword,
            [
                'asset_name', 'public_ip', 'mtproxy_link', 'account_label', 'cloud_account__external_account_id', 'cloud_account__name', 'user__tg_user_id',
                'user__username', 'order__order_no',
            ],
        ).distinct().order_by(*ordering)
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
            deduped_assets = _dedupe_cloud_asset_rows(list(queryset))
            total = len(deduped_assets)
            offset = (page - 1) * page_size
            items = [_asset_payload(asset) for asset in deduped_assets[offset:offset + page_size]]
            return _ok({'items': items, 'total': total, 'page': page, 'page_size': page_size})
        items = [_asset_payload(asset) for asset in _dedupe_cloud_asset_rows(list(queryset))]
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


def _auto_renew_failure_was_price_missing(reason: str | None) -> bool:
    return '缺少续费价格' in str(reason or '') or '缺少价格' in str(reason or '')


def _order_has_renewal_price(order) -> bool:
    try:
        _renewal_price(order, getattr(order, 'user', None))
        return True
    except RenewalPriceMissingError:
        return False


def _auto_renew_task_status(order, now, *, latest_failure_reason: str | None = None):
    if not getattr(order, 'auto_renew_enabled', False):
        return None
    last_renewed_at = getattr(order, 'last_renewed_at', None)
    if last_renewed_at and last_renewed_at >= now - timezone.timedelta(days=1):
        return 'auto_renew_success', '自动续费成功'
    expires_at = getattr(order, 'service_expires_at', None)
    suspend_at = getattr(order, 'suspend_at', None)
    in_renew_window = bool(expires_at and expires_at <= now + timezone.timedelta(days=1) and expires_at > now)
    in_shutdown_fallback = bool(expires_at and expires_at <= now and suspend_at and suspend_at > now)
    in_retry_window = bool(in_renew_window or in_shutdown_fallback or expires_at and expires_at <= now)
    price_missing_fixed = bool(
        order.status == 'renew_pending'
        and in_retry_window
        and _auto_renew_failure_was_price_missing(latest_failure_reason)
        and _order_has_renewal_price(order)
    )
    if price_missing_fixed:
        return 'auto_renew_pending', '自动续费待执行'
    if order.status == 'renew_pending' and in_retry_window:
        return 'auto_renew_failed', '自动续费失败/待补余额'
    if order.status in {'completed', 'expiring', 'renew_pending'} and (in_renew_window or in_shutdown_fallback):
        return 'auto_renew_pending', '自动续费待执行'
    return None


def _auto_renew_pinned_task(now):
    orders = list(CloudServerOrder.objects.filter(auto_renew_enabled=True).order_by('-updated_at')[:500])
    order_ids = [order.id for order in orders]
    latest_failure_reasons = {}
    for log in CloudAutoRenewPatrolLog.objects.filter(order_id__in=order_ids, is_success=False).order_by('-executed_at', '-id'):
        if log.order_id not in latest_failure_reasons:
            latest_failure_reasons[log.order_id] = log.failure_reason
    statuses = [_auto_renew_task_status(order, now, latest_failure_reason=latest_failure_reasons.get(order.id)) for order in orders]
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
        **_notice_status_payload(sent_at=sent_at, latest_log=latest_log, queue_status=queue_status),
        **_notice_channel_payload(user, latest_log),
        'notice_text_preview': _notice_task_text_preview(order, notice_type, notice),
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


def _auto_renew_order_has_active_notice(order) -> bool:
    if not order:
        return False
    if getattr(order, 'status', None) not in {'completed', 'expiring', 'renew_pending'}:
        return False
    notice = _notice_payload_for_order(order)
    ip = str(notice.get('ip') if notice else getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '').strip()
    if not ip:
        return False
    linked_assets = list(CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, order=order).only('id', 'status', 'is_active')[:20])
    if not linked_assets:
        return True
    excluded_statuses = {
        CloudAsset.STATUS_DELETED,
        CloudAsset.STATUS_DELETING,
        CloudAsset.STATUS_TERMINATED,
        CloudAsset.STATUS_TERMINATING,
    }
    return any(asset.is_active and asset.status not in excluded_statuses for asset in linked_assets)


def _auto_renew_future_plan_items(now, next_run_at, due_orders: list):
    plan_items = []
    seen = set()
    for order in due_orders:
        if not _auto_renew_order_has_active_notice(order):
            continue
        seen.add(order.id)
        plan_items.append(_auto_renew_due_item_payload(order, queue_status='due_now', queue_status_label='本轮待执行', next_run_at=next_run_at))
    future_qs = CloudServerOrder.objects.select_related('user').filter(auto_renew_enabled=True, status__in=['completed', 'expiring', 'renew_pending']).exclude(id__in=list(seen)).order_by('service_expires_at', 'id')[:50]
    for order in future_qs:
        if not _auto_renew_order_has_active_notice(order):
            continue
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


def _collect_auto_renew_due_orders(now):
    due = async_to_sync(_get_due_orders)()
    due_orders = [order for order in list(due.get('auto_renew') or []) if _auto_renew_order_has_active_notice(order)]
    due_ids = {order.id for order in due_orders}
    history_qs = CloudAutoRenewPatrolLog.objects.select_related('order', 'user').order_by('-executed_at', '-id')

    retry_orders = []
    recent_logs = history_qs.filter(executed_at__gte=now - timezone.timedelta(days=7))
    seen_history_order_ids = set()
    for log in recent_logs:
        order = getattr(log, 'order', None)
        if not order:
            continue
        if order.id in seen_history_order_ids:
            continue
        seen_history_order_ids.add(order.id)
        if log.is_success:
            continue
        if not getattr(order, 'auto_renew_enabled', False):
            continue
        if order.id in due_ids:
            continue
        if not _auto_renew_order_has_active_notice(order):
            continue
        due_ids.add(order.id)
        retry_orders.append((order, 'retry_failed', '失败待重试', log.failure_reason))

    fallback_orders = []
    fallback_qs = CloudServerOrder.objects.select_related('user').filter(
        auto_renew_enabled=True,
        status__in=['completed', 'expiring', 'renew_pending'],
        service_expires_at__isnull=False,
        service_expires_at__lte=now,
    ).exclude(id__in=list(due_ids)).order_by('service_expires_at', 'id')[:50]
    for order in fallback_qs:
        if not _auto_renew_order_has_active_notice(order):
            continue
        due_ids.add(order.id)
        fallback_orders.append((order, 'fallback_retry', '过期后兜底重试', None))

    return {
        'due_orders': due_orders,
        'retry_orders': retry_orders,
        'fallback_orders': fallback_orders,
        'history_qs': history_qs,
        'due_ids': due_ids,
    }


async def _await_result(awaitable):
    return await awaitable


def _run_auto_renew_sync(order_id: int):
    result = _run_auto_renew(order_id)
    if inspect.isawaitable(result):
        return async_to_sync(_await_result)(result)
    return result


def _manual_run_auto_renew_queue(orders: list[tuple[CloudServerOrder, str]], *, batch_id: str | None = None):
    batch_id = batch_id or uuid.uuid4().hex[:16]
    results = []
    for order, queue_status in orders:
        notice = _notice_payload_for_order(order) or {}
        renewed, err, balance_change = _run_auto_renew_sync(order.id)
        renewed_order_id = getattr(renewed, 'id', None) or order.id
        ip = notice.get('ip') or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '未分配'
        ok = not bool(err)
        async_to_sync(_record_auto_renew_patrol_log)(
            order.id,
            batch_id=batch_id,
            ip=ip,
            ok=ok,
            error=err,
            balance_change=balance_change,
            renewed_order_id=renewed_order_id,
        )
        results.append({
            'order_id': order.id,
            'renewed_order_id': renewed_order_id,
            'order_no': order.order_no,
            'ip': ip,
            'queue_status': queue_status,
            'ok': ok,
            'error': err,
        })
    return {
        'batch_id': batch_id,
        'items': results,
        'total': len(results),
        'success_count': sum(1 for item in results if item['ok']),
        'failure_count': sum(1 for item in results if not item['ok']),
    }


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


_NOTICE_TASK_TYPES = {
    'renew_notice': {'label': '到期提醒', 'field': 'renew_notice_sent_at', 'event': 'renew_notice_batch'},
    'auto_renew_notice': {'label': '自动续费预提醒', 'field': 'auto_renew_notice_sent_at', 'event': 'auto_renew_notice'},
    'delete_notice': {'label': '删机提醒', 'field': 'delete_notice_sent_at', 'event': 'delete_notice'},
    'recycle_notice': {'label': 'IP回收提醒', 'field': 'recycle_notice_sent_at', 'event': 'recycle_notice'},
}

_NOTICE_HISTORY_LABELS = {
    **{key: item['label'] for key, item in _NOTICE_TASK_TYPES.items()},
    'renew_notice_batch': '到期提醒',
}


def _notice_task_time(order, notice_type: str, notice: dict | None = None):
    notice = notice or _notice_payload_for_order(order) or {}
    if notice_type == 'renew_notice':
        expires_at = notice.get('expires_at') or getattr(order, 'service_expires_at', None)
        try:
            notice_days = max(1, int(get_runtime_config('cloud_renew_notice_days', 5) or 5))
        except Exception:
            notice_days = 5
        return expires_at - timezone.timedelta(days=notice_days) if expires_at else None
    if notice_type == 'auto_renew_notice':
        expires_at = notice.get('expires_at') or getattr(order, 'service_expires_at', None)
        return expires_at - timezone.timedelta(days=2) if expires_at else None
    if notice_type == 'delete_notice':
        delete_at = notice.get('delete_at') or getattr(order, 'delete_at', None)
        return delete_at - timezone.timedelta(days=1) if delete_at else None
    if notice_type == 'recycle_notice':
        recycle_at = notice.get('ip_recycle_at') or getattr(order, 'ip_recycle_at', None)
        return recycle_at - timezone.timedelta(days=1) if recycle_at else None
    return None


def _notice_task_text_preview(order, notice_type: str, notice: dict | None = None) -> str:
    notice = notice or _notice_payload_for_order(order) or {}
    ip = notice.get('ip') or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '未分配'
    expires_at = notice.get('expires_at') or getattr(order, 'service_expires_at', None)
    if notice_type == 'renew_notice':
        return f'到期提醒：IP {ip} 将于 {_iso(expires_at) or "-"} 到期，请及时续费或确认自动续费状态。'
    if notice_type == 'auto_renew_notice':
        auto_renew_at = expires_at - timezone.timedelta(days=1) if expires_at else None
        return f'自动续费预提醒：IP {ip} 预计于 {_iso(auto_renew_at) or "-"} 自动续费。'
    if notice_type == 'delete_notice':
        return f'删机提醒：IP {ip} 计划于 {_iso(notice.get("delete_at") or getattr(order, "delete_at", None)) or "-"} 删除。'
    if notice_type == 'recycle_notice':
        return f'IP回收提醒：IP {ip} 计划于 {_iso(notice.get("ip_recycle_at") or getattr(order, "ip_recycle_at", None)) or "-"} 回收。'
    return f'{_NOTICE_TASK_TYPES.get(notice_type, {}).get("label", notice_type)}：IP {ip}'


def _notice_attempt_label(attempt: dict) -> str:
    channel = attempt.get('channel') or ''
    if channel == 'bot':
        name = attempt.get('channel_label') or 'Bot'
    elif channel == 'account':
        name = attempt.get('account_label') or (f"账号{attempt.get('account_id')}" if attempt.get('account_id') else '个人号')
    else:
        name = attempt.get('channel_label') or channel or '未知渠道'
    status = '成功' if attempt.get('ok') else '失败'
    error = str(attempt.get('error') or '').strip()
    return f'{name}{status}' + (f'：{error}' if error and not attempt.get('ok') else '')


def _notice_attempts_label(log) -> str:
    attempts = ((getattr(log, 'extra', None) or {}).get('send_attempts') or []) if log else []
    return '；'.join(_notice_attempt_label(attempt) for attempt in attempts)


def _notice_attempt_payload(attempt: dict, *, pending: bool = False) -> dict:
    channel = attempt.get('channel') or ''
    if channel == 'bot':
        name = attempt.get('channel_label') or 'Bot'
    elif channel == 'account':
        name = attempt.get('account_label') or (f"账号{attempt.get('account_id')}" if attempt.get('account_id') else '个人号')
    else:
        name = attempt.get('channel_label') or channel or '未知渠道'
    ok = bool(attempt.get('ok'))
    status = 'pending' if pending else ('success' if ok else 'failed')
    return {
        'channel': channel,
        'label': name,
        'status': status,
        'status_label': '待轮询' if pending else ('成功' if ok else '失败'),
        'error': str(attempt.get('error') or '').strip(),
        'account_id': attempt.get('account_id'),
    }


def _planned_notice_attempts(user) -> list[dict]:
    attempts = [{'channel': 'bot', 'channel_label': 'Bot', 'ok': False, 'error': ''}]
    accounts = TelegramLoginAccount.objects.filter(status='logged_in', notify_enabled=True).exclude(session_string__isnull=True).exclude(session_string='').order_by('-updated_at', '-id')[:10]
    for account in accounts:
        attempts.append({'channel': 'account', 'account_id': account.id, 'account_label': account.label or f'账号{account.id}', 'ok': False, 'error': ''})
    return [_notice_attempt_payload(attempt, pending=True) for attempt in attempts]


def _notice_channel_attempts_payload(user, latest_log=None) -> list[dict]:
    attempts = ((getattr(latest_log, 'extra', None) or {}).get('send_attempts') or []) if latest_log else []
    if attempts:
        return [_notice_attempt_payload(attempt) for attempt in attempts]
    if getattr(user, 'tg_user_id', None) if user else None:
        return _planned_notice_attempts(user)
    return []


def _notice_channel_payload(user, latest_log=None) -> dict:
    attempts = ((getattr(latest_log, 'extra', None) or {}).get('send_attempts') or []) if latest_log else []
    attempt_items = _notice_channel_attempts_payload(user, latest_log)
    success = next((attempt for attempt in attempts if attempt.get('ok')), None)
    if success:
        if success.get('channel') == 'bot':
            return {'notice_channel': 'telegram_bot', 'notice_channel_label': '机器人通知成功', 'notice_channel_attempts': attempt_items}
        account_label = success.get('account_label') or (f"账号{success.get('account_id')}" if success.get('account_id') else '个人号')
        return {'notice_channel': 'telegram_account', 'notice_channel_label': f'{account_label} 通知成功', 'notice_channel_attempts': attempt_items}
    if attempts:
        return {'notice_channel': 'telegram_fallback', 'notice_channel_label': '机器人优先，失败后账号轮询', 'notice_channel_attempts': attempt_items}
    tg_user_id = getattr(user, 'tg_user_id', None) if user else None
    if tg_user_id:
        account_count = max(len(attempt_items) - 1, 0)
        label = f'Bot优先，失败后轮询{account_count}个账号' if account_count else 'Bot优先，暂无账号兜底'
        return {'notice_channel': 'telegram_fallback', 'notice_channel_label': label, 'notice_channel_attempts': attempt_items}
    return {'notice_channel': 'unbound', 'notice_channel_label': '未绑定通知渠道', 'notice_channel_attempts': []}


def _notice_status_payload(*, sent_at=None, latest_log=None, queue_status='scheduled_future') -> dict:
    if sent_at:
        return {'notice_status': 'sent', 'notice_status_label': '已通知', 'retry_label': '-'}
    if latest_log and not latest_log.delivered:
        return {'notice_status': 'failed_retry', 'notice_status_label': '通知失败，待重试', 'retry_label': (_notice_attempts_label(latest_log) + '；' if _notice_attempts_label(latest_log) else '') + '未标记已通知，下一轮生命周期巡检会继续重试'}
    if queue_status in {'due_now', 'fallback_notice'}:
        return {'notice_status': 'pending', 'notice_status_label': '待本轮通知', 'retry_label': '发送失败不会写入已通知时间，会在后续巡检重试'}
    if queue_status == 'within_window':
        return {'notice_status': 'scheduled_soon', 'notice_status_label': '3天内待通知', 'retry_label': '到通知时间后自动发送，失败则重试'}
    return {'notice_status': 'scheduled', 'notice_status_label': '未来计划', 'retry_label': '未到通知时间'}


def _notice_task_item_payload(order, notice_type: str, *, queue_status='scheduled_future', queue_status_label='未来计划', next_run_at=None, latest_log=None):
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
    sent_at = getattr(order, _NOTICE_TASK_TYPES.get(notice_type, {}).get('field', ''), None)
    return {
        'id': f'{notice_type}-{order.id}',
        'order_id': order.id,
        'order_no': order.order_no,
        'notice_type': notice_type,
        'notice_type_label': _NOTICE_TASK_TYPES.get(notice_type, {}).get('label', notice_type),
        'ip': notice.get('ip') or getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '未分配',
        'provider': order.provider,
        'provider_label': _provider_label(order.provider),
        'status': order.status,
        'status_label': _status_label(order.status, CloudServerOrder.STATUS_CHOICES),
        'queue_status': queue_status,
        'queue_status_label': queue_status_label,
        **_notice_status_payload(sent_at=sent_at, latest_log=latest_log, queue_status=queue_status),
        **_notice_channel_payload(user, latest_log),
        'notice_text_preview': _notice_task_text_preview(order, notice_type, notice),
        'user_id': user.id if user else None,
        'tg_user_id': user.tg_user_id if user else None,
        'user_display_name': user_payload['display_name'] if user_payload else '未绑定用户',
        'username_label': user_payload['username_label'] if user_payload else '-',
        'notice_at': _iso(_notice_task_time(order, notice_type, notice)),
        'service_expires_at': _iso(expires_at),
        'auto_renew_at': _iso(expires_at - timezone.timedelta(days=1)) if expires_at else None,
        'next_run_at': _iso(next_run_at),
        'suspend_at': _iso(notice.get('suspend_at') or getattr(order, 'suspend_at', None)),
        'delete_at': _iso(notice.get('delete_at') or getattr(order, 'delete_at', None)),
        'ip_recycle_at': _iso(notice.get('ip_recycle_at') or getattr(order, 'ip_recycle_at', None)),
        'related_path': f'/admin/cloud-orders/{order.id}',
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
    }


def _notice_event_type(notice_type: str) -> str:
    return _NOTICE_TASK_TYPES.get(notice_type, {}).get('event') or notice_type


def _notice_actual_batch_payload(notice_type: str, order_ids: list[int]) -> dict:
    if notice_type == 'renew_notice':
        return async_to_sync(_renew_notice_batch_payload)(order_ids)
    if notice_type == 'auto_renew_notice':
        return async_to_sync(_auto_renew_notice_batch_payload)(order_ids)
    if notice_type == 'delete_notice':
        return async_to_sync(_lifecycle_notice_batch_payload)(
            '⚠️ 云服务器删机提醒',
            order_ids,
            '如仍需使用，请尽快续费或联系人工客服处理。',
        )
    if notice_type == 'recycle_notice':
        return async_to_sync(_lifecycle_notice_batch_payload)(
            '♻️ 固定 IP 回收提醒',
            order_ids,
            '固定 IP 回收后将无法继续保留原 IP；如需恢复，请尽快联系人工客服。',
        )
    return {'text': '', 'order_ids': order_ids, 'first_order_id': order_ids[0] if order_ids else None, 'count': len(order_ids)}


def _notice_manual_text_payload(notice_type: str, user_id: int | None, order_ids: list[int]) -> dict:
    event = _notice_event_type(notice_type)
    manual_text = _get_notice_text_override(event, user_id, order_ids)
    return {
        'notice_event': event,
        'notice_override_key': _notice_override_key(event, user_id, order_ids),
        'notice_manual_text': manual_text,
        'notice_has_manual_text': bool(manual_text),
    }


def _notice_latest_log_map():
    logs = CloudUserNoticeLog.objects.filter(event_type__in=list(_NOTICE_HISTORY_LABELS)).order_by('-created_at', '-id')[:1000]
    mapped = {}
    for log in logs:
        keys = [(log.event_type, log.order_id)]
        canonical_event = 'renew_notice' if log.event_type == 'renew_notice_batch' else log.event_type
        keys.append((canonical_event, log.order_id))
        for order_id in (log.extra or {}).get('order_ids') or []:
            keys.append((canonical_event, order_id))
        for key in keys:
            if key[1] and key not in mapped:
                mapped[key] = log
    return mapped


def _notice_task_future_items(now, next_run_at, seen_keys: set[tuple[str, int]], latest_logs: dict, *, due_window_days=3, future_limit=10):
    items = []
    qs = CloudServerOrder.objects.select_related('user').filter(
        status__in=['completed', 'expiring', 'renew_pending', 'suspended', 'deleting', 'deleted'],
    ).order_by('service_expires_at', 'delete_at', 'ip_recycle_at', 'id')[:1000]
    for order in qs:
        notice = _notice_payload_for_order(order)
        if not notice:
            continue
        for notice_type, config in _NOTICE_TASK_TYPES.items():
            if (notice_type, order.id) in seen_keys:
                continue
            sent_at = getattr(order, config['field'], None)
            if sent_at:
                continue
            if notice_type == 'renew_notice' and (not order.cloud_reminder_enabled or order.status not in {'completed', 'expiring', 'renew_pending'}):
                continue
            if notice_type == 'auto_renew_notice' and (not order.auto_renew_enabled or order.status not in {'completed', 'expiring', 'renew_pending'}):
                continue
            if notice_type == 'delete_notice' and (not order.delete_reminder_enabled or order.status not in {'suspended', 'deleting'}):
                continue
            if notice_type == 'recycle_notice' and (not order.ip_recycle_reminder_enabled or order.status != 'deleted'):
                continue
            notice_at = _notice_task_time(order, notice_type, notice)
            if not notice_at:
                continue
            if notice_at <= now:
                queue_status, queue_status_label = 'fallback_notice', '已到通知时间'
            elif notice_at <= now + timezone.timedelta(days=due_window_days):
                queue_status, queue_status_label = 'within_window', '3天内待通知'
            else:
                queue_status, queue_status_label = 'scheduled_future', '未来计划'
            items.append(_notice_task_item_payload(
                order,
                notice_type,
                queue_status=queue_status,
                queue_status_label=queue_status_label,
                next_run_at=next_run_at,
                latest_log=latest_logs.get((notice_type, order.id)) or latest_logs.get((_notice_event_type(notice_type), order.id)),
            ))
            seen_keys.add((notice_type, order.id))
            if len(items) >= 200:
                break
    items.sort(key=lambda item: parse_datetime(item.get('notice_at') or '') or timezone.datetime.max.replace(tzinfo=dt_timezone.utc))
    due_items = [item for item in items if item.get('queue_status') in {'fallback_notice', 'within_window'}]
    future_items = [item for item in items if item.get('queue_status') == 'scheduled_future']
    return due_items, future_items


def _notice_group_summary_items(items: list[dict], *, limit: int | None = None) -> list[dict]:
    grouped = {}
    for item in items:
        notice_type = item.get('notice_type') or ''
        user_key = item.get('user_id') or f"unbound:{item.get('tg_user_id') or item.get('user_display_name') or 'unknown'}"
        key = f'{user_key}:{notice_type}'
        group = grouped.setdefault(key, {
            'id': key,
            'user_id': item.get('user_id'),
            'tg_user_id': item.get('tg_user_id'),
            'user_display_name': item.get('user_display_name') or '未绑定用户',
            'username_label': item.get('username_label') or '-',
            'notice_channel': item.get('notice_channel') or 'unbound',
            'notice_channel_label': item.get('notice_channel_label') or '未绑定通知渠道',
            'notice_channel_attempts': item.get('notice_channel_attempts') or [],
            'notice_type': notice_type,
            'notice_type_label': item.get('notice_type_label') or notice_type,
            'notice_event': _notice_event_type(notice_type),
            'notice_count': 0,
            'ip_count': 0,
            'ips': [],
            'order_ids': [],
            'pending_count': 0,
            'failed_retry_count': 0,
            'next_notice_at': item.get('notice_at'),
            'notice_text_preview': '',
            'retry_label': item.get('retry_label') or '-',
            'related_path': item.get('related_path') or '',
        })
        if item.get('notice_channel_attempts') and not group.get('notice_channel_attempts'):
            group['notice_channel_attempts'] = item.get('notice_channel_attempts') or []
        group['notice_count'] += 1
        order_id = item.get('order_id')
        if order_id and order_id not in group['order_ids']:
            group['order_ids'].append(order_id)
        ip = item.get('ip') or '-'
        if ip not in group['ips']:
            group['ips'].append(ip)
            group['ip_count'] += 1
        if item.get('notice_status') in {'pending', 'scheduled_soon'}:
            group['pending_count'] += 1
        if item.get('notice_status') == 'failed_retry':
            group['failed_retry_count'] += 1
            group['retry_label'] = item.get('retry_label') or group['retry_label']
        notice_at = item.get('notice_at')
        if notice_at and (not group.get('next_notice_at') or notice_at < group['next_notice_at']):
            group['next_notice_at'] = notice_at
            group['related_path'] = item.get('related_path') or group.get('related_path') or ''
        if not group.get('notice_text_preview'):
            label = group.get('notice_type_label') or '通知'
            group['notice_text_preview'] = f'{label}：{group["user_display_name"]} 共 {group["ip_count"]} 个 IP，系统会合并成一条通知发送。'
    summary = sorted(grouped.values(), key=lambda item: item.get('next_notice_at') or '')
    for group in summary:
        order_ids = group.get('order_ids') or []
        payload = _notice_actual_batch_payload(group.get('notice_type') or '', order_ids)
        manual_payload = _notice_manual_text_payload(group.get('notice_type') or '', group.get('user_id'), order_ids)
        manual_text = manual_payload.get('notice_manual_text') or ''
        group.update(manual_payload)
        group['notice_text_preview'] = manual_text or payload.get('text') or group.get('notice_text_preview') or ''
        group['notice_count'] = 1
        group['ip_count'] = int(payload.get('count') or group.get('ip_count') or 0)
    return summary[:limit] if limit else summary


def _notice_history_group_items(logs) -> list[dict]:
    items = []
    for log in logs:
        extra = log.extra or {}
        order_ids = extra.get('order_ids') or ([log.order_id] if log.order_id else [])
        notice_type = 'renew_notice' if log.event_type == 'renew_notice_batch' else log.event_type
        ip_count = len(order_ids) if order_ids else 1
        item = _notice_task_history_item_payload(log)
        item.update({
            'id': log.batch_id or log.id,
            'notice_event': log.event_type,
            'order_ids': order_ids,
            'notice_type': notice_type,
            'notice_type_label': _NOTICE_HISTORY_LABELS.get(log.event_type, log.event_type),
            'notice_count': 1,
            'ip_count': ip_count,
            'ips': [log.ip] if log.ip else [],
            'notice_text_preview': log.text_preview or '',
        })
        items.append(item)
    return items


def _notice_task_history_item_payload(log):
    return {
        'id': log.id,
        'batch_id': log.batch_id,
        'order_id': log.order_id,
        'order_no': log.order_no or '-',
        'notice_type': log.event_type,
        'notice_type_label': _NOTICE_HISTORY_LABELS.get(log.event_type, log.event_type),
        'ip': log.ip or '-',
        'user_id': log.user_id,
        'tg_user_id': getattr(log.user, 'tg_user_id', None) if getattr(log, 'user', None) else None,
        'user_display_name': getattr(log.user, 'display_name', '') or getattr(log.user, 'username', '') or '未绑定用户' if getattr(log, 'user', None) else '未绑定用户',
        'username_label': f'@{log.user.username}' if getattr(log, 'user', None) and getattr(log.user, 'username', '') else '-',
        'delivered': bool(log.delivered),
        'notice_status': 'sent' if log.delivered else 'failed_retry',
        'notice_status_label': '已通知' if log.delivered else '通知失败，待重试',
        'result_label': (_notice_attempts_label(log) or '已送达') if log.delivered else (_notice_attempts_label(log) or '未送达，后续巡检重试'),
        'target_chat_id': log.target_chat_id,
        **_notice_channel_payload(getattr(log, 'user', None), log),
        'text_preview': log.text_preview or '',
        'retry_label': '-' if log.delivered else (_notice_attempts_label(log) + '；' if _notice_attempts_label(log) else '') + '未成功送达，不会写入已通知时间；后续生命周期巡检会重试',
        'created_at': _iso(log.created_at),
        'related_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
        'detail_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
        'order_detail_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
        'order_link_path': f'/admin/cloud-orders/{log.order_id}' if log.order_id else '',
    }


@csrf_exempt
@dashboard_login_required
@require_POST
def update_notice_plan_text(request):
    payload = _read_payload(request)
    event = str(payload.get('notice_event') or payload.get('event') or '').strip()
    user_id = payload.get('user_id')
    order_ids = payload.get('order_ids') or []
    text = str(payload.get('notice_text') or payload.get('text') or '').strip()
    if not event:
        return _error('缺少通知事件类型', status=400)
    if not isinstance(order_ids, list) or not order_ids:
        return _error('缺少通知订单列表', status=400)
    try:
        normalized_user_id = int(user_id) if user_id else None
        normalized_order_ids = [int(item) for item in order_ids if item]
    except Exception:
        return _error('通知订单参数无效', status=400)
    key = _set_notice_text_override(event, normalized_user_id, normalized_order_ids, text)
    return _ok({
        'notice_override_key': key,
        'notice_manual_text': text,
        'notice_has_manual_text': bool(text),
    })


@dashboard_login_required
@require_GET
def notice_task_detail(request):
    now = timezone.now()
    due = async_to_sync(_get_due_orders)()
    next_run_at = now + timezone.timedelta(minutes=10)
    latest_logs = _notice_latest_log_map()
    due_items = []
    seen_keys = set()
    for notice_type, config in _NOTICE_TASK_TYPES.items():
        for order in list(due.get(notice_type) or []):
            if getattr(order, config['field'], None):
                continue
            due_items.append(_notice_task_item_payload(
                order,
                notice_type,
                queue_status='due_now',
                queue_status_label='本轮待通知',
                next_run_at=next_run_at,
                latest_log=latest_logs.get((notice_type, order.id)) or latest_logs.get((_notice_event_type(notice_type), order.id)),
            ))
            seen_keys.add((notice_type, order.id))
    due_items.sort(key=lambda item: parse_datetime(item.get('notice_at') or '') or timezone.datetime.max.replace(tzinfo=dt_timezone.utc))
    window_due_items, future_plan_items = _notice_task_future_items(now, next_run_at, seen_keys, latest_logs)
    due_items.extend(window_due_items)
    due_items.sort(key=lambda item: parse_datetime(item.get('notice_at') or '') or timezone.datetime.max.replace(tzinfo=dt_timezone.utc))
    due_user_summary_items = _notice_group_summary_items(due_items)
    future_user_summary_items = _notice_group_summary_items(future_plan_items, limit=10)
    history_qs = CloudUserNoticeLog.objects.select_related('order', 'user').filter(event_type__in=list(_NOTICE_HISTORY_LABELS)).order_by('-created_at', '-id')
    recent_since = now - timezone.timedelta(days=1)
    recent_logs = history_qs.filter(created_at__gte=recent_since)
    latest_log = history_qs.first()
    return _ok({
        'task_key': 'cloud_notice_plan',
        'task_label': '通知计划',
        'status_label': '置顶任务',
        'interval_minutes': 10,
        'last_run_at': _iso(getattr(latest_log, 'created_at', None)),
        'next_run_at': _iso(next_run_at),
        'due_count': len(due_items),
        'due_user_count': len(due_user_summary_items),
        'future_count': len(future_plan_items),
        'future_user_count': len(future_user_summary_items),
        'recent_success_count': recent_logs.filter(delivered=True).count(),
        'recent_success_user_count': recent_logs.filter(delivered=True).values('user_id').distinct().count(),
        'recent_failure_count': recent_logs.filter(delivered=False).count(),
        'recent_failure_user_count': recent_logs.filter(delivered=False).values('user_id').distinct().count(),
        'retry_policy_label': '通知失败不会写入已通知时间；生命周期巡检会在下一轮继续重试，直到成功送达。',
        'due_items': due_items,
        'due_user_summary_items': due_user_summary_items,
        'future_plan_items': future_plan_items,
        'future_user_summary_items': future_user_summary_items,
        'history_items': _notice_history_group_items(history_qs[:200]),
    })


@dashboard_login_required
@require_GET
def auto_renew_task_detail(request):
    now = timezone.now()
    queue = _collect_auto_renew_due_orders(now)
    due_orders = queue['due_orders']
    history_qs = queue['history_qs']
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

    due_items = [
        _auto_renew_due_item_payload(order, queue_status='due_now', queue_status_label='本轮待执行', next_run_at=next_run_at)
        for order in due_orders
    ]
    due_items.extend([
        _auto_renew_due_item_payload(order, queue_status=queue_status, queue_status_label=queue_status_label, next_run_at=next_run_at, last_failure_reason=last_failure_reason)
        for order, queue_status, queue_status_label, last_failure_reason in queue['retry_orders']
    ])
    due_items.extend([
        _auto_renew_due_item_payload(order, queue_status=queue_status, queue_status_label=queue_status_label, next_run_at=next_run_at)
        for order, queue_status, queue_status_label, _ in queue['fallback_orders']
    ])

    future_plan_items = _auto_renew_future_plan_items(
        now,
        next_run_at,
        [
            *due_orders,
            *[item[0] for item in queue['retry_orders']],
            *[item[0] for item in queue['fallback_orders']],
        ],
    )
    future_plan_items = [item for item in future_plan_items if item.get('queue_status') != 'fallback_retry']
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


@csrf_exempt
@dashboard_login_required
@require_POST
def run_auto_renew_tasks(request):
    now = timezone.now()
    queue = _collect_auto_renew_due_orders(now)
    orders = [(order, 'due_now') for order in queue['due_orders']]
    orders.extend((order, queue_status) for order, queue_status, _, _ in queue['retry_orders'])
    orders.extend((order, queue_status) for order, queue_status, _, _ in queue['fallback_orders'])
    if not orders:
        return _ok({
            'batch_id': '',
            'items': [],
            'total': 0,
            'success_count': 0,
            'failure_count': 0,
            'message': '当前没有可执行的续费任务',
        })
    result = _manual_run_auto_renew_queue(orders)
    result['message'] = f"本次共执行 {result['total']} 条续费任务"
    return _ok(result)


@csrf_exempt
@dashboard_login_required
@require_POST
def run_auto_renew_order(request, order_id):
    order = CloudServerOrder.objects.select_related('user').filter(pk=order_id).first()
    if not order:
        return _error('订单不存在', status=404)
    if not order.auto_renew_enabled:
        return _error('该订单未开启自动续费', status=400)
    if order.status not in {'completed', 'expiring', 'renew_pending'}:
        return _error('当前订单状态不可执行续费', status=400)
    result = _manual_run_auto_renew_queue([(order, 'manual_single')])
    result['message'] = '续费任务已执行'
    return _ok(result)


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
        'previous_public_ip': order.previous_public_ip,
        'service_started_at': _iso(order.service_started_at),
        'service_expires_at': _iso(order.service_expires_at),
        'created_at': _iso(order.created_at),
        'updated_at': _iso(order.updated_at),
        'replacement_for_id': order.replacement_for_id,
        'detail_path': f'/admin/cloud-orders/{order.id}',
        'order_detail_path': f'/admin/cloud-orders/{order.id}',
        'order_link_path': f'/admin/cloud-orders/{order.id}',
    }


def _order_lineage_ids(order):
    if not order:
        return set()
    seen = set()
    queue = [order.id]
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


def _cloud_asset_detail_log_queryset(asset, order):
    order_ids = _order_lineage_ids(order)
    asset_names = {str(asset.asset_name or '').strip(), str(asset.instance_id or '').strip()}
    ip_values = {str(asset.public_ip or '').strip(), str(asset.previous_public_ip or '').strip()}
    order_nos = set()
    if order_ids:
        for item in CloudServerOrder.objects.filter(id__in=order_ids).only('order_no', 'server_name', 'instance_id', 'public_ip', 'previous_public_ip'):
            order_nos.add(str(item.order_no or '').strip())
            asset_names.add(str(item.server_name or '').strip())
            asset_names.add(str(item.instance_id or '').strip())
            ip_values.add(str(item.public_ip or '').strip())
            ip_values.add(str(item.previous_public_ip or '').strip())
    asset_names.discard('')
    ip_values.discard('')
    order_nos.discard('')
    related_asset_ids = set([asset.id])
    asset_lookup = Q()
    if order_ids:
        asset_lookup |= Q(order_id__in=order_ids)
    if asset_names:
        asset_lookup |= Q(asset_name__in=asset_names) | Q(instance_id__in=asset_names)
    if ip_values:
        asset_lookup |= Q(public_ip__in=ip_values) | Q(previous_public_ip__in=ip_values)
    if asset_lookup:
        related_asset_ids.update(CloudAsset.objects.filter(asset_lookup).values_list('id', flat=True)[:200])
    log_lookup = Q(asset_id__in=related_asset_ids)
    if order_ids:
        log_lookup |= Q(order_id__in=order_ids)
    if asset_names:
        log_lookup |= Q(asset_name__in=asset_names) | Q(instance_id__in=asset_names)
    if ip_values:
        log_lookup |= Q(public_ip__in=ip_values) | Q(previous_public_ip__in=ip_values)
    for order_no in order_nos:
        log_lookup |= Q(order_no=order_no) | Q(note__icontains=order_no)
    return CloudIpLog.objects.filter(log_lookup).distinct().order_by('-created_at', '-id')


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
        deduped.append(item)
    deduped.sort(key=lambda item: (0 if item.id == order.id else 1, -(item.created_at.timestamp() if item.created_at else 0), -item.id))
    return [_cloud_order_summary_payload(item) for item in deduped]


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
        'suspend_time_config': str(get_runtime_config('cloud_suspend_time', '15:00') or '15:00').strip() or '15:00',
        'delete_time_config': str(get_runtime_config('cloud_delete_time', '15:00') or '15:00').strip() or '15:00',
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
        .exclude(Q(order_no__startswith='SRVMANUAL'))
        .annotate(
            deleted_rank=Case(
                When(status='deleted', then=Value(1)),
                default=Value(0),
                output_field=IntegerField(),
            )
        )
        .order_by('deleted_rank', '-created_at', '-id')
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

        if status == 'renew_pending':
            item['renew_status'] = 'renew_pending'
            item['renew_status_label'] = '续费待支付'
        elif status == 'expiring':
            item['renew_status'] = 'expiring'
            item['renew_status_label'] = '已到期待处理'
        elif status == 'suspended':
            item['renew_status'] = 'suspended'
            item['renew_status_label'] = '已关机待续费'
        elif status == 'deleting':
            item['renew_status'] = 'deleting'
            item['renew_status_label'] = '删除中'
        elif status == 'deleted':
            item['renew_status'] = 'deleted'
            item['renew_status_label'] = '实例已删除'
        elif status == 'expired':
            item['renew_status'] = 'expired'
            item['renew_status_label'] = '已过期'
        elif status in {'pending', 'cancelled', 'failed'}:
            item['renew_status'] = 'unpaid'
            item['renew_status_label'] = '未付款'
        elif status in {'paid', 'provisioning'}:
            item['renew_status'] = 'paid'
            item['renew_status_label'] = '已付款'
        elif status == 'completed' and service_expires_dt and service_expires_dt <= now:
            item['renew_status'] = 'expiring'
            item['renew_status_label'] = '已到期待处理'
        elif status == 'completed':
            item['renew_status'] = 'completed'
            item['renew_status_label'] = '已完成'
        else:
            item['renew_status'] = 'unknown'
            item['renew_status_label'] = '状态未知'

        item['can_renew'] = status not in {'pending', 'cancelled', 'failed', 'paid', 'provisioning'}
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
        'status_label': '旧机保留中' if server.status == Server.STATUS_DELETING and '旧机保留期' in str(server.provider_status or '') else _status_label(server.status, Server.STATUS_CHOICES),
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
        'preserve_link_status': _preserve_link_status_with_countdown(
            _preserve_link_status_label(server.note, getattr(order, 'provision_note', None)),
            _countdown_label(server.expires_at),
        ),
        'is_active': server.is_active,
        'updated_at': _iso(server.updated_at),
    }


@dashboard_login_required
@require_GET
def servers_list(request):
    keyword = _get_keyword(request)
    dedup_raw = (request.GET.get('dedup') or '').lower()
    dedup = dedup_raw not in {'0', 'false', 'no', 'off'}
    sort_by = (request.GET.get('sort_by') or '').strip().lower()
    sort_direction = _dashboard_sort_direction(request)
    ordering = ['expires_at', '-updated_at', '-id']
    if sort_by in {'expires_at', 'days_left', 'remaining_days'}:
        ordering = _dashboard_expiry_ordering('expires_at', sort_direction)
    queryset = Server.objects.select_related('user', 'order').exclude(status=Server.STATUS_DELETED).exclude(public_ip__isnull=True).exclude(public_ip='').order_by(*ordering)
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
    return prepend_note(order.provision_note, note)


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
        _update_order_primary_records(
            order,
            asset_updates={
                'is_active': True,
                'note': order.provision_note,
            },
            server_updates={
                'is_active': True,
                'status': Server.STATUS_RUNNING if new_status == 'completed' else Server.STATUS_PENDING,
                'note': order.provision_note,
            },
            now=now,
        )
    elif new_status in inactive_statuses:
        _update_order_primary_records(
            order,
            asset_updates={
                'is_active': False,
                'note': order.provision_note,
            },
            server_updates={
                'is_active': False,
                'status': Server.STATUS_DELETED if new_status == 'deleted' else Server.STATUS_STOPPED,
                'note': order.provision_note,
            },
            now=now,
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

            original_public_ip = order.public_ip
            for field in ('server_name', 'public_ip', 'previous_public_ip', 'instance_id', 'provider_resource_id', 'static_ip_name', 'mtproxy_host', 'mtproxy_link', 'provision_note'):
                if field in payload:
                    setattr(order, field, payload.get(field) or None)
                    changed_fields.add(field)
            if 'public_ip' in payload and original_public_ip and original_public_ip != order.public_ip and 'previous_public_ip' not in payload:
                order.previous_public_ip = original_public_ip
                changed_fields.add('previous_public_ip')
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
            if 'service_expires_at' in changed_fields and 'service_expires_at' in payload:
                lifecycle_updates = _cloud_order_lifecycle_fields(order.service_expires_at, getattr(order, 'renew_extension_days', 0)) if order.service_expires_at else {
                    'renew_grace_expires_at': None,
                    'suspend_at': None,
                    'delete_at': None,
                    'ip_recycle_at': None,
                }
                for field, value in lifecycle_updates.items():
                    if field not in payload:
                        setattr(order, field, value)
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
                    if 'previous_public_ip' in changed_fields:
                        asset_updates['previous_public_ip'] = order.previous_public_ip
                        server_updates['previous_public_ip'] = order.previous_public_ip
                if 'server_name' in changed_fields:
                    asset_updates['asset_name'] = order.server_name
                    server_updates['server_name'] = order.server_name
                if 'service_expires_at' in changed_fields:
                    asset_updates['actual_expires_at'] = order.service_expires_at
                    server_updates['expires_at'] = order.service_expires_at
                if asset_updates or server_updates:
                    _update_order_primary_records(order, asset_updates=asset_updates, server_updates=server_updates)
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
            asset_updates={'note': failure_note},
            server_updates={'note': failure_note},
        )


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
        'message': '已发起 AWS 重装迁移，后台失败会自动重试（最多 3 次），成功后旧实例保留 3 天再删除。',
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

    def _clear_order_cloud_binding(target_order):
        if not target_order:
            return False
        target_order.server_name = ''
        target_order.instance_id = ''
        target_order.provider_resource_id = ''
        target_order.public_ip = None
        target_order.previous_public_ip = None
        target_order.static_ip_name = ''
        target_order.mtproxy_host = ''
        target_order.mtproxy_port = 0
        target_order.mtproxy_secret = ''
        target_order.mtproxy_link = ''
        target_order.proxy_links = []
        target_order.login_user = ''
        target_order.login_password = ''
        target_order.provision_note = append_note(
            target_order.provision_note,
            f'后台代理列表删除已清除云资源绑定；原IP={previous_public_ip or "-"}；后续云同步按全新资源处理，不再继承本订单状态；时间: {now.isoformat()}。',
        )
        target_order.save(update_fields=[
            'server_name', 'instance_id', 'provider_resource_id', 'public_ip', 'previous_public_ip',
            'static_ip_name', 'mtproxy_host', 'mtproxy_port', 'mtproxy_secret', 'mtproxy_link',
            'proxy_links', 'login_user', 'login_password', 'provision_note', 'updated_at',
        ])
        return True

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

    server_lookup = Q()
    if order:
        server_lookup |= Q(order=order)
    for value, fields in [
        (asset.instance_id, ['instance_id']),
        (asset.provider_resource_id, ['provider_resource_id']),
        (asset.public_ip, ['public_ip', 'previous_public_ip']),
        (asset.previous_public_ip, ['public_ip', 'previous_public_ip']),
    ]:
        value = str(value or '').strip()
        if not value:
            continue
        for field in fields:
            server_lookup |= Q(**{field: value})
    related_servers = Server.objects.filter(server_lookup).distinct() if server_lookup else Server.objects.none()
    removed_server_ids = []
    for server in related_servers:
        record_cloud_ip_log(
            event_type='deleted',
            order=getattr(server, 'order', None),
            server=server,
            previous_public_ip=server.public_ip or server.previous_public_ip,
            public_ip=None,
            note=f'{note}；已一并清理关联服务器本地状态，后续云同步按全新资源处理',
        )
        removed_server_ids.append(server.id)
        server.delete()

    if _looks_like_local_residual(asset):
        record_cloud_ip_log(event_type='deleted', order=order, asset=asset, previous_public_ip=previous_public_ip, public_ip=None, note=note)
    order_status_changed = _clear_order_cloud_binding(order)
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
        'order_status_changed': order_status_changed,
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
    updated = 0
    for server in missing_servers:
        server.status = Server.STATUS_DELETED
        server.provider_status = '已删除'
        server.is_active = False
        server.note = append_note(server.note, missing_note)
        server.updated_at = now
        server.save(update_fields=['status', 'provider_status', 'is_active', 'note', 'updated_at'])
        updated += 1
    order_ids = [item.order_id for item in missing_servers if item.order_id]
    instance_ids = [item.instance_id for item in missing_servers if item.instance_id]
    asset_scope = CloudAsset.objects.filter(kind=CloudAsset.KIND_SERVER, provider=provider)
    if region:
        asset_scope = asset_scope.filter(Q(region_code=region) | Q(region_code='') | Q(region_code__isnull=True))
    if account:
        label = cloud_account_label(account)
        asset_scope = asset_scope.filter(Q(cloud_account=account) | Q(account_label=label))
    if order_ids:
        for order in CloudServerOrder.objects.filter(id__in=order_ids).exclude(status='deleted'):
            order.status = 'deleted'
            order.provision_note = prepend_note(order.provision_note, missing_note)
            order.updated_at = now
            order.save(update_fields=['status', 'provision_note', 'updated_at'])
        for asset in asset_scope.filter(order_id__in=order_ids):
            asset.status = CloudAsset.STATUS_DELETED
            asset.provider_status = '已删除'
            asset.is_active = False
            asset.note = append_note(asset.note, missing_note)
            asset.updated_at = now
            asset.save(update_fields=['status', 'provider_status', 'is_active', 'note', 'updated_at'])
    if instance_ids:
        for asset in asset_scope.filter(instance_id__in=instance_ids):
            asset.status = CloudAsset.STATUS_DELETED
            asset.provider_status = '已删除'
            asset.is_active = False
            asset.note = append_note(asset.note, missing_note)
            asset.updated_at = now
            asset.save(update_fields=['status', 'provider_status', 'is_active', 'note', 'updated_at'])
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


def _sync_provider_for_asset(asset) -> str:
    provider = str(getattr(asset, 'provider', '') or '').strip().lower()
    if provider == 'aws_lightsail':
        return CloudAccountConfig.PROVIDER_AWS
    if provider == 'aliyun_simple':
        return CloudAccountConfig.PROVIDER_ALIYUN
    return ''


def _resolve_sync_account_for_asset(asset):
    provider = _sync_provider_for_asset(asset)
    if not provider:
        return None
    account = getattr(asset, 'cloud_account', None)
    if account and account.provider == provider and account.is_active:
        return account
    account_label = str(
        getattr(asset, 'account_label', '')
        or cloud_account_label(account)
        or getattr(getattr(asset, 'order', None), 'account_label', '')
        or ''
    ).strip()
    queryset = CloudAccountConfig.objects.filter(provider=provider, is_active=True).order_by('id')
    if getattr(asset, 'cloud_account_id', None):
        matched = queryset.filter(id=asset.cloud_account_id).first()
        if matched:
            return matched
    if account_label:
        for candidate in queryset:
            if cloud_account_label(candidate) == account_label:
                return candidate
    return None


@csrf_exempt
@dashboard_login_required
@require_POST
def sync_cloud_asset_status(request, asset_id):
    asset = CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'telegram_group').filter(pk=asset_id).first()
    if not asset:
        return _error('云资产不存在', status=404)
    provider = _sync_provider_for_asset(asset)
    if not provider:
        return _error('当前资产暂不支持单条状态更新', status=400)
    account = _resolve_sync_account_for_asset(asset)
    if not account:
        return _error('未找到可用的云账号配置，请先检查该代理绑定的云账号是否启用', status=400)

    region_code = str(getattr(asset, 'region_code', '') or getattr(account, 'region_hint', '') or '').strip()
    command_output = io.StringIO()
    errors = []
    command_name = 'sync_aws_assets' if provider == CloudAccountConfig.PROVIDER_AWS else 'sync_aliyun_assets'
    request_payload = {
        'asset_id': asset.id,
        'provider': provider,
        'region_code': region_code or 'all',
        'account_id': account.id,
    }
    try:
        command_kwargs = {'account_id': str(account.id), 'stdout': command_output}
        if region_code:
            command_kwargs['region'] = region_code
        _call_command_capture(command_name, **command_kwargs)
    except Exception as exc:
        errors.append(str(exc))
        logger.exception(
            'DASHBOARD_SYNC_SINGLE_ASSET_FAILED asset_id=%s provider=%s region=%s account_id=%s',
            asset.id,
            provider,
            region_code or 'all',
            getattr(account, 'id', None),
        )

    refreshed = CloudAsset.objects.select_related('order', 'user', 'cloud_account', 'telegram_group').filter(pk=asset_id).first()
    response_payload = {
        'ok': not errors,
        'asset': _asset_payload(refreshed) if refreshed else None,
        'provider': provider,
        'region_code': region_code or 'all',
        'account': _sync_account_payload(account),
        'errors': errors,
        'logs': _sync_log_tail(command_output),
    }
    _record_dashboard_sync_log(
        action='sync_cloud_asset_status',
        target=f'asset:{asset.id}',
        request_payload=request_payload,
        response_payload={**response_payload, 'log_text': _sync_log_text(command_output)},
        is_success=not errors,
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
    warnings = []
    sync_tasks = []
    for aliyun_account in aliyun_accounts:
        sync_tasks.append({
            'provider': 'aliyun',
            'account': aliyun_account,
            'command': 'sync_aliyun_assets',
            'kwargs': {'region': aliyun_region, 'account_id': str(aliyun_account.id)},
        })
    for aws_account in aws_accounts:
        sync_tasks.append({
            'provider': 'aws',
            'account': aws_account,
            'command': 'sync_aws_assets',
            'kwargs': {'region': aws_region, 'account_id': str(aws_account.id)},
        })

    def run_task(task):
        command, log_text = _call_command_capture_threaded(task['command'], **task['kwargs'])
        return {'task': task, 'command': command, 'log_text': log_text}

    max_workers = max(1, min(4, len(sync_tasks) or 1))
    if sync_tasks:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(run_task, task): task for task in sync_tasks}
            for future in as_completed(future_map):
                task = future_map[future]
                account = task['account']
                try:
                    result = future.result()
                    command_output.write(result.get('log_text') or '')
                    if task['provider'] == 'aliyun':
                        synced['aliyun'] = True
                    else:
                        command = result['command']
                        account_regions = getattr(command, 'synced_regions', None) or [aws_region or 'all']
                        aws_regions.extend(region for region in account_regions if region not in aws_regions)
                        warnings.extend(getattr(command, 'sync_errors', []) or [])
                        synced['aws'] = True
                except Exception as exc:
                    if task['provider'] == 'aliyun':
                        message = f'阿里云账号#{getattr(account, "id", "-")}代理同步失败: {exc}'
                        logger.exception('DASHBOARD_SYNC_ASSETS_ALIYUN_FAILED account_id=%s region=%s', getattr(account, 'id', None), aliyun_region)
                    else:
                        message = f'AWS账号#{getattr(account, "id", "-")}代理同步失败: {exc}'
                        logger.exception('DASHBOARD_SYNC_ASSETS_AWS_FAILED account_id=%s region=%s', getattr(account, 'id', None), aws_region or 'all')
                    errors.append(message)
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
        'server_id': item.server_id,
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
        queryset = queryset.filter(
            Q(order__isnull=False)
            | Q(server__isnull=False)
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


__all__ = [
    'cloud_assets_list',
    'cloud_assets_sync_status',
    'sync_cloud_asset_status',
    'cloud_ip_logs_list',
    'cloud_order_detail',
    'cloud_orders_list',
    'delete_cloud_order',
    'notice_task_detail',
    'update_notice_plan_text',
    'auto_renew_task_detail',
    'run_auto_renew_order',
    'run_auto_renew_tasks',
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
