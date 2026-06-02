import asyncio
import contextlib
import contextvars
import logging
import math
import re
import secrets
import time
from datetime import datetime as dt_datetime
from decimal import Decimal, InvalidOperation
from html import escape
from urllib.parse import parse_qs, urlparse, unquote

import httpx

from asgiref.sync import sync_to_async
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, ErrorEvent, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from core.cache import get_config
from cloud.asset_expiry import order_asset_expiry
from cloud.note_utils import append_note
from cloud.ip_guard import validate_server_connection_ip
from bot.config import BOT_TOKEN
from bot.fsm import create_fsm_storage
from bot.states import AdminReplyStates, CustomServerStates, MonitorStates, RechargeStates, CloudQueryStates
from orders.services import get_exchange_rate_display, usdt_to_trx
from bot.keyboards import (
    main_menu, monitor_menu, monitor_list as kb_monitor_list,
    monitor_detail as kb_monitor_detail, monitor_threshold_currency,
    recharge_currency_menu, product_list, quantity_keyboard,
    pay_method_keyboard, order_list as kb_order_list,
    recharge_list as kb_recharge_list, profile_menu, reminder_list_menu, reminder_ip_detail_menu,
    custom_region_menu, custom_plan_menu, custom_quantity_keyboard, custom_payment_keyboard, custom_currency_keyboard, custom_wallet_keyboard, custom_order_wallet_keyboard,
    cloud_server_list, cloud_auto_renew_server_list, cloud_server_detail, cloud_order_list, cloud_order_readonly_detail, cloud_expiry_actions, cloud_server_renew_payment, order_query_menu, balance_details_list, support_contact_button, cloud_lifecycle_notice_actions,
    cloud_server_change_ip_region_menu,
    cart_menu, wallet_recharge_prompt_menu, cloud_ip_query_result,
    cloud_query_menu, configured_link_for_label, configured_link_menu,
    cloud_detail_callback, cloud_asset_detail_callback, cloud_previous_detail_callback, cloud_asset_action_callback, append_back_callback, compact_callback_path, expand_compact_region_code,
)
from bot.services import create_admin_reply_link, get_admin_reply_link, get_admin_reply_link_by_id, get_or_create_user, get_admin_forward_mute_status, is_admin_forward_muted, mute_admin_forward_for_days, record_bot_operation_log, record_telegram_message, should_forward_telegram_group
from cloud.services import (
    RenewalPriceMissingError,
    buy_cloud_server_with_balance,
    create_cloud_server_order,
    create_cloud_server_renewal,
    create_cloud_server_renewal_by_public_query,
    create_cloud_server_renewal_for_user,
    disable_all_cloud_server_auto_renew,
    disable_all_cloud_server_auto_renew_admin,
    enable_all_cloud_server_auto_renew,
    enable_all_cloud_server_auto_renew_admin,
    get_cloud_plan,
    get_cloud_order_group_balance_lines,
    get_cloud_server_auto_renew,
    get_user_reminder_summary,
    get_cloud_server_by_ip,
    get_cloud_server_by_ip_for_user,
    get_cloud_server_for_admin,
    get_proxy_asset_by_ip_for_admin,
    get_proxy_asset_by_ip_for_user,
    get_proxy_asset_detail_for_admin,
    get_group_proxy_asset_detail,
    get_user_cloud_server,
    get_user_proxy_asset_detail,
    ensure_cloud_asset_operation_order,
    initialize_proxy_asset,
    is_cloud_asset_renewal_order,
    is_retained_ip_order_visible_in_group,
    list_all_auto_renew_cloud_servers,
    list_group_auto_renew_cloud_servers,
    list_group_cloud_servers,
    list_custom_regions,
    list_region_plans,
    list_cloud_asset_renewal_plans,
    list_retained_ip_renewal_plans,
    list_retained_ip_renewal_plans_by_asset,
    list_user_auto_renew_cloud_servers,
    list_user_cloud_servers,
    list_cloud_server_upgrade_plans,
    create_cloud_server_upgrade_order,
    mark_cloud_server_ip_change_requested,
    mark_cloud_server_reinit_requested,
    mute_all_user_reminders,
    mute_cloud_order_reminders,
    mute_cloud_reminders,
    pay_cloud_server_order_with_balance,
    pay_cloud_server_renewal_with_balance,
    prepare_cloud_server_order_instances,
    prepare_cloud_asset_renewal_with_link,
    prepare_retained_ip_renewal_with_link,
    run_cloud_server_renewal_postcheck,
    set_cloud_order_reminder,
    _order_primary_asset,
    set_group_cloud_server_auto_renew,
    set_cloud_server_auto_renew,
    set_cloud_server_auto_renew_admin,
    start_cloud_server_from_admin,
    unmute_all_user_reminders,
    unmute_cloud_reminders,
    update_cloud_item_expiry_for_admin,
)
from orders.services import (
    add_monitor,
    add_to_cart,
    buy_with_balance,
    clear_cart,
    create_address_order,
    create_cart_address_orders,
    create_cart_balance_orders,
    create_recharge,
    delete_monitor,
    get_balance_detail,
    get_cloud_order,
    get_monitor,
    get_order,
    get_product,
    get_recharge,
    list_balance_details,
    list_cart_items,
    list_cloud_orders,
    list_monitors,
    list_orders,
    list_products,
    list_recharges,
    remove_cart_item,
    set_monitor_threshold,
    toggle_monitor_flag,
)
from orders.tron_parser import is_valid_tron_address
from core.formatters import fmt_amount, fmt_pay_amount
from core.models import SiteConfig
from core.texts import site_text
from core.trongrid import build_trongrid_headers
from cloud.provisioning import get_provision_progress, provision_cloud_server, reprovision_cloud_server_bootstrap
from cloud.bootstrap import _normalize_mtproxy_core_secret, _probe_mtproxy_state, build_mtproxy_links
from cloud.ports import MTPROXY_DEFAULT_PORT, get_mtproxy_port_plan

logger = logging.getLogger(__name__)

_CUSTOM_REGIONS_CACHE: dict[str, object] = {'expires_at': 0.0, 'items': None}
_REGION_PLANS_CACHE: dict[str, tuple[float, object]] = {}
_TG_CHAT_CACHE: dict[int, tuple[float, dict[str, object]]] = {}
_USER_SYNC_CACHE: dict[int, tuple[float, tuple[str | None, str | None, tuple[str, ...]]]] = {}
_ASSET_REINIT_INFLIGHT: set[int] = set()
_CLOUD_PROVISION_INFLIGHT: set[int] = set()
_CALLBACK_ONCE_KEYS: dict[str, float] = {}
_REINSTALL_CONFIRM_TTL = 600
_CUSTOM_REGIONS_CACHE_TTL = 60
_REGION_PLANS_CACHE_TTL = 60
_TG_CHAT_CACHE_TTL = 120
_USER_SYNC_CACHE_TTL = 15
_CALLBACK_ONCE_TTL = 600
_CUSTOM_CLOUD_MIN_QUANTITY = 1
_CUSTOM_CLOUD_MAX_QUANTITY = 99
_NOTICE_COPY_SENDING = contextvars.ContextVar('notice_copy_sending', default=False)
TRONGRID_BASE_URL = 'https://api.trongrid.io'
USDT_CONTRACT = 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'


def _extract_query_ip(raw_text: str) -> str:
    text = (raw_text or '').strip()
    if not text:
        return ''
    if '://' in text:
        parsed = urlparse(text)
        query_server = (parse_qs(parsed.query).get('server') or [''])[0]
        if query_server and parsed.scheme in {'tg', 'http', 'https'} and (parsed.netloc == 'proxy' or parsed.netloc == 't.me'):
            return query_server.strip().strip('[]')
        hostname = parsed.hostname or ''
        if hostname:
            return hostname.strip('[]')
    candidate = text.split()[0].strip()
    if candidate.startswith('tg://proxy?') or candidate.startswith('https://t.me/proxy?'):
        parsed = urlparse(candidate)
        query_server = (parse_qs(parsed.query).get('server') or [''])[0]
        if query_server:
            return query_server.strip().strip('[]')
    host_port_match = re.match(r'^\[?([0-9a-zA-Z\-\._:]+)\]?(?::\d+)?$', candidate)
    if host_port_match:
        return host_port_match.group(1).strip('[]')
    return candidate


def _extract_query_ips(raw_text: str) -> list[str]:
    text = (raw_text or '').strip()
    if not text:
        return []
    candidates: list[str] = []
    for url_match in re.finditer(r'(?:tg://proxy\?[^\s]+|https://t\.me/proxy\?[^\s]+|https?://[^\s]+)', text):
        extracted = _extract_query_ip(url_match.group(0))
        if extracted:
            candidates.append(extracted)
    for ip_match in re.finditer(r'(?<!\d)(?:\d{1,3}\.){3}\d{1,3}(?::\d+)?(?!\d)', text):
        extracted = _extract_query_ip(ip_match.group(0))
        if extracted:
            candidates.append(extracted)
    unique_ips: list[str] = []
    seen = set()
    for item in candidates:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_ips.append(normalized)
    return unique_ips


def _extract_proxy_links_by_ip(raw_text: str) -> dict[str, dict[str, str]]:
    links: dict[str, dict[str, str]] = {}
    for url_match in re.finditer(r'(?:tg://proxy\?[^\s]+|https://t\.me/proxy\?[^\s]+)', raw_text or ''):
        link_data = _parse_proxy_link(url_match.group(0))
        if link_data and link_data.get('server') and link_data['server'] not in links:
            links[link_data['server']] = link_data
    return links


def _extract_tron_addresses(raw_text: str) -> list[str]:
    text = (raw_text or '').strip()
    if not text:
        return []
    seen = set()
    result: list[str] = []
    for match in re.finditer(r'(?<![A-Za-z0-9])T[1-9A-HJ-NP-Za-km-z]{33}(?![A-Za-z0-9])', text):
        address = match.group(0).strip()
        if address in seen:
            continue
        seen.add(address)
        result.append(address)
    return result


def _detect_message_kind(raw_text: str) -> str:
    text = (raw_text or '').strip()
    if not text:
        return 'empty'
    if text.startswith('/'):
        return 'command'
    if _extract_query_ips(text):
        return 'link'
    if _extract_tron_addresses(text):
        return 'address'
    return 'text'


def _format_ts_ms(value) -> str:
    try:
        timestamp_ms = int(value or 0)
    except Exception:
        return '-'
    if timestamp_ms <= 0:
        return '-'
    return timezone.localtime(timezone.datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.get_current_timezone())).strftime('%Y-%m-%d %H:%M:%S')


def _tron_account_type_label(raw_type: str) -> str:
    mapping = {
        'Normal': '普通账户',
        'Contract': '合约账户',
        'AssetIssue': '资产发行账户',
    }
    return mapping.get((raw_type or '').strip(), raw_type or '普通账户')


def _format_permission_block(title: str, permission: dict | None) -> list[str]:
    if not permission:
        return [f'{title} 权限：无']
    lines = [f'{title} 权限']
    lines.append(f'权限名称：{permission.get("permission_name") or permission.get("type") or "-"}(阈值 {permission.get("threshold", "-")})')
    for key in permission.get('keys') or []:
        address = key.get('address') or '-'
        weight = key.get('weight', '-')
        lines.append(f'{address}(权重: {weight})')
    return lines


def _tronscan_address_url(address: str) -> str:
    return f'https://tronscan.org/#/address/{address}'


def _tronscan_transfers_url(address: str) -> str:
    return f'https://tronscan.org/#/address/{address}/transfers'


def _tronscan_tx_url(tx_hash: str) -> str:
    return f'https://tronscan.org/#/transaction/{tx_hash}'


def _tron_address_action_keyboard(address: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text='🔎 链上详情查询', url=_tronscan_address_url(address)),
            InlineKeyboardButton(text='📜 查询转账记录', url=_tronscan_transfers_url(address)),
        ]]
    )


def _trongrid_headers_without_key(headers: dict | None) -> dict:
    return {key: value for key, value in dict(headers or {}).items() if key.lower() != 'tron-pro-api-key'}


async def _trongrid_get_with_key_fallback(client: httpx.AsyncClient, url: str, headers: dict):
    resp = await client.get(url, headers=headers)
    if resp.status_code == 401 and headers.get('TRON-PRO-API-KEY'):
        resp = await client.get(url, headers=_trongrid_headers_without_key(headers))
    return resp


async def _trongrid_post_with_key_fallback(client: httpx.AsyncClient, url: str, payload: dict, headers: dict):
    resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code == 401 and headers.get('TRON-PRO-API-KEY'):
        resp = await client.post(url, json=payload, headers=_trongrid_headers_without_key(headers))
    return resp


async def _fetch_tron_address_summary(address: str) -> dict:
    headers = await build_trongrid_headers()
    base_url = await get_config('trongrid_base_url', TRONGRID_BASE_URL)
    base_url = str(base_url or TRONGRID_BASE_URL).rstrip('/')
    async with httpx.AsyncClient(timeout=15) as client:
        account_resp = await _trongrid_post_with_key_fallback(
            client,
            f'{base_url}/wallet/getaccount',
            {'address': address, 'visible': True},
            headers,
        )
        account_resp.raise_for_status()
        account_data = account_resp.json() or {}
        resource_resp = await _trongrid_post_with_key_fallback(
            client,
            f'{base_url}/wallet/getaccountresource',
            {'address': address, 'visible': True},
            headers,
        )
        resource_resp.raise_for_status()
        resource_data = resource_resp.json() or {}
        account_v1_resp = await _trongrid_get_with_key_fallback(client, f'{base_url}/v1/accounts/{address}', headers)
        account_v1_resp.raise_for_status()
        account_v1_data = account_v1_resp.json() or {}
        trc20_resp = await _trongrid_get_with_key_fallback(
            client,
            f'{base_url}/v1/accounts/{address}/transactions/trc20?limit=20&only_confirmed=true&order_by=block_timestamp,desc',
            headers,
        )
        trc20_resp.raise_for_status()
        trc20_data = trc20_resp.json() or {}
        trx_resp = await _trongrid_get_with_key_fallback(
            client,
            f'{base_url}/v1/accounts/{address}/transactions?limit=20&only_confirmed=true&order_by=block_timestamp,desc',
            headers,
        )
        trx_resp.raise_for_status()
        trx_data = trx_resp.json() or {}
    trx_balance = Decimal(str(account_data.get('balance', 0) or 0)) / Decimal('1000000')
    frozen_v2 = account_data.get('frozenV2') or []
    frozen_total = Decimal('0')
    for item in frozen_v2:
        try:
            frozen_total += Decimal(str(item.get('amount', 0) or 0)) / Decimal('1000000')
        except Exception:
            continue
    free_net_limit = int(resource_data.get('freeNetLimit', 0) or 0)
    free_net_used = int(resource_data.get('freeNetUsed', 0) or 0)
    net_limit = int(resource_data.get('NetLimit', 0) or 0)
    net_used = int(resource_data.get('NetUsed', 0) or 0)
    energy_limit = int(resource_data.get('EnergyLimit', 0) or 0)
    energy_used = int(resource_data.get('EnergyUsed', 0) or 0)
    usdt_balance = Decimal('0')
    account_items = account_v1_data.get('data') or []
    if account_items:
        trc20_balances = account_items[0].get('trc20') or []
        for item in trc20_balances:
            value = item.get(USDT_CONTRACT)
            if value is None:
                continue
            try:
                usdt_balance = Decimal(str(value or '0')) / Decimal('1000000')
            except Exception:
                usdt_balance = Decimal('0')
            break
    usdt_in_count = 0
    usdt_out_count = 0
    recent_items: list[dict] = []
    for item in trc20_data.get('data') or []:
        token_info = item.get('token_info') or {}
        if (token_info.get('address') or '') != USDT_CONTRACT:
            continue
        from_addr = item.get('from') or ''
        to_addr = item.get('to') or ''
        direction = '转入' if to_addr == address else '转出'
        if direction == '转入':
            usdt_in_count += 1
        else:
            usdt_out_count += 1
        try:
            decimals = int(token_info.get('decimals', 6) or 6)
        except Exception:
            decimals = 6
        try:
            amount = Decimal(str(item.get('value') or '0')) / (Decimal(10) ** decimals)
        except Exception:
            amount = Decimal('0')
        recent_items.append({
            'timestamp': int(item.get('block_timestamp') or 0),
            'text': f'{_format_ts_ms(item.get("block_timestamp"))} {direction} {fmt_amount(amount)} USDT',
            'tx_hash': item.get('transaction_id') or item.get('tx_id') or '',
        })
    for item in trx_data.get('data') or []:
        raw_data = item.get('raw_data') or {}
        contracts = raw_data.get('contract') or []
        if not contracts:
            continue
        contract = contracts[0]
        parameter = ((contract.get('parameter') or {}).get('value') or {})
        amount_sun = parameter.get('amount')
        owner_address = parameter.get('owner_address') or ''
        to_address = parameter.get('to_address') or ''
        contract_type = contract.get('type') or ''
        if contract_type != 'TransferContract' or amount_sun is None:
            continue
        direction = '转入' if to_address == address else '转出' if owner_address == address else ''
        if not direction:
            continue
        try:
            amount = Decimal(str(amount_sun or 0)) / Decimal('1000000')
        except Exception:
            amount = Decimal('0')
        recent_items.append({
            'timestamp': int(raw_data.get('timestamp') or 0),
            'text': f'{_format_ts_ms(raw_data.get("timestamp"))} {direction} {fmt_amount(amount)} TRX',
            'tx_hash': item.get('txID') or item.get('tx_id') or '',
        })
    recent_items.sort(key=lambda item: item['timestamp'], reverse=True)
    return {
        'address': address,
        'account_type': _tron_account_type_label(account_data.get('account_type') or 'Normal'),
        'created_at': _format_ts_ms(account_data.get('create_time')),
        'last_active_at': _format_ts_ms(account_data.get('latest_opration_time')),
        'trx_balance': trx_balance,
        'trx_frozen': frozen_total,
        'usdt_balance': usdt_balance,
        'energy_used': energy_used,
        'energy_limit': energy_limit,
        'net_used': net_used,
        'net_limit': net_limit,
        'free_net_used': free_net_used,
        'free_net_limit': free_net_limit,
        'owner_permission': account_data.get('owner_permission') or {},
        'active_permission': (account_data.get('active_permission') or [{}])[0] if (account_data.get('active_permission') or []) else {},
        'usdt_in_count': usdt_in_count,
        'usdt_out_count': usdt_out_count,
        'recent_transactions': recent_items[:5],
    }


async def _reply_tron_address_summary(message: Message, address: str):
    summary = await _fetch_tron_address_summary(address)
    lines = [
        f'👤账户类型: {escape(summary["account_type"])}',
        f'🔍查询地址: <code>{escape(summary["address"])}</code>',
        f'⏰创建时间: {escape(summary["created_at"])}',
        f'🌟最后活跃: {escape(summary["last_active_at"])}',
        '➖➖➖➖资源➖➖➖➖',
        f'💰 TRX 余额： {fmt_amount(summary["trx_balance"])} TRX',
        f'💰 TRX 质押： {fmt_amount(summary["trx_frozen"])} TRX',
        f'💰USDT余额： {fmt_amount(summary["usdt_balance"])} USDT',
        f'🔋能量： {summary["energy_used"]} / {summary["energy_limit"]}',
        f'📡质押带宽： {summary["net_used"]} / {summary["net_limit"]}',
        f'📡免费带宽： {summary["free_net_used"]} / {summary["free_net_limit"]}',
        '➖➖➖➖权限➖➖➖➖',
        *_format_permission_block('拥有者 (Owner)', summary['owner_permission']),
        *_format_permission_block('活跃 (Active)', summary['active_permission']),
        '➖➖➖➖最近交易➖➖➖➖',
        f'⤴️USDT支出笔数：{summary["usdt_out_count"]} ⤵️USDT收入笔数：{summary["usdt_in_count"]}',
    ]
    for item in summary['recent_transactions']:
        tx_hash = (item.get('tx_hash') or '').strip()
        text = escape(item.get('text') or '-')
        if tx_hash:
            lines.append(f'<a href="{_tronscan_tx_url(tx_hash)}">{text}</a>')
        else:
            lines.append(text)
    await message.answer(
        '\n'.join(lines),
        parse_mode='HTML',
        reply_markup=_tron_address_action_keyboard(summary['address']),
        disable_web_page_preview=True,
    )


async def _reply_cloud_query_results(message: Message, raw_text: str, state: FSMContext | None = None, include_start: bool = False):
    query_ips = _extract_query_ips(raw_text)
    proxy_links_by_ip = _extract_proxy_links_by_ip(raw_text)
    results = []
    user = None
    if not include_start:
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
    for index, ip in enumerate(query_ips):
        input_link = proxy_links_by_ip.get(ip)
        if include_start:
            asset = await get_proxy_asset_by_ip_for_admin(ip)
        else:
            asset = await get_proxy_asset_by_ip_for_user(ip, user.id)
            if not asset:
                asset = await get_proxy_asset_by_ip_for_admin(ip)
        if asset:
            display_ip = str(getattr(asset, 'matched_query_ip', None) or asset.public_ip or asset.previous_public_ip or ip).strip()
            expires_at = getattr(asset, 'actual_expires_at', None)
            expires_text = _format_local_dt(expires_at).split(' ', 1)[0] if expires_at else '未设置'
            provider_status_text = str(getattr(asset, 'provider_status', '') or '').strip()
            if '未附加固定IP' in provider_status_text or '未附加IP' in provider_status_text:
                status_text = '已停止，请尽快续费'
            elif provider_status_text and provider_status_text not in {'unknown', '未知状态'}:
                status_text = provider_status_text
            else:
                status_text = asset.get_status_display() if hasattr(asset, 'get_status_display') else str(getattr(asset, 'status', '') or '未知')
                if status_text in {'unknown', '未知状态', '未知'}:
                    status_text = '状态待同步'
            account_label = str(getattr(asset, 'account_label', '') or '').strip()
            account_text = f'\n账号标签: <code>{escape(account_label)}</code>' if include_start and account_label else ''
            is_owned_asset = bool(user and getattr(asset, 'user_id', None) == user.id)
            is_public_view = bool(not include_start and not is_owned_asset)
            if input_link and (include_start or is_owned_asset) and asset.provider == 'aws_lightsail' and not getattr(asset, 'mtproxy_link', None):
                try:
                    asset = await _save_asset_main_proxy_link(asset.id, None, input_link)
                    logger.info('CLOUD_QUERY_PROXY_LINK_SAVED target=asset asset_id=%s ip=%s port=%s', asset.id, display_ip, input_link.get('port'))
                except Exception as exc:
                    logger.warning('CLOUD_QUERY_PROXY_LINK_SAVE_FAILED target=asset asset_id=%s ip=%s error=%s', getattr(asset, 'id', None), display_ip, exc)
            is_unattached_ip_asset = bool(
                asset.provider == 'aws_lightsail'
                and display_ip
                and (
                    '未附加固定IP' in provider_status_text
                    or '未附加IP' in provider_status_text
                    or 'StaticIp' in str(getattr(asset, 'provider_resource_id', '') or '')
                )
            )
            public_renew_order_id = getattr(asset, 'order_id', None) or 0
            linked_order = await _cloud_asset_order_summary(public_renew_order_id) if public_renew_order_id else {}
            linked_order_status = str(linked_order.get('status') or '').strip()
            linked_order_provider = str(linked_order.get('provider') or asset.provider or '').strip()
            auto_renew_text = '已开启' if linked_order.get('auto_renew_enabled') else ('未开启' if public_renew_order_id else '未绑定订单')
            can_linked_order_operate = bool(
                public_renew_order_id
                and linked_order_provider == 'aws_lightsail'
                and display_ip
                and not is_unattached_ip_asset
                and linked_order_status in {'completed', 'expiring', 'suspended', 'renew_pending'}
            )
            can_admin_asset_reinit = bool(include_start and can_linked_order_operate and (linked_order.get('login_password') or getattr(asset, 'login_password', None)))
            can_admin_asset_config = bool(include_start and can_linked_order_operate and linked_order_status in {'completed', 'expiring', 'suspended'})
            can_admin_asset_change_ip = bool(include_start and can_linked_order_operate and linked_order_status in {'completed', 'expiring', 'suspended'})
            can_user_asset_operate = bool(is_owned_asset and can_linked_order_operate)
            can_user_asset_change_ip = bool(can_user_asset_operate and max(int(linked_order.get('ip_change_quota') or 0), 0) > 0)
            can_asset_renew = bool((is_owned_asset or include_start or is_public_view) and (is_unattached_ip_asset or not public_renew_order_id))
            action_order_id = public_renew_order_id if public_renew_order_id and not is_unattached_ip_asset else 0
            time_label = '删除时间' if is_unattached_ip_asset else '到期时间'
            public_text = f'IP: <code>{escape(display_ip)}</code>\n{time_label}: {expires_text}'
            if is_public_view and is_unattached_ip_asset:
                public_text = f'{public_text}\n状态: {escape(status_text)}'
            action_asset_id = asset.id if can_asset_renew and (is_unattached_ip_asset or not public_renew_order_id) else 0
            results.append({
                'ip': display_ip,
                'text': public_text if is_public_view else f'IP: <code>{escape(display_ip)}</code>\n{time_label}: {expires_text}\n自动续费: {auto_renew_text}\n状态: {escape(status_text)}{account_text}',
                'renewable': bool(can_asset_renew or action_order_id),
                'order_id': action_order_id,
                'asset_id': action_asset_id,
                'start_order_id': public_renew_order_id if include_start else 0,
                'auto_renew_enabled': bool(linked_order.get('auto_renew_enabled')),
                'can_auto_renew': bool(include_start or is_owned_asset),
                'can_change_ip': can_admin_asset_change_ip or can_user_asset_change_ip,
                'can_reinit': can_admin_asset_reinit or can_user_asset_operate,
                'can_config': can_admin_asset_config or can_user_asset_operate,
                'can_support': is_owned_asset,
                '_expires_at': expires_at,
                '_input_index': index,
            })
            continue
        if include_start:
            order = await get_cloud_server_by_ip(ip)
        else:
            order = await get_cloud_server_by_ip_for_user(ip, user.id)
            if not order:
                order = await get_cloud_server_by_ip(ip)
        if not order:
            continue
        display_ip = str(getattr(order, 'matched_query_ip', None) or order.public_ip or order.previous_public_ip or ip).strip()
        is_owned_order = bool(user and getattr(order, 'user_id', None) == user.id)
        is_public_view = bool(not include_start and not is_owned_order)
        if input_link and (include_start or is_owned_order) and order.provider == 'aws_lightsail' and not getattr(order, 'mtproxy_link', None):
            try:
                order = await _save_user_main_proxy_link(order.id, input_link)
                logger.info('CLOUD_QUERY_PROXY_LINK_SAVED target=order order_id=%s ip=%s port=%s', order.id, display_ip, input_link.get('port'))
            except Exception as exc:
                logger.warning('CLOUD_QUERY_PROXY_LINK_SAVE_FAILED target=order order_id=%s ip=%s error=%s', getattr(order, 'id', None), display_ip, exc)
        is_retained_ip = bool(order.status == 'deleted' and getattr(order, 'ip_recycle_at', None) and order.ip_recycle_at > timezone.now() and display_ip and not str(getattr(order, 'instance_id', '') or '').strip())
        is_deleted = order.status in {'deleted', 'deleting', 'expired'} or not display_ip
        if is_deleted and not is_retained_ip:
            continue
        expires_at = order_asset_expiry(order)
        expires_text = _format_local_dt(expires_at).split(' ', 1)[0] if expires_at else '今天到期'
        status_text = '固定 IP 保留中，可续费恢复' if is_retained_ip else '可续费'
        auto_renew_text = '已开启' if getattr(order, 'auto_renew_enabled', False) else '未开启'
        account_label = str(getattr(order, 'account_label', '') or '').strip()
        account_text = f'\n账号标签: <code>{escape(account_label)}</code>' if include_start and account_label else ''
        group_balance_lines = await get_cloud_order_group_balance_lines(order.id)
        balance_block = ''
        if group_balance_lines:
            balance_block = '\n多用户余额:\n' + '\n'.join(escape(line) for line in group_balance_lines)
        can_admin_reinit = bool(include_start and order.provider == 'aws_lightsail' and display_ip and getattr(order, 'login_password', None) and order.status in {'completed', 'expiring', 'renew_pending', 'suspended', 'failed'})
        can_admin_config = bool(include_start and order.provider == 'aws_lightsail' and order.status in {'completed', 'expiring', 'suspended'})
        can_admin_change_ip = bool(include_start and order.provider == 'aws_lightsail' and display_ip and order.status in {'completed', 'expiring', 'suspended'})
        can_user_change_ip = bool(is_owned_order and order.provider == 'aws_lightsail' and display_ip and order.status in {'completed', 'expiring', 'suspended'} and max(int(getattr(order, 'ip_change_quota', 0) or 0), 0) > 0)
        can_user_reinit = bool(is_owned_order and order.provider == 'aws_lightsail' and display_ip and getattr(order, 'login_password', None) and order.status == 'completed')
        can_user_config = bool(is_owned_order and order.provider == 'aws_lightsail' and order.status in {'completed', 'expiring', 'suspended'})
        results.append({
            'ip': display_ip,
            'text': f'IP: <code>{escape(display_ip)}</code>\n到期时间: {expires_text}' if is_public_view else f'IP: <code>{escape(display_ip)}</code>\n到期时间: {expires_text}\n自动续费: {auto_renew_text}\n状态: {status_text}{account_text}{balance_block}',
            'renewable': True,
            'order_id': order.id,
            'asset_id': 0,
            'auto_renew_enabled': bool(getattr(order, 'auto_renew_enabled', False)),
            'can_auto_renew': bool(include_start or is_owned_order),
            'can_change_ip': can_admin_change_ip or can_user_change_ip,
            'can_reinit': can_admin_reinit or can_user_reinit,
            'can_config': can_admin_config or can_user_config,
            'can_support': is_owned_order,
            '_expires_at': expires_at,
            '_input_index': index,
        })
    results.sort(key=lambda item: (
        0 if item['_expires_at'] is None else 1,
        item['_expires_at'] or timezone.now(),
        item['_input_index'],
    ))
    results = [
        {key: value for key, value in item.items() if not key.startswith('_')}
        for item in results
    ]
    if state is not None:
        await state.update_data(cloud_query_results=results)
        await state.set_state(CloudQueryStates.waiting_ip)
    if not results:
        text = _bot_text('bot_query_ip_empty', '🔎 IP查询到期\n\n未查询到可续费的有效 IP 记录。')
        sent = await message.answer(text, reply_markup=order_query_menu())
        logger.info(
            'BOT_MESSAGE_SEND route=cloud_ip_query_empty user_id=%s chat_id=%s reply_to=%s sent_message_id=%s text_preview=%s',
            getattr(message.from_user, 'id', None),
            message.chat.id,
            message.message_id,
            getattr(sent, 'message_id', None),
            text.replace('\n', ' ')[:180],
        )
        return
    page = 1
    per_page = 8
    total_pages = max(1, math.ceil(len(results) / per_page))
    page_items = results[(page - 1) * per_page: page * per_page]
    text = '🔎 IP查询结果\n\n' + '\n\n'.join(item['text'] for item in page_items)
    renewable_items = [{'ip': item['ip'], 'order_id': item.get('order_id') or 0, 'asset_id': item.get('asset_id') or 0, 'start_order_id': item.get('start_order_id') or 0, 'auto_renew_enabled': item.get('auto_renew_enabled'), 'can_auto_renew': item.get('can_auto_renew'), 'can_change_ip': item.get('can_change_ip'), 'can_reinit': item.get('can_reinit'), 'can_config': item.get('can_config'), 'can_support': item.get('can_support')} for item in page_items if item['renewable'] and (item.get('order_id') or item.get('asset_id'))]
    sent = await message.answer(text, reply_markup=cloud_ip_query_result(page_items, renewable_items, page, total_pages, include_start=include_start, include_reinit=include_start), parse_mode='HTML')
    logger.info(
        'BOT_MESSAGE_SEND route=cloud_ip_query_result user_id=%s chat_id=%s reply_to=%s sent_message_id=%s result_count=%s renewable_count=%s page=%s total_pages=%s include_start=%s text_preview=%s',
        getattr(message.from_user, 'id', None),
        message.chat.id,
        message.message_id,
        getattr(sent, 'message_id', None),
        len(results),
        len(renewable_items),
        page,
        total_pages,
        include_start,
        text.replace('\n', ' ')[:180],
    )


@sync_to_async
def _get_site_config_value(key: str, default: str = '') -> str:
    from core.models import SiteConfig
    return SiteConfig.get(key, default)


def _message_text_for_router(message: Message) -> str:
    return str(message.text or message.caption or '').strip()


def _message_content_type(message: Message) -> str:
    raw = getattr(message, 'content_type', None)
    value = getattr(raw, 'value', None) or str(raw or 'text')
    value = value.split('.')[-1].strip().lower()
    return value or 'text'


def _safe_preview_text(text: str, limit: int = 80) -> str:
    value = str(text or '').replace('\n', ' ').strip()
    if len(value) <= limit:
        return value
    return value[:limit] + '...'


def _parse_admin_chat_ids(raw_value: str) -> list[int]:
    raw_text = (
        str(raw_value or '')
        .replace('，', ',')
        .replace('；', ',')
        .replace(';', ',')
        .replace('\n', ',')
        .strip()
    )
    if not raw_text:
        return []
    result: list[int] = []
    seen: set[int] = set()
    for part in raw_text.split(','):
        item = part.strip()
        if not item:
            continue
        try:
            chat_id = int(item)
        except Exception:
            logger.warning('bot_admin_chat_id 存在无法解析的 chat id: %s', item)
            continue
        if chat_id in seen:
            continue
        seen.add(chat_id)
        result.append(chat_id)
    return result


def _reply_markup_button_summary(reply_markup) -> str:
    if not reply_markup:
        return ''
    rows = getattr(reply_markup, 'inline_keyboard', None)
    if rows is None and isinstance(reply_markup, dict):
        rows = reply_markup.get('inline_keyboard')
    labels = []
    for row in rows or []:
        row_labels = []
        for button in row or []:
            label = getattr(button, 'text', None)
            if label is None and isinstance(button, dict):
                label = button.get('text')
            if label:
                row_labels.append(str(label))
        if row_labels:
            labels.append(' | '.join(row_labels))
    return '\n'.join(labels)


async def _copy_user_notice_to_admins(bot: Bot, chat_id: int, text: str, parse_mode: str | None = None, title: str = '通知抄送', reply_markup=None):
    copy_chat_ids = _parse_admin_chat_ids(await _get_site_config_value('bot_notice_copy_chat_ids', ''))
    if not copy_chat_ids:
        return
    button_summary = _reply_markup_button_summary(reply_markup)
    if button_summary:
        button_text = escape(button_summary) if parse_mode == 'HTML' else button_summary
        text = f'{text}\n\n按钮:\n{button_text}'
    copy_text = f'📣 {title}\n用户 TG ID: {chat_id}\n\n{text}'
    for copy_chat_id in copy_chat_ids:
        try:
            await bot.send_message(chat_id=copy_chat_id, text=copy_text, parse_mode=parse_mode)
            logger.info('用户通知抄送成功 title=%s copy_chat_id=%s chat_id=%s text_preview=%s', title, copy_chat_id, chat_id, str(text or '').replace('\n', ' ')[:180])
        except Exception as exc:
            logger.warning('用户通知抄送失败 copy_chat_id=%s chat_id=%s err=%s', copy_chat_id, chat_id, exc)


async def _send_admin_user_action_notice(bot: Bot | None, user, action: str, details: list[tuple[str, object]] | None = None):
    if bot is None:
        return
    copy_chat_ids = _parse_admin_chat_ids(await _get_site_config_value('bot_notice_copy_chat_ids', ''))
    if not copy_chat_ids:
        return
    chat_id = int(getattr(user, 'tg_user_id', None) or getattr(user, 'id', 0) or 0)
    username = _display_username(user)
    first_name = str(getattr(user, 'first_name', '') or '').strip() or '-'
    lines = [
        f'📣 用户{escape(str(action))}',
        '',
        f'用户: {escape(username)}',
        f'昵称: {escape(first_name)}',
        f'TG ID: <code>{chat_id or "-"}</code>',
    ]
    for label, value in details or []:
        lines.append(f'{escape(str(label))}: {escape(str(value if value is not None else "-"))}')
    text = '\n'.join(lines)
    for copy_chat_id in copy_chat_ids:
        try:
            await bot.send_message(chat_id=copy_chat_id, text=text, parse_mode='HTML')
        except Exception as exc:
            logger.warning('用户动作抄送失败 action=%s copy_chat_id=%s chat_id=%s err=%s', action, copy_chat_id, chat_id, exc)


async def _send_user_notice(bot: Bot, chat_id: int, text: str, reply_markup=None, parse_mode: str | None = None, disable_web_page_preview: bool | None = None):
    await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode=parse_mode, disable_web_page_preview=disable_web_page_preview)


async def _copy_user_operation_to_admins(bot: Bot | None, user, action: str, details: list[tuple[str, object]] | None = None):
    await _send_admin_user_action_notice(bot, user, action, details)


def _operation_event_details(event: TelegramObject, route_label: str, chat_id, message_id, callback_data: str | None) -> list[tuple[str, object]]:
    details: list[tuple[str, object]] = [
        ('入口', route_label),
        ('Chat ID', chat_id or '-'),
        ('消息ID', message_id or '-'),
    ]
    if callback_data:
        details.append(('按钮数据', callback_data[:180]))
    if isinstance(event, Message):
        text = _message_text_for_router(event)
        if text:
            details.append(('内容', text[:300]))
        else:
            details.append(('消息类型', _message_content_type(event)))
    return details


async def _notice_copy_recipient_ids() -> set[str]:
    return {str(item) for item in _parse_admin_chat_ids(await _get_site_config_value('bot_notice_copy_chat_ids', ''))}


def _install_notice_copy_wrapper(bot: Bot):
    if getattr(bot, '_notice_copy_wrapper_installed', False):
        return
    original_send_message = bot.send_message
    original_edit_message_text = bot.edit_message_text

    async def _send_message_with_copy(*args, **kwargs):
        result = await original_send_message(*args, **kwargs)
        if _NOTICE_COPY_SENDING.get():
            return result
        chat_id = kwargs.get('chat_id') if 'chat_id' in kwargs else (args[0] if args else None)
        text = kwargs.get('text') if 'text' in kwargs else (args[1] if len(args) > 1 else '')
        try:
            if chat_id is None or int(chat_id) < 0:
                return result
        except (TypeError, ValueError):
            return result
        try:
            copy_recipient_ids = await _notice_copy_recipient_ids()
            if str(text or '').startswith('📣'):
                return result
            token = _NOTICE_COPY_SENDING.set(True)
            try:
                await _copy_user_notice_to_admins(bot, int(chat_id), str(text or ''), parse_mode=kwargs.get('parse_mode'), title='机器人回复', reply_markup=kwargs.get('reply_markup'))
            finally:
                _NOTICE_COPY_SENDING.reset(token)
        except Exception as exc:
            logger.warning('用户结果抄送失败 chat_id=%s err=%s', chat_id, exc)
        return result

    async def _edit_message_text_with_copy(*args, **kwargs):
        result = await original_edit_message_text(*args, **kwargs)
        if _NOTICE_COPY_SENDING.get():
            return result
        text = kwargs.get('text') if 'text' in kwargs else (args[0] if args else '')
        chat_id = kwargs.get('chat_id') if 'chat_id' in kwargs else (args[1] if len(args) > 1 else None)
        if chat_id is None:
            return result
        try:
            if int(chat_id) < 0:
                return result
        except (TypeError, ValueError):
            return result
        try:
            copy_recipient_ids = await _notice_copy_recipient_ids()
            if str(text or '').startswith('📣'):
                return result
            token = _NOTICE_COPY_SENDING.set(True)
            try:
                await _copy_user_notice_to_admins(bot, int(chat_id), str(text or ''), parse_mode=kwargs.get('parse_mode'), title='机器人回复（编辑消息）', reply_markup=kwargs.get('reply_markup'))
            finally:
                _NOTICE_COPY_SENDING.reset(token)
        except Exception as exc:
            logger.warning('用户编辑结果抄送失败 chat_id=%s err=%s', chat_id, exc)
        return result

    bot.send_message = _send_message_with_copy
    bot.edit_message_text = _edit_message_text_with_copy
    bot._notice_copy_wrapper_installed = True


def _is_admin_forward_media_type(content_type: str) -> bool:
    return content_type in {'photo', 'video', 'animation', 'sticker', 'document', 'voice', 'video_note', 'audio'}


def _admin_reply_keyboard(user_tg_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(text='↩️ 回复用户', callback_data=f'adminreply:hint:{user_tg_id}'),
            InlineKeyboardButton(text='🔕 关闭3天转发', callback_data=f'adminreply:mute3d:{user_tg_id}'),
        ]]
    )


async def _is_admin_chat(message: Message) -> bool:
    raw_admin_value = await _get_site_config_value('bot_admin_chat_id', '')
    return int(getattr(message.chat, 'id', 0) or 0) in set(_parse_admin_chat_ids(raw_admin_value))


async def _send_admin_reply_to_link(bot: Bot, message: Message, link) -> bool:
    text = _message_text_for_router(message)
    content_type = _message_content_type(message)
    try:
        if _is_admin_forward_media_type(content_type):
            if text:
                await bot.send_message(chat_id=link.user_chat_id, text='👩‍💻 客服回复')
            sent = await bot.copy_message(
                chat_id=link.user_chat_id,
                from_chat_id=message.chat.id,
                message_id=message.message_id,
            )
        elif text:
            sent = await bot.send_message(chat_id=link.user_chat_id, text=f'👩‍💻 客服回复\n\n{text}')
        else:
            await message.reply('当前消息类型暂不支持转发给用户，请发送文字、图片、视频、文件或语音。')
            return True
        await record_telegram_message(
            tg_user_id=link.user.tg_user_id,
            chat_id=link.user_chat_id,
            message_id=getattr(sent, 'message_id', None),
            direction='out',
            content_type=content_type,
            text=text,
            username=link.user.primary_username,
            first_name=link.user.first_name or '客服',
        )
        await message.reply('✅ 已发送给用户')
        logger.info('ADMIN_REPLY_SENT admin_chat_id=%s admin_message_id=%s user_id=%s user_chat_id=%s sent_message_id=%s type=%s', message.chat.id, message.message_id, link.user_id, link.user_chat_id, getattr(sent, 'message_id', None), content_type)
        return True
    except Exception as exc:
        logger.warning('ADMIN_REPLY_FAILED admin_chat_id=%s admin_message_id=%s user_chat_id=%s error=%s', message.chat.id, message.message_id, getattr(link, 'user_chat_id', None), exc)
        await message.reply(f'❌ 发送失败：{exc}')
        return True


async def _handle_admin_reply_message(bot: Bot, message: Message, state: FSMContext | None = None) -> bool:
    if not await _is_admin_chat(message):
        return False
    text = _message_text_for_router(message)
    reply_to = getattr(message, 'reply_to_message', None)
    link = None
    if state is not None and await state.get_state() == AdminReplyStates.waiting_reply:
        data = await state.get_data()
        link_id = data.get('admin_reply_link_id')
        if link_id:
            link = await get_admin_reply_link_by_id(int(link_id))
            if link:
                handled = await _send_admin_reply_to_link(bot, message, link)
                if handled:
                    await state.clear()
                return handled
        await state.clear()
    if reply_to and getattr(reply_to, 'message_id', None):
        link = await get_admin_reply_link(message.chat.id, reply_to.message_id)
    command_match = re.match(r'^/reply(?:@\w+)?\s+(-?\d+)\s+([\s\S]+)$', text)
    if not link and command_match:
        target_chat_id = int(command_match.group(1))
        reply_text = command_match.group(2).strip()
        if reply_text:
            sent = await bot.send_message(chat_id=target_chat_id, text=f'👩‍💻 客服回复\n\n{reply_text}')
            await record_telegram_message(
                tg_user_id=target_chat_id,
                chat_id=target_chat_id,
                message_id=getattr(sent, 'message_id', None),
                direction='out',
                content_type='text',
                text=reply_text,
                username=None,
                first_name='客服',
            )
            await message.reply('✅ 已发送给用户')
            logger.info('ADMIN_REPLY_COMMAND_SENT admin_chat_id=%s admin_message_id=%s user_chat_id=%s sent_message_id=%s', message.chat.id, message.message_id, target_chat_id, getattr(sent, 'message_id', None))
            return True
        await message.reply('用法：/reply 用户TGID 回复内容；或直接回复我转发给你的用户消息。')
        return True
    if not link:
        if reply_to:
            await message.reply('这条消息没有找到回复通道。请回复机器人转发的“用户消息转发”那条消息，或使用 /reply 用户TGID 回复内容。')
            return True
        return False
    return await _send_admin_reply_to_link(bot, message, link)


async def _forward_plain_text_to_admin(bot: Bot, message: Message):
    raw_admin_value = await _get_site_config_value('bot_admin_chat_id', '')
    admin_chat_ids = _parse_admin_chat_ids(raw_admin_value)
    text = _message_text_for_router(message)
    sender = getattr(message.from_user, 'id', None)
    content_type = _message_content_type(message)
    logger.info(
        '管理员转发开始 sender=%s type=%s raw_admin_value=%r parsed_admin_ids=%s text_preview=%r',
        sender,
        content_type,
        raw_admin_value,
        admin_chat_ids,
        _safe_preview_text(text),
    )
    if not admin_chat_ids:
        logger.warning('管理员转发跳过：未配置有效 chat id raw_admin_value=%r', raw_admin_value)
        return
    if not sender:
        logger.warning('管理员转发跳过：缺少发送者 sender=%s type=%s', sender, content_type)
        return
    chat = getattr(message, 'chat', None)
    chat_id = int(getattr(chat, 'id', 0) or 0)
    if chat_id < 0:
        should_forward_group = await should_forward_telegram_group(
            chat_id=chat_id,
            title=getattr(chat, 'title', None),
            username=getattr(chat, 'username', None),
        )
        if not should_forward_group:
            logger.info('管理员转发跳过：群组通知已关闭 chat_id=%s sender=%s type=%s', chat_id, sender, content_type)
            return
    if not text and not _is_admin_forward_media_type(content_type):
        logger.warning('管理员转发跳过：消息无可转发文本/媒体 sender=%s type=%s', sender, content_type)
        return
    if await is_admin_forward_muted(int(sender)):
        logger.info('管理员转发跳过：用户处于3天静默 sender=%s type=%s', sender, content_type)
        return
    sender_name = getattr(message.from_user, 'first_name', None) or ''
    sender_username = getattr(message.from_user, 'username', None) or ''
    forward_text = (
        '📨 用户消息转发\n\n'
        f'用户TG ID: {sender or "-"}\n'
        f'用户名: {"@" + sender_username if sender_username else "-"}\n'
        f'昵称: {sender_name or "-"}\n'
        f'消息类型: {content_type}\n\n'
        f'内容:\n{text or "[无文本内容]"}'
    )
    success_count = 0
    for admin_chat_id in admin_chat_ids:
        try:
            sent_header = await bot.send_message(chat_id=admin_chat_id, text=forward_text, reply_markup=_admin_reply_keyboard(sender or 0))
            await create_admin_reply_link(
                admin_chat_id=admin_chat_id,
                admin_message_id=sent_header.message_id,
                user_tg_id=sender or 0,
                user_chat_id=message.chat.id,
                user_message_id=message.message_id,
                source_content_type=content_type,
            )
            if _is_admin_forward_media_type(content_type):
                sent_copy = await bot.copy_message(
                    chat_id=admin_chat_id,
                    from_chat_id=message.chat.id,
                    message_id=message.message_id,
                )
                await create_admin_reply_link(
                    admin_chat_id=admin_chat_id,
                    admin_message_id=sent_copy.message_id,
                    user_tg_id=sender or 0,
                    user_chat_id=message.chat.id,
                    user_message_id=message.message_id,
                    source_content_type=content_type,
                )
            success_count += 1
            logger.info('管理员转发成功 sender=%s chat_id=%s header_message_id=%s type=%s', sender, admin_chat_id, getattr(sent_header, 'message_id', None), content_type)
        except Exception as exc:
            logger.warning('管理员转发失败 sender=%s chat_id=%s type=%s error=%s', sender, admin_chat_id, content_type, exc)
            continue
    logger.info('管理员转发结束 sender=%s success=%s total=%s', sender, success_count, len(admin_chat_ids))
    if success_count == 0:
        logger.warning('管理员转发全部失败，配置值=%r', raw_admin_value)


async def _get_cached_custom_regions():
    now = time.monotonic()
    items = _CUSTOM_REGIONS_CACHE.get('items')
    expires_at = float(_CUSTOM_REGIONS_CACHE.get('expires_at') or 0.0)
    if items is not None and expires_at > now:
        return items
    items = await list_custom_regions()
    _CUSTOM_REGIONS_CACHE['items'] = items
    _CUSTOM_REGIONS_CACHE['expires_at'] = now + _CUSTOM_REGIONS_CACHE_TTL
    return items


async def _get_cached_region_plans(region_code: str):
    now = time.monotonic()
    cached = _REGION_PLANS_CACHE.get(region_code)
    if cached and cached[0] > now:
        return cached[1]
    plans = await list_region_plans(region_code)
    _REGION_PLANS_CACHE[region_code] = (now + _REGION_PLANS_CACHE_TTL, plans)
    return plans


async def _get_cached_chat_profile(bot: Bot, user_id: int):
    now = time.monotonic()
    cached = _TG_CHAT_CACHE.get(user_id)
    if cached and cached[0] > now:
        return cached[1]
    chat = await bot.get_chat(user_id)
    profile = {
        'active_usernames': list(getattr(chat, 'active_usernames', None) or []),
        'username': getattr(chat, 'username', None),
        'first_name': getattr(chat, 'first_name', None),
        'chat': chat,
    }
    _TG_CHAT_CACHE[user_id] = (now + _TG_CHAT_CACHE_TTL, profile)
    return profile


def _link_button_for_text(text: str | None):
    from core.button_config import load_button_config

    value = (text or '').strip()
    if not value:
        return None
    for item in load_button_config().get('items', []):
        if item.get('type') == 'link' and item.get('enabled', True) and item.get('label') == value and item.get('url'):
            return item
    return None


def _should_sync_user(user_id: int, username: str | None, first_name: str | None, active_usernames: list[str] | tuple[str, ...] | None) -> bool:
    normalized_usernames = tuple(str(item) for item in (active_usernames or []))
    key = (username, first_name, normalized_usernames)
    now = time.monotonic()
    cached = _USER_SYNC_CACHE.get(user_id)
    if cached and cached[0] > now and cached[1] == key:
        return False
    _USER_SYNC_CACHE[user_id] = (now + _USER_SYNC_CACHE_TTL, key)
    return True


def _callback_route_label(callback_data: str | None) -> str:
    data = callback_data or ''
    exact = {
        'cloud:querymenu': 'cloud.querymenu 到期时间查询菜单',
        'cloud:list': 'cloud.list 代理列表',
        'cloud:autorenewlist': 'cloud.autorenewlist 自动续费列表',
        'cloud:queryip': 'cloud.queryip IP查询到期',
        'profile:orders': 'profile.orders 订单查询入口',
        'profile:orders:cloud': 'profile.orders.cloud 云服务器订单',
        'profile:cart': 'profile.cart 购物车入口',
        'profile:balance_details': 'profile.balance_details 余额明细',
        'profile:reminders': 'profile.reminders 提醒列表',
        'profile:reminders:muteall': 'profile.reminders.mute_all 一键关闭所有提醒',
        'profile:reminders:unmuteall': 'profile.reminders.unmute_all 一键开启全部提醒',
        'profile:reminders:page': 'profile.reminders.page 提醒列表分页',
        'profile:back_to_menu': 'profile.back_to_menu 返回个人中心',
        'profile:back': 'profile.back 返回主菜单',
        'profile:recharge': 'profile.recharge 充值余额',
        'profile:recharges': 'profile.recharges 充值记录',
        'profile:monitors': 'profile.monitors 地址监控',
        'custom:back': 'custom.back 返回主菜单',
        'custom:regions': 'custom.regions 定制地区',
        'custom:regions:more': 'custom.regions.more 更多地区',
        'mon:add': 'monitor.add 添加监控',
        'mon:list': 'monitor.list 监控列表',
        'mon:back': 'monitor.back 返回监控列表',
        'cloud:renewall:confirm': 'cloud.renewall.confirm 全部续费确认',
        'cloud:renewall:pay': 'cloud.renewall.pay 确认全部续费',
        'cart:checkout:balance:USDT': 'cart.checkout.balance.USDT 购物车余额结算',
        'cart:checkout:address:USDT': 'cart.checkout.address.USDT 购物车地址结算',
        'cart:clear': 'cart.clear 清空购物车',
        'back_to_products': 'product.back_to_products 返回商品列表',
        'noop': 'noop 无操作',
    }
    if data in exact:
        return exact[data]
    prefixes = [
        ('cloud:orderdetail:', 'cloud.orderdetail 云订单详情'),
        ('cloud:assetreinitconfirm:', 'cloud.assetreinitconfirm 确认资产重建'),
        ('cloud:assetinit:', 'cloud.assetinit 资产重新安装'),
        ('cloud:aa:', 'cloud.aa 资产操作'),
        ('cloud:assetaction:', 'cloud.assetaction 资产操作'),
        ('cad:', 'cad 人工代理详情短回调'),
        ('csd:', 'csd 云服务器资产详情短回调'),
        ('cloud:ad:', 'cloud.ad 人工代理详情'),
        ('cloud:assetdetail:', 'cloud.assetdetail 人工代理详情'),
        ('cloud:detail:', 'cloud.detail 代理详情'),
        ('cloud:list:page:', 'cloud.list.page 代理列表分页'),
        ('clp:', 'cloud.list.page 代理列表短分页'),
        ('cloud:autorenewlist:all:', 'cloud.autorenewlist.all 自动续费批量开关'),
        ('cloud:autorenewlist:page:', 'cloud.autorenewlist.page 自动续费列表分页'),
        ('cloud:autorenewlist:on:', 'cloud.autorenewlist.on 开启自动续费'),
        ('cloud:autorenewlist:off:', 'cloud.autorenewlist.off 关闭自动续费'),
        ('cloud:queryip:page:', 'cloud.queryip.page IP查询分页'),
        ('poc:', 'profile.orders.cloud.compact 云订单短返回'),
        ('cloud:rp:', 'cloud.rp 续费钱包支付短回调'),
        ('cloud:renewpay:', 'cloud.renewpay 续费钱包支付'),
        ('cloud:renewwallet:', 'cloud.renewwallet 自动续费钱包支付'),
        ('ar:', 'ar 资产续费短回调'),
        ('ac:', 'ac 资产更换IP短回调'),
        ('au:', 'au 资产修改配置短回调'),
        ('ai:', 'ai 资产重新安装短回调'),
        ('arp:', 'arp 未绑定资产续费选套餐短回调'),
        ('cloud:assetrenewplan:', 'cloud.assetrenewplan 未绑定资产续费选套餐'),
        ('rnp:', 'rnp 保留IP续费选套餐短回调'),
        ('cloud:renewplan:', 'cloud.renewplan 保留IP续费选套餐'),
        ('r:', 'r 续费短回调'),
        ('cloud:renew:', 'cloud.renew 续费'),
        ('cloud:start:', 'cloud.start 管理员开机'),
        ('cloud:autorenew:', 'cloud.autorenew 自动续费开关'),
        ('cloud:mute:', 'cloud.mute 关闭提醒'),
        ('im:', 'im 更换IP更多地区短回调'),
        ('ir:', 'ir 更换IP选地区短回调'),
        ('i:', 'i 更换IP短回调'),
        ('cloud:ipregions:more:', 'cloud.ipregions.more 更换IP更多地区'),
        ('cloud:ipregion:', 'cloud.ipregion 更换IP选地区'),
        ('cloud:ip:', 'cloud.ip 更换IP'),
        ('p:', 'p 续费钱包支付超短回调'),
        ('upp:', 'upp 修改配置支付短回调'),
        ('cloud:upgradepay:', 'cloud.upgradepay 修改配置支付'),
        ('u:', 'u 修改配置短回调'),
        ('cloud:upgrade:', 'cloud.upgrade 修改配置'),
        ('ri:', 'ri 重新安装短回调'),
        ('cloud:reinitconfirm:', 'cloud.reinitconfirm 确认重新初始化'),
        ('cloud:reinit:', 'cloud.reinit 重新安装/继续初始化'),
        ('exp:', 'exp 修改到期时间短回调'),
        ('cloud:adminexp:', 'cloud.adminexp 修改到期时间'),
        ('profile:orders:cloud:page:', 'profile.orders.cloud.page 云服务器订单分页'),
        ('profile:orders:cloud:filter:', 'profile.orders.cloud.filter 云服务器订单筛选'),
        ('profile:reminders:ip:', 'profile.reminders.ip IP提醒详情'),
        ('profile:reminders:order:', 'profile.reminders.order 单IP生命周期提醒开关'),
        ('profile:reminders:auto:', 'profile.reminders.auto 单IP自动续费开关'),
        ('profile:reminders:page:', 'profile.reminders.page 提醒列表分页'),
        ('profile:balance_details:filter:', 'profile.balance_details.filter 余额明细筛选'),
        ('support:contact:', 'support.contact 联系客服'),
        ('adminreply:hint:', 'adminreply.hint 回复用户提示'),
        ('adminreply:mute3d:', 'adminreply.mute3d 关闭3天转发'),
        ('custom:region:', 'custom.region 选择地区'),
        ('custom:plan:', 'custom.plan 选择套餐'),
        ('custom:quantitypage:', 'custom.quantitypage 数量页'),
        ('custom:qty:', 'custom.qty 选择数量'),
        ('custom:paypage:', 'custom.paypage 支付页'),
        ('custom:orderpaypage:', 'custom.orderpaypage 订单支付页'),
        ('custom:qtycart:', 'custom.qtycart 加入购物车'),
        ('custom:walletpay:', 'custom.walletpay 钱包补付'),
        ('custom:wallet:', 'custom.wallet 钱包支付币种'),
        ('custom:currency:', 'custom.currency 支付币种'),
        ('custom:balance:', 'custom.balance 钱包支付'),
        ('balance:detail:', 'balance.detail 余额明细详情'),
        ('bdpage:', 'balance.page 余额明细分页'),
        ('rcur:', 'recharge.currency 充值币种'),
        ('rpage:', 'recharge.page 充值分页'),
        ('rdetail:', 'recharge.detail 充值详情'),
        ('cart:remove:', 'cart.remove 删除购物车商品'),
        ('product:', 'product.detail 商品详情'),
        ('ppage:', 'product.page 商品分页'),
        ('qty:', 'product.quantity 选择商品数量'),
        ('pay:', 'product.pay 商品支付'),
        ('order_detail:', 'order.detail 商品订单详情'),
        ('opage:', 'order.page 商品订单分页'),
        ('mon:detail:', 'monitor.detail 监控详情'),
        ('mon:toggle:', 'monitor.toggle 监控开关'),
        ('mon:threshold:', 'monitor.threshold 设置阈值'),
        ('mon:setthr:', 'monitor.set_threshold 选择阈值币种'),
        ('mon:delete:', 'monitor.delete 删除监控'),
        ('mon:txd:', 'monitor.transfer_detail 转账详情'),
        ('mon:resd:', 'monitor.resource_detail 资源详情'),
    ]
    for prefix, label in prefixes:
        if data.startswith(prefix):
            return label
    return 'callback.unknown 未匹配按钮'


def _handler_name(handler) -> str:
    return getattr(handler, '__name__', None) or getattr(getattr(handler, 'callback', None), '__name__', None) or handler.__class__.__name__


class RawUserLoggingMiddleware:
    async def __call__(self, handler, event: TelegramObject, data: dict):
        started_at = time.monotonic()
        user = getattr(event, 'from_user', None)
        handler_label = _handler_name(handler)
        callback_data = getattr(event, 'data', None) if isinstance(event, CallbackQuery) else None
        route_label = _callback_route_label(callback_data) if isinstance(event, CallbackQuery) else f'message.{_message_content_type(event)}' if isinstance(event, Message) else event.__class__.__name__
        chat_id = None
        message_id = None
        if isinstance(event, Message):
            chat_id = getattr(getattr(event, 'chat', None), 'id', None)
            message_id = getattr(event, 'message_id', None)
        elif isinstance(event, CallbackQuery):
            callback_message = getattr(event, 'message', None)
            chat_id = getattr(getattr(callback_message, 'chat', None), 'id', None)
            message_id = getattr(callback_message, 'message_id', None)
        if user and getattr(user, 'id', None):
            logger.info(
                '收到机器人更新：事件=%s 路由=%s 处理器=%s 用户ID=%s 用户名=%s 昵称=%s 会话ID=%s 消息ID=%s 按钮数据=%s 文本=%s',
                event.__class__.__name__,
                route_label,
                handler_label,
                user.id,
                getattr(user, 'username', None),
                getattr(user, 'first_name', None),
                chat_id,
                message_id,
                callback_data,
                (_message_text_for_router(event)[:500] if isinstance(event, Message) else None),
            )
            bot = data.get('bot')
            active_usernames = []
            event_chat = getattr(event, 'chat', None)
            chat_type = getattr(event_chat, 'type', '')
            is_group_chat = str(chat_type) in {'group', 'supergroup', 'channel'}
            chat_username = None if is_group_chat else getattr(user, 'username', None)
            first_name = getattr(user, 'first_name', None)
            if bot and not is_group_chat:
                try:
                    profile = await _get_cached_chat_profile(bot, user.id)
                    active_usernames = profile['active_usernames']
                    chat_username = profile['username'] or chat_username
                    first_name = profile['first_name'] or first_name
                    chat = profile['chat']
                    if user.id == 1457254228:
                        chat_payload = {
                            'id': getattr(chat, 'id', None),
                            'type': getattr(chat, 'type', None),
                            'first_name': getattr(chat, 'first_name', None),
                            'last_name': getattr(chat, 'last_name', None),
                            'username': getattr(chat, 'username', None),
                            'active_usernames': getattr(chat, 'active_usernames', None),
                            'model_extra': getattr(chat, 'model_extra', None),
                        }
                        logger.debug('Telegram get_chat用户对象: user_id=%s payload=%s', user.id, chat_payload)
                except Exception as exc:
                    logger.warning('Telegram get_chat用户对象获取失败: user_id=%s err=%s', user.id, exc)

            if user.id == 1457254228:
                payload = {
                    'id': user.id,
                    'is_bot': getattr(user, 'is_bot', None),
                    'first_name': getattr(user, 'first_name', None),
                    'last_name': getattr(user, 'last_name', None),
                    'full_name': getattr(user, 'full_name', None),
                    'username': getattr(user, 'username', None),
                    'language_code': getattr(user, 'language_code', None),
                    'is_premium': getattr(user, 'is_premium', None),
                    'added_to_attachment_menu': getattr(user, 'added_to_attachment_menu', None),
                    'model_extra': getattr(user, 'model_extra', None),
                }
                logger.debug('原始Telegram用户对象: event=%s payload=%s', event.__class__.__name__, payload)

            if _should_sync_user(user.id, chat_username, first_name, active_usernames):
                await get_or_create_user(user.id, chat_username, first_name, active_usernames)
            if bot and not is_group_chat:
                try:
                    await _copy_user_operation_to_admins(
                        bot,
                        user,
                        '操作',
                        _operation_event_details(event, route_label, chat_id, message_id, callback_data),
                    )
                except Exception as exc:
                    logger.warning('用户操作抄送失败 user_id=%s route=%s err=%s', user.id, route_label, exc)
            if isinstance(event, Message):
                message_text = _message_text_for_router(event)
                chat_id = getattr(event.chat, 'id', user.id)
                message_id = getattr(event, 'message_id', None)
                try:
                    await record_telegram_message(
                        tg_user_id=user.id,
                        chat_id=chat_id,
                        message_id=message_id,
                        direction='in',
                        content_type=_message_content_type(event),
                        text=message_text,
                        username=chat_username,
                        first_name=first_name,
                    )
                except Exception as exc:
                    logger.warning('Telegram聊天记录保存失败: user_id=%s err=%s', user.id, exc)
                try:
                    await record_bot_operation_log(
                        tg_user_id=user.id,
                        chat_id=chat_id,
                        message_id=message_id,
                        action_type='message',
                        action_label='发送消息',
                        payload=message_text or _message_content_type(event),
                        username=chat_username,
                        first_name=first_name,
                    )
                except Exception as exc:
                    logger.warning('机器人操作日志保存失败: user_id=%s err=%s', user.id, exc)
            elif isinstance(event, CallbackQuery):
                callback_message = getattr(event, 'message', None)
                try:
                    await record_bot_operation_log(
                        tg_user_id=user.id,
                        chat_id=getattr(getattr(callback_message, 'chat', None), 'id', None),
                        message_id=getattr(callback_message, 'message_id', None),
                        action_type='callback',
                        action_label=route_label,
                        payload=getattr(event, 'data', '') or '',
                        username=chat_username,
                        first_name=first_name,
                    )
                except Exception as exc:
                    logger.warning('机器人操作日志保存失败: user_id=%s err=%s', user.id, exc)
        bot = data.get('bot')
        if bot and isinstance(event, Message):
            try:
                if await _handle_admin_reply_message(bot, event, data.get('state')):
                    logger.info(
                        '管理员回复已处理：用户ID=%s 会话ID=%s 消息ID=%s 路由=%s',
                        getattr(user, 'id', None),
                        chat_id,
                        message_id,
                        route_label,
                    )
                    return None
            except Exception as exc:
                logger.warning('管理员回复处理失败：用户ID=%s 会话ID=%s 消息ID=%s 错误=%s', getattr(user, 'id', None), chat_id, message_id, exc)
        try:
            result = await handler(event, data)
            logger.info(
                '机器人更新处理完成：事件=%s 路由=%s 处理器=%s 用户ID=%s 会话ID=%s 消息ID=%s 按钮数据=%s 耗时=%.1f毫秒',
                event.__class__.__name__,
                route_label,
                handler_label,
                getattr(user, 'id', None),
                chat_id,
                message_id,
                callback_data,
                (time.monotonic() - started_at) * 1000,
            )
            return result
        except Exception as exc:
            logger.exception(
                '机器人更新处理失败：事件=%s 路由=%s 处理器=%s 用户ID=%s 会话ID=%s 消息ID=%s 按钮数据=%s 耗时=%.1f毫秒 错误=%s',
                event.__class__.__name__,
                route_label,
                handler_label,
                getattr(user, 'id', None),
                chat_id,
                message_id,
                callback_data,
                (time.monotonic() - started_at) * 1000,
                exc,
            )
            raise


async def _safe_edit_text(message: Message, text: str, **kwargs):
    reply_markup = kwargs.get('reply_markup')
    if reply_markup is not None and not isinstance(reply_markup, (InlineKeyboardMarkup, dict)):
        logger.warning('BOT_MESSAGE_EDIT_DROP_INVALID_MARKUP chat_id=%s message_id=%s markup_type=%s text_preview=%s', getattr(getattr(message, 'chat', None), 'id', None), getattr(message, 'message_id', None), type(reply_markup).__name__, str(text or '').replace('\n', ' ')[:180])
        kwargs.pop('reply_markup', None)
    try:
        sent = await message.edit_text(text, **kwargs)
        logger.info('BOT_MESSAGE_EDIT chat_id=%s message_id=%s text_preview=%s', getattr(getattr(message, 'chat', None), 'id', None), getattr(message, 'message_id', None), str(text or '').replace('\n', ' ')[:180])
        bot = getattr(message, 'bot', None)
        chat_id = getattr(getattr(message, 'chat', None), 'id', None)
        if bot and chat_id:
            try:
                await _copy_user_notice_to_admins(bot, int(chat_id), str(text or ''), parse_mode=kwargs.get('parse_mode'), title='机器人回复（编辑消息）', reply_markup=kwargs.get('reply_markup'))
            except Exception as exc:
                logger.warning('用户编辑结果抄送失败 chat_id=%s err=%s', chat_id, exc)
        return sent
    except TelegramBadRequest as exc:
        error_text = str(exc).lower()
        if 'message is not modified' in error_text:
            logger.info('BOT_MESSAGE_EDIT_NOT_MODIFIED chat_id=%s message_id=%s text_preview=%s', getattr(getattr(message, 'chat', None), 'id', None), getattr(message, 'message_id', None), str(text or '').replace('\n', ' ')[:180])
            return None
        if "message can't be edited" in error_text or 'message to edit not found' in error_text:
            logger.warning('BOT_MESSAGE_EDIT_UNAVAILABLE chat_id=%s message_id=%s error=%s text_preview=%s', getattr(getattr(message, 'chat', None), 'id', None), getattr(message, 'message_id', None), exc, str(text or '').replace('\n', ' ')[:180])
            return None
        logger.warning('BOT_MESSAGE_EDIT_FAILED chat_id=%s message_id=%s error=%s text_preview=%s', getattr(getattr(message, 'chat', None), 'id', None), getattr(message, 'message_id', None), exc, str(text or '').replace('\n', ' ')[:180])
        raise


async def _safe_callback_answer(callback: CallbackQuery, *args, **kwargs):
    try:
        return await callback.answer(*args, **kwargs)
    except TelegramBadRequest as exc:
        message = str(exc).lower()
        if 'query is too old' in message or 'query id is invalid' in message or 'query_id_invalid' in message or 'response timeout expired' in message or 'query is already answered' in message:
            return None
        raise
    except TelegramNetworkError as exc:
        logger.warning('TELEGRAM_CALLBACK_ANSWER_NETWORK_ERROR callback_data=%s error=%s', getattr(callback, 'data', None), exc)
        return None


def _public_cloud_error_text(error) -> str:
    raw = str(error or '')
    if not raw:
        return '任务暂未完成，请稍后在查询中心查看，或联系人工客服。'
    sensitive_markers = ('account', '账号', 'instance', '实例', 'server_name', 'instance_id', 'arn:', 'aws+', 'aliyun+', 'CloudAccount', 'lightsail', 'aliyun', '阿里云', 'region', 'ap-', 'cn-', 'eu-', 'us-')
    if any(marker.lower() in raw.lower() for marker in sensitive_markers) or re.search(r'\b(?:aws|ali)\b', raw, flags=re.IGNORECASE):
        return '云服务器任务执行失败，内部诊断信息已记录；请联系人工客服处理。'
    text = re.sub(r'aws\+[^\s，；,。)）]+', '云账号', raw)
    text = re.sub(r'aliyun\+[^\s，；,。)）]+', '云账号', text)
    text = re.sub(r'\b\d{12,}\b', '***', text)
    return text[:180]


def _public_cloud_stage_text(stage: str) -> str:
    text = str(stage or '').strip()
    if not text:
        return '正在执行云服务器初始化'
    text = re.sub(r'（账号[^）]*）', '', text)
    text = re.sub(r'\(账号[^)]*\)', '', text)
    text = re.sub(r'账号\s*[^，。；,\s]+', '云账号', text)
    text = re.sub(r'aws\+[^\s，；,。)）]+', '云账号', text)
    text = re.sub(r'aliyun\+[^\s，；,。)）]+', '云账号', text)
    text = re.sub(r'\baws\s*lightsail\b|\blightsail\b|\baws\b', '云服务器', text, flags=re.IGNORECASE)
    text = re.sub(r'\b(?:aliyun|ali)\b|阿里云', '云服务器', text, flags=re.IGNORECASE)
    text = re.sub(r'\b[a-z]{2}-[a-z]+-[a-z]+-\d\b', '节点', text)
    text = re.sub(r'\b(?:cn|ap|eu|us)-[a-z0-9-]+\b', '节点', text)
    text = re.sub(r'\b\d{12,}\b', '***', text)
    if any(marker in text for marker in ['实例名', 'instance_id', 'server_name', 'provider', 'region']):
        return '正在处理云服务器资源'
    if '创建 云服务器 实例' in text or '创建云服务器实例' in text:
        return '正在创建云服务器'
    return text or '正在执行云服务器初始化'


def _public_region_text(value) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    code_names = {
        'ap-southeast-1': '新加坡',
        'cn-hongkong': '香港',
        'ap-northeast-1': '日本',
        'ap-northeast-2': '韩国',
        'us-east-1': '美国',
    }
    if text in code_names:
        return code_names[text]
    if re.fullmatch(r'[a-z]{2}-[a-z]+-[a-z]+-\d', text) or re.fullmatch(r'(?:cn|ap|eu|us)-[a-z0-9-]+', text):
        return ''
    text = re.sub(r'\b(?:aws|lightsail|aliyun|ali)\b|AWS|Lightsail|阿里云', '', text, flags=re.IGNORECASE)
    return re.sub(r'\s+', ' ', text).strip(' -_/')


def _public_region_line(value) -> str:
    text = _public_region_text(value)
    return f'地区: {escape(text)}\n' if text else ''


async def _safe_remove_inline_keyboard(message: Message | None):
    if not message:
        return None
    try:
        return await message.edit_reply_markup(reply_markup=None)
    except TelegramBadRequest as exc:
        if 'message is not modified' in str(exc).lower():
            return None
        logger.warning('TELEGRAM_REMOVE_INLINE_KEYBOARD_FAILED message_id=%s error=%s', getattr(message, 'message_id', None), exc)
        return None
    except TelegramNetworkError as exc:
        logger.warning('TELEGRAM_REMOVE_INLINE_KEYBOARD_NETWORK_ERROR message_id=%s error=%s', getattr(message, 'message_id', None), exc)
        return None


def _telegram_socks_link_from_raw(link: str) -> str:
    try:
        parsed = urlparse(str(link or ''))
        if parsed.scheme != 'socks5' or not parsed.hostname or not parsed.port:
            return str(link or '')
        username = unquote(parsed.username or '')
        password = unquote(parsed.password or '')
        return f'tg://socks?server={parsed.hostname}&port={parsed.port}&user={username}&pass={password}'
    except Exception:
        return str(link or '')


def _cloud_server_created_text(order, port: int | None = None, title: str | None = None) -> str:
    mtproxy_link = getattr(order, 'mtproxy_link', '') or ''
    share_link = ''
    extra_links = []
    seen_links = set()
    public_ip = getattr(order, 'public_ip', '') or ''
    actual_port = port or getattr(order, 'mtproxy_port', '') or ''
    raw_secret = getattr(order, 'mtproxy_secret', '') or ''
    display_secret = ''
    def _link_port(link: str) -> str:
        try:
            return (parse_qs(urlparse(str(link or '')).query).get('port') or [''])[0]
        except Exception:
            return ''

    def add_extra_link(link: str):
        link = str(link or '').strip().strip('"\'，。')
        if link.startswith('socks5://'):
            link = _telegram_socks_link_from_raw(link)
        if link.startswith(('tg://proxy?', 'https://t.me/proxy?')) and _link_port(link) == str(get_mtproxy_port_plan(actual_port or MTPROXY_DEFAULT_PORT)['socks5']):
            return
        if link and link not in seen_links:
            extra_links.append(link)
            seen_links.add(link)

    for item in getattr(order, 'proxy_links', None) or []:
        link = item.get('url') if isinstance(item, dict) else ''
        if link:
            add_extra_link(link)
            if not mtproxy_link and str(link).startswith(('tg://proxy?', 'https://t.me/proxy?')):
                mtproxy_link = link
    note = getattr(order, 'provision_note', '') or ''
    for line in note.splitlines():
        if line.startswith('TG链接: '):
            mtproxy_link = mtproxy_link or line.split(': ', 1)[1].strip()
        elif line.startswith('分享链接: '):
            share_link = line.split(': ', 1)[1].strip()
        elif 'https://t.me/proxy?' in line and not share_link:
            share_link = line[line.find('https://t.me/proxy?'):].strip()
        if 'tg://proxy?' in line:
            link = line[line.find('tg://proxy?'):].strip().strip('"\',，。')
            add_extra_link(link)
            if not mtproxy_link:
                mtproxy_link = link
        if 'socks5://' in line:
            add_extra_link(line[line.find('socks5://'):].strip())
    has_socks5_link = any(str(link).startswith(('socks5://', 'tg://socks?')) for link in extra_links)
    if not has_socks5_link and 'SOCKS5:' in note and public_ip and raw_secret:
        socks5_secret = _normalize_mtproxy_core_secret(raw_secret) or raw_secret
        socks5_port = get_mtproxy_port_plan(actual_port or MTPROXY_DEFAULT_PORT)['socks5']
        port_match = re.search(r'SOCKS5:\s*[^\n]*?端口\s*(\d+)', note)
        if port_match:
            socks5_port = int(port_match.group(1))
        add_extra_link(f'socks5://{socks5_secret}:{socks5_secret}@{public_ip}:{socks5_port}')
    one_click_link = mtproxy_link or share_link or '-'
    if 'secret=' in one_click_link:
        display_secret = one_click_link.split('secret=', 1)[1].split('&', 1)[0].strip()
    elif mtproxy_link and 'secret=' in mtproxy_link:
        display_secret = mtproxy_link.split('secret=', 1)[1].split('&', 1)[0].strip()
    else:
        display_secret = raw_secret
    lines = [title or _bot_text('bot_cloud_create_success', '✅ 云服务器创建完成')]
    lines.append(f'端口: <code>{escape(str(actual_port or "-"))}</code>')
    lines.append(f'IP: <code>{escape(public_ip or "-")}</code>')
    lines.append(f'密钥: <code>{escape(display_secret or "-")}</code>')
    lines.append(f'一键链接: {escape(one_click_link)}')
    additional_links = [link for link in extra_links if link != mtproxy_link and link != share_link]
    if additional_links:
        lines.append('')
        lines.append('备用链路:')
        socks5_links = [link for link in additional_links if str(link).startswith(('socks5://', 'tg://socks?'))]
        other_links = [link for link in additional_links if not str(link).startswith(('socks5://', 'tg://socks?'))]
        for link in socks5_links:
            lines.append(f'SOCKS5: {escape(link)}')
        for index, link in enumerate(other_links[:8], start=1):
            lines.append(f'{index}. {escape(link)}')
    lines.append('')
    lines.append(_cloud_order_plan_text(order))
    return '\n'.join(lines)


async def _initialize_proxy_asset_and_notify(bot: Bot, chat_id: int, user_id: int, asset_id: int):
    try:
        logger.info('同步资产代理初始化任务开始: chat_id=%s user_id=%s asset_id=%s', chat_id, user_id, asset_id)
        asset, err = await initialize_proxy_asset(asset_id, user_id)
        if err:
            error_text = f'❌ 同步资产代理初始化失败\n\nIP: {getattr(asset, "public_ip", None) or getattr(asset, "previous_public_ip", None) or "未分配"}\n原因: {_public_cloud_error_text(err)}\n\n不需要提醒可点击下方关闭提醒；如需处理请联系人工客服。'
            _log_bot_cloud_notice('asset_init_failed', chat_id=chat_id, order=asset or type('AssetNotice', (), {'id': asset_id, 'order_no': None, 'public_ip': None, 'previous_public_ip': None, 'status': 'failed'})(), text=error_text, keyboard='cloud_lifecycle_notice_actions')
            await _send_user_notice(bot, chat_id, error_text, reply_markup=_cloud_notice_keyboard_for_order(getattr(asset, 'order_id', None) or asset_id, 'cloud_asset_init_failed'))
            logger.warning('同步资产代理初始化失败: chat_id=%s user_id=%s asset_id=%s error=%s', chat_id, user_id, asset_id, err)
            return
        success_text = '✅ 同步资产代理初始化完成\n\n' + _cloud_server_created_text(asset, getattr(asset, 'mtproxy_port', None))
        _log_bot_cloud_notice('asset_init_completed', chat_id=chat_id, order=asset, text=success_text, keyboard='cloud_lifecycle_notice_actions')
        await _send_user_notice(bot, chat_id, success_text, reply_markup=_cloud_notice_keyboard_for_order(getattr(asset, 'order_id', None) or asset_id, 'cloud_asset_init_completed'), parse_mode='HTML', disable_web_page_preview=True)
        logger.info('同步资产代理初始化完成: chat_id=%s user_id=%s asset_id=%s ip=%s', chat_id, user_id, asset_id, getattr(asset, 'public_ip', None))
    except Exception as exc:
        logger.exception('同步资产代理初始化异常: chat_id=%s user_id=%s asset_id=%s error=%s', chat_id, user_id, asset_id, exc)
        await _send_user_notice(bot, chat_id, f'❌ 同步资产代理初始化任务异常\n错误: {_public_cloud_error_text(exc)}', reply_markup=main_menu())


async def _cloud_task_progress_reporter(bot: Bot, chat_id: int, order_id: int, action_label: str, interval_seconds: int = 1):
    started_at = timezone.now()
    status_message = None
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            order = await get_cloud_order(order_id)
            if not order or order.status in {'completed', 'failed', 'deleted', 'cancelled', 'expired'}:
                return
            elapsed_seconds = max(1, int((timezone.now() - started_at).total_seconds()))
            progress = get_provision_progress(order_id)
            stage = _public_cloud_stage_text(progress.get('stage') or '') or '正在执行 BBR/MTProxy 安装或云资源同步'
            stage_started_at = progress.get('stage_started_at')
            stage_elapsed_text = ''
            if stage_started_at:
                stage_elapsed = max(0, int((timezone.now() - stage_started_at).total_seconds()))
                stage_elapsed_text = f'\n本阶段已运行: 约 {stage_elapsed} 秒'
            if not progress:
                if not getattr(order, 'public_ip', None):
                    stage = '正在创建云服务器并等待公网 IP'
                elif order.status == 'provisioning':
                    stage = '服务器已创建，正在执行初始化脚本'
            text = (
                f'⏳ 云服务器{action_label}仍在执行中\n'
                f'订单号: {getattr(order, "order_no", "-") or "-"}\n'
                f'当前阶段: {stage}{stage_elapsed_text}\n'
                f'总计已等待: 约 {elapsed_seconds} 秒\n'
                '请不要重复点击按钮，完成后我会自动发送结果。'
            )
            if status_message is None:
                status_message = await bot.send_message(chat_id=chat_id, text=text)
            else:
                await _safe_edit_text(status_message, text)
            logger.info('云服务器后台任务活跃提示: chat_id=%s order_id=%s action=%s elapsed_seconds=%s status=%s stage=%s', chat_id, order_id, action_label, elapsed_seconds, getattr(order, 'status', None), stage)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning('云服务器后台任务活跃提示失败: chat_id=%s order_id=%s error=%s', chat_id, order_id, exc)


def _cloud_notice_keyboard_for_order(order_id: int, context: str):
    return cloud_lifecycle_notice_actions(order_id, context)


def _log_bot_cloud_notice(event: str, *, chat_id: int, order, text: str, keyboard: str):
    ip = str(getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '').strip()
    logger.info(
        'BOT_CLOUD_NOTICE_SEND event=%s chat_id=%s order_id=%s order_no=%s ip=%s status=%s keyboard=%s has_ip=%s has_mute_button=%s has_support_button=%s text_preview=%s',
        event,
        chat_id,
        getattr(order, 'id', None),
        getattr(order, 'order_no', None),
        ip or '-',
        getattr(order, 'status', None),
        keyboard,
        bool(ip),
        True,
        True,
        str(text or '').replace('\n', ' ')[:240],
    )


async def _provision_cloud_server_and_notify(bot: Bot, chat_id: int, order_id: int, port: int, retry_only: bool = False):
    action_label = '重试初始化' if retry_only else '创建/重建'
    progress_task = None
    order_key = int(order_id)
    if order_key in _CLOUD_PROVISION_INFLIGHT:
        logger.info('云服务器后台创建任务重复提交已忽略: chat_id=%s order_id=%s retry_only=%s', chat_id, order_id, retry_only)
        await _send_user_notice(bot, chat_id, '⏳ 这台服务器的创建/恢复任务正在执行中，请等待当前任务完成。')
        return
    _CLOUD_PROVISION_INFLIGHT.add(order_key)
    try:
        logger.info('云服务器后台创建任务开始: chat_id=%s order_id=%s port=%s retry_only=%s', chat_id, order_id, port, retry_only)
        progress_task = asyncio.create_task(_cloud_task_progress_reporter(bot, chat_id, order_id, action_label))
        provisioned = await (reprovision_cloud_server_bootstrap(order_id) if retry_only else provision_cloud_server(order_id))
        if provisioned and provisioned.status == 'completed':
            if getattr(provisioned, 'replacement_for_id', None):
                success_text = _cloud_server_created_text(provisioned, port, title='✅ 服务器重建完成，固定 IP 已迁移\n\n下面是新的代理链接，请直接复制使用。')
            else:
                success_text = _cloud_server_created_text(provisioned, port)
            if retry_only:
                success_text = '✅ 云服务器重试初始化完成\n\n' + success_text.removeprefix('✅ 云服务器创建完成\n')
            _log_bot_cloud_notice('provision_completed', chat_id=chat_id, order=provisioned, text=success_text, keyboard='cloud_lifecycle_notice_actions')
            await _send_user_notice(bot, chat_id, success_text, reply_markup=_cloud_notice_keyboard_for_order(provisioned.id, 'cloud_provision_completed'), parse_mode='HTML', disable_web_page_preview=True)
            logger.info('云服务器后台创建任务完成: chat_id=%s order_id=%s status=%s retry_only=%s ip=%s', chat_id, order_id, provisioned.status, retry_only, getattr(provisioned, 'public_ip', None) or getattr(provisioned, 'previous_public_ip', None))
            return
        current_status = provisioned.get_status_display() if hasattr(provisioned, 'get_status_display') else getattr(provisioned, 'status', '未知')
        action_label = '重试初始化' if retry_only else '创建'
        current_ip = getattr(provisioned, 'public_ip', None) or getattr(provisioned, 'previous_public_ip', None) or '未分配'
        incomplete_text = _bot_text_format('bot_async_task_incomplete', '⚠️ 云服务器{action_label}暂未完成\n\nIP: {ip}\n订单号: {order_no}\n当前状态: {current_status}\n\n请稍后在查询中心查看；不需要提醒可点击下方关闭提醒。', action_label=action_label, ip=current_ip, order_no=getattr(provisioned, 'order_no', '-') or '-', current_status=current_status)
        _log_bot_cloud_notice('provision_incomplete', chat_id=chat_id, order=provisioned, text=incomplete_text, keyboard='cloud_lifecycle_notice_actions')
        await _send_user_notice(bot, chat_id, incomplete_text, reply_markup=_cloud_notice_keyboard_for_order(order_id, 'cloud_provision_incomplete'))
        logger.warning('云服务器后台创建任务未完成: chat_id=%s order_id=%s status=%s retry_only=%s', chat_id, order_id, current_status, retry_only)
    except Exception as exc:
        logger.exception('云服务器后台创建任务异常: chat_id=%s order_id=%s retry_only=%s error=%s', chat_id, order_id, retry_only, exc)
        action_label = '重试初始化' if retry_only else '创建'
        error_text = _bot_text_format('bot_async_task_error', '❌ 云服务器{action_label}任务异常\n\nIP: {ip}\n错误: {error}\n\n不需要提醒可点击下方关闭提醒；如需处理请联系人工客服。', action_label=action_label, ip='未分配', error=_public_cloud_error_text(exc))
        _log_bot_cloud_notice('provision_error', chat_id=chat_id, order=type('OrderNotice', (), {'id': order_id, 'order_no': None, 'public_ip': None, 'previous_public_ip': None, 'status': 'error'})(), text=error_text, keyboard='cloud_lifecycle_notice_actions')
        await _send_user_notice(bot, chat_id, error_text, reply_markup=_cloud_notice_keyboard_for_order(order_id, 'cloud_provision_error'))
    finally:
        if progress_task:
            progress_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await progress_task
        _CLOUD_PROVISION_INFLIGHT.discard(order_key)


def _renew_balance_change_text(balance_change: dict | None) -> str:
    if not balance_change:
        return ''
    currency = balance_change.get('currency') or 'USDT'
    amount = balance_change.get('amount')
    before = balance_change.get('before')
    after = balance_change.get('after')
    if amount is None or before is None or after is None:
        return ''
    return (
        f'扣款金额: {fmt_pay_amount(amount)} {currency}\n'
        f'扣款前余额: {fmt_pay_amount(before)} {currency}\n'
        f'扣款后余额: {fmt_pay_amount(after)} {currency}'
    )


async def _cloud_renewal_postcheck_and_notify(bot: Bot, chat_id: int, order_id: int, balance_change: dict | None = None):
    try:
        await _send_user_notice(bot, chat_id, '🔎 续费已完成，正在检查服务器运行状态和 MTProxy 链路。')
        checked, err = await run_cloud_server_renewal_postcheck(order_id)
        if _requires_recovery_provision(checked):
            await _send_user_notice(bot, chat_id, '🛠 固定 IP 保留期续费已进入自动恢复流程。\n系统会保持旧 IP / 旧端口 / 旧密钥不变，完成后自动发送代理链接。')
            asyncio.create_task(_provision_cloud_server_and_notify(bot, chat_id, checked.id, checked.mtproxy_port or MTPROXY_DEFAULT_PORT))
            return
        if err:
            await _send_user_notice(bot, chat_id, f'⚠️ 续费后巡检发现异常，已记录并尝试修复。\n订单号: {getattr(checked, "order_no", "-") or "-"}\n请稍后再查看代理状态，或联系人工客服。')
            return
        balance_text = _renew_balance_change_text(balance_change)
        plan_text = _cloud_order_plan_text(checked, include_warnings=False) if checked else ''
        await _send_user_notice(
            bot,
            chat_id,
            '\n'.join(filter(None, [
                f'IP: {_cloud_order_ip_text(checked)}',
                plan_text,
                balance_text,
            ])),
        )
    except Exception as exc:
        logger.exception('云服务器续费后巡检通知任务异常: chat_id=%s order_id=%s error=%s', chat_id, order_id, exc)
        with contextlib.suppress(Exception):
            await _send_user_notice(bot, chat_id, '⚠️ 续费已完成，但续费后巡检通知失败；后台已记录错误，请稍后在查询中心查看状态。')


def _requires_recovery_provision(order) -> bool:
    return bool(
        order
        and (getattr(order, 'replacement_for_id', None) or is_cloud_asset_renewal_order(order))
        and getattr(order, 'status', None) in {'paid', 'provisioning', 'failed'}
    )


def _is_supported_payment_currency(currency: str | None) -> bool:
    return str(currency or '').upper() in {'USDT', 'TRX'}


def _parse_custom_cloud_quantity(raw_quantity) -> int | None:
    try:
        quantity = int(raw_quantity)
    except (TypeError, ValueError):
        return None
    if _CUSTOM_CLOUD_MIN_QUANTITY <= quantity <= _CUSTOM_CLOUD_MAX_QUANTITY:
        return quantity
    return None


def _consume_callback_once(key: str, ttl_seconds: int = _CALLBACK_ONCE_TTL) -> bool:
    now = time.time()
    expired = [item_key for item_key, expires_at in _CALLBACK_ONCE_KEYS.items() if expires_at <= now]
    for item_key in expired[:200]:
        _CALLBACK_ONCE_KEYS.pop(item_key, None)
    if key in _CALLBACK_ONCE_KEYS and _CALLBACK_ONCE_KEYS[key] > now:
        return False
    _CALLBACK_ONCE_KEYS[key] = now + ttl_seconds
    return True


async def _create_cloud_order_and_notify(bot: Bot, chat_id: int, user_id: int, plan_id: int, quantity: int, currency: str, plan_name: str, region_name: str):
    try:
        logger.info('云服务器后台建单任务开始: chat_id=%s user_id=%s plan_id=%s quantity=%s currency=%s', chat_id, user_id, plan_id, quantity, currency)
        order = await create_cloud_server_order(user_id, plan_id, currency, quantity)
        receive_address = _receive_address()
        text = (
            '🧾 订单详情\n\n'
            f'{_public_region_line(region_name)}'
            f'套餐: {plan_name}\n'
            f'数量: {order.quantity}\n'
            f'支付金额: {fmt_pay_amount(order.pay_amount or order.total_amount)} {order.currency or currency}\n'
            f'支付地址: <code>{escape(receive_address)}</code>\n'
            '订单 5 分钟有效，请在有效期内完成支付。\n\n'
            f'系统已开始自动监控 {order.currency or currency} 到账，检测到支付成功后会自动进入后续流程。'
        )
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=custom_currency_keyboard(None, None, None, order.id), parse_mode='HTML', disable_web_page_preview=True)
        await _send_admin_user_action_notice(bot, type('UserNotice', (), {'tg_user_id': chat_id, 'username': None, 'first_name': ''})(), '购买', [
            ('订单号', order.order_no),
            ('套餐', plan_name),
            ('节点', _public_region_text(region_name) or '-'),
            ('数量', order.quantity),
            ('金额', f'{fmt_pay_amount(order.pay_amount or order.total_amount)} {order.currency}'),
        ])
        logger.info('云服务器后台建单任务完成: chat_id=%s user_id=%s order_id=%s order=%s currency=%s total=%s pay_amount=%s', chat_id, user_id, order.id, order.order_no, order.currency, order.total_amount, order.pay_amount)
    except Exception as exc:
        logger.exception('云服务器后台建单任务异常: chat_id=%s user_id=%s plan_id=%s quantity=%s currency=%s error=%s', chat_id, user_id, plan_id, quantity, currency, exc)
        await bot.send_message(chat_id=chat_id, text=_bot_text_format('bot_create_order_failed', '❌ 创建订单失败，请稍后重试。\n错误: {error}', error=_public_cloud_error_text(exc)), reply_markup=main_menu())


async def _buy_cloud_server_with_balance_and_notify(bot: Bot, chat_id: int, user_id: int, plan_id: int, quantity: int, currency: str):
    try:
        logger.info('云服务器后台钱包直付任务开始: chat_id=%s user_id=%s plan_id=%s quantity=%s currency=%s', chat_id, user_id, plan_id, quantity, currency)
        order, err = await buy_cloud_server_with_balance(user_id, plan_id, currency, quantity)
        if err:
            logger.warning('云服务器后台钱包直付失败: chat_id=%s user_id=%s plan_id=%s quantity=%s currency=%s error=%s', chat_id, user_id, plan_id, quantity, currency, err)
            await bot.send_message(
                chat_id=chat_id,
                text=f"{_bot_text('bot_custom_balance_insufficient', '❌ 余额不足，请先充值')}\n\n当前支付币种: {currency}",
                reply_markup=wallet_recharge_prompt_menu(f'custom:quantitypage:{plan_id}:{quantity}', '🔙 返回数量'),
            )
            return
        orders = await prepare_cloud_server_order_instances(order.id, user_id, MTPROXY_DEFAULT_PORT)
        task_count = len(orders)
        await bot.send_message(
            chat_id=chat_id,
            text=(
                '✅ 钱包支付成功\n\n'
                f'{_public_region_line(order.region_name)}'
                f'套餐: {order.plan_name}\n'
                f'数量: {order.quantity}\n'
                f'支付金额: {fmt_pay_amount(order.pay_amount)} {order.currency}\n'
                f'端口: {MTPROXY_DEFAULT_PORT}\n\n'
                f'{task_count or 1} 台服务器创建任务已提交，完成后会自动发送创建结果。'
            ),
            reply_markup=main_menu(),
        )
        await _send_admin_user_action_notice(bot, type('UserNotice', (), {'tg_user_id': chat_id, 'username': None, 'first_name': ''})(), '购买', [
            ('订单号', order.order_no),
            ('套餐', order.plan_name),
            ('节点', _public_region_text(order.region_name) or '-'),
            ('数量', order.quantity),
            ('金额', f'{fmt_pay_amount(order.pay_amount)} {order.currency}'),
            ('支付方式', '钱包余额'),
        ])
        for item in orders:
            asyncio.create_task(_provision_cloud_server_and_notify(bot, chat_id, item.id, item.mtproxy_port or MTPROXY_DEFAULT_PORT))
        logger.info('云服务器后台钱包直付任务完成: chat_id=%s user_id=%s order_id=%s order=%s currency=%s qty=%s pay_amount=%s tasks=%s', chat_id, user_id, order.id, order.order_no, currency, order.quantity, order.pay_amount, task_count)
    except Exception as exc:
        logger.exception('云服务器后台钱包直付任务异常: chat_id=%s user_id=%s plan_id=%s quantity=%s currency=%s error=%s', chat_id, user_id, plan_id, quantity, currency, exc)
        await bot.send_message(chat_id=chat_id, text=_bot_text_format('bot_wallet_pay_failed', '❌ 钱包支付失败，请稍后重试。\n错误: {error}', error=_public_cloud_error_text(exc)), reply_markup=main_menu())


async def _pay_cloud_server_order_with_balance_and_notify(bot: Bot, chat_id: int, user_id: int, order_id: int, currency: str):
    try:
        logger.info('云服务器后台钱包补付任务开始: chat_id=%s user_id=%s order_id=%s currency=%s', chat_id, user_id, order_id, currency)
        order, err = await pay_cloud_server_order_with_balance(order_id, user_id, currency)
        if err:
            logger.warning('云服务器后台钱包补付失败: chat_id=%s user_id=%s order_id=%s currency=%s error=%s', chat_id, user_id, order_id, currency, err)
            await bot.send_message(
                chat_id=chat_id,
                text=f"{_bot_text('bot_custom_balance_insufficient', '❌ 余额不足，请先充值')}\n\n当前支付币种: {currency}",
                reply_markup=wallet_recharge_prompt_menu(f'custom:orderpaypage:{order_id}', '🔙 返回支付页'),
            )
            return
        if is_cloud_asset_renewal_order(order):
            await bot.send_message(
                chat_id=chat_id,
                text=(
                    '✅ 钱包支付成功\n\n'
                    f'{_public_region_line(order.region_name)}'
                    f'套餐: {order.plan_name}\n'
                    f'支付金额: {fmt_pay_amount(order.pay_amount)} {order.currency}\n\n'
                    '正在恢复未绑定代理资产固定 IP，系统会自动创建服务器并绑定旧 IP。'
                ),
            )
            await _send_admin_user_action_notice(bot, type('UserNotice', (), {'tg_user_id': chat_id, 'username': None, 'first_name': ''})(), '续费', [
                ('订单号', order.order_no),
                ('套餐', order.plan_name),
                ('金额', f'{fmt_pay_amount(order.pay_amount)} {order.currency}'),
                ('支付方式', '钱包余额'),
            ])
            asyncio.create_task(_provision_cloud_server_and_notify(bot, chat_id, order.id, order.mtproxy_port or MTPROXY_DEFAULT_PORT))
            return
        orders = await prepare_cloud_server_order_instances(order.id, user_id, MTPROXY_DEFAULT_PORT)
        task_count = len(orders)
        await bot.send_message(
            chat_id=chat_id,
            text=(
                '✅ 钱包支付成功\n\n'
                f'{_public_region_line(order.region_name)}'
                f'套餐: {order.plan_name}\n'
                f'数量: {order.quantity}\n'
                f'支付金额: {fmt_pay_amount(order.pay_amount)} {order.currency}\n'
                f'端口: {MTPROXY_DEFAULT_PORT}\n\n'
                f'{task_count or 1} 台服务器创建任务已提交，完成后会自动发送创建结果。'
            ),
            reply_markup=main_menu(),
        )
        await _send_admin_user_action_notice(bot, type('UserNotice', (), {'tg_user_id': chat_id, 'username': None, 'first_name': ''})(), '购买', [
            ('订单号', order.order_no),
            ('套餐', order.plan_name),
            ('节点', _public_region_text(order.region_name) or '-'),
            ('数量', order.quantity),
            ('金额', f'{fmt_pay_amount(order.pay_amount)} {order.currency}'),
            ('支付方式', '钱包余额'),
        ])
        for item in orders:
            asyncio.create_task(_provision_cloud_server_and_notify(bot, chat_id, item.id, item.mtproxy_port or MTPROXY_DEFAULT_PORT))
        logger.info('云服务器后台钱包补付任务完成: chat_id=%s user_id=%s order_id=%s order=%s currency=%s qty=%s pay_amount=%s tasks=%s', chat_id, user_id, order.id, order.order_no, currency, order.quantity, order.pay_amount, task_count)
    except Exception as exc:
        logger.exception('云服务器后台钱包补付任务异常: chat_id=%s user_id=%s order_id=%s currency=%s error=%s', chat_id, user_id, order_id, currency, exc)
        await bot.send_message(chat_id=chat_id, text=_bot_text_format('bot_wallet_pay_failed', '❌ 钱包支付失败，请稍后重试。\n错误: {error}', error=_public_cloud_error_text(exc)), reply_markup=main_menu())


def _orders_page(orders, page: int, total: int):
    total_pages = max(1, math.ceil(total / 5))
    if not orders:
        return _bot_text('bot_no_orders', '暂无订单记录。'), None
    return _bot_text('bot_orders_list_title', '📋 我的订单：'), kb_order_list(orders, page, total_pages)


def _balance_details_page(items, page: int, total: int, detail_filter: str = 'all'):
    total_pages = max(1, math.ceil(total / 8))
    if not items:
        return _bot_text('bot_balance_details_empty', '💳 余额明细\n\n暂无余额流水。'), balance_details_list([], 1, 1, detail_filter)
    lines = [_bot_text('bot_balance_detail_title', '💳 余额明细'), '']
    for item in items:
        icon = '🟢' if item['direction'] == 'in' else '🔴'
        created_at = item['created_at'].strftime('%m-%d %H:%M') if item.get('created_at') else '-'
        lines.append(f"{icon} {item['title']} | {item['amount']} {item['currency']} | {created_at}")
    return '\n'.join(lines), balance_details_list(items, page, total_pages, detail_filter)


def _parse_proxy_link(text: str) -> dict[str, str] | None:
    raw = (text or '').strip()
    if 'https://t.me/proxy?' in raw:
        raw = raw[raw.find('https://t.me/proxy?'):].strip()
    elif 'tg://proxy?' in raw:
        raw = raw[raw.find('tg://proxy?'):].strip()
    raw = raw.strip().strip('"\',，。')
    if raw.startswith('tg://proxy?'):
        query = raw.split('?', 1)[1]
    elif raw.startswith('https://t.me/proxy?'):
        query = urlparse(raw).query
    else:
        return None
    params = parse_qs(query)
    server = (params.get('server') or [''])[0].strip()
    port = (params.get('port') or [''])[0].strip()
    secret = unquote((params.get('secret') or [''])[0].strip())
    if not server or not port or not secret:
        return None
    return {'url': raw, 'server': server, 'port': port, 'secret': secret}


def _normalize_proxy_secret(secret: str) -> str:
    value = unquote(str(secret or '')).strip().strip('"\'')
    value = re.sub(r'[^0-9a-fA-F]', '', value).lower()
    if not value:
        return ''
    if value.startswith(('ee', 'dd')) and len(value) >= 34:
        return value[2:34]
    return value[:32]


def _secret_log_hint(secret: str) -> str:
    value = _normalize_proxy_secret(secret)
    if not value:
        return '-'
    if len(value) <= 10:
        return f'{value[:2]}***{value[-2:]}({len(value)})'
    return f'{value[:6]}***{value[-6:]}({len(value)})'


async def _validate_reinstall_proxy_link(order, link_data: dict[str, str], probe_when_possible: bool = True, allow_client_port: bool = False) -> tuple[bool, str]:
    order_ip = str(order.public_ip or order.previous_public_ip or '').strip()
    stored_order_port = str(order.mtproxy_port or link_data['port'] or MTPROXY_DEFAULT_PORT)
    probe_port = str(link_data['port'] if allow_client_port else stored_order_port)
    parsed_secret = _normalize_proxy_secret(link_data.get('secret', ''))
    logger.info(
        'CLOUD_REINSTALL_LINK_PARSED item_id=%s ip_expected=%s stored_port=%s probe_port=%s parsed_server=%s parsed_port=%s parsed_secret=%s probe_when_possible=%s has_login_password=%s allow_client_port=%s',
        getattr(order, 'id', None),
        order_ip,
        stored_order_port,
        probe_port,
        link_data.get('server'),
        link_data.get('port'),
        _secret_log_hint(link_data.get('secret', '')),
        probe_when_possible,
        bool(getattr(order, 'login_password', None)),
        allow_client_port,
    )
    guard_ok, guard_note = validate_server_connection_ip(link_data.get('server'), [order_ip], context=f'reinstall_link:{getattr(order, "id", None)}')
    if not guard_ok:
        logger.warning('CLOUD_REINSTALL_LINK_COMPARE_FAIL reason=ip_guard item_id=%s expected_ip=%s parsed_ip=%s note=%s', getattr(order, 'id', None), order_ip, link_data.get('server'), guard_note)
        return False, f'链接 IP 不匹配。当前服务器 IP 是 {order_ip or "未记录"}，你发的是 {link_data["server"]}'
    if not allow_client_port and link_data['port'] != stored_order_port:
        logger.warning('CLOUD_REINSTALL_LINK_COMPARE_FAIL reason=port item_id=%s expected_port=%s parsed_port=%s', getattr(order, 'id', None), stored_order_port, link_data['port'])
        return False, f'链接端口不匹配。当前主代理端口是 {stored_order_port}，你发的是 {link_data["port"]}'
    if allow_client_port and link_data['port'] != stored_order_port:
        logger.info('CLOUD_REINSTALL_LINK_PORT_OVERRIDE item_id=%s stored_port=%s parsed_port=%s', getattr(order, 'id', None), stored_order_port, link_data['port'])
    if not probe_when_possible or not getattr(order, 'login_password', None):
        logger.info('CLOUD_REINSTALL_LINK_COMPARE_SKIP_PROBE item_id=%s parsed_secret=%s reason=%s', getattr(order, 'id', None), _secret_log_hint(parsed_secret), 'disabled' if not probe_when_possible else 'missing_login_password')
        return True, '主链接格式和 IP 校验通过'
    probe = {}
    ok = False
    probe_user = order.login_user or 'root'
    probe_users = []
    for candidate in (probe_user, 'admin', 'root'):
        candidate = (candidate or '').strip()
        if candidate and candidate not in probe_users:
            probe_users.append(candidate)
    for candidate in probe_users:
        ok, probe = await _probe_mtproxy_state(order_ip, candidate, order.login_password, int(probe_port))
        if ok:
            probe_user = candidate
            break
    remote_secret = probe.get('MTPROXY_PROBE_SECRET', '')
    remote_secret_normalized = _normalize_proxy_secret(remote_secret)
    logger.info(
        'CLOUD_REINSTALL_SERVER_PROBE item_id=%s ip=%s user=%s port=%s ok=%s proc_ok=%s port_ok=%s daemon=%s remote_secret=%s parsed_secret=%s secret_match=%s',
        getattr(order, 'id', None),
        order_ip,
        probe_user,
        probe_port,
        ok,
        probe.get('MTPROXY_PROBE_PROC_OK'),
        probe.get('MTPROXY_PROBE_PORT_OK'),
        probe.get('MTPROXY_PROBE_DAEMON'),
        _secret_log_hint(remote_secret_normalized),
        _secret_log_hint(parsed_secret),
        bool(remote_secret_normalized and remote_secret_normalized == parsed_secret),
    )
    if not ok:
        stored_secret = _normalize_proxy_secret(getattr(order, 'mtproxy_secret', '') or '')
        if stored_secret and stored_secret == parsed_secret:
            logger.warning('CLOUD_REINSTALL_LINK_COMPARE_PROBE_FAILED_BUT_STORED_SECRET_MATCH item_id=%s ip=%s port=%s users=%s', getattr(order, 'id', None), order_ip, probe_port, ','.join(probe_users))
            return True, '主链接格式、IP 和已记录密钥校验通过'
        return False, '无法登录服务器确认代理状态，请稍后再试或联系后台检查 SSH/代理服务'
    if remote_secret_normalized != parsed_secret:
        logger.warning('CLOUD_REINSTALL_LINK_COMPARE_FAIL reason=secret item_id=%s remote_secret=%s parsed_secret=%s', getattr(order, 'id', None), _secret_log_hint(remote_secret_normalized), _secret_log_hint(parsed_secret))
        return False, '链接密钥和服务器实际运行密钥不一致，请检查后重新发送主链接'
    logger.info('CLOUD_REINSTALL_LINK_COMPARE_OK item_id=%s ip=%s port=%s secret=%s', getattr(order, 'id', None), order_ip, probe_port, _secret_log_hint(parsed_secret))
    return True, '主链接校验通过'


@sync_to_async
def _cloud_asset_order_summary(order_id: int) -> dict:
    from cloud.models import CloudServerOrder
    order = CloudServerOrder.objects.filter(id=order_id).first()
    if not order:
        return {}
    return {
        'id': order.id,
        'status': order.status,
        'provider': order.provider,
        'auto_renew_enabled': bool(order.auto_renew_enabled),
        'login_password': bool(order.login_password),
        'ip_change_quota': int(order.ip_change_quota or 0),
    }


@sync_to_async
def _save_asset_main_proxy_link(asset_id: int, user_id: int | None, link_data: dict[str, str]):
    from cloud.models import CloudAsset
    qs = CloudAsset.objects.filter(id=asset_id)
    if user_id is not None:
        qs = qs.filter(user_id=user_id)
    asset = qs.get()
    asset.mtproxy_link = link_data['url']
    asset.mtproxy_secret = link_data['secret']
    asset.mtproxy_host = link_data['server']
    asset.mtproxy_port = int(link_data['port'])
    links = list(asset.proxy_links or [])
    links = [item for item in links if not (isinstance(item, dict) and str(item.get('port') or '') == str(asset.mtproxy_port))]
    links.insert(0, {'name': '主代理 mtg', 'server': link_data['server'], 'port': link_data['port'], 'secret': link_data['secret'], 'url': link_data['url']})
    asset.proxy_links = links
    asset.save(update_fields=['mtproxy_link', 'mtproxy_secret', 'mtproxy_host', 'mtproxy_port', 'proxy_links', 'updated_at'])
    return asset


@sync_to_async
def _save_user_main_proxy_link(order_id: int, link_data: dict[str, str]):
    from cloud.models import CloudAsset, CloudServerOrder
    from cloud.services import _update_order_primary_records
    order = CloudServerOrder.objects.get(id=order_id)
    order.mtproxy_link = link_data['url']
    order.mtproxy_secret = link_data['secret']
    order.mtproxy_host = link_data['server']
    order.mtproxy_port = int(link_data['port'])
    links = list(order.proxy_links or [])
    links = [item for item in links if not (isinstance(item, dict) and str(item.get('port') or '') == str(order.mtproxy_port))]
    links.insert(0, {'name': '主代理 mtg', 'server': link_data['server'], 'port': link_data['port'], 'secret': link_data['secret'], 'url': link_data['url']})
    order.proxy_links = links
    order.provision_note = append_note(order.provision_note, '用户补充并校验主代理链接，准备重新安装。')
    order.save(update_fields=['mtproxy_link', 'mtproxy_secret', 'mtproxy_host', 'mtproxy_port', 'proxy_links', 'provision_note', 'updated_at'])
    _update_order_primary_records(
        order,
        asset_updates={
            'mtproxy_link': order.mtproxy_link,
            'mtproxy_secret': order.mtproxy_secret,
            'mtproxy_host': order.mtproxy_host,
            'mtproxy_port': order.mtproxy_port,
            'proxy_links': links,
        },
        now=timezone.now(),
    )
    return order


def _reinstall_confirm_keyboard(order_id: int, token: str, back_callback: str | None = None):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='确认重新安装', callback_data=f'cloud:reinitconfirm:{order_id}:{token}')],
        [InlineKeyboardButton(text='取消', callback_data=cloud_previous_detail_callback(order_id, back_callback))],
    ])


def _asset_reinstall_confirm_keyboard(asset_id: int, token: str, back_callback: str | None = None):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='确认重新安装', callback_data=f'cloud:assetreinitconfirm:{asset_id}:{token}')],
        [InlineKeyboardButton(text='取消', callback_data=cloud_asset_detail_callback(asset_id, back_callback))],
    ])


async def _issue_reinstall_confirm_token(state: FSMContext, *, kind: str, item_id: int) -> str:
    token = secrets.token_urlsafe(6)
    await state.set_state(None)
    await state.update_data(
        reinstall_confirm_token=token,
        reinstall_confirm_kind=kind,
        reinstall_confirm_id=int(item_id),
        reinstall_confirm_created_at=time.time(),
    )
    return token


async def _consume_reinstall_confirm_token(state: FSMContext, *, kind: str, item_id: int, token: str) -> bool:
    data = await state.get_data()
    expected_token = str(data.get('reinstall_confirm_token') or '')
    expected_kind = str(data.get('reinstall_confirm_kind') or '')
    expected_id = int(data.get('reinstall_confirm_id') or 0)
    created_at = float(data.get('reinstall_confirm_created_at') or 0)
    is_valid = bool(
        expected_token
        and secrets.compare_digest(expected_token, str(token or ''))
        and expected_kind == kind
        and expected_id == int(item_id)
        and time.time() - created_at <= _REINSTALL_CONFIRM_TTL
    )
    if is_valid:
        await state.update_data(
            reinstall_confirm_token='',
            reinstall_confirm_kind='',
            reinstall_confirm_id=0,
            reinstall_confirm_created_at=0,
        )
    return is_valid


def _cloud_order_status_hint(order) -> str:
    has_ip = bool(order.public_ip or order.previous_public_ip)
    if has_ip:
        missing = []
        if order.status in {'paid', 'provisioning'}:
            if not order.login_password:
                missing.append('登录密码')
            if not order.mtproxy_secret:
                missing.append('密钥')
            if not order.mtproxy_link:
                missing.append('代理链接')
        if missing:
            return f'初始化说明: 已分配 IP，但尚未完成初始化，缺少 {"、".join(missing)}。可点“继续初始化”查看处理提示。'
        return ''
    if order.status == 'pending':
        return _bot_text('bot_cloud_unassigned_pending', '未分配IP说明: 订单未付款')
    if order.status in {'paid', 'provisioning'}:
        return _bot_text('bot_cloud_unassigned_paid', '未分配IP说明: 已支付但尚未完成，请联系人工处理')
    if order.status == 'failed':
        return _bot_text('bot_cloud_unassigned_failed', '未分配IP说明: 创建失败，请联系人工处理')
    return f'未分配IP说明: 当前状态为 {order.get_status_display()}'


def _proxy_links_text(order) -> str:
    links = []
    seen = set()
    main_link = str(getattr(order, 'mtproxy_link', '') or '')
    main_port = str(getattr(order, 'mtproxy_port', '') or '')
    if main_link:
        links.append(('主代理', main_link))
        seen.add(main_link)
    for item in getattr(order, 'proxy_links', None) or []:
        if not isinstance(item, dict):
            continue
        link = item.get('url') or ''
        if str(link).startswith('socks5://'):
            link = _telegram_socks_link_from_raw(link)
        if not link or link in seen:
            continue
        if main_link and main_port and str(item.get('port') or '') == main_port:
            continue
        label = item.get('name') or f"端口 {item.get('port') or '-'}"
        links.append((label, link))
        seen.add(link)
    if not links:
        return f'代理链接: {escape(str(main_link or "尚未生成"))}'
    lines = ['代理链路:']
    for label, link in links:
        lines.append(f'- {escape(str(label))}: {escape(link)}')
    return '\n'.join(lines)


def _format_local_dt(value) -> str:
    if not value:
        return '未设置'
    try:
        return timezone.localtime(value).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return str(value)


def _parse_admin_expiry_input(raw_text: str):
    value = str(raw_text or '').strip()
    if not value:
        return None
    normalized = value.replace('年', '-').replace('月', '-').replace('日', ' ').replace('/', '-').strip()
    parsed = parse_datetime(normalized)
    if parsed is None:
        parsed_date = parse_date(normalized)
        if parsed_date:
            parsed = dt_datetime.combine(parsed_date, dt_datetime.min.time()).replace(hour=15)
    if parsed is None:
        return None
    if timezone.is_naive(parsed):
        parsed = timezone.make_aware(parsed, timezone.get_current_timezone())
    return parsed.astimezone(timezone.get_current_timezone())


def _cloud_order_ip_text(order) -> str:
    return getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '未分配'


def _cloud_order_plan_text(order, include_warnings: bool = True) -> str:
    expires_at = order_asset_expiry(order)
    suspend_at = getattr(order, 'suspend_at', None)
    delete_at = getattr(order, 'delete_at', None)
    auto_renew_enabled = bool(getattr(order, 'auto_renew_enabled', False))
    auto_renew_at = expires_at - timezone.timedelta(days=1) if expires_at else None
    lines = [f'到期时间: {_format_local_dt(expires_at)}']
    if auto_renew_enabled:
        lines.append(f'自动续费: 已开启，预计 {_format_local_dt(auto_renew_at)} 自动续费')
    else:
        lines.append('自动续费: 本IP未开启自动续费')
    lines.extend([
        f'关机计划: {_format_local_dt(suspend_at)}',
        f'删除计划: {_format_local_dt(delete_at)}',
    ])
    if include_warnings and suspend_at:
        lines.append(f'请务必在 {_format_local_dt(suspend_at)} 之前完成续费，避免关机。')
    return '\n'.join(lines)


def _display_username(user) -> str:
    primary = getattr(user, 'primary_username', None)
    username = primary or (str(getattr(user, 'username', '') or '').split(',')[0].strip())
    return f'@{username}' if username else '无'


def _reminder_list_text(summary: dict, page: int = 1, per_page: int = 5) -> str:
    if not summary:
        return '🔔 提醒列表\n\n暂无提醒设置。'
    cloud_orders = summary.get('cloud_orders') or []
    total_pages = max(1, math.ceil(len(cloud_orders) / per_page))
    page = min(max(1, page), total_pages)
    page_orders = cloud_orders[(page - 1) * per_page: page * per_page]
    lines = [f'🔔 提醒列表（{page}/{total_pages}）', '', '云服务器:']
    if page_orders:
        for order in page_orders:
            ip = order.public_ip or order.previous_public_ip or order.order_no
            reminder_count = sum(1 for field in ('cloud_reminder_enabled', 'suspend_reminder_enabled', 'delete_reminder_enabled', 'ip_recycle_reminder_enabled') if getattr(order, field, True))
            auto = '自动续费开' if order.auto_renew_enabled else '自动续费关'
            lines.append(f'- {escape(str(ip))} | 到期 {_format_local_dt(order_asset_expiry(order))} | 提醒 {reminder_count}/4 | {auto}')
    else:
        lines.append('- 暂无云服务器提醒')
    lines.extend(['', '这里只管理 IP 到期提醒和自动续费提醒。'])
    return '\n'.join(lines)


def _reminder_page_items(summary: dict, page: int = 1, per_page: int = 5):
    cloud_orders = (summary or {}).get('cloud_orders') or []
    total_pages = max(1, math.ceil(len(cloud_orders) / per_page))
    page = min(max(1, page), total_pages)
    return cloud_orders[(page - 1) * per_page: page * per_page], page, total_pages


def _find_reminder_order(summary: dict, order_id: int):
    for order in (summary or {}).get('cloud_orders') or []:
        if int(getattr(order, 'id', 0) or 0) == int(order_id):
            return order
    return None


def _reminder_ip_detail_text(order, page: int = 1) -> str:
    ip = order.public_ip or order.previous_public_ip or order.order_no
    expiry = '已开启' if getattr(order, 'cloud_reminder_enabled', True) else '已关闭'
    suspend = '已开启' if getattr(order, 'suspend_reminder_enabled', True) else '已关闭'
    delete = '已开启' if getattr(order, 'delete_reminder_enabled', True) else '已关闭'
    ip_recycle = '已开启' if getattr(order, 'ip_recycle_reminder_enabled', True) else '已关闭'
    auto = '已开启' if getattr(order, 'auto_renew_enabled', False) else '已关闭'
    return '\n'.join([
        '🌐 IP 提醒设置',
        '',
        f'IP: <code>{escape(str(ip))}</code>',
        f'订单号: {escape(str(order.order_no))}',
        f'到期时间: {_format_local_dt(order_asset_expiry(order))}',
        f'到期提醒: {expiry}',
        f'停机提醒: {suspend}',
        f'删机提醒: {delete}',
        f'IP保留期提醒: {ip_recycle}',
        f'自动续费提醒/续费: {auto}',
        '',
        '可以分别开启或关闭这台 IP 的各类生命周期提醒。',
    ])


def _cloud_asset_detail_text(item) -> str:
    proxy_links_text = _proxy_links_text(item)
    return (
        '☁️ 代理详情\n\n'
        f'名称: {escape(str(getattr(item, "order_no", "-") or "-"))}\n'
        f'{_public_region_line(getattr(item, "region_name", ""))}'
        f'状态: {escape(str(item.get_status_display() if hasattr(item, "get_status_display") else getattr(item, "status", "-")))}\n'
        f'IP: <code>{escape(str(getattr(item, "public_ip", "") or getattr(item, "previous_public_ip", "") or "未分配"))}</code>\n'
        f'端口: <code>{escape(str(getattr(item, "mtproxy_port", None) or "未设置"))}</code>\n'
        f'密钥: <code>{escape(str(getattr(item, "mtproxy_secret", None) or "尚未生成"))}</code>\n'
        f'{proxy_links_text}\n'
        f'到期时间: {_format_local_dt(getattr(item, "actual_expires_at", None))}\n'
        f'创建时间: {_format_local_dt(getattr(item, "created_at", None))}'
    )


def _cloud_server_detail_text(order) -> str:
    status_hint = _cloud_order_status_hint(order)
    expires_at = order_asset_expiry(order)
    expires_at_label = _format_local_dt(expires_at) if expires_at else '今天到期'
    renew_price = getattr(order, 'renewal_price', None) or order.pay_amount or order.total_amount
    auto_renew_status = '已开启' if getattr(order, 'auto_renew_enabled', False) else '已关闭'
    proxy_links_text = _proxy_links_text(order)
    text = (
        '☁️ 云服务器详情\n\n'
        f'订单号: {escape(str(order.order_no or "-"))}\n'
        f'{_public_region_line(order.region_name)}'
        f'套餐: {escape(str(order.plan_name or "-"))}\n'
        f'数量: {order.quantity}\n'
        f'状态: {escape(str(order.get_status_display()))}\n'
        f'支付方式: {"余额" if order.pay_method == "balance" else "地址"}\n'
        f'金额: {fmt_pay_amount(order.pay_amount or order.total_amount)} {escape(str(order.currency or ""))}\n'
        f'IP: <code>{escape(order.public_ip or order.previous_public_ip or "未分配")}</code>\n'
        f'端口: <code>{escape(str(order.mtproxy_port or "未设置"))}</code>\n'
        f'密钥: <code>{escape(str(order.mtproxy_secret or "尚未生成"))}</code>\n'
        f'{proxy_links_text}\n'
        f'到期时间: {expires_at_label}\n'
        f'续费价格: {fmt_pay_amount(renew_price)} {escape(str(order.currency or ""))}\n'
        f'自动续费: {auto_renew_status}\n'
        f'IP保留到期: {order.ip_recycle_at or "未设置"}\n'
        f'创建时间: {order.created_at:%Y-%m-%d %H:%M:%S}'
    )
    if status_hint:
        text += f'\n{status_hint}'
    return text


@sync_to_async
def _hydrate_order_proxy_links(order):
    if not order:
        return order
    current_links = list(getattr(order, 'proxy_links', None) or [])
    if len(current_links) > 1:
        return order
    try:
        from cloud.models import CloudAsset, CloudServerOrder
        candidates = []
        asset = _order_primary_asset(order)
        if asset:
            candidates.append(asset)
        lookup_values = [value for value in [order.instance_id, order.provider_resource_id, order.server_name, order.public_ip, order.previous_public_ip] if value]
        try:
            link_server = parse_qs(urlparse(str(getattr(order, 'mtproxy_link', '') or '')).query).get('server', [''])[0]
            if link_server:
                lookup_values.append(link_server)
        except Exception:
            pass
        if lookup_values:
            asset_qs = CloudAsset.objects.filter(
                Q(instance_id__in=lookup_values) | Q(provider_resource_id__in=lookup_values) | Q(asset_name__in=lookup_values) | Q(public_ip__in=lookup_values) | Q(previous_public_ip__in=lookup_values)
            )
            scope_q = Q(order_id=order.id)
            if getattr(order, 'user_id', None):
                scope_q |= Q(user_id=order.user_id)
            asset_qs = asset_qs.filter(scope_q)
            if getattr(order, 'provider', None):
                asset_qs = asset_qs.filter(provider=order.provider)
            if getattr(order, 'cloud_account_id', None):
                from core.cloud_accounts import cloud_account_label_variants

                account_labels = set()
                if getattr(order, 'account_label', None):
                    account_labels.add(order.account_label)
                if getattr(order, 'cloud_account', None):
                    account_labels.update(cloud_account_label_variants(order.cloud_account))
                account_scope = Q(cloud_account_id=order.cloud_account_id)
                if account_labels:
                    account_scope |= Q(account_label__in=list(account_labels))
                asset_qs = asset_qs.filter(account_scope)
            elif getattr(order, 'account_label', None):
                asset_qs = asset_qs.filter(account_label=order.account_label)
            candidates.extend(asset_qs.order_by('-updated_at', '-id')[:5])
        for source in candidates:
            links = list(getattr(source, 'proxy_links', None) or [])
            if len(links) > len(current_links):
                order.proxy_links = links
                current_links = links
            if not getattr(order, 'mtproxy_link', None) and getattr(source, 'mtproxy_link', None):
                order.mtproxy_link = source.mtproxy_link
            if not getattr(order, 'mtproxy_secret', None) and getattr(source, 'mtproxy_secret', None):
                order.mtproxy_secret = source.mtproxy_secret
            if not getattr(order, 'mtproxy_port', None) and getattr(source, 'mtproxy_port', None):
                order.mtproxy_port = source.mtproxy_port
        if len(current_links) <= 1 and getattr(order, 'mtproxy_link', None):
            richer_order = CloudServerOrder.objects.filter(user_id=order.user_id, mtproxy_link=order.mtproxy_link).exclude(id=order.id).order_by('-updated_at', '-id').first()
            richer_links = list(getattr(richer_order, 'proxy_links', None) or []) if richer_order else []
            if len(richer_links) > len(current_links):
                order.proxy_links = richer_links
                current_links = richer_links
        if len(current_links) <= 1 and getattr(order, 'replacement_for_id', None):
            source_order = CloudServerOrder.objects.filter(id=order.replacement_for_id).first()
            source_links = list(getattr(source_order, 'proxy_links', None) or []) if source_order else []
            if len(source_links) > len(current_links):
                order.proxy_links = source_links
    except Exception as exc:
        logger.warning('CLOUD_ORDER_PROXY_LINK_HYDRATE_FAILED order_id=%s error=%s', getattr(order, 'id', None), exc)
    return order


def _chain_trace_text(item) -> str:
    payer = str(getattr(item, 'payer_address', '') or '').strip()
    receiver = str(getattr(item, 'receive_address', '') or '').strip()
    tx_hash = str(getattr(item, 'tx_hash', '') or '').strip()
    lines = []
    if payer:
        lines.append(f'付款地址: <a href="{_tronscan_address_url(payer)}">{escape(payer)}</a>')
    if receiver:
        lines.append(f'收款地址: <a href="{_tronscan_address_url(receiver)}">{escape(receiver)}</a>')
    if tx_hash:
        lines.append(f'链上交易: <a href="{_tronscan_tx_url(tx_hash)}">{escape(tx_hash)}</a>')
    return '\n'.join(lines)


def _cloud_order_readonly_text(order) -> str:
    status_hint = _cloud_order_status_hint(order)
    expires_at = order_asset_expiry(order)
    expires_at_label = _format_local_dt(expires_at) if expires_at else '未设置'
    paid_at = getattr(order, 'paid_at', None) or getattr(order, 'completed_at', None)
    paid_at_text = f'{paid_at:%Y-%m-%d %H:%M:%S}' if paid_at else '未支付'
    proxy_links_text = _proxy_links_text(order)
    chain_trace = _chain_trace_text(order)
    text = (
        '☁️ 云服务器订单详情\n\n'
        f'订单号: {escape(str(order.order_no or "-"))}\n'
        f'{_public_region_line(order.region_name)}'
        f'套餐: {escape(str(order.plan_name or "-"))}\n'
        f'数量: {order.quantity}\n'
        f'状态: {escape(str(order.get_status_display()))}\n'
        f'支付方式: {"余额" if order.pay_method == "balance" else "地址"}\n'
        f'金额: {fmt_pay_amount(order.pay_amount or order.total_amount)} {escape(str(order.currency or ""))}\n'
        f'IP: <code>{escape(order.public_ip or order.previous_public_ip or "未分配")}</code>\n'
        f'端口: <code>{escape(str(order.mtproxy_port or "未设置"))}</code>\n'
        f'密钥: <code>{escape(str(order.mtproxy_secret or "尚未生成"))}</code>\n'
        f'{proxy_links_text}\n'
        f'到期时间: {expires_at_label}\n'
        f'支付时间: {paid_at_text}'
    )
    if chain_trace:
        text += f'\n{chain_trace}'
    text += f'\n创建时间: {order.created_at:%Y-%m-%d %H:%M:%S}'
    if status_hint:
        text += f'\n{status_hint}'
    text += '\n\n此处仅用于查询订单，不提供自助操作。如需续费、初始化或其他处理，请联系人工客服。'
    return text


def _cloud_order_detail_text(order) -> str:
    return _cloud_order_readonly_text(order)


def _balance_detail_text(item) -> str:
    created_at = item['created_at'].strftime('%Y-%m-%d %H:%M:%S') if item.get('created_at') else '-'
    before_balance = item.get('before_balance') or '-'
    after_balance = item.get('after_balance') or '-'
    direction_label = '收入' if item['direction'] == 'in' else '支出'
    return (
        '💳 余额明细详情\n\n'
        f"类型: {item['title']}\n"
        f"方向: {direction_label}\n"
        f"金额: {item['amount']} {item['currency']}\n"
        f"变动前余额: {before_balance}\n"
        f"变动后余额: {after_balance}\n"
        f"说明: {item['description']}\n"
        f"时间: {created_at}"
    )


def _monitor_detail_text(monitor) -> str:
    icon = '🟢' if monitor.is_active else '🔴'
    return (
        f'{icon} 监控详情\n'
        f'监控地址: <code>{escape(str(monitor.address or "-"))}</code>\n'
        f'备注: {escape(str(monitor.remark or "无"))}\n'
        f'💸 监控转账: {"开启" if monitor.monitor_transfers else "关闭"}\n'
        f'⚡ 监控资源: {"开启" if monitor.monitor_resources else "关闭"}\n'
        f'USDT 阈值: {fmt_amount(monitor.usdt_threshold)}\n'
        f'TRX 阈值: {fmt_amount(monitor.trx_threshold)}\n'
        f'能量增加阈值: {int(monitor.energy_threshold or 0)}\n'
        f'带宽增加阈值: {int(monitor.bandwidth_threshold or 0)}\n\n'
        f'📘 使用说明:\n'
        f'1. 监控转账：地址收到 USDT/TRX 转账时通知。\n'
        f'2. 监控资源：地址可用能量/带宽增加时通知；正常转账消耗不通知。'
    )


def _recharges_page(recharges, page: int, total: int):
    total_pages = max(1, math.ceil(total / 5))
    if not recharges:
        return _bot_text('bot_recharges_empty', '暂无充值记录。'), None
    return _bot_text('bot_recharges_title', '📜 充值记录：'), kb_recharge_list(recharges, page, total_pages)




def _plan_display_name(plan) -> str:
    return getattr(plan, 'display_plan_name', None) or getattr(plan, 'plan_name', '-') or '-'


def _custom_plan_text(region_name: str, plans) -> str:
    if not plans:
        return f'🛠 {region_name}\n\n当前地区暂无可用套餐。'
    labels = ['套餐一', '套餐二', '套餐三', '套餐四', '套餐五', '套餐六', '套餐七', '套餐八', '套餐九']
    lines = [f'🛠 {region_name} 可用套餐', '']
    for idx, plan in enumerate(plans, start=1):
        display_name = _plan_display_name(plan)
        display_description = (getattr(plan, 'display_description', None) or getattr(plan, 'plan_description', None) or '').strip()
        label = labels[idx - 1] if idx - 1 < len(labels) else f'套餐{idx}'
        lines.append(f'{label}｜{display_name}')
        if display_description:
            lines.append(display_description)
        lines.append('')
    lines.append('请选择下面的套餐按钮：')
    return '\n'.join(lines)


def _cloud_order_payment_text(order) -> str:
    receive_address = _receive_address()
    display_name = _plan_display_name(order)
    amount = Decimal(str(getattr(order, 'pay_amount', None) or getattr(order, 'total_amount', None) or 0))
    currency = getattr(order, 'currency', None) or 'USDT'
    return (
        _bot_text('bot_custom_payment_title', '🧾 支付页面') + '\n\n'
        f'订单号: {getattr(order, "order_no", "-")}\n'
        f'{_public_region_line(getattr(order, "region_name", None))}'
        f'套餐: {display_name}\n'
        f'数量: {getattr(order, "quantity", 1) or 1}\n'
        f'支付金额: {fmt_pay_amount(amount)} {currency}\n'
        f'支付地址: <code>{escape(receive_address)}</code>\n\n'
        + _bot_text('bot_custom_order_notice', f'系统已开始自动监控 {currency} 到账，检测到支付成功后会自动进入后续流程。')
    )


def _retained_ip_renewal_plan_text(order, plans, user=None) -> str:
    labels = ['套餐一', '套餐二', '套餐三', '套餐四', '套餐五', '套餐六', '套餐七', '套餐八', '套餐九']
    ip = getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '-'
    region = _public_region_text(getattr(order, 'region_name', None) or getattr(order, 'region_code', None)) or '-'
    lines = [
        _bot_text_format(
            'bot_retained_ip_renewal_plan_intro',
            '🔄 未附加固定 IP 续费\n\n保留 IP: {ip}\n地区: {region}\n\n请选择要恢复的新服务器套餐。选好后，我会要求你发送旧的主代理链接，用来保持原链接/密钥不变。',
            ip=ip,
            region=region,
        ),
        '',
    ]
    for idx, plan in enumerate(plans[:9], start=1):
        label = labels[idx - 1] if idx - 1 < len(labels) else f'套餐{idx}'
        display_name = _plan_display_name(plan)
        display_description = (getattr(plan, 'display_description', None) or getattr(plan, 'plan_description', None) or '').strip()
        discount_rate = Decimal(str(getattr(user, 'cloud_discount_rate', 100) or 100))
        display_price = (Decimal(str(getattr(plan, 'price', 0) or 0)) * discount_rate / Decimal('100')).quantize(Decimal('0.001'))
        currency = getattr(plan, 'currency', None) or 'USDT'
        lines.append(f'{label}｜{display_name}｜{fmt_amount(display_price)} {currency}')
        if display_description:
            lines.append(display_description)
        lines.append('')
    lines.append(_bot_text('bot_retained_ip_renewal_plan_footer', '请选择下面的套餐按钮：'))
    return '\n'.join(lines)


def _retained_ip_renewal_plan_keyboard(order_id: int, plans, back_callback: str | None = None):
    labels = ['套餐一', '套餐二', '套餐三', '套餐四', '套餐五', '套餐六', '套餐七', '套餐八', '套餐九']
    buttons = []
    for idx, plan in enumerate(plans[:9]):
        label = labels[idx] if idx < len(labels) else f'套餐{idx + 1}'
        buttons.append(InlineKeyboardButton(text=label, callback_data=append_back_callback(f'rnp:{order_id}:{plan.id}', back_callback)))
    rows = [buttons[index:index + 3] for index in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text='🔙 返回详情', callback_data=cloud_previous_detail_callback(order_id, back_callback))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def _asset_renewal_plan_text(asset, plans, user=None) -> str:
    labels = ['套餐一', '套餐二', '套餐三', '套餐四', '套餐五', '套餐六', '套餐七', '套餐八', '套餐九']
    ip = getattr(asset, 'public_ip', None) or getattr(asset, 'previous_public_ip', None) or '-'
    region = _public_region_text(getattr(asset, 'region_name', None) or getattr(asset, 'region_code', None)) or '-'
    lines = [
        f'🔄 未绑定代理资产续费\n\nIP: {ip}\n地区: {region}\n\n这条代理还未绑定订单，请先选择套餐；选择后发送旧主代理链接，系统会生成支付订单。',
        '',
    ]
    for idx, plan in enumerate(plans[:9], start=1):
        label = labels[idx - 1] if idx - 1 < len(labels) else f'套餐{idx}'
        display_name = _plan_display_name(plan)
        display_description = (getattr(plan, 'display_description', None) or getattr(plan, 'plan_description', None) or '').strip()
        discount_rate = Decimal(str(getattr(user, 'cloud_discount_rate', 100) or 100))
        display_price = (Decimal(str(getattr(plan, 'price', 0) or 0)) * discount_rate / Decimal('100')).quantize(Decimal('0.001'))
        currency = getattr(plan, 'currency', None) or 'USDT'
        lines.append(f'{label}｜{display_name}｜{fmt_amount(display_price)} {currency}')
        if display_description:
            lines.append(display_description)
        lines.append('')
    lines.append('请选择下面的套餐按钮：')
    return '\n'.join(lines)


def _asset_renewal_plan_keyboard(asset_id: int, plans, back_callback: str | None = None):
    labels = ['套餐一', '套餐二', '套餐三', '套餐四', '套餐五', '套餐六', '套餐七', '套餐八', '套餐九']
    buttons = []
    for idx, plan in enumerate(plans[:9]):
        label = labels[idx] if idx < len(labels) else f'套餐{idx + 1}'
        buttons.append(InlineKeyboardButton(text=label, callback_data=append_back_callback(f'arp:{asset_id}:{plan.id}', back_callback)))
    rows = [buttons[index:index + 3] for index in range(0, len(buttons), 3)]
    rows.append([InlineKeyboardButton(text='🔙 返回代理详情', callback_data=cloud_asset_detail_callback(asset_id, back_callback))])
    return InlineKeyboardMarkup(inline_keyboard=rows)


async def _send_cloud_renewal_payment_prompt(message: Message, order, user, *, edit: bool = False, back_callback: str | None = None):
    wallet_usdt_amount = Decimal(str(getattr(order, 'total_amount', None) or getattr(order, 'pay_amount', 0) or 0))
    trx_amount = None
    trx_rate_notice = ''
    try:
        trx_amount = await usdt_to_trx(wallet_usdt_amount)
    except Exception as exc:
        logger.warning('云服务器续费 TRX 汇率获取失败，已隐藏 TRX 钱包支付按钮: order_id=%s error=%s', getattr(order, 'id', None), exc)
        trx_rate_notice = '\nTRX 钱包支付暂不可用，请先使用 USDT 钱包续费或地址支付。'
    receive_address = _receive_address()
    auto_renew_enabled = await get_cloud_server_auto_renew(order.id, getattr(order, 'user_id', getattr(user, 'id', None)))
    group_balance_lines = await get_cloud_order_group_balance_lines(order.id)
    balance_text = '\n'.join(['多用户余额：', *group_balance_lines]) if group_balance_lines else ''
    balance_suffix = f'\n\n{balance_text}' if balance_text else ''
    display_ip = str(getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '').strip() or '未分配'
    text = (
        '🔄 云服务器续费\n\n'
        f'IP: <code>{escape(display_ip)}</code>\n\n'
        '续费时长: 31天\n\n'
        f'地址支付金额: {fmt_pay_amount(order.pay_amount)} {order.currency}\n'
        f'钱包续费金额: {fmt_pay_amount(wallet_usdt_amount)} USDT\n\n'
        f'自动续费: {"已开启" if auto_renew_enabled else "已关闭"}\n\n'
        f'收款地址: <code>{escape(receive_address)}</code>'
        f'{balance_suffix}\n\n'
        f'地址支付仅监控 {order.currency or "USDT"} 精确到账；也可使用下方钱包续费。自动续费默认使用钱包余额扣款，请保证余额充足。'
        f'{trx_rate_notice}'
    )
    markup = cloud_server_renew_payment(order.id, wallet_usdt_amount, trx_amount, bool(auto_renew_enabled), back_callback)
    bot = getattr(message, 'bot', None)
    if edit:
        result = await _safe_edit_text(message, text, parse_mode='HTML', disable_web_page_preview=True, reply_markup=markup)
    else:
        result = await message.reply(text, parse_mode='HTML', disable_web_page_preview=True, reply_markup=markup)
    await _send_admin_user_action_notice(bot, user, '续费', [
        ('订单号', getattr(order, 'order_no', '-') or '-'),
        ('IP', display_ip),
        ('金额', f'{fmt_pay_amount(order.pay_amount)} {order.currency}'),
        ('时长', '31天'),
    ])
    return result


def _receive_address() -> str:
    from core.cache import get_cached_config_value
    return get_cached_config_value('receive_address', '')


def _bot_text(key: str, default: str) -> str:
    return site_text(key, default)


def _bot_text_format(key: str, default: str, **kwargs) -> str:
    template = _bot_text(key, default)
    try:
        return template.format(**kwargs)
    except Exception:
        return default.format(**kwargs)


# ── 辅助：检查是否在 FSM 状态中，如果是则不处理 ──
class _NotInState:
    """仅当用户不在任何 FSM 状态时匹配。"""
    __slots__ = ()

    def __call__(self, obj):
        return True  # 由 aiogram 内部的 StateFilter 机制处理


def _current_menu_labels() -> set[str]:
    try:
        from core.button_config import load_button_config
        return {str(item.get('label') or '').strip() for item in load_button_config().get('items', []) if item.get('enabled', True)}
    except Exception:
        return {'🛠 购买节点', '🔎 到期时间查询', '👤 个人中心'}


MENU_BUTTONS = {'🛠 购买节点', '🔎 到期时间查询', '👤 个人中心'}


def register_handlers(dp: Dispatcher):
    @dp.errors()
    async def bot_error_handler(event: ErrorEvent):
        exc = getattr(event, 'exception', None)
        update = getattr(event, 'update', None)
        callback = getattr(update, 'callback_query', None)
        message = getattr(update, 'message', None)
        logger.error(
            'BOT_HANDLER_ERROR update_id=%s callback_data=%s message_text=%s error=%s',
            getattr(update, 'update_id', None),
            getattr(callback, 'data', None),
            _safe_preview_text(getattr(message, 'text', None) or ''),
            exc,
            exc_info=(type(exc), exc, getattr(exc, '__traceback__', None)) if exc else None,
        )
        if callback:
            with contextlib.suppress(Exception):
                await _safe_callback_answer(callback, '操作无效或已过期，请重新进入菜单。', show_alert=True)
        return True

    async def _handle_menu_interrupt(message: Message, state: FSMContext) -> bool:
        if (message.text or '').strip() not in (MENU_BUTTONS | _current_menu_labels()):
            return False
        await menu_handler(message, state)
        return True

    # ══════════════════════════════════════════════════════════════════════
    # FSM 状态处理器（必须先注册，优先级高于菜单按钮）
    # ══════════════════════════════════════════════════════════════════════

    @dp.message(MonitorStates.waiting_address)
    async def mon_address_input(message: Message, state: FSMContext):
        if await _handle_menu_interrupt(message, state):
            return
        address = message.text.strip()
        if not is_valid_tron_address(address):
            await message.answer(_bot_text('bot_monitor_invalid_address', '❌ 无效 TRON 地址，请重新输入：'))
            return
        await state.update_data(monitor_address=address)
        await state.set_state(MonitorStates.waiting_remark)
        await message.answer(_bot_text('bot_monitor_remark_prompt', '请输入备注（可选，输入 - 跳过）：\n\n可随时点击底部菜单打断当前输入。'))

    @dp.message(MonitorStates.waiting_remark)
    async def mon_remark_input(message: Message, state: FSMContext):
        if await _handle_menu_interrupt(message, state):
            return
        remark = message.text.strip()
        if remark == '-':
            remark = ''
        data = await state.get_data()
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        mon = await add_monitor(user.id, data['monitor_address'], remark)
        # 写入 Redis 缓存
        from cloud.cache import add_monitor_to_cache
        await add_monitor_to_cache(
            mon.id, user.id, mon.address, remark,
            mon.usdt_threshold, mon.trx_threshold,
            mon.monitor_transfers, mon.monitor_resources,
            mon.energy_threshold, mon.bandwidth_threshold,
        )
        await state.clear()
        short = f'{data["monitor_address"][:6]}...{data["monitor_address"][-4:]}'
        await message.answer(_bot_text_format('bot_monitor_added', '✅ 监控已添加: {address}', address=short), reply_markup=main_menu())

    @dp.message(MonitorStates.waiting_usdt_threshold)
    async def mon_usdt_threshold_input(message: Message, state: FSMContext):
        if await _handle_menu_interrupt(message, state):
            return
        try:
            val = Decimal(message.text.strip())
            if val <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            await message.answer(_bot_text('bot_monitor_invalid_usdt_threshold', '❌ 请输入有效金额。'))
            return
        data = await state.get_data()
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        mid = data['threshold_monitor_id']
        updated = await set_monitor_threshold(mid, user.id, 'USDT', val)
        if not updated:
            await state.clear()
            await message.answer(_bot_text('bot_monitor_missing', '监控不存在'), reply_markup=main_menu())
            return
        from cloud.cache import update_monitor_threshold_in_cache
        mon = await get_monitor(mid, user.id)
        if mon:
            await update_monitor_threshold_in_cache(mon.address, 'USDT', val, mon.id)
        await state.clear()
        await message.answer(_bot_text_format('bot_monitor_usdt_threshold_updated', '✅ USDT 阈值已更新为 {amount}', amount=fmt_amount(val)), reply_markup=main_menu())

    @dp.message(MonitorStates.waiting_trx_threshold)
    async def mon_trx_threshold_input(message: Message, state: FSMContext):
        if await _handle_menu_interrupt(message, state):
            return
        try:
            val = Decimal(message.text.strip())
            if val <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            await message.answer(_bot_text('bot_monitor_invalid_trx_threshold', '❌ 请输入有效金额。'))
            return
        data = await state.get_data()
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        mid = data['threshold_monitor_id']
        updated = await set_monitor_threshold(mid, user.id, 'TRX', val)
        if not updated:
            await state.clear()
            await message.answer(_bot_text('bot_monitor_missing', '监控不存在'), reply_markup=main_menu())
            return
        from cloud.cache import update_monitor_threshold_in_cache
        mon = await get_monitor(mid, user.id)
        if mon:
            await update_monitor_threshold_in_cache(mon.address, 'TRX', val, mon.id)
        await state.clear()
        await message.answer(_bot_text_format('bot_monitor_trx_threshold_updated', '✅ TRX 阈值已更新为 {amount}', amount=fmt_amount(val)), reply_markup=main_menu())

    @dp.message(MonitorStates.waiting_energy_threshold)
    async def mon_energy_threshold_input(message: Message, state: FSMContext):
        if await _handle_menu_interrupt(message, state):
            return
        try:
            val = int(message.text.strip())
            if val < 0:
                raise ValueError
        except (TypeError, ValueError):
            await message.answer(_bot_text('bot_monitor_invalid_resource_threshold', '❌ 请输入有效整数，0 表示只要增加就通知。'))
            return
        data = await state.get_data()
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        mid = data['threshold_monitor_id']
        updated = await set_monitor_threshold(mid, user.id, 'ENERGY', val)
        if not updated:
            await state.clear()
            await message.answer(_bot_text('bot_monitor_missing', '监控不存在'), reply_markup=main_menu())
            return
        from cloud.cache import update_monitor_threshold_in_cache
        mon = await get_monitor(mid, user.id)
        if mon:
            await update_monitor_threshold_in_cache(mon.address, 'ENERGY', val, mon.id)
        await state.clear()
        await message.answer(_bot_text_format('bot_monitor_energy_threshold_updated', '✅ 能量增加阈值已更新为 {amount}', amount=val), reply_markup=main_menu())

    @dp.message(MonitorStates.waiting_bandwidth_threshold)
    async def mon_bandwidth_threshold_input(message: Message, state: FSMContext):
        if await _handle_menu_interrupt(message, state):
            return
        try:
            val = int(message.text.strip())
            if val < 0:
                raise ValueError
        except (TypeError, ValueError):
            await message.answer(_bot_text('bot_monitor_invalid_resource_threshold', '❌ 请输入有效整数，0 表示只要增加就通知。'))
            return
        data = await state.get_data()
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        mid = data['threshold_monitor_id']
        updated = await set_monitor_threshold(mid, user.id, 'BANDWIDTH', val)
        if not updated:
            await state.clear()
            await message.answer(_bot_text('bot_monitor_missing', '监控不存在'), reply_markup=main_menu())
            return
        from cloud.cache import update_monitor_threshold_in_cache
        mon = await get_monitor(mid, user.id)
        if mon:
            await update_monitor_threshold_in_cache(mon.address, 'BANDWIDTH', val, mon.id)
        await state.clear()
        await message.answer(_bot_text_format('bot_monitor_bandwidth_threshold_updated', '✅ 带宽增加阈值已更新为 {amount}', amount=val), reply_markup=main_menu())

    @dp.message(RechargeStates.waiting_amount)
    async def recharge_amount_input(message: Message, state: FSMContext):
        if await _handle_menu_interrupt(message, state):
            return
        try:
            amount = Decimal(message.text.strip())
            if amount <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            await message.answer(_bot_text('bot_recharge_invalid_amount', '❌ 请输入有效的正数金额。'))
            return
        data = await state.get_data()
        currency = data['recharge_currency']
        addr = _receive_address()
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        rc = await create_recharge(user.id, amount, currency, addr)
        await state.clear()
        await message.answer(
            _bot_text_format(
                'bot_recharge_order_created',
                '💰 充值订单已创建\n充值金额: {amount} {currency}\n支付金额: {pay_amount} {currency}\n收款地址: <code>{address}</code>\n\n⏰ 请在 30 分钟内转账精确金额到上述地址。',
                amount=fmt_amount(amount),
                pay_amount=fmt_pay_amount(rc.pay_amount),
                currency=currency,
                address=escape(addr),
            ),
            reply_markup=main_menu(),
            parse_mode='HTML',
        )
        await _send_admin_user_action_notice(getattr(message, 'bot', None), user, '充值', [
            ('充值ID', f'#{rc.id}'),
            ('充值金额', f'{fmt_amount(amount)} {currency}'),
            ('支付金额', f'{fmt_pay_amount(rc.pay_amount)} {currency}'),
        ])

    @dp.message(CustomServerStates.waiting_quantity)
    async def custom_quantity_input(message: Message, state: FSMContext):
        if await _handle_menu_interrupt(message, state):
            return
        text = message.text.strip()
        logger.info('云服务器自定义数量输入: tg_user_id=%s raw_text=%s', getattr(message.from_user, 'id', None), text)
        quantity = _parse_custom_cloud_quantity(text)
        if quantity is None:
            await message.answer(_bot_text('bot_custom_quantity_invalid', '请输入 1-99 的购买数量：\n\n可随时点击底部菜单打断当前输入。'))
            return
        data = await state.get_data()
        plan_id = int(data['custom_plan_id'])
        logger.info('云服务器自定义数量确认: tg_user_id=%s plan_id=%s quantity=%s state_data=%s', getattr(message.from_user, 'id', None), plan_id, quantity, {k: v for k, v in data.items() if k.startswith('custom_')})
        await state.clear()
        plan = await get_cloud_plan(plan_id)
        if not plan:
            await message.answer(_bot_text('bot_custom_plan_missing', '套餐不存在或已下架，请重新选择。'), reply_markup=main_menu())
            return
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        order = await create_cloud_server_order(user.id, plan.id, 'USDT', quantity)
        display_name = _plan_display_name(plan)
        logger.info('云服务器下单进入详情: tg_user_id=%s user=%s order_id=%s order=%s qty=%s region=%s plan_id=%s plan_name=%s currency=%s total=%s pay_amount=%s', getattr(message.from_user, 'id', None), user.id, order.id, order.order_no, order.quantity, order.region_code, plan.id, plan.plan_name, order.currency, order.total_amount, order.pay_amount)
        receive_address = _receive_address()
        await message.answer(
            '🧾 订单详情\n\n'
            f'{_public_region_line(plan.region_name)}'
            f'套餐: {display_name}\n'
            f'数量: {order.quantity}\n'
            f'支付金额: {fmt_pay_amount(order.pay_amount or order.total_amount)} {order.currency or "USDT"}\n'
            f'支付地址: <code>{escape(receive_address)}</code>\n'
            '订单 5 分钟有效，请在有效期内完成支付。\n\n'
            + _bot_text('bot_custom_order_notice', f'系统已开始自动监控 {order.currency or "USDT"} 到账，检测到支付成功后会自动进入后续流程。'),
            reply_markup=custom_currency_keyboard(None, None, None, order.id),
            parse_mode='HTML',
            disable_web_page_preview=True,
        )
        await _send_admin_user_action_notice(getattr(message, 'bot', None), user, '购买', [
            ('订单号', order.order_no),
            ('套餐', display_name),
            ('节点', _public_region_text(plan.region_name) or '-'),
            ('数量', order.quantity),
            ('金额', f'{fmt_pay_amount(order.pay_amount or order.total_amount)} {order.currency}'),
        ])

    # ══════════════════════════════════════════════════════════════════════
    # 普通消息（菜单按钮 + /start）
    # ══════════════════════════════════════════════════════════════════════

    @dp.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext):
        await state.clear()
        await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        sent = await message.answer(_bot_text('bot_welcome', '欢迎使用商城机器人！请选择操作：'), reply_markup=main_menu())
        logger.info('机器人消息已发送：路由=start 用户ID=%s 会话ID=%s 回复消息ID=%s 发送消息ID=%s', getattr(message.from_user, 'id', None), message.chat.id, message.message_id, getattr(sent, 'message_id', None))

    @dp.message(lambda message: (message.text or '').strip() in (MENU_BUTTONS | _current_menu_labels()))
    async def menu_handler(message: Message, state: FSMContext):
        current = await state.get_state()
        if current:
            await state.clear()

        text = (message.text or '').strip()
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        link_item = configured_link_for_label(text)
        if link_item:
            sent = await message.answer(
                str(link_item.get('message') or '请点击下方按钮打开链接。'),
                reply_markup=configured_link_menu(text) or main_menu(),
            )
            logger.info('机器人消息已发送：路由=自定义菜单链接 标签=%s 用户ID=%s 会话ID=%s 回复消息ID=%s 发送消息ID=%s', text, getattr(message.from_user, 'id', None), message.chat.id, message.message_id, getattr(sent, 'message_id', None))
            return

        if text == '✨ 订阅':
            sent = await message.answer(_bot_text('bot_removed_products_entry', '商品购买入口已移除，请使用“🛠 购买节点”或“🔎 到期时间查询”。'), reply_markup=main_menu())
            logger.info('机器人消息已发送：路由=已移除菜单 标签=%s 用户ID=%s 会话ID=%s 回复消息ID=%s 发送消息ID=%s', text, getattr(message.from_user, 'id', None), message.chat.id, message.message_id, getattr(sent, 'message_id', None))

        elif text == '🛠 购买节点':
            regions = await list_custom_regions()
            sent = await message.answer(_bot_text('bot_custom_region_entry', '🛠 购买节点\n\n请选择热门地区：'), reply_markup=custom_region_menu(regions, expanded=False))
            logger.info('机器人消息已发送：路由=购买节点菜单 用户ID=%s 会话ID=%s 回复消息ID=%s 发送消息ID=%s', getattr(message.from_user, 'id', None), message.chat.id, message.message_id, getattr(sent, 'message_id', None))

        elif text == '🔎 到期时间查询':
            sent = await message.answer(_bot_text('bot_query_center_entry', '🔎 查询中心\n\n请选择查询方式：'), reply_markup=cloud_query_menu())
            logger.info('机器人消息已发送：路由=查询中心菜单 用户ID=%s 会话ID=%s 回复消息ID=%s 发送消息ID=%s', getattr(message.from_user, 'id', None), message.chat.id, message.message_id, getattr(sent, 'message_id', None))

        elif text == '👤 个人中心':
            sent = await message.answer(
                f'👤 个人中心\n用户ID: {user.tg_user_id}\n用户名: {_display_username(user)}\n'
                f'💵 USDT 余额: {fmt_amount(user.balance)}\n🪙 TRX 余额: {fmt_amount(user.balance_trx)}\n\n'
                f'请选择要进入的功能：',
                reply_markup=profile_menu(),
            )
            logger.info('机器人消息已发送：路由=个人中心菜单 用户ID=%s 会话ID=%s 回复消息ID=%s 发送消息ID=%s', getattr(message.from_user, 'id', None), message.chat.id, message.message_id, getattr(sent, 'message_id', None))

    @dp.callback_query(F.data == 'cloud:querymenu')
    async def cb_cloud_query_menu(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await _safe_callback_answer(callback)
        await _safe_edit_text(
            callback.message,
            _bot_text('bot_query_center_entry', '🔎 查询中心\n\n请选择查询方式：'),
            reply_markup=cloud_query_menu(),
        )

    @dp.callback_query(F.data == 'cloud:queryip')
    async def cb_cloud_query_ip(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await _safe_callback_answer(callback)
        await state.set_state(CloudQueryStates.waiting_ip)
        await _safe_edit_text(callback.message, _bot_text('bot_query_ip_prompt', '🔎 IP查询到期\n\n请输入要查询的 IP 地址：\n\n可随时点击底部菜单打断当前输入。'))

    @dp.message(CloudQueryStates.waiting_ip)
    async def input_cloud_query_ip(message: Message, state: FSMContext):
        if await _handle_menu_interrupt(message, state):
            return
        raw_text = (message.text or '').strip()
        query_ips = _extract_query_ips(raw_text)
        if not query_ips:
            await message.answer(_bot_text('bot_query_ip_invalid', '请输入包含 IP 或代理链接的文本内容。'))
            return
        await _reply_cloud_query_results(message, raw_text, state, include_start=await _is_admin_chat(message))

    @dp.callback_query(F.data.startswith('cloud:queryip:page:'))
    async def cb_cloud_query_ip_page(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        data = await state.get_data()
        results = data.get('cloud_query_results') or []
        if not results:
            await _safe_edit_text(callback.message, _bot_text('bot_query_ip_expired', '🔎 IP查询到期\n\n查询结果已失效，请重新输入 IP。'), reply_markup=order_query_menu())
            return
        page = max(1, int(callback.data.split(':')[3]))
        per_page = 8
        total_pages = max(1, math.ceil(len(results) / per_page))
        page = min(page, total_pages)
        page_items = results[(page - 1) * per_page: page * per_page]
        text = '🔎 IP查询结果\n\n' + '\n\n'.join(item['text'] for item in page_items)
        renewable_items = [{'ip': item['ip'], 'order_id': item.get('order_id') or 0, 'asset_id': item.get('asset_id') or 0, 'start_order_id': item.get('start_order_id') or 0, 'auto_renew_enabled': item.get('auto_renew_enabled'), 'can_auto_renew': item.get('can_auto_renew'), 'can_change_ip': item.get('can_change_ip'), 'can_reinit': item.get('can_reinit'), 'can_config': item.get('can_config'), 'can_support': item.get('can_support')} for item in page_items if item['renewable'] and (item.get('order_id') or item.get('asset_id'))]
        is_admin = await _is_admin_chat(callback.message)
        await _safe_edit_text(callback.message, text, reply_markup=cloud_ip_query_result(page_items, renewable_items, page, total_pages, include_start=is_admin, include_reinit=is_admin), parse_mode='HTML')

    async def _render_profile_cloud_orders(callback: CallbackQuery, page: int = 1, order_filter: str = 'all'):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        per_page = 8
        order_filter = str(order_filter or 'all').strip().lower()
        orders, total = await list_cloud_orders(user.id, page=page, per_page=per_page, order_filter=order_filter)
        total_pages = max(1, math.ceil(total / per_page))
        if not orders:
            await _safe_edit_text(
                callback.message,
                _bot_text('bot_cloud_orders_empty', '☁️ 云服务器订单\n\n暂无云服务器订单。'),
                reply_markup=cloud_order_list([], 1, 1, f'profile:orders:cloud:filter:{order_filter}:page', order_filter=order_filter),
            )
            return
        prefix = f'profile:orders:cloud:filter:{order_filter}:page'
        await _safe_edit_text(
            callback.message,
            _bot_text('bot_cloud_orders_entry', '☁️ 云服务器订单\n\n请选择要查看的订单：'),
            reply_markup=cloud_order_list(orders, page, total_pages, prefix, order_filter=order_filter),
        )

    @dp.callback_query(F.data == 'profile:orders')
    async def cb_profile_orders(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await _render_profile_cloud_orders(callback, page=1)
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'profile:orders:cloud')
    async def cb_profile_cloud_orders(callback: CallbackQuery):
        await _render_profile_cloud_orders(callback, page=1)
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('profile:orders:cloud:filter:'))
    async def cb_profile_cloud_orders_filter(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        parts = callback.data.split(':')
        order_filter = parts[4] if len(parts) > 4 else 'all'
        page = int(parts[6]) if len(parts) > 6 and parts[5] == 'page' else 1
        await _render_profile_cloud_orders(callback, page=page, order_filter=order_filter)

    @dp.callback_query(F.data.startswith('poc:'))
    async def cb_profile_cloud_orders_compact(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        parts = callback.data.split(':')
        order_filter = parts[1] if len(parts) > 1 and parts[1] else 'all'
        try:
            page = max(1, int(parts[2])) if len(parts) > 2 else 1
        except (TypeError, ValueError):
            page = 1
        await _render_profile_cloud_orders(callback, page=page, order_filter=order_filter)

    @dp.callback_query(F.data == 'profile:cart')
    async def cb_profile_cart(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        await _safe_edit_text(callback.message, _bot_text('bot_cart_removed', '商品/购物车入口已移除，请使用云服务器相关功能。'), reply_markup=profile_menu())

    @dp.callback_query(F.data == 'profile:balance_details')
    async def cb_profile_balance_details(callback: CallbackQuery):
        await _render_profile_balance_details(callback, page=1, detail_filter='all')
        await _safe_callback_answer(callback)

    async def _render_profile_balance_details(callback: CallbackQuery, page: int = 1, detail_filter: str = 'all'):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        detail_filter = str(detail_filter or 'all').strip().lower()
        items, total = await list_balance_details(user.id, page=page, detail_filter=detail_filter)
        text_out, kb = _balance_details_page(items, page, total, detail_filter)
        await _safe_edit_text(callback.message, text_out, reply_markup=kb)

    @dp.callback_query(F.data.startswith('profile:balance_details:filter:'))
    async def cb_profile_balance_details_filter(callback: CallbackQuery):
        detail_filter = callback.data.rsplit(':', 1)[-1] or 'all'
        await _render_profile_balance_details(callback, page=1, detail_filter=detail_filter)
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('bdpage:'))
    async def cb_profile_balance_details_page(callback: CallbackQuery):
        parts = callback.data.split(':')
        if len(parts) >= 3:
            detail_filter = parts[1] or 'all'
            page = int(parts[2])
        else:
            detail_filter = 'all'
            page = int(parts[1])
        await _render_profile_balance_details(callback, page=max(1, page), detail_filter=detail_filter)
        await _safe_callback_answer(callback)

    async def _render_profile_reminders(callback: CallbackQuery, page: int = 1):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        summary = await get_user_reminder_summary(user.id)
        is_muted = bool(summary and not summary.get('cloud_reminder_enabled'))
        page_orders, page, total_pages = _reminder_page_items(summary, page)
        await _safe_edit_text(
            callback.message,
            _reminder_list_text(summary, page),
            reply_markup=reminder_list_menu(page_orders, is_muted, page, total_pages),
        )

    @dp.callback_query(F.data == 'profile:reminders')
    async def cb_profile_reminders(callback: CallbackQuery):
        await _render_profile_reminders(callback, page=1)
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('profile:reminders:page:'))
    async def cb_profile_reminders_page(callback: CallbackQuery):
        page = int(callback.data.split(':')[-1])
        await _render_profile_reminders(callback, page=page)
        await _safe_callback_answer(callback)

    async def _render_profile_reminder_ip(callback: CallbackQuery, order_id: int, page: int = 1):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        summary = await get_user_reminder_summary(user.id)
        order = _find_reminder_order(summary, order_id)
        if not order:
            await _safe_callback_answer(callback, '订单不存在', show_alert=True)
            return None
        await _safe_edit_text(
            callback.message,
            _reminder_ip_detail_text(order, page),
            reply_markup=reminder_ip_detail_menu(order, page),
            parse_mode='HTML',
        )
        return order

    @dp.callback_query(F.data.startswith('profile:reminders:ip:'))
    async def cb_profile_reminders_ip(callback: CallbackQuery):
        parts = callback.data.split(':')
        order_id = int(parts[3])
        page = int(parts[4]) if len(parts) > 4 else 1
        order = await _render_profile_reminder_ip(callback, order_id, page)
        if order:
            await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'profile:reminders:muteall')
    async def cb_profile_reminders_mute_all(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        result = await mute_all_user_reminders(user.id)
        logger.info('PROFILE_REMINDERS_MUTE_ALL user_id=%s result=%s', user.id, result)
        await _render_profile_reminders(callback, page=1)
        await _safe_callback_answer(callback, '已关闭所有提醒')

    @dp.callback_query(F.data == 'profile:reminders:unmuteall')
    async def cb_profile_reminders_unmute_all(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        result = await unmute_all_user_reminders(user.id)
        logger.info('PROFILE_REMINDERS_UNMUTE_ALL user_id=%s result=%s', user.id, result)
        await _render_profile_reminders(callback, page=1)
        await _safe_callback_answer(callback, '已开启全部提醒')

    @dp.callback_query(F.data.startswith('profile:reminders:order:'))
    async def cb_profile_reminders_order_toggle(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        parts = callback.data.split(':')
        if len(parts) >= 7:
            reminder_type = parts[3]
            action = parts[4]
            raw_order_id = parts[5]
            page = int(parts[6]) if len(parts) > 6 else 1
        else:
            reminder_type = 'expiry'
            action = parts[3]
            raw_order_id = parts[4]
            page = int(parts[5]) if len(parts) > 5 else 1
        enabled = action == 'on'
        order = await set_cloud_order_reminder(int(raw_order_id), user.id, enabled, reminder_type)
        if not order:
            await _safe_callback_answer(callback, '订单不存在', show_alert=True)
            return
        type_label = {'expiry': '到期提醒', 'suspend': '停机提醒', 'delete': '删机提醒', 'ip_recycle': 'IP保留期提醒'}.get(reminder_type, '提醒')
        logger.info('PROFILE_REMINDERS_ORDER_TOGGLE user_id=%s order_id=%s reminder_type=%s enabled=%s page=%s', user.id, order.id, reminder_type, enabled, page)
        await _render_profile_reminder_ip(callback, order.id, page)
        label = order.public_ip or order.previous_public_ip or order.order_no
        await _safe_callback_answer(callback, f'{"已开启" if enabled else "已关闭"} {label} {type_label}')

    @dp.callback_query(F.data.startswith('profile:reminders:auto:'))
    async def cb_profile_reminders_auto_toggle(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        parts = callback.data.split(':')
        action = parts[3]
        raw_order_id = parts[4]
        page = int(parts[5]) if len(parts) > 5 else 1
        enabled = action == 'on'
        order = await set_cloud_server_auto_renew(int(raw_order_id), user.id, enabled)
        if order is False:
            await _safe_callback_answer(callback, '当前状态不可开启自动续费', show_alert=True)
            return
        if not order:
            await _safe_callback_answer(callback, '订单不存在', show_alert=True)
            return
        logger.info('PROFILE_REMINDERS_AUTO_TOGGLE user_id=%s order_id=%s enabled=%s page=%s', user.id, order.id, enabled, page)
        await _render_profile_reminder_ip(callback, order.id, page)
        label = order.public_ip or order.previous_public_ip or order.order_no
        await _safe_callback_answer(callback, f'{"已开启" if enabled else "已关闭"} {label} 自动续费')

    @dp.callback_query(F.data == 'profile:back_to_menu')
    async def cb_profile_back_to_menu(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        await _safe_edit_text(callback.message, 
            f'👤 个人中心\n用户ID: {user.tg_user_id}\n用户名: {_display_username(user)}\n'
            f'💵 USDT 余额: {fmt_amount(user.balance)}\n🪙 TRX 余额: {fmt_amount(user.balance_trx)}\n\n'
            f'请选择要进入的功能：',
            reply_markup=profile_menu(),
        )
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'profile:recharge')
    async def cb_profile_recharge(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        text = _bot_text('bot_recharge_currency_prompt', '💰 请选择充值币种：\n\n可随时点击底部菜单打断当前输入。')
        markup = recharge_currency_menu()
        edited = await _safe_edit_text(callback.message, text, reply_markup=markup)
        if edited is None:
            sent = await callback.message.reply(text, reply_markup=markup)
            logger.info('BOT_MESSAGE_SEND route=profile_recharge_fallback user_id=%s chat_id=%s reply_to=%s sent_message_id=%s', getattr(callback.from_user, 'id', None), callback.message.chat.id, callback.message.message_id, getattr(sent, 'message_id', None))
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'profile:recharges')
    async def cb_profile_recharges(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        recharges, total = await list_recharges(user.id)
        text_out, kb = _recharges_page(recharges, 1, total)
        await _safe_edit_text(callback.message, text_out, reply_markup=kb)
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'profile:monitors')
    async def cb_profile_monitors(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await _safe_edit_text(callback.message, _bot_text('bot_monitor_entry', '🔍 地址监控'), reply_markup=monitor_menu())
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'profile:back')
    async def cb_profile_back(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await _safe_edit_text(callback.message, _bot_text('bot_back_to_menu', '已返回主菜单，请使用底部按钮继续操作。'))
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'custom:back')
    async def cb_custom_back(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await _safe_edit_text(callback.message, _bot_text('bot_back_to_menu', '已返回主菜单，请使用底部按钮继续操作。'))
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'custom:regions')
    async def cb_custom_regions(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await _safe_callback_answer(callback)
        regions = await _get_cached_custom_regions()
        await _safe_edit_text(callback.message, '🛠 云服务器定制\n\n请选择热门地区：', reply_markup=custom_region_menu(regions, expanded=False))

    @dp.callback_query(F.data == 'custom:regions:more')
    async def cb_custom_regions_more(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await _safe_callback_answer(callback)
        regions = await _get_cached_custom_regions()
        await _safe_edit_text(callback.message, '🛠 云服务器定制\n\n请选择地区：', reply_markup=custom_region_menu(regions, expanded=True))

    @dp.callback_query(F.data.startswith('custom:region:'))
    async def cb_custom_region(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        region_code = callback.data.split(':', 2)[2]
        logger.info('云服务器选择地区: tg_user_id=%s region_code=%s callback=%s', getattr(callback.from_user, 'id', None), region_code, callback.data)
        plans = await _get_cached_region_plans(region_code)
        region_name = plans[0].region_name if plans else region_code
        await state.update_data(custom_region_code=region_code, custom_region_name=region_name)
        logger.info('云服务器地区已记录: tg_user_id=%s region_code=%s region_name=%s plans_count=%s', getattr(callback.from_user, 'id', None), region_code, region_name, len(plans or []))
        await _safe_edit_text(callback.message, _custom_plan_text(region_name, plans), reply_markup=custom_plan_menu(region_code, plans))

    @dp.callback_query(F.data.startswith('custom:plan:'))
    async def cb_custom_plan(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        plan_id = int(callback.data.split(':')[2])
        logger.info('云服务器选择套餐: tg_user_id=%s plan_id=%s callback=%s', getattr(callback.from_user, 'id', None), plan_id, callback.data)
        plan = await get_cloud_plan(plan_id)
        if not plan:
            await _safe_callback_answer(callback, '套餐不存在或已下架', show_alert=True)
            return
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        display_name = _plan_display_name(plan)
        discount_rate = Decimal(str(getattr(user, 'cloud_discount_rate', 100) or 100))
        display_price = (Decimal(str(plan.price)) * discount_rate / Decimal('100')).quantize(Decimal('0.001'))
        await state.update_data(custom_plan_id=plan.id, custom_plan_name=display_name, custom_plan_price=str(display_price), custom_region_code=plan.region_code, custom_region_name=plan.region_name)
        logger.info('云服务器套餐已记录: tg_user_id=%s plan_id=%s plan_name=%s region=%s price=%s', getattr(callback.from_user, 'id', None), plan.id, plan.plan_name, plan.region_code, display_price)
        text = (
            _bot_text('bot_custom_quantity_title', '请选择购买数量') + '\n\n'
            f'{_public_region_line(plan.region_name)}'
            f'套餐: {display_name}\n'
            f'套餐说明: {getattr(plan, "plan_description", None) or "无"}\n'
            f'单价: {fmt_amount(display_price)} USDT\n'
            f'专属折扣: {discount_rate}%\n\n'
            + _bot_text('bot_custom_quantity_hint', '请选择数量，或输入自定义数量。')
        )
        await _safe_edit_text(callback.message, text, reply_markup=custom_quantity_keyboard(plan.id, 1))

    @dp.callback_query(F.data.startswith('custom:qty:'))
    async def cb_custom_quantity(callback: CallbackQuery, state: FSMContext):
        _, _, plan_id_text, qty_text = callback.data.split(':')
        plan_id = int(plan_id_text)
        logger.info('云服务器选择数量: tg_user_id=%s plan_id=%s qty=%s callback=%s', getattr(callback.from_user, 'id', None), plan_id, qty_text, callback.data)
        plan = await get_cloud_plan(plan_id)
        if not plan:
            await _safe_callback_answer(callback, '套餐不存在或已下架', show_alert=True)
            return
        if qty_text == 'custom':
            await _safe_callback_answer(callback)
            display_name = _plan_display_name(plan)
            await state.update_data(custom_plan_id=plan_id, custom_plan_name=display_name, custom_plan_price=str(plan.price), custom_region_code=plan.region_code, custom_region_name=plan.region_name)
            logger.info('云服务器进入自定义数量输入: tg_user_id=%s plan_id=%s plan_name=%s', getattr(callback.from_user, 'id', None), plan_id, plan.plan_name)
            await state.set_state(CustomServerStates.waiting_quantity)
            await _safe_edit_text(callback.message, '请输入购买数量（1-99）：')
            return
        quantity = _parse_custom_cloud_quantity(qty_text)
        if quantity is None:
            await _safe_callback_answer(callback, '请输入 1-99 的购买数量', show_alert=True)
            return
        display_name = _plan_display_name(plan)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        pending_order = await create_cloud_server_order(user.id, plan.id, 'USDT', quantity)
        await state.update_data(custom_plan_id=plan.id, custom_plan_name=display_name, custom_plan_price=str(plan.price), custom_region_code=plan.region_code, custom_region_name=plan.region_name, custom_quantity=quantity, custom_order_id=pending_order.id)
        usdt_amount = Decimal(str(pending_order.pay_amount or pending_order.total_amount or 0))
        receive_address = _receive_address()
        text = (
            _bot_text('bot_custom_payment_title', '🧾 支付页面') + '\n\n'
            f'订单号: {pending_order.order_no}\n'
            f'{_public_region_line(plan.region_name)}'
            f'套餐: {display_name}\n'
            f'数量: {quantity}\n'
            f'支付金额: {fmt_pay_amount(usdt_amount)} USDT\n'
            f'支付地址: <code>{escape(receive_address)}</code>\n\n'
            + _bot_text('bot_custom_order_notice', '系统已开始自动监控 USDT 到账，检测到支付成功后会自动进入后续流程。')
        )
        await _safe_callback_answer(callback)
        await _safe_edit_text(callback.message, text, reply_markup=custom_payment_keyboard(pending_order.id, plan.id, quantity), parse_mode='HTML', disable_web_page_preview=True)


    @dp.callback_query(F.data.startswith('custom:quantitypage:'))
    async def cb_custom_quantity_page(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        _, _, plan_id_text, quantity_text = callback.data.split(':')
        plan_id = int(plan_id_text)
        quantity = _parse_custom_cloud_quantity(quantity_text) or 1
        plan = await get_cloud_plan(plan_id)
        if not plan:
            await _safe_callback_answer(callback, '套餐不存在或已下架', show_alert=True)
            return
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        display_name = _plan_display_name(plan)
        discount_rate = Decimal(str(getattr(user, 'cloud_discount_rate', 100) or 100))
        display_price = (Decimal(str(plan.price)) * discount_rate / Decimal('100')).quantize(Decimal('0.001'))
        await state.update_data(custom_plan_id=plan.id, custom_plan_name=display_name, custom_plan_price=str(display_price), custom_region_code=plan.region_code, custom_region_name=plan.region_name, custom_quantity=quantity)
        text = (
            _bot_text('bot_custom_quantity_title', '请选择购买数量') + '\n\n'
            f'{_public_region_line(plan.region_name)}'
            f'套餐: {display_name}\n'
            f'套餐说明: {getattr(plan, "plan_description", None) or "无"}\n'
            f'单价: {fmt_amount(display_price)} USDT\n'
            f'专属折扣: {discount_rate}%\n\n'
            + _bot_text('bot_custom_quantity_hint', '请选择数量，或输入自定义数量。')
        )
        await _safe_edit_text(callback.message, text, reply_markup=custom_quantity_keyboard(plan.id, quantity))


    @dp.callback_query(F.data.startswith('custom:paypage:'))
    async def cb_custom_paypage(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        _, _, plan_id_text, quantity_text = callback.data.split(':')
        plan_id = int(plan_id_text)
        quantity = _parse_custom_cloud_quantity(quantity_text)
        if quantity is None:
            await _safe_callback_answer(callback, '请输入 1-99 的购买数量', show_alert=True)
            return
        plan = await get_cloud_plan(plan_id)
        if not plan:
            await _safe_callback_answer(callback, '套餐不存在或已下架', show_alert=True)
            return
        display_name = _plan_display_name(plan)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        pending_order = await create_cloud_server_order(user.id, plan.id, 'USDT', quantity)
        await state.update_data(custom_plan_id=plan.id, custom_plan_name=display_name, custom_plan_price=str(plan.price), custom_region_code=plan.region_code, custom_region_name=plan.region_name, custom_quantity=quantity, custom_order_id=pending_order.id)
        usdt_amount = Decimal(str(pending_order.pay_amount or pending_order.total_amount or 0))
        receive_address = _receive_address()
        text = (
            _bot_text('bot_custom_payment_title', '🧾 支付页面') + '\n\n'
            f'订单号: {pending_order.order_no}\n'
            f'{_public_region_line(plan.region_name)}'
            f'套餐: {display_name}\n'
            f'数量: {quantity}\n'
            f'支付金额: {fmt_pay_amount(usdt_amount)} USDT\n'
            f'支付地址: <code>{escape(receive_address)}</code>\n\n'
            + _bot_text('bot_custom_order_notice', '系统已开始自动监控 USDT 到账，检测到支付成功后会自动进入后续流程。')
        )
        await _safe_edit_text(callback.message, text, reply_markup=custom_payment_keyboard(pending_order.id, plan.id, quantity), parse_mode='HTML', disable_web_page_preview=True)


    @dp.callback_query(F.data.startswith('custom:orderpaypage:'))
    async def cb_custom_order_paypage(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        order_id = int(callback.data.split(':')[2])
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        from cloud.models import CloudServerOrder
        order = await asyncio.to_thread(lambda: CloudServerOrder.objects.filter(id=order_id, user_id=user.id).first())
        if not order:
            await _safe_callback_answer(callback, '订单不存在', show_alert=True)
            return
        plan_id = int(getattr(order, 'plan_id', None) or 0)
        if plan_id <= 0:
            await _safe_callback_answer(callback, '订单缺少套餐信息，无法返回支付页', show_alert=True)
            return
        quantity = int(getattr(order, 'quantity', 1) or 1)
        await state.update_data(
            custom_plan_id=plan_id,
            custom_plan_name=_plan_display_name(order),
            custom_plan_price=str(getattr(order, 'pay_amount', None) or getattr(order, 'total_amount', None) or 0),
            custom_region_code=getattr(order, 'region_code', None),
            custom_region_name=getattr(order, 'region_name', None),
            custom_quantity=quantity,
            custom_order_id=order.id,
        )
        await _safe_edit_text(
            callback.message,
            _cloud_order_payment_text(order),
            reply_markup=custom_payment_keyboard(order.id, plan_id, quantity),
            parse_mode='HTML',
            disable_web_page_preview=True,
        )


    @dp.callback_query(F.data.startswith('custom:qtycart:'))
    async def cb_custom_quantity_add_to_cart(callback: CallbackQuery):
        _, _, plan_id_text, qty_text = callback.data.split(':')
        quantity = _parse_custom_cloud_quantity(qty_text)
        if quantity is None:
            await _safe_callback_answer(callback, '请输入 1-99 的购买数量', show_alert=True)
            return
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        item = await add_to_cart(user.id, int(plan_id_text), quantity, item_type='cloud_plan')
        if not item:
            await _safe_callback_answer(callback, '套餐不存在或已下架', show_alert=True)
            return
        await _safe_callback_answer(callback, '已加入购物车')

    @dp.callback_query(F.data.startswith('custom:wallet:'))
    async def cb_custom_wallet(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        _, _, plan_id_text, quantity_text = callback.data.split(':')
        plan_id = int(plan_id_text)
        quantity = _parse_custom_cloud_quantity(quantity_text)
        if quantity is None:
            await _safe_callback_answer(callback, '请输入 1-99 的购买数量', show_alert=True)
            return
        logger.info('云服务器进入钱包币种选择: tg_user_id=%s plan_id=%s quantity=%s callback=%s', getattr(callback.from_user, 'id', None), plan_id, quantity, callback.data)
        data = await state.get_data()
        plan = None
        if data.get('custom_plan_id') == plan_id and data.get('custom_plan_price'):
            class _PlanView:
                id = plan_id
                price = Decimal(str(data['custom_plan_price']))
            plan = _PlanView()
        if plan is None:
            plan = await get_cloud_plan(plan_id)
        if not plan:
            await _safe_callback_answer(callback, '套餐不存在或已下架', show_alert=True)
            return
        await state.update_data(custom_plan_id=plan_id, custom_quantity=quantity)
        order_id = data.get('custom_order_id')
        usdt_amount = Decimal(str(getattr(plan, 'price', 0))) * quantity
        trx_amount = await usdt_to_trx(usdt_amount)
        logger.info('云服务器钱包币种页准备完成: tg_user_id=%s plan_id=%s quantity=%s usdt=%s trx=%s', getattr(callback.from_user, 'id', None), plan_id, quantity, usdt_amount, trx_amount)
        await _safe_edit_text(
            callback.message,
            _bot_text('bot_custom_wallet_title', '请选择钱包支付币种：'),
            reply_markup=custom_wallet_keyboard(plan.id, quantity, usdt_amount, trx_amount, order_id),
        )

    @dp.callback_query(F.data.startswith('custom:currency:'))
    async def cb_custom_currency(callback: CallbackQuery, state: FSMContext, bot: Bot):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, plan_id_text, quantity_text, currency = callback.data.split(':')
        currency = str(currency or '').upper()
        if not _is_supported_payment_currency(currency):
            await _safe_callback_answer(callback, '不支持的支付币种', show_alert=True)
            return
        if currency != 'USDT':
            await _safe_callback_answer(callback, '云服务器地址支付仅支持 USDT；TRX 请使用钱包支付。', show_alert=True)
            return
        plan_id = int(plan_id_text)
        quantity = _parse_custom_cloud_quantity(quantity_text)
        if quantity is None:
            await _safe_callback_answer(callback, '请输入 1-99 的购买数量', show_alert=True)
            return
        await _safe_callback_answer(callback, '订单创建中，完成后将主动通知你')
        logger.info('云服务器创建待支付订单: tg_user_id=%s user=%s plan_id=%s quantity=%s currency=%s callback=%s', getattr(callback.from_user, 'id', None), user.id, plan_id, quantity, currency, callback.data)
        data = await state.get_data()
        plan = None
        if data.get('custom_plan_id') == plan_id and data.get('custom_plan_name') and data.get('custom_plan_price') and data.get('custom_region_name'):
            class _PlanView:
                id = plan_id
                plan_name = data['custom_plan_name']
                price = Decimal(str(data['custom_plan_price']))
                region_name = data['custom_region_name']
            plan = _PlanView()
        if plan is None:
            plan = await get_cloud_plan(plan_id)
        if not plan:
            await _safe_callback_answer(callback, '套餐不存在或已下架', show_alert=True)
            return
        await _safe_edit_text(
            callback.message,
            _bot_text('bot_custom_pending_order', '⏳ 正在后台创建订单，请稍候…\n\n创建完成后会主动把支付信息发给你。'),
        )
        asyncio.create_task(
            _create_cloud_order_and_notify(
                bot,
                callback.from_user.id,
                user.id,
                plan_id,
                quantity,
                currency,
                plan.plan_name,
                plan.region_name,
            )
        )


    @dp.callback_query(F.data.startswith('custom:balance:'))
    async def cb_custom_balance(callback: CallbackQuery, state: FSMContext, bot: Bot):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, plan_id_text, quantity_text, currency = callback.data.split(':')
        currency = str(currency or '').upper()
        if not _is_supported_payment_currency(currency):
            await _safe_callback_answer(callback, '不支持的支付币种', show_alert=True)
            return
        plan_id = int(plan_id_text)
        quantity = _parse_custom_cloud_quantity(quantity_text)
        if quantity is None:
            await _safe_callback_answer(callback, '请输入 1-99 的购买数量', show_alert=True)
            return
        logger.info('云服务器钱包直付开始: tg_user_id=%s user=%s plan_id=%s quantity=%s currency=%s callback=%s', getattr(callback.from_user, 'id', None), user.id, plan_id, quantity, currency, callback.data)
        plan = await get_cloud_plan(plan_id)
        if not plan:
            await _safe_callback_answer(callback, '套餐不存在或已下架', show_alert=True)
            return
        discount_rate = Decimal(str(getattr(user, 'cloud_discount_rate', 100) or 100))
        total_usdt = (Decimal(str(plan.price)) * discount_rate / Decimal('100') * quantity).quantize(Decimal('0.001'))
        total_amount = await usdt_to_trx(total_usdt) if currency == 'TRX' else total_usdt
        current_balance = Decimal(str(getattr(user, 'balance_trx' if currency == 'TRX' else 'balance', 0) or 0))
        if current_balance < total_amount:
            await _safe_callback_answer(callback, f'{currency} 余额不足', show_alert=True)
            data = await state.get_data()
            back_order_id = data.get('custom_order_id')
            back_callback = f'custom:orderpaypage:{back_order_id}' if back_order_id else f'custom:quantitypage:{plan_id}:{quantity}'
            back_text = '🔙 返回支付页' if back_order_id else '🔙 返回数量'
            await _safe_edit_text(
                callback.message,
                f"{_bot_text('bot_custom_balance_insufficient', '❌ 余额不足，请先充值')}\n\n当前支付币种: {currency}",
                reply_markup=wallet_recharge_prompt_menu(back_callback, back_text),
            )
            return
        message_key = f'{getattr(getattr(callback.message, "chat", None), "id", 0)}:{getattr(callback.message, "message_id", 0)}'
        once_key = f'custom_balance:{user.id}:{message_key}:{plan_id}:{quantity}:{currency}'
        if not _consume_callback_once(once_key):
            logger.warning('云服务器钱包直付重复点击已拦截: tg_user_id=%s user=%s plan_id=%s quantity=%s currency=%s message_key=%s', getattr(callback.from_user, 'id', None), user.id, plan_id, quantity, currency, message_key)
            await _safe_callback_answer(callback, '这笔钱包支付正在处理中，请不要重复点击。', show_alert=True)
            return
        await _safe_callback_answer(callback, '钱包支付处理中，完成后将主动通知你')
        await state.update_data(custom_plan_id=plan_id, custom_quantity=quantity, custom_currency=currency)
        await _safe_edit_text(
            callback.message,
            _bot_text('bot_custom_pending_wallet', '⏳ 正在后台处理钱包支付，请稍候…\n\n处理完成后会主动把结果发给你。'),
        )
        asyncio.create_task(_buy_cloud_server_with_balance_and_notify(bot, callback.from_user.id, user.id, plan_id, quantity, currency))

    @dp.callback_query(F.data.startswith('custom:walletpay:'))
    async def cb_custom_walletpay(callback: CallbackQuery, state: FSMContext, bot: Bot):
        await _safe_callback_answer(callback)
        parts = callback.data.split(':')
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(parts[2])
        logger.info('云服务器订单钱包支付入口: tg_user_id=%s user=%s order_id=%s callback=%s', getattr(callback.from_user, 'id', None), user.id, order_id, callback.data)
        if len(parts) == 3:
            from cloud.models import CloudServerOrder
            order = await asyncio.to_thread(lambda: CloudServerOrder.objects.filter(id=order_id, user_id=user.id).first())
            if not order:
                await _safe_callback_answer(callback, '订单不存在', show_alert=True)
                return
            payable_amount = Decimal(str(order.total_amount or order.pay_amount or 0))
            trx_amount = await usdt_to_trx(payable_amount)
            logger.info('云服务器订单钱包币种页准备完成: tg_user_id=%s user=%s order_id=%s total=%s pay=%s trx=%s', getattr(callback.from_user, 'id', None), user.id, order.id, order.total_amount, order.pay_amount, trx_amount)
            await _safe_edit_text(callback.message, 
                _bot_text('bot_custom_wallet_title', '请选择钱包支付币种：'),
                reply_markup=custom_order_wallet_keyboard(order.id, payable_amount, trx_amount),
            )
            return
        currency = str(parts[3] or '').upper()
        if not _is_supported_payment_currency(currency):
            await _safe_callback_answer(callback, '不支持的支付币种', show_alert=True)
            return
        logger.info('云服务器订单钱包补付开始: tg_user_id=%s user=%s order_id=%s currency=%s', getattr(callback.from_user, 'id', None), user.id, order_id, currency)
        from cloud.models import CloudServerOrder
        order = await asyncio.to_thread(lambda: CloudServerOrder.objects.filter(id=order_id, user_id=user.id).first())
        if not order:
            await _safe_callback_answer(callback, '订单不存在', show_alert=True)
            return
        payable_amount = Decimal(str(order.total_amount or order.pay_amount or 0))
        total_amount = await usdt_to_trx(payable_amount) if currency == 'TRX' else payable_amount
        current_balance = Decimal(str(getattr(user, 'balance_trx' if currency == 'TRX' else 'balance', 0) or 0))
        if current_balance < total_amount:
            await _safe_callback_answer(callback, f'{currency} 余额不足', show_alert=True)
            await _safe_edit_text(
                callback.message,
                f"{_bot_text('bot_custom_balance_insufficient', '❌ 余额不足，请先充值')}\n\n当前支付币种: {currency}",
                reply_markup=wallet_recharge_prompt_menu(f'custom:orderpaypage:{order_id}', '🔙 返回支付页'),
            )
            return
        await _safe_edit_text(
            callback.message,
            _bot_text('bot_custom_pending_wallet', '⏳ 正在后台处理钱包支付，请稍候…\n\n处理完成后会主动把结果发给你。'),
        )
        asyncio.create_task(_pay_cloud_server_order_with_balance_and_notify(bot, callback.from_user.id, user.id, order_id, currency))

    def _is_group_chat_message(message) -> bool:
        return int(getattr(getattr(message, 'chat', None), 'id', 0) or 0) < 0

    async def _deny_group_high_risk_callback(callback: CallbackQuery, action_label: str) -> bool:
        if _is_group_chat_message(callback.message) and not await _is_admin_chat(callback.message):
            await _safe_callback_answer(callback, f'群聊仅支持续费和自动续费，{action_label}请在私聊中操作。', show_alert=True)
            return True
        return False

    async def _is_recent_public_query_order(state: FSMContext, order_id: int) -> bool:
        data = await state.get_data()
        for item in data.get('cloud_query_results') or []:
            try:
                item_order_id = int(item.get('order_id') or 0)
            except (TypeError, ValueError):
                item_order_id = 0
            if item_order_id == int(order_id) and bool(item.get('renewable')):
                return True
        return False

    async def _can_use_renewal_payment_callback(callback: CallbackQuery, state: FSMContext, user, order_id: int) -> tuple[bool, bool, bool]:
        is_admin = await _is_admin_chat(callback.message)
        group_context = await _is_group_visible_order(callback, order_id)
        if is_admin or group_context:
            return True, is_admin, group_context
        existing = await get_cloud_order(order_id, user.id)
        if existing:
            return True, is_admin, group_context
        if not _is_group_chat_message(callback.message):
            visible_servers = await list_user_cloud_servers(user.id)
            for item in visible_servers:
                try:
                    visible_order_id = int(getattr(item, 'order_id', None) or 0)
                except (TypeError, ValueError):
                    visible_order_id = 0
                if visible_order_id == int(order_id):
                    return True, is_admin, group_context
        if await _is_recent_public_query_order(state, order_id):
            return True, is_admin, group_context
        return False, is_admin, group_context

    async def _visible_cloud_servers_for_context(callback: CallbackQuery, user):
        if _is_group_chat_message(callback.message):
            return await list_group_cloud_servers(callback.message.chat.id), '本群代理'
        return await list_user_cloud_servers(user.id), '我的代理'

    async def _visible_auto_renew_servers_for_context(callback: CallbackQuery, user, is_admin: bool):
        if _is_group_chat_message(callback.message):
            return await list_group_auto_renew_cloud_servers(callback.message.chat.id), '本群代理'
        if is_admin:
            return await list_all_auto_renew_cloud_servers(), '全部代理（管理员）'
        return await list_user_auto_renew_cloud_servers(user.id), '我的代理'

    async def _bulk_renew_visible_order_ids(callback: CallbackQuery, user) -> tuple[list[int], str, bool]:
        visible_servers, scope = await _visible_cloud_servers_for_context(callback, user)
        group_context = _is_group_chat_message(callback.message)
        return _bulk_renewable_order_ids(visible_servers, user_id=user.id, group_context=group_context), scope, group_context

    async def _is_group_visible_order(callback: CallbackQuery, order_id: int) -> bool:
        if not _is_group_chat_message(callback.message):
            return False
        visible_servers = await list_group_cloud_servers(callback.message.chat.id)
        for item in visible_servers:
            visible_order_id = int(getattr(item, 'order_id', None) or 0)
            if visible_order_id == int(order_id):
                return True
        return await is_retained_ip_order_visible_in_group(order_id, callback.message.chat.id)

    def _bulk_renewable_order_ids(items, *, user_id: int, group_context: bool) -> list[int]:
        order_ids = []
        seen = set()
        allowed_statuses = {'running', 'completed', 'expiring', 'suspended', 'renew_pending', 'paid', 'provisioning'}
        for item in items:
            order_id = int(getattr(item, 'order_id', None) or getattr(item, 'id', 0) or 0)
            if not order_id or order_id in seen:
                continue
            if not group_context and getattr(item, 'order_user_id', None) not in {None, user_id}:
                continue
            if not str(getattr(item, 'public_ip', None) or getattr(item, 'previous_public_ip', None) or '').strip():
                continue
            if str(getattr(item, 'status', '') or '') not in allowed_statuses:
                continue
            order_ids.append(order_id)
            seen.add(order_id)
        return order_ids

    @dp.callback_query(F.data == 'cloud:list')
    async def cb_cloud_list(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        visible_servers, scope = await _visible_cloud_servers_for_context(callback, user)
        page = 1
        per_page = 8
        total_visible = len(visible_servers)
        total_pages = max(1, math.ceil(total_visible / per_page))
        page_items = visible_servers[(page - 1) * per_page: page * per_page]
        if not page_items:
            await callback.message.delete()
            await callback.message.answer(_bot_text('bot_query_cloud_empty', '🔎 查询中心\n\n暂无可查询的代理记录。'), reply_markup=main_menu())
        else:
            await _safe_edit_text(callback.message, f'🔎 代理列表\n\n{scope}\n请选择要查看的代理：', reply_markup=cloud_server_list(page_items, page, total_pages, 'cloud:list:page', renew_all=True))

    @dp.callback_query(F.data.startswith('clp:'))
    @dp.callback_query(F.data.startswith('cloud:list:page:'))
    async def cb_cloud_list_page(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        if callback.data.startswith('clp:'):
            page = max(1, int(callback.data.split(':')[1]))
        else:
            page = max(1, int(callback.data.split(':')[3]))
        visible_servers, scope = await _visible_cloud_servers_for_context(callback, user)
        per_page = 8
        total_visible = len(visible_servers)
        total_pages = max(1, math.ceil(total_visible / per_page))
        page = min(page, total_pages)
        page_items = visible_servers[(page - 1) * per_page: page * per_page]
        if not page_items:
            await _safe_edit_text(callback.message, '🔎 查询中心\n\n暂无可查询的代理记录。', reply_markup=main_menu())
            return
        await _safe_edit_text(
            callback.message,
            f'🔎 代理列表\n\n{scope}\n请选择要查看的代理：',
            reply_markup=cloud_server_list(page_items, page, total_pages, 'cloud:list:page', renew_all=True),
        )

    @dp.callback_query(F.data == 'cloud:renewall:confirm')
    async def cb_cloud_renew_all_confirm(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_ids, scope, _group_context = await _bulk_renew_visible_order_ids(callback, user)
        if not order_ids:
            await _safe_callback_answer(callback, '当前列表暂无可续费代理', show_alert=True)
            return
        await _safe_edit_text(
            callback.message,
            '🔄 全部续费确认\n\n'
            f'{scope}\n'
            f'可续费数量: {len(order_ids)} 台\n\n'
            '确认后将使用你的 USDT 钱包余额逐台续费 31 天；若余额不足或某台需要单独补充资料，会跳过并在结果里说明。',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text=f'✅ 确认续费 {len(order_ids)} 台', callback_data='cloud:renewall:pay')],
                [InlineKeyboardButton(text='🔙 返回代理列表', callback_data='cloud:list')],
            ]),
        )

    @dp.callback_query(F.data == 'cloud:renewall:pay')
    async def cb_cloud_renew_all_pay(callback: CallbackQuery):
        await _safe_callback_answer(callback, '正在批量续费')
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        is_admin = await _is_admin_chat(callback.message)
        order_ids, scope, group_context = await _bulk_renew_visible_order_ids(callback, user)
        if not order_ids:
            await _safe_edit_text(callback.message, f'🔄 全部续费\n\n{scope}\n暂无可续费代理。', reply_markup=cloud_query_menu())
            return
        await _safe_edit_text(callback.message, f'🔄 全部续费\n\n{scope}\n正在处理 {len(order_ids)} 台代理，请稍候……')
        paid = []
        skipped = []
        failed = []
        for order_id in order_ids:
            retained_order, retained_plans, retained_err = await list_retained_ip_renewal_plans(order_id, user.id, admin=is_admin or group_context)
            if retained_err:
                failed.append(f'#{order_id}: {retained_err}')
                continue
            if retained_order and retained_plans:
                skipped.append(f'#{order_id}: 未附加固定 IP 需单独选择套餐/补链接')
                continue
            try:
                renewal = await create_cloud_server_renewal_by_public_query(order_id, 31) if (is_admin or group_context) else await create_cloud_server_renewal(order_id, user.id, 31)
            except RenewalPriceMissingError as exc:
                failed.append(f'#{order_id}: {exc}')
                continue
            if renewal is False:
                failed.append(f'#{order_id}: 该服务器 IP 已删除，禁止续费')
                continue
            if not renewal:
                failed.append(f'#{order_id}: 续费订单创建失败')
                continue
            paid_order, pay_err = await pay_cloud_server_renewal_with_balance(renewal.id, user.id, 'USDT', 31)
            if pay_err:
                failed.append(f'#{order_id}: {pay_err}')
                continue
            paid.append(paid_order)
            if _requires_recovery_provision(paid_order):
                asyncio.create_task(_provision_cloud_server_and_notify(callback.bot, callback.from_user.id, paid_order.id, paid_order.mtproxy_port or MTPROXY_DEFAULT_PORT))
            else:
                asyncio.create_task(_cloud_renewal_postcheck_and_notify(callback.bot, callback.from_user.id, paid_order.id, getattr(paid_order, 'renew_balance_change', None)))
        lines = ['🔄 全部续费结果', '', scope, f'成功: {len(paid)} 台', f'跳过: {len(skipped)} 台', f'失败: {len(failed)} 台']
        if paid:
            lines.append('')
            lines.append('已续费:')
            for order in paid[:10]:
                lines.append(f'- {getattr(order, "public_ip", None) or getattr(order, "previous_public_ip", None) or getattr(order, "order_no", None)}')
            if len(paid) > 10:
                lines.append(f'- 另 {len(paid) - 10} 台')
        if skipped:
            lines.append('')
            lines.append('需单独处理:')
            lines.extend(f'- {item}' for item in skipped[:8])
        if failed:
            lines.append('')
            lines.append('失败原因:')
            lines.extend(f'- {_public_cloud_error_text(item)}' for item in failed[:8])
        await _safe_edit_text(callback.message, '\n'.join(lines), reply_markup=cloud_query_menu())

    async def _render_cloud_auto_renew_list(callback: CallbackQuery, page: int = 1):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        is_admin = await _is_admin_chat(callback.message)
        visible_servers, scope = await _visible_auto_renew_servers_for_context(callback, user, is_admin)
        per_page = 8
        total_visible = len(visible_servers)
        total_pages = max(1, math.ceil(total_visible / per_page))
        page = min(max(1, page), total_pages)
        page_items = visible_servers[(page - 1) * per_page: page * per_page]
        if not page_items:
            await _safe_edit_text(callback.message, f'⚡ 自动续费列表\n\n{scope}\n暂无可设置自动续费的代理。', reply_markup=cloud_query_menu())
            return
        enabled_count = sum(1 for item in visible_servers if getattr(item, 'auto_renew_enabled', False))
        title = '⚡ 自动续费列表'
        text = f'{title}\n\n{scope}\n已开启 {enabled_count}/{total_visible}。\n✅=已开启，❌=已关闭；点击每行可开启/关闭。'
        await _safe_edit_text(
            callback.message,
            text,
            reply_markup=cloud_auto_renew_server_list(page_items, page, total_pages, is_admin=is_admin and not _is_group_chat_message(callback.message)),
        )

    @dp.callback_query(F.data == 'cloud:autorenewlist')
    async def cb_cloud_auto_renew_list(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await _safe_callback_answer(callback)
        await _render_cloud_auto_renew_list(callback, page=1)

    @dp.callback_query(F.data.startswith('cloud:autorenewlist:all:'))
    async def cb_cloud_auto_renew_list_all_toggle(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        is_admin = await _is_admin_chat(callback.message)
        _, _, _, action, raw_page = callback.data.split(':')
        enabled = action == 'on'
        page = max(1, int(raw_page or 1))
        group_context = _is_group_chat_message(callback.message)
        if group_context:
            result = await set_group_cloud_server_auto_renew(callback.message.chat.id, enabled)
        elif is_admin:
            result = await enable_all_cloud_server_auto_renew_admin() if enabled else await disable_all_cloud_server_auto_renew_admin()
        elif enabled:
            result = await enable_all_cloud_server_auto_renew(user.id)
        else:
            result = await disable_all_cloud_server_auto_renew(user.id)
        verb = '开启' if enabled else '关闭'
        await _safe_callback_answer(callback, f'已{verb} {result.get("updated", 0)} 个，跳过 {result.get("skipped", 0)} 个', show_alert=True)
        await _render_cloud_auto_renew_list(callback, page=page)

    @dp.callback_query(F.data.startswith('cloud:autorenewlist:on:'))
    @dp.callback_query(F.data.startswith('cloud:autorenewlist:off:'))
    async def cb_cloud_auto_renew_list_toggle(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, action, raw_order_id, raw_page = callback.data.split(':')
        enabled = action == 'on'
        order_id = int(raw_order_id)
        page = max(1, int(raw_page or 1))
        is_admin = await _is_admin_chat(callback.message)
        group_context = await _is_group_visible_order(callback, order_id)
        if _is_group_chat_message(callback.message) and not (is_admin or group_context):
            await _safe_callback_answer(callback, '该代理不属于当前群', show_alert=True)
            return
        if is_admin or group_context:
            order = await set_cloud_server_auto_renew_admin(order_id, enabled)
        else:
            order = await set_cloud_server_auto_renew(order_id, user.id, enabled)
        if order is False:
            await _safe_callback_answer(callback, '当前状态不可开启自动续费', show_alert=True)
            return
        if not order:
            await _safe_callback_answer(callback, '代理记录不存在', show_alert=True)
            return
        await _safe_callback_answer(callback, '已开启自动续费' if enabled else '已关闭自动续费')
        await _render_cloud_auto_renew_list(callback, page=page)

    @dp.callback_query(F.data.startswith('cloud:autorenewlist:page:'))
    async def cb_cloud_auto_renew_list_page(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await _safe_callback_answer(callback)
        page = max(1, int(callback.data.split(':')[3]))
        await _render_cloud_auto_renew_list(callback, page=page)

    @dp.callback_query(F.data.startswith('profile:orders:cloud:page:'))
    async def cb_profile_cloud_orders_page(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        page = max(1, int(callback.data.split(':')[4]))
        await _render_profile_cloud_orders(callback, page=page, order_filter='all')

    @dp.callback_query(F.data.startswith('cloud:orderdetail:'))
    async def cb_cloud_order_detail(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        parts = callback.data.split(':')
        order_id = int(parts[2])
        back_callback = 'profile:orders:cloud:page:1'
        if len(parts) > 3:
            back_callback = compact_callback_path(':'.join(parts[3:]))
        order = await get_cloud_order(order_id, user.id)
        if not order:
            await _safe_callback_answer(callback, '订单不存在', show_alert=True)
            return
        order = await _hydrate_order_proxy_links(order)
        logger.info('CLOUD_ORDER_READONLY_RENDER user_id=%s order_id=%s order_no=%s status=%s back=%s proxy_links=%s', user.id, order.id, order.order_no, order.status, back_callback, len(getattr(order, 'proxy_links', None) or []))
        await _safe_edit_text(
            callback.message,
            _cloud_order_readonly_text(order),
            reply_markup=cloud_order_readonly_detail(order.id, back_callback),
            parse_mode='HTML',
        )

    @dp.callback_query(F.data.startswith('adminreply:hint:'))
    async def cb_admin_reply_hint(callback: CallbackQuery, state: FSMContext):
        if not await _is_admin_chat(callback.message):
            await _safe_callback_answer(callback, '仅管理员可使用', show_alert=True)
            return
        link = await get_admin_reply_link(callback.message.chat.id, callback.message.message_id)
        if not link:
            await _safe_callback_answer(callback, '没有找到回复通道，请回复转发消息或使用 /reply 用户TGID 内容', show_alert=True)
            return
        await state.set_state(AdminReplyStates.waiting_reply)
        await state.update_data(admin_reply_link_id=link.id)
        await _safe_callback_answer(callback, '已进入回复模式：请直接发送下一条文字/图片/文件，我会转发给用户。', show_alert=True)

    @dp.callback_query(F.data.startswith('adminreply:mute3d:'))
    async def cb_admin_forward_mute_3d(callback: CallbackQuery):
        if not await _is_admin_chat(callback.message):
            await _safe_callback_answer(callback, '仅管理员可使用', show_alert=True)
            return
        raw_user_id = callback.data.rsplit(':', 1)[-1]
        if not raw_user_id.isdigit():
            await _safe_callback_answer(callback, '用户ID无效', show_alert=True)
            return
        muted_until = await mute_admin_forward_for_days(int(raw_user_id), 3)
        muted_text = muted_until.astimezone(timezone.get_current_timezone()).strftime('%Y-%m-%d %H:%M')
        await _safe_callback_answer(callback, f'已关闭该用户转发至 {muted_text}', show_alert=True)
        if callback.message:
            try:
                await callback.message.edit_reply_markup(reply_markup=_admin_reply_keyboard(int(raw_user_id)))
            except Exception:
                pass
        logger.info('ADMIN_FORWARD_MUTED_BY_ADMIN admin_chat_id=%s admin_user_id=%s muted_until=%s', getattr(callback.message.chat, 'id', None), raw_user_id, muted_until)

    @dp.callback_query(F.data.startswith('support:contact:'))
    async def cb_support_contact(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        await _safe_edit_text(
            callback.message,
            '👩‍💻 联系客服\n\n请直接在当前聊天发送你的问题、订单号或截图，我会转发给客服处理。',
            reply_markup=main_menu(),
        )

    @dp.callback_query(F.data.startswith('cloud:assetdetail:'))
    @dp.callback_query(F.data.startswith('cloud:ad:'))
    @dp.callback_query(F.data.startswith('cad:'))
    @dp.callback_query(F.data.startswith('csd:'))
    async def cb_cloud_asset_detail(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        parts = callback.data.split(':')
        if len(parts) >= 2 and parts[0] in {'cad', 'csd'}:
            item_kind = 'asset' if parts[0] == 'cad' else 'server'
            raw_item_id = parts[1]
            back_parts = parts[2:]
        elif len(parts) >= 4 and parts[:2] == ['cloud', 'ad']:
            item_kind = parts[2] or 'asset'
            raw_item_id = parts[3]
            back_parts = parts[4:]
        elif len(parts) >= 4 and parts[:2] == ['cloud', 'assetdetail'] and not str(parts[2]).isdigit():
            item_kind = parts[2] or 'asset'
            raw_item_id = parts[3]
            back_parts = parts[4:]
        elif len(parts) >= 3 and parts[:2] == ['cloud', 'assetdetail']:
            item_kind = 'asset'
            raw_item_id = parts[2]
            back_parts = parts[3:]
        else:
            await _safe_callback_answer(callback, '代理详情参数无效', show_alert=True)
            return
        if not str(raw_item_id).isdigit():
            await _safe_callback_answer(callback, '代理详情参数无效', show_alert=True)
            return
        item_id = int(raw_item_id)
        back_callback = compact_callback_path(':'.join(back_parts)) if back_parts else 'cloud:list'
        if _is_group_chat_message(callback.message):
            item = await get_group_proxy_asset_detail(item_id, callback.message.chat.id, item_kind)
        else:
            item = await get_proxy_asset_detail_for_admin(item_id, item_kind) if await _is_admin_chat(callback.message) else await get_user_proxy_asset_detail(item_id, user.id, item_kind)
        if not item:
            logger.warning('CLOUD_ASSET_DETAIL_DENIED user_id=%s item_id=%s callback_data=%s', user.id, item_id, callback.data)
            await _safe_callback_answer(callback, '代理记录不存在', show_alert=True)
            return
        has_link = bool(getattr(item, 'mtproxy_link', None) or getattr(item, 'proxy_links', None))
        order_id = getattr(item, 'order_id', None) if getattr(item, 'order_user_id', None) == user.id else None
        logger.info('CLOUD_ASSET_DETAIL_RENDER user_id=%s item_id=%s kind=%s ip=%s back=%s order_id=%s has_link=%s', user.id, item_id, getattr(item, '_proxy_item_kind', None), item.public_ip, back_callback, order_id, has_link)
        is_group_context = _is_group_chat_message(callback.message)
        is_admin_context = await _is_admin_chat(callback.message)
        item_order_id = getattr(item, 'order_id', None)
        if is_group_context and not is_admin_context:
            rows = []
            if item_order_id:
                rows.append([
                    InlineKeyboardButton(text='🔄 续费', callback_data=append_back_callback(f'cloud:renew:{item_order_id}', back_callback)),
                    InlineKeyboardButton(text=f'{"⛔ 关闭" if getattr(item, "auto_renew_enabled", False) else "⚡ 开启"}自动续费', callback_data=f'cloud:autorenew:{"off" if getattr(item, "auto_renew_enabled", False) else "on"}:{item_order_id}'),
                ])
            else:
                rows.append([InlineKeyboardButton(text='🔄 续费', callback_data=cloud_asset_action_callback('renew', item_id, back_callback))])
        else:
            status = str(getattr(item, 'status', '') or '')
            provider = str(getattr(item, 'provider', '') or '')
            has_ip = bool(str(getattr(item, 'public_ip', '') or '').strip())
            can_change_ip = bool(provider == 'aws_lightsail' and status in {'completed', 'running', 'expiring', 'suspended'} and (is_admin_context or getattr(item, 'order_user_id', None) == user.id))
            can_reinit = bool(provider == 'aws_lightsail' and has_ip and getattr(item, 'login_password', None) and (is_admin_context or status in {'completed', 'running'}))
            can_config = bool(provider == 'aws_lightsail' and status in {'completed', 'running', 'expiring', 'suspended'} and (is_admin_context or getattr(item, 'order_user_id', None) == user.id))
            rows = [[InlineKeyboardButton(text='🔄 续费', callback_data=cloud_asset_action_callback('renew', item_id, back_callback))]]
            second_row = []
            if can_change_ip:
                second_row.append(InlineKeyboardButton(text='🌐 更换IP', callback_data=cloud_asset_action_callback('changeip', item_id, back_callback)))
            if can_reinit:
                second_row.append(InlineKeyboardButton(text='🛠 重新安装', callback_data=append_back_callback(f'cloud:assetinit:{item_id}', back_callback)))
            if second_row:
                rows.append(second_row)
            third_row = []
            if can_config:
                third_row.append(InlineKeyboardButton(text='⚙️ 修改配置', callback_data=cloud_asset_action_callback('upgrade', item_id, back_callback)))
            if is_admin_context:
                third_row.append(InlineKeyboardButton(text='🕒 修改时间', callback_data=append_back_callback(f'exp:a:{item_id}', back_callback)))
            if third_row:
                rows.append(third_row)
        rows.append([support_contact_button('cloud_asset', item_id)])
        rows.append([InlineKeyboardButton(text='🔙 返回代理列表', callback_data=back_callback)])
        await _safe_edit_text(callback.message, _cloud_asset_detail_text(item), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode='HTML')

    @dp.callback_query(F.data.startswith('cloud:assetaction:'))
    @dp.callback_query(F.data.startswith('cloud:aa:'))
    @dp.callback_query(F.data.startswith('ar:'))
    @dp.callback_query(F.data.startswith('ac:'))
    @dp.callback_query(F.data.startswith('au:'))
    async def cb_cloud_asset_action(callback: CallbackQuery):
        if callback.data.startswith(('ar:', 'ac:', 'au:')):
            action_prefix = callback.data.split(':', 1)[0]
            action = {'ar': 'renew', 'ac': 'changeip', 'au': 'upgrade'}[action_prefix]
            parts = callback.data.split(':', 2)
            asset_id = int(parts[1])
            back_callback = compact_callback_path(parts[2]) if len(parts) > 2 else 'clp:1'
        else:
            parts = callback.data.split(':', 4)
            action = parts[2]
            asset_id = int(parts[3])
            back_callback = compact_callback_path(parts[4]) if len(parts) > 4 else 'clp:1'
        asset_detail_back = cloud_asset_detail_callback(asset_id, back_callback)
        if action == 'upgrade':
            await _safe_callback_answer(callback, '正在加载修改配置')
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        is_admin = await _is_admin_chat(callback.message)
        group_context = _is_group_chat_message(callback.message)
        if group_context and not is_admin and action != 'renew':
            await _safe_callback_answer(callback, '群聊仅支持续费和自动续费，高风险操作请在私聊中操作。', show_alert=True)
            return
        if group_context and not is_admin:
            item = await get_group_proxy_asset_detail(asset_id, callback.message.chat.id, 'asset')
        else:
            item = await get_proxy_asset_detail_for_admin(asset_id, 'asset') if is_admin else await get_user_proxy_asset_detail(asset_id, user.id, 'asset')
        public_asset_renewal = False
        if not item and action == 'renew':
            retained_group_chat_id = callback.message.chat.id if group_context and not is_admin else None
            retained_order, retained_plans, retained_err = await list_retained_ip_renewal_plans_by_asset(asset_id, user.id, admin=is_admin, group_chat_id=retained_group_chat_id)
            if retained_err:
                await _safe_callback_answer(callback, retained_err, show_alert=True)
                return
            if retained_order and retained_plans:
                display_user = getattr(retained_order, 'user', None) or user
                await _safe_edit_text(
                    callback.message,
                    _retained_ip_renewal_plan_text(retained_order, retained_plans, display_user),
                    reply_markup=_retained_ip_renewal_plan_keyboard(retained_order.id, retained_plans, asset_detail_back),
                )
                return
        if item and action == 'renew' and group_context and not is_admin and not getattr(item, 'order_id', None):
            public_asset_renewal = True
        if not item and action == 'renew' and not is_admin:
            public_asset, public_plans, public_err = await list_cloud_asset_renewal_plans(asset_id, user.id, public=True)
            if public_asset and public_plans:
                item = public_asset
                public_asset_renewal = True
        if not item:
            await _safe_callback_answer(callback, '代理记录不存在', show_alert=True)
            return
        if action == 'renew' and not getattr(item, 'order_id', None):
            asset, plans, plan_err = await list_cloud_asset_renewal_plans(asset_id, user.id, admin=is_admin, public=public_asset_renewal)
            if plan_err:
                await _safe_callback_answer(callback, plan_err, show_alert=True)
                return
            if asset and plans:
                display_user = user if public_asset_renewal else (getattr(asset, 'user', None) or user)
                await _safe_edit_text(
                    callback.message,
                    _asset_renewal_plan_text(asset, plans, display_user),
                    reply_markup=_asset_renewal_plan_keyboard(asset.id, plans, back_callback),
                )
                return
        order, err = await ensure_cloud_asset_operation_order(asset_id, user.id, admin=is_admin)
        if err:
            await _safe_callback_answer(callback, err, show_alert=True)
            return
        logger.info('CLOUD_ASSET_ACTION_START user_id=%s asset_id=%s order_id=%s action=%s ip=%s admin=%s', user.id, asset_id, order.id, action, getattr(item, 'public_ip', None), is_admin)
        if action == 'renew':
            retained_order, retained_plans, retained_err = await list_retained_ip_renewal_plans(order.id, user.id, admin=is_admin)
            if retained_err:
                await _safe_callback_answer(callback, retained_err, show_alert=True)
                return
            if retained_order and retained_plans:
                display_user = getattr(retained_order, 'user', None) or user
                await _safe_edit_text(
                    callback.message,
                    _retained_ip_renewal_plan_text(retained_order, retained_plans, display_user),
                    reply_markup=_retained_ip_renewal_plan_keyboard(retained_order.id, retained_plans, asset_detail_back),
                )
                return
            try:
                renewal = await create_cloud_server_renewal_by_public_query(order.id, 31) if is_admin else await create_cloud_server_renewal_for_user(order.id, user.id, 31)
            except RenewalPriceMissingError as exc:
                await _safe_callback_answer(callback, str(exc), show_alert=True)
                return
            if renewal is False:
                await _safe_callback_answer(callback, '该服务器IP已删除，禁止续费', show_alert=True)
                return
            if not renewal:
                await _safe_callback_answer(callback, '续费订单创建失败', show_alert=True)
                return
            await _send_cloud_renewal_payment_prompt(callback.message, renewal, user, edit=True, back_callback=asset_detail_back)
            return
        if action == 'changeip':
            if order.provider != 'aws_lightsail':
                logger.info('CLOUD_ASSET_ACTION_DENIED user_id=%s asset_id=%s order_id=%s action=%s reason=unsupported_provider provider=%s', user.id, asset_id, order.id, action, order.provider)
                await _safe_callback_answer(callback, '当前代理暂不支持更换 IP', show_alert=True)
                await _safe_edit_text(
                    callback.message,
                    '🌐 更换IP\n\n当前代理暂不支持自助更换 IP。',
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [support_contact_button('cloud_asset_changeip_provider', asset_id)],
                        [InlineKeyboardButton(text='🔙 返回代理详情', callback_data=asset_detail_back)],
                    ]),
                )
                return
            if max(int(getattr(order, 'ip_change_quota', 0) or 0), 0) <= 0:
                logger.info('CLOUD_ASSET_ACTION_DENIED user_id=%s asset_id=%s order_id=%s action=%s reason=no_ip_change_quota quota=%s', user.id, asset_id, order.id, action, getattr(order, 'ip_change_quota', None))
                await _safe_callback_answer(callback, '剩余更换 IP 次数不足，请续费后再试', show_alert=True)
                await _safe_edit_text(
                    callback.message,
                    '🌐 更换IP\n\n这台代理当前剩余更换 IP 次数为 0。\n\n请先续费获取新的更换 IP 次数，或联系客服人工处理。',
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text='🔄 去续费', callback_data=cloud_asset_action_callback('renew', asset_id, back_callback))],
                        [support_contact_button('cloud_asset_changeip_quota', asset_id)],
                        [InlineKeyboardButton(text='🔙 返回代理详情', callback_data=asset_detail_back)],
                    ]),
                )
                return
            regions = [(code, name) for code, name in await _get_cached_custom_regions() if code != 'cn-hongkong']
            text = '🌐 更换IP\n\n请选择新的地区：'
            markup = cloud_server_change_ip_region_menu(order.id, regions, expanded=False, back_callback=asset_detail_back)
            edited = await _safe_edit_text(callback.message, text, reply_markup=markup)
            if edited is None:
                sent = await callback.message.reply(text, reply_markup=markup)
                logger.info('BOT_MESSAGE_SEND route=asset_change_ip_regions_fallback user_id=%s asset_id=%s order_id=%s chat_id=%s reply_to=%s sent_message_id=%s regions=%s', user.id, asset_id, order.id, callback.message.chat.id, callback.message.message_id, getattr(sent, 'message_id', None), regions)
            else:
                logger.info('BOT_MESSAGE_EDIT route=asset_change_ip_regions user_id=%s asset_id=%s order_id=%s chat_id=%s message_id=%s regions=%s', user.id, asset_id, order.id, callback.message.chat.id, callback.message.message_id, regions)
            return
        if action == 'upgrade':
            plans, err = await list_cloud_server_upgrade_plans(order.id, user.id, admin=is_admin)
            if err:
                logger.info('CLOUD_ASSET_UPGRADE_PLAN_DENIED user_id=%s asset_id=%s order_id=%s admin=%s reason=%s', user.id, asset_id, order.id, is_admin, err)
                await _safe_callback_answer(callback, err, show_alert=True)
                await callback.message.reply(f'⚙️ 修改配置暂不可用\n\n原因：{err}')
                return
            if not plans:
                logger.info('CLOUD_ASSET_UPGRADE_PLAN_EMPTY user_id=%s asset_id=%s order_id=%s admin=%s', user.id, asset_id, order.id, is_admin)
                await _safe_callback_answer(callback, '暂无可修改的配置', show_alert=True)
                await callback.message.reply('⚙️ 修改配置暂不可用\n\n原因：暂无可修改的配置')
                return
            rows = []
            text_lines = ['⚙️ 修改配置', '', '请选择目标配置。升级会从 USDT 余额扣除差价；降级不退差价。系统会创建目标规格服务器，主/备用代理链接保持不变。']
            for plan in plans[:10]:
                action_text = '升级' if plan.get('action') == 'upgrade' else '降级'
                charge_text = f"补 {plan['diff']} U" if plan.get('action') == 'upgrade' else '无需补差价'
                text_lines.append(f"- {plan['name']}：{action_text}，{charge_text}，到期补足 {plan['target_days']} 天")
                rows.append([InlineKeyboardButton(text=f"{action_text}到 {plan['name']} {charge_text}", callback_data=append_back_callback(f"upp:{order.id}:{plan['id']}", asset_detail_back))])
            rows.append([InlineKeyboardButton(text='🔙 返回详情', callback_data=asset_detail_back)])
            text = '\n'.join(text_lines)
            markup = InlineKeyboardMarkup(inline_keyboard=rows)
            edited = await _safe_edit_text(callback.message, text, reply_markup=markup)
            if edited is None:
                sent = await callback.message.reply(text, reply_markup=markup)
                logger.info('BOT_MESSAGE_SEND route=asset_upgrade_plans_fallback user_id=%s asset_id=%s order_id=%s chat_id=%s reply_to=%s sent_message_id=%s plans=%s', user.id, asset_id, order.id, callback.message.chat.id, callback.message.message_id, getattr(sent, 'message_id', None), len(plans))
            else:
                logger.info('BOT_MESSAGE_EDIT route=asset_upgrade_plans user_id=%s asset_id=%s order_id=%s chat_id=%s message_id=%s plans=%s', user.id, asset_id, order.id, callback.message.chat.id, callback.message.message_id, len(plans))
            await _safe_callback_answer(callback)
            return
        await _safe_callback_answer(callback, '未知操作', show_alert=True)

    @dp.callback_query(F.data.startswith('ai:'))
    @dp.callback_query(F.data.startswith('cloud:assetinit:'))
    async def cb_cloud_asset_init(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        if await _deny_group_high_risk_callback(callback, '重新安装'):
            return
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        if callback.data.startswith('ai:'):
            parts = callback.data.split(':', 2)
            asset_id = int(parts[1])
            back_callback = compact_callback_path(parts[2]) if len(parts) > 2 else 'cloud:list:page:1'
        else:
            parts = callback.data.split(':', 3)
            asset_id = int(parts[2])
            back_callback = compact_callback_path(parts[3]) if len(parts) > 3 else 'cloud:list:page:1'
        is_admin = await _is_admin_chat(callback.message)
        item = await get_proxy_asset_detail_for_admin(asset_id, 'asset') if is_admin else await get_user_proxy_asset_detail(asset_id, user.id, 'asset')
        if not item:
            await _safe_callback_answer(callback, '代理记录不存在', show_alert=True)
            return
        await state.update_data(reinstall_asset_id=asset_id, reinstall_order_id=0, reinstall_admin=is_admin, reinstall_back=back_callback)
        await state.set_state(CustomServerStates.waiting_reinstall_link)
        logger.info('CLOUD_ASSET_REINSTALL_LINK_WAIT user_id=%s asset_id=%s ip=%s', user.id, asset_id, getattr(item, 'public_ip', None))
        await callback.message.reply(_bot_text('bot_reinstall_need_main_link', '当前服务器缺少主代理链接。请直接发送这台服务器的主代理链接，我会先校验 IP、端口和服务器实际密钥，再让你确认是否重新安装。'))

    @dp.callback_query(F.data.startswith('cloud:assetreinitconfirm:'))
    async def cb_cloud_asset_reinit_confirm(callback: CallbackQuery, bot: Bot, state: FSMContext):
        if await _deny_group_high_risk_callback(callback, '重新安装'):
            return
        parts = callback.data.split(':')
        asset_id = int(parts[2])
        token = parts[3] if len(parts) > 3 else ''
        if not await _consume_reinstall_confirm_token(state, kind='asset', item_id=asset_id, token=token):
            await _safe_callback_answer(callback, '这个确认按钮已过期或已使用，请重新进入详情并重新生成按钮。', show_alert=True)
            return
        await _safe_callback_answer(callback, '已确认，后台处理中')
        await _safe_remove_inline_keyboard(callback.message)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        is_admin = await _is_admin_chat(callback.message)
        item = await get_proxy_asset_detail_for_admin(asset_id, 'asset') if is_admin else await get_user_proxy_asset_detail(asset_id, user.id, 'asset')
        if not item:
            await callback.message.reply('代理记录不存在，请重新进入详情并重新生成按钮。')
            return
        if asset_id in _ASSET_REINIT_INFLIGHT:
            logger.info('CLOUD_ASSET_REINIT_DUPLICATE user_id=%s asset_id=%s ip=%s', user.id, asset_id, getattr(item, 'public_ip', None))
            await callback.message.reply('这台代理正在重新安装，请勿重复点击；如需再次操作，请等待当前任务结束后重新生成按钮。')
            return
        order, err = await ensure_cloud_asset_operation_order(asset_id, user.id, admin=is_admin)
        if err or not order:
            logger.warning('CLOUD_ASSET_REINIT_DENIED user_id=%s asset_id=%s reason=%s', user.id, asset_id, err or '无法创建代理操作订单')
            await callback.message.reply(err or '无法创建代理操作订单，请重新进入详情并重新生成按钮。')
            return
        rebuild_order = await mark_cloud_server_reinit_requested(order.id, None if is_admin else user.id)
        if not rebuild_order:
            logger.warning('CLOUD_ASSET_REINIT_DENIED user_id=%s asset_id=%s order_id=%s reason=missing_order', user.id, asset_id, order.id)
            await callback.message.reply('服务器记录不存在，请重新进入详情并重新生成按钮。')
            return
        if isinstance(rebuild_order, str):
            logger.warning('CLOUD_ASSET_REINIT_DENIED user_id=%s asset_id=%s order_id=%s reason=%s', user.id, asset_id, order.id, rebuild_order)
            await callback.message.reply(f'{rebuild_order}\n请重新进入详情并重新生成按钮。')
            return
        _ASSET_REINIT_INFLIGHT.add(asset_id)
        logger.info('CLOUD_ASSET_REINIT_SUBMIT user_id=%s asset_id=%s order_id=%s target_order_id=%s ip=%s', user.id, asset_id, order.id, rebuild_order.id, getattr(item, 'public_ip', None))
        await callback.message.reply('🛠 已确认重新安装：后台只会在当前服务器重新执行 BBR/MTProxy 安装，不会创建新实例，也不会迁移固定 IP。预计约 5 分钟，完成后会自动通知你。\n\n后台处理期间，底部菜单和其它按钮可正常使用。', reply_markup=main_menu())
        task = asyncio.create_task(_provision_cloud_server_and_notify(bot, callback.from_user.id, rebuild_order.id, rebuild_order.mtproxy_port or MTPROXY_DEFAULT_PORT, retry_only=True))
        task.add_done_callback(lambda _task, _asset_id=asset_id: _ASSET_REINIT_INFLIGHT.discard(_asset_id))

    @dp.callback_query(F.data.startswith('cloud:detail:'))
    async def cb_cloud_detail(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        parts = callback.data.split(':')
        order_id = int(parts[2])
        back_callback = ':'.join(parts[3:]) if len(parts) > 3 else 'cloud:list'
        order = await get_user_cloud_server(order_id, user.id)
        if not order:
            logger.warning('CLOUD_DETAIL_DENIED user_id=%s order_id=%s reason=not_found callback_data=%s', user.id, order_id, callback.data)
            await _safe_callback_answer(callback, '服务器记录不存在', show_alert=True)
            return
        if order.status not in {'completed', 'expiring', 'suspended', 'renew_pending', 'provisioning', 'paid', 'failed'} or not str(order.public_ip or '').strip():
            logger.info(
                'CLOUD_DETAIL_READONLY user_id=%s order_id=%s order_no=%s status=%s public_ip=%s reason=not_operable callback_data=%s',
                user.id,
                order.id,
                order.order_no,
                order.status,
                order.public_ip,
                callback.data,
            )
        now = timezone.now()
        group_limited = _is_group_chat_message(callback.message) and not await _is_admin_chat(callback.message)
        can_renew = bool(order.public_ip and order.status in {'completed', 'expiring', 'suspended', 'renew_pending', 'provisioning', 'paid'})
        can_change_ip = bool(not group_limited and order.provider == 'aws_lightsail' and order.status in {'completed', 'expiring', 'suspended'} and max(int(getattr(order, 'ip_change_quota', 0) or 0), 0) > 0)
        can_resume_init = bool(not group_limited and order.status in {'paid', 'provisioning', 'failed'} and (order.public_ip or not order.mtproxy_secret or not order.mtproxy_link or not order.login_password))
        can_reinit = bool(not group_limited and order.public_ip and order.login_password and order.status == 'completed')
        can_upgrade = bool(not group_limited and order.provider == 'aws_lightsail' and order.status in {'completed', 'expiring', 'suspended'})
        logger.info(
            'CLOUD_DETAIL_RENDER user_id=%s order_id=%s order_no=%s status=%s provider=%s public_ip=%s login_password=%s mtproxy_secret=%s mtproxy_link=%s buttons={renew:%s,change_ip:%s,resume_init:%s,reinit:%s,config:%s} back=%s',
            user.id,
            order.id,
            order.order_no,
            order.status,
            order.provider,
            order.public_ip,
            bool(order.login_password),
            bool(order.mtproxy_secret),
            bool(order.mtproxy_link),
            can_renew,
            can_change_ip,
            can_resume_init,
            can_reinit,
            can_upgrade,
            back_callback,
        )
        await _safe_edit_text(
            callback.message,
            _cloud_server_detail_text(order),
            reply_markup=cloud_server_detail(order.id, can_renew, can_change_ip, can_reinit, back_callback, can_upgrade, can_resume_init),
            parse_mode='HTML',
        )

    @dp.callback_query(F.data.startswith('cloud:mute:'))
    async def cb_cloud_mute(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        parts = callback.data.split(':')
        raw_order_id = parts[2] if len(parts) >= 3 else ''
        updated = await mute_cloud_order_reminders(int(raw_order_id), user.id) if str(raw_order_id).isdigit() else None
        if not updated:
            if len(parts) >= 4 and str(parts[3]).isdigit():
                legacy = await mute_cloud_reminders(user.id, int(parts[3]))
                if legacy:
                    await _safe_callback_answer(callback, '已关闭提醒')
                    return
            await _safe_callback_answer(callback, '关闭提醒失败', show_alert=True)
            return
        logger.info('CLOUD_NOTICE_MUTE_ORDER user_id=%s order_id=%s order_no=%s callback_data=%s', user.id, updated.id, updated.order_no, callback.data)
        await _safe_callback_answer(callback, '已关闭该订单提醒')
        await _safe_remove_inline_keyboard(callback.message)

    @dp.callback_query(F.data.startswith('r:'))
    @dp.callback_query(F.data.startswith('cloud:renew:'))
    async def cb_cloud_renew(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        if callback.data.startswith('r:'):
            parts = callback.data.split(':', 2)
            order_id = int(parts[1])
            back_callback = compact_callback_path(parts[2]) if len(parts) > 2 else ''
        else:
            parts = callback.data.split(':', 3)
            order_id = int(parts[2])
            back_callback = compact_callback_path(parts[3]) if len(parts) > 3 else ''
        is_admin = await _is_admin_chat(callback.message)
        group_context = await _is_group_visible_order(callback, order_id)
        if _is_group_chat_message(callback.message) and not (is_admin or group_context):
            await _safe_callback_answer(callback, '该代理不属于当前群', show_alert=True)
            return
        retained_order, retained_plans, retained_err = await list_retained_ip_renewal_plans(order_id, user.id, admin=is_admin or group_context)
        if retained_err:
            await _safe_callback_answer(callback, retained_err, show_alert=True)
            return
        if retained_order and retained_plans:
            await _safe_edit_text(
                callback.message,
                _retained_ip_renewal_plan_text(retained_order, retained_plans, getattr(retained_order, 'user', None) or user),
                reply_markup=_retained_ip_renewal_plan_keyboard(retained_order.id, retained_plans, back_callback),
            )
            return
        try:
            public_query_context = False
            if is_admin or group_context:
                order = await create_cloud_server_renewal_by_public_query(order_id, 31)
            else:
                order = await create_cloud_server_renewal(order_id, user.id, 31)
                if not order and await _is_recent_public_query_order(state, order_id):
                    public_query_context = True
                    order = await create_cloud_server_renewal_by_public_query(order_id, 31)
                if not order and not public_query_context:
                    logger.warning('CLOUD_RENEW_DENIED user_id=%s order_id=%s reason=not_owner_or_recent_query callback_data=%s', user.id, order_id, callback.data)
        except RenewalPriceMissingError as exc:
            await _safe_callback_answer(callback, str(exc), show_alert=True)
            return
        if order is False:
            await _safe_callback_answer(callback, '该服务器IP已删除，禁止续费', show_alert=True)
            return
        if not order:
            await _safe_callback_answer(callback, '续费订单创建失败', show_alert=True)
            return
        await _send_cloud_renewal_payment_prompt(callback.message, order, user, edit=True, back_callback=back_callback)

    @dp.callback_query(F.data.startswith('arp:'))
    @dp.callback_query(F.data.startswith('cloud:assetrenewplan:'))
    async def cb_cloud_asset_renew_plan(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        if callback.data.startswith('arp:'):
            parts = callback.data.split(':', 3)
            _, raw_asset_id, raw_plan_id = parts[:3]
            back_callback = compact_callback_path(parts[3]) if len(parts) > 3 else ''
        else:
            parts = callback.data.split(':', 4)
            _, _, raw_asset_id, raw_plan_id = parts[:4]
            back_callback = compact_callback_path(parts[4]) if len(parts) > 4 else ''
        asset_id = int(raw_asset_id)
        plan_id = int(raw_plan_id)
        is_admin = await _is_admin_chat(callback.message)
        asset, plans, err = await list_cloud_asset_renewal_plans(asset_id, user.id, admin=is_admin, public=not is_admin)
        if err:
            await _safe_callback_answer(callback, err, show_alert=True)
            return
        plan = next((item for item in plans if item.id == plan_id), None)
        if not asset or not plan:
            await _safe_callback_answer(callback, '套餐不存在或当前状态已变化，请重新进入详情', show_alert=True)
            return
        await state.update_data(asset_renewal_asset_id=asset.id, asset_renewal_plan_id=plan.id, asset_renewal_admin=is_admin, asset_renewal_public=not is_admin and getattr(asset, 'user_id', None) != user.id, asset_renewal_back=back_callback)
        await state.set_state(CustomServerStates.waiting_retained_ip_renewal_link)
        ip = getattr(asset, 'public_ip', None) or getattr(asset, 'previous_public_ip', None) or '-'
        await callback.message.reply(
            f'🔄 未绑定代理资产续费\n\n已选择套餐: {_plan_display_name(plan)}\nIP: {ip}\n\n请直接发送这台代理旧的主代理链接（tg://proxy?... 或 https://t.me/proxy?...）。\n校验通过后生成支付订单，支付完成后系统会自动创建服务器并绑定旧 IP。'
        )

    @dp.callback_query(F.data.startswith('rnp:'))
    @dp.callback_query(F.data.startswith('cloud:renewplan:'))
    async def cb_cloud_retained_ip_renew_plan(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        if callback.data.startswith('rnp:'):
            parts = callback.data.split(':', 3)
            _, raw_order_id, raw_plan_id = parts[:3]
            back_callback = compact_callback_path(parts[3]) if len(parts) > 3 else ''
        else:
            parts = callback.data.split(':', 4)
            _, _, raw_order_id, raw_plan_id = parts[:4]
            back_callback = compact_callback_path(parts[4]) if len(parts) > 4 else ''
        order_id = int(raw_order_id)
        plan_id = int(raw_plan_id)
        is_admin = await _is_admin_chat(callback.message)
        group_context = await _is_group_visible_order(callback, order_id)
        if _is_group_chat_message(callback.message) and not (is_admin or group_context):
            await _safe_callback_answer(callback, '该代理不属于当前群', show_alert=True)
            return
        order, plans, err = await list_retained_ip_renewal_plans(order_id, user.id, admin=is_admin or group_context)
        if err:
            await _safe_callback_answer(callback, err, show_alert=True)
            return
        plan = next((item for item in plans if item.id == plan_id), None)
        if not order or not plan:
            await _safe_callback_answer(callback, '套餐不存在或当前状态已变化，请重新进入详情', show_alert=True)
            return
        await state.update_data(
            retained_ip_renewal_order_id=order.id,
            retained_ip_renewal_plan_id=plan.id,
            retained_ip_renewal_admin=is_admin,
            retained_ip_renewal_group_chat_id=callback.message.chat.id if group_context else None,
            retained_ip_renewal_back=back_callback,
        )
        await state.set_state(CustomServerStates.waiting_retained_ip_renewal_link)
        ip = getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '-'
        await callback.message.reply(
            _bot_text_format(
                'bot_retained_ip_renewal_link_prompt',
                '🔄 未附加固定 IP 续费\n\n已选择套餐: {plan_name}\n保留 IP: {ip}\n\n请直接发送这台服务器旧的主代理链接（tg://proxy?... 或 https://t.me/proxy?...）。\n我会校验 IP、端口和密钥；如果系统记录的主端口不对，会以你发送的主链接端口为准；校验通过后再生成续费支付订单。',
                plan_name=_plan_display_name(plan),
                ip=ip,
            )
        )

    @dp.callback_query(F.data.startswith('cloud:start:'))
    async def cb_cloud_start(callback: CallbackQuery):
        if not await _is_admin_chat(callback.message):
            await _safe_callback_answer(callback, '仅管理员可使用开机', show_alert=True)
            return
        order_id = int(callback.data.split(':')[2])
        logger.info('CLOUD_ADMIN_START_CLICK admin_id=%s order_id=%s callback_data=%s', getattr(callback.from_user, 'id', None), order_id, callback.data)
        await _safe_callback_answer(callback, '正在检查开机状态')
        order, err = await start_cloud_server_from_admin(order_id)
        ip = getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '-'
        order_no = getattr(order, 'order_no', None) or f'#{order_id}'
        if err:
            logger.warning('CLOUD_ADMIN_START_RESULT admin_id=%s order_id=%s order_no=%s ip=%s ok=false error=%s', getattr(callback.from_user, 'id', None), order_id, order_no, ip, err)
            await _safe_edit_text(
                callback.message,
                f'⚠️ 开机结果\n\n订单: {order_no}\nIP: {ip}\n状态: {_public_cloud_error_text(err)}',
            )
            return
        note = str(getattr(order, 'provision_note', '') or '').split('管理员手动开机：')[-1].strip() or '已提交开机检查。'
        logger.info('CLOUD_ADMIN_START_RESULT admin_id=%s order_id=%s order_no=%s ip=%s ok=true note=%s', getattr(callback.from_user, 'id', None), order_id, order_no, ip, note)
        await _safe_edit_text(
            callback.message,
            f'✅ 开机检查完成\n\n订单: {order_no}\nIP: {ip}\n状态: {note}',
        )

    @dp.callback_query(F.data.startswith('cloud:autorenew:'))
    async def cb_cloud_auto_renew_toggle(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, action, order_id_text = callback.data.split(':')
        order_id = int(order_id_text)
        enabled = action == 'on'
        is_admin = await _is_admin_chat(callback.message)
        group_context = await _is_group_visible_order(callback, order_id)
        if _is_group_chat_message(callback.message) and not (is_admin or group_context):
            await _safe_callback_answer(callback, '该代理不属于当前群', show_alert=True)
            return
        if is_admin or group_context:
            order = await set_cloud_server_auto_renew_admin(order_id, enabled)
        else:
            order = await set_cloud_server_auto_renew(order_id, user.id, enabled)
        if order is False:
            await _safe_callback_answer(callback, '当前状态不可开启钱包自动续费', show_alert=True)
            return
        if not order:
            await _safe_callback_answer(callback, '服务器记录不存在', show_alert=True)
            return
        label = order.public_ip or order.previous_public_ip or order.order_no
        await _safe_callback_answer(callback, f'{label} 钱包自动续费已{"开启" if enabled else "关闭"}', show_alert=True)
        if callback.message:
            message_text = callback.message.html_text or callback.message.text or ''
            for old_status in ('自动续费: 未开启', '自动续费: 已关闭', '自动续费: 已开启'):
                if old_status in message_text:
                    message_text = message_text.replace(old_status, f'自动续费: {"已开启" if enabled else "未开启"}', 1)
                    break
            current_markup = getattr(callback.message, 'reply_markup', None)
            new_markup = None
            if current_markup:
                rows = []
                target_on = f'cloud:autorenew:on:{order.id}'
                target_off = f'cloud:autorenew:off:{order.id}'
                for row in current_markup.inline_keyboard:
                    new_row = []
                    for button in row:
                        if button.callback_data in {target_on, target_off}:
                            new_row.append(InlineKeyboardButton(text=f'{"⛔ 关闭" if enabled else "⚡ 开启"}自动续费', callback_data=target_off if enabled else target_on))
                        else:
                            new_row.append(button)
                    rows.append(new_row)
                new_markup = InlineKeyboardMarkup(inline_keyboard=rows)
            await _safe_edit_text(callback.message, message_text, reply_markup=new_markup or current_markup, parse_mode='HTML')

    def _retained_recovery_missing_payment_text(order) -> str:
        if not order or order.status != 'completed' or not order.paid_at:
            return ''
        has_retained_ip = bool(
            order.provider == 'aws_lightsail'
            and getattr(order, 'ip_recycle_at', None)
            and (order.public_ip or order.previous_public_ip)
            and not str(getattr(order, 'instance_id', '') or '').strip()
        )
        if not has_retained_ip:
            return ''
        missing = []
        if not str(getattr(order, 'static_ip_name', '') or '').strip():
            missing.append('固定 IP 名称')
        if not str(getattr(order, 'mtproxy_secret', '') or '').strip():
            missing.append('旧 MTProxy 密钥')
        if not missing:
            return ''
        return '、'.join(missing)

    @dp.callback_query(F.data.startswith('cloud:renewwallet:'))
    async def cb_cloud_renew_wallet(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback, '钱包自动续费处理中')
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        parts = callback.data.split(':', 3)
        order_id = int(parts[2])
        back_callback = compact_callback_path(parts[3]) if len(parts) > 3 else ''
        allowed, is_admin, group_context = await _can_use_renewal_payment_callback(callback, state, user, order_id)
        if not allowed:
            logger.warning('CLOUD_RENEWWALLET_DENIED user_id=%s order_id=%s reason=not_owner_group_admin_or_recent_query callback_data=%s', user.id, order_id, callback.data)
            await _safe_callback_answer(callback, '这笔续费不属于当前账号或当前查询结果，请重新进入代理详情或重新查询 IP。', show_alert=True)
            return
        order, err = await pay_cloud_server_renewal_with_balance(order_id, user.id, 'USDT', 31)
        if err:
            existing = await get_cloud_order(order_id, user.id)
            if not existing and err == '当前订单状态不可钱包支付' and (is_admin or group_context):
                candidate = await get_cloud_server_for_admin(order_id)
                existing = candidate if _requires_recovery_provision(candidate) else None
            if err == '当前订单状态不可钱包支付' and _requires_recovery_provision(existing):
                await _safe_edit_text(callback.message, '✅ 这笔续费已支付，固定 IP 恢复正在处理中。\n\n系统会保持旧 IP / 旧端口 / 旧密钥不变，完成后自动发送代理链接。')
                asyncio.create_task(_provision_cloud_server_and_notify(callback.bot, callback.from_user.id, existing.id, existing.mtproxy_port or MTPROXY_DEFAULT_PORT))
                return
            if err == '当前订单状态不可钱包支付' and existing and existing.status == 'completed' and existing.paid_at:
                missing_recovery = _retained_recovery_missing_payment_text(existing)
                if missing_recovery:
                    await _safe_edit_text(callback.message, f'⚠️ 这笔续费已记录为已支付，但固定 IP 恢复资料不完整。\n\n订单号: {existing.order_no}\n缺少: {missing_recovery}\n\n我没有再次扣款。请先通过未附加 IP 续费流程补充旧主代理链接，或联系管理员核对这笔订单。')
                    return
                asyncio.create_task(_cloud_renewal_postcheck_and_notify(callback.bot, callback.from_user.id, existing.id))
                await _safe_edit_text(callback.message, f'✅ 这笔续费已完成。\n\n订单号: {existing.order_no}\n{_cloud_order_plan_text(existing)}\n\n我会继续执行续费后巡检。')
                return
            await _safe_edit_text(callback.message, 
                f'❌ 钱包自动续费失败：{_public_cloud_error_text(err)}。\n请先充值余额后再试，或使用下方地址支付。',
                reply_markup=wallet_recharge_prompt_menu(append_back_callback(f'cloud:renew:{order_id}', back_callback), '🔙 返回续费支付'),
            )
            return
        if _requires_recovery_provision(order):
            await _safe_edit_text(callback.message, '✅ 云服务器钱包续费成功，正在自动恢复固定 IP 服务器。\n\n系统会保持旧 IP / 旧端口 / 旧密钥不变，完成后自动发送代理链接。')
            await _send_admin_user_action_notice(callback.bot, user, '续费', [
                ('订单号', order.order_no),
                ('IP', getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '-'),
                ('金额', f'{fmt_pay_amount(order.pay_amount)} {order.currency}'),
                ('支付方式', '钱包自动续费'),
            ])
            asyncio.create_task(_provision_cloud_server_and_notify(callback.bot, callback.from_user.id, order.id, order.mtproxy_port or MTPROXY_DEFAULT_PORT))
            return
        asyncio.create_task(_cloud_renewal_postcheck_and_notify(callback.bot, callback.from_user.id, order.id, getattr(order, 'renew_balance_change', None)))
        await _safe_edit_text(callback.message, 
            '✅ 云服务器钱包自动续费成功\n\n'
            f'订单号: {order.order_no}\n'
            '续费时长: 31天\n'
            '支付方式: 钱包自动续费\n'
            '支付币种: USDT\n'
            + _cloud_order_plan_text(order),
            reply_markup=cloud_server_detail(
                order.id,
                can_renew=True,
                can_change_ip=bool(order.provider == 'aws_lightsail' and order.public_ip and order.status in {"completed", "expiring", "suspended"} and max(int(getattr(order, 'ip_change_quota', 0) or 0), 0) > 0),
                can_reinit=bool(order.public_ip and order.login_password and order.status == "completed"),
                back_callback=back_callback or 'cloud:list',
                can_upgrade=bool(order.provider == 'aws_lightsail' and order.status in {"completed", "expiring", "suspended"}),
                can_resume_init=bool(order.status in {"paid", "provisioning", "failed"} and (order.public_ip or not order.mtproxy_secret or not order.mtproxy_link or not order.login_password)),
            ),
        )
        await _send_admin_user_action_notice(callback.bot, user, '续费', [
            ('订单号', order.order_no),
            ('IP', getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '-'),
            ('金额', f'{fmt_pay_amount(order.pay_amount)} {order.currency}'),
            ('时长', '31天'),
            ('支付方式', '钱包自动续费'),
        ])

    @dp.callback_query(F.data.startswith('p:'))
    @dp.callback_query(F.data.startswith('cloud:rp:'))
    @dp.callback_query(F.data.startswith('cloud:renewpay:'))
    async def cb_cloud_renew_pay(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback, '续费钱包支付处理中')
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        if callback.data.startswith('p:'):
            parts = callback.data.split(':', 3)
            _, order_id_text, currency = parts[:3]
            back_callback = compact_callback_path(parts[3]) if len(parts) > 3 else ''
            currency = {'U': 'USDT', 'T': 'TRX'}.get(str(currency or '').upper(), currency)
        else:
            parts = callback.data.split(':', 4)
            _, action, order_id_text, currency = parts[:4]
            back_callback = compact_callback_path(parts[4]) if len(parts) > 4 else ''
            if action not in {'renewpay', 'rp'}:
                await _safe_callback_answer(callback, '续费钱包支付参数无效', show_alert=True)
                return
        currency = str(currency or '').upper()
        if not _is_supported_payment_currency(currency):
            await _safe_callback_answer(callback, '不支持的支付币种', show_alert=True)
            return
        order_id = int(order_id_text)
        allowed, is_admin, group_context = await _can_use_renewal_payment_callback(callback, state, user, order_id)
        if not allowed:
            logger.warning('CLOUD_RENEWPAY_DENIED user_id=%s order_id=%s currency=%s reason=not_owner_group_admin_or_recent_query callback_data=%s', user.id, order_id, currency, callback.data)
            await _safe_callback_answer(callback, '这笔续费不属于当前账号或当前查询结果，请重新进入代理详情或重新查询 IP。', show_alert=True)
            return
        order, err = await pay_cloud_server_renewal_with_balance(order_id, user.id, currency, 31)
        if err:
            existing = await get_cloud_order(order_id, user.id)
            if not existing and err == '当前订单状态不可钱包支付' and (is_admin or group_context):
                candidate = await get_cloud_server_for_admin(order_id)
                existing = candidate if _requires_recovery_provision(candidate) else None
            if err == '当前订单状态不可钱包支付' and _requires_recovery_provision(existing):
                await _safe_edit_text(callback.message, '✅ 这笔续费已支付，固定 IP 恢复正在处理中。\n\n系统会保持旧 IP / 旧端口 / 旧密钥不变，完成后自动发送代理链接。')
                asyncio.create_task(_provision_cloud_server_and_notify(callback.bot, callback.from_user.id, existing.id, existing.mtproxy_port or MTPROXY_DEFAULT_PORT))
                return
            if err == '当前订单状态不可钱包支付' and existing and existing.status == 'completed' and existing.paid_at:
                missing_recovery = _retained_recovery_missing_payment_text(existing)
                if missing_recovery:
                    await _safe_edit_text(callback.message, f'⚠️ 这笔续费已记录为已支付，但固定 IP 恢复资料不完整。\n\n订单号: {existing.order_no}\n缺少: {missing_recovery}\n\n我没有再次扣款。请先通过未附加 IP 续费流程补充旧主代理链接，或联系管理员核对这笔订单。')
                    return
                asyncio.create_task(_cloud_renewal_postcheck_and_notify(callback.bot, callback.from_user.id, existing.id))
                await _safe_edit_text(callback.message, f'✅ 这笔续费已完成。\n\n订单号: {existing.order_no}\n{_cloud_order_plan_text(existing)}\n\n我会继续执行续费后巡检。')
                return
            await _safe_edit_text(callback.message, f'❌ {_public_cloud_error_text(err)}。', reply_markup=wallet_recharge_prompt_menu(append_back_callback(f'cloud:renew:{order_id}', back_callback), '🔙 返回续费支付'))
            return
        if _requires_recovery_provision(order):
            await _safe_edit_text(callback.message, '✅ 云服务器续费成功，正在自动恢复固定 IP 服务器。\n\n系统会保持旧 IP / 旧端口 / 旧密钥不变，完成后自动发送代理链接。')
            await _send_admin_user_action_notice(callback.bot, user, '续费', [
                ('订单号', order.order_no),
                ('IP', getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '-'),
                ('金额', f'{fmt_pay_amount(order.pay_amount)} {order.currency}'),
                ('支付币种', currency),
            ])
            asyncio.create_task(_provision_cloud_server_and_notify(callback.bot, callback.from_user.id, order.id, order.mtproxy_port or MTPROXY_DEFAULT_PORT))
            return
        asyncio.create_task(_cloud_renewal_postcheck_and_notify(callback.bot, callback.from_user.id, order.id, getattr(order, 'renew_balance_change', None)))
        await _safe_edit_text(callback.message, 
            '✅ 云服务器续费成功\n\n'
            f'订单号: {order.order_no}\n'
            '续费时长: 31天\n'
            f'支付币种: {currency}\n'
            + _cloud_order_plan_text(order),
            reply_markup=cloud_server_detail(
                order.id,
                can_renew=True,
                can_change_ip=bool(order.provider == 'aws_lightsail' and order.public_ip and order.status in {"completed", "expiring", "suspended"} and max(int(getattr(order, 'ip_change_quota', 0) or 0), 0) > 0),
                can_reinit=bool(order.public_ip and order.login_password and order.status == "completed"),
                back_callback=back_callback or 'cloud:list',
                can_upgrade=bool(order.provider == 'aws_lightsail' and order.status in {"completed", "expiring", "suspended"}),
                can_resume_init=bool(order.status in {"paid", "provisioning", "failed"} and (order.public_ip or not order.mtproxy_secret or not order.mtproxy_link or not order.login_password)),
            ),
        )
        await _send_admin_user_action_notice(callback.bot, user, '续费', [
            ('订单号', order.order_no),
            ('IP', getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '-'),
            ('金额', f'{fmt_pay_amount(order.pay_amount)} {order.currency}'),
            ('时长', '31天'),
            ('支付币种', currency),
        ])

    @dp.callback_query(F.data.startswith('i:'))
    @dp.callback_query(F.data.startswith('cloud:ip:'))
    async def cb_cloud_change_ip(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        if await _deny_group_high_risk_callback(callback, '更换 IP'):
            return
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        if callback.data.startswith('i:'):
            parts = callback.data.split(':', 2)
            order_id = int(parts[1])
            back_callback = compact_callback_path(parts[2]) if len(parts) > 2 else ''
        else:
            parts = callback.data.split(':', 3)
            order_id = int(parts[2])
            back_callback = compact_callback_path(parts[3]) if len(parts) > 3 else ''
        is_admin = await _is_admin_chat(callback.message)
        order = await get_cloud_server_for_admin(order_id) if is_admin else await get_user_cloud_server(order_id, user.id)
        if not order:
            await _safe_callback_answer(callback, '服务器记录不存在', show_alert=True)
            return
        if order.provider != 'aws_lightsail':
            await _safe_callback_answer(callback, '当前代理暂不支持更换 IP', show_alert=True)
            return
        if max(int(getattr(order, 'ip_change_quota', 0) or 0), 0) <= 0:
            await _safe_callback_answer(callback, '剩余更换 IP 次数不足，请续费后再试', show_alert=True)
            return
        regions = [(code, name) for code, name in await _get_cached_custom_regions() if code != 'cn-hongkong']
        await _safe_edit_text(callback.message, 
            '🌐 更换IP\n\n请选择新的地区：',
            reply_markup=cloud_server_change_ip_region_menu(order.id, regions, expanded=False, back_callback=back_callback),
        )

    @dp.callback_query(F.data.startswith('im:'))
    @dp.callback_query(F.data.startswith('cloud:ipregions:more:'))
    async def cb_cloud_change_ip_regions_more(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        if await _deny_group_high_risk_callback(callback, '更换 IP'):
            return
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        if callback.data.startswith('im:'):
            parts = callback.data.split(':', 2)
            order_id = int(parts[1])
            back_callback = compact_callback_path(parts[2]) if len(parts) > 2 else ''
        else:
            parts = callback.data.split(':', 4)
            order_id = int(parts[3])
            back_callback = compact_callback_path(parts[4]) if len(parts) > 4 else ''
        order = await get_cloud_server_for_admin(order_id) if await _is_admin_chat(callback.message) else await get_user_cloud_server(order_id, user.id)
        if not order:
            await _safe_callback_answer(callback, '服务器记录不存在', show_alert=True)
            return
        regions = [(code, name) for code, name in await _get_cached_custom_regions() if code != 'cn-hongkong']
        await _safe_edit_text(callback.message, 
            '🌐 更换IP\n\n请选择新的地区：',
            reply_markup=cloud_server_change_ip_region_menu(order_id, regions, expanded=True, back_callback=back_callback),
        )

    @dp.callback_query(F.data.startswith('ir:'))
    @dp.callback_query(F.data.startswith('cloud:ipregion:'))
    async def cb_cloud_change_ip_region(callback: CallbackQuery, state: FSMContext, bot: Bot):
        await _safe_callback_answer(callback)
        if await _deny_group_high_risk_callback(callback, '更换 IP'):
            return
        if callback.data.startswith('ir:'):
            parts = callback.data.split(':', 3)
            _, raw_order_id, region_code = parts[:3]
            region_code = expand_compact_region_code(region_code)
        else:
            parts = callback.data.split(':', 4)
            _, _, raw_order_id, region_code = parts[:4]
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(raw_order_id)
        is_admin = await _is_admin_chat(callback.message)
        order = await get_cloud_server_for_admin(order_id) if is_admin else await get_user_cloud_server(order_id, user.id)
        if not order:
            await _safe_callback_answer(callback, '服务器记录不存在', show_alert=True)
            return
        if region_code == 'cn-hongkong':
            await _safe_callback_answer(callback, '当前节点暂不支持更换 IP', show_alert=True)
            return
        regions = [(code, name) for code, name in await _get_cached_custom_regions() if code != 'cn-hongkong']
        region_name = next((name for code, name in regions if code == region_code), region_code)
        new_order = await mark_cloud_server_ip_change_requested(order_id, user.id, region_code, MTPROXY_DEFAULT_PORT, admin=is_admin)
        await state.clear()
        if new_order is False:
            await _safe_callback_answer(callback, '当前状态不可更换 IP', show_alert=True)
            return
        if not new_order:
            await _safe_callback_answer(callback, '创建更换 IP 新服务器失败', show_alert=True)
            return
        await callback.message.reply(
            f'🌐 已为你创建同配置新服务器\n\n新节点: {_public_region_text(new_order.region_name or region_name) or "默认节点"}\n新端口: {new_order.mtproxy_port or MTPROXY_DEFAULT_PORT}\n系统会重写生成新的 IP，请在 5 天内迁移。',
            reply_markup=main_menu(),
        )
        await _send_admin_user_action_notice(bot, user, '换IP', [
            ('新订单号', new_order.order_no),
            ('新节点', _public_region_text(new_order.region_name or region_name) or '默认节点'),
            ('新端口', new_order.mtproxy_port or MTPROXY_DEFAULT_PORT),
        ])
        asyncio.create_task(_provision_cloud_server_and_notify(bot, callback.from_user.id, new_order.id, new_order.mtproxy_port or MTPROXY_DEFAULT_PORT))

    @dp.callback_query(F.data.startswith('u:'))
    @dp.callback_query(F.data.startswith('cloud:upgrade:'))
    async def cb_cloud_upgrade(callback: CallbackQuery):
        await _safe_callback_answer(callback, '正在加载修改配置')
        if await _deny_group_high_risk_callback(callback, '修改配置'):
            return
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        if callback.data.startswith('u:'):
            parts = callback.data.split(':', 2)
            order_id = int(parts[1])
            back_callback = compact_callback_path(parts[2]) if len(parts) > 2 else ''
        else:
            parts = callback.data.split(':', 3)
            order_id = int(parts[2])
            back_callback = compact_callback_path(parts[3]) if len(parts) > 3 else ''
        is_admin = await _is_admin_chat(callback.message)
        logger.info('CLOUD_UPGRADE_PLAN_START user_id=%s order_id=%s admin=%s callback_data=%s', user.id, order_id, is_admin, callback.data)
        plans, err = await list_cloud_server_upgrade_plans(order_id, user.id, admin=is_admin)
        if err:
            logger.info('CLOUD_UPGRADE_PLAN_DENIED user_id=%s order_id=%s admin=%s reason=%s', user.id, order_id, is_admin, err)
            await _safe_callback_answer(callback, err, show_alert=True)
            await callback.message.reply(f'⚙️ 修改配置暂不可用\n\n原因：{err}')
            return
        if not plans:
            logger.info('CLOUD_UPGRADE_PLAN_EMPTY user_id=%s order_id=%s admin=%s', user.id, order_id, is_admin)
            await _safe_callback_answer(callback, '暂无可修改的配置', show_alert=True)
            await callback.message.reply('⚙️ 修改配置暂不可用\n\n原因：暂无可修改的配置')
            return
        rows = []
        text_lines = ['⚙️ 修改配置', '', '请选择目标配置。升级会从 USDT 余额扣除差价；降级不退差价。系统会创建目标规格服务器，主/备用代理链接保持不变。']
        for plan in plans[:10]:
            action_text = '升级' if plan.get('action') == 'upgrade' else '降级'
            charge_text = f"补 {plan['diff']} U" if plan.get('action') == 'upgrade' else '无需补差价'
            text_lines.append(f"- {plan['name']}：{action_text}，{charge_text}，到期补足 {plan['target_days']} 天")
            rows.append([InlineKeyboardButton(text=f"{action_text}到 {plan['name']} {charge_text}", callback_data=append_back_callback(f"upp:{order_id}:{plan['id']}", back_callback))])
        rows.append([InlineKeyboardButton(text='🔙 返回详情', callback_data=cloud_detail_callback(order_id, back_callback))])
        text = '\n'.join(text_lines)
        markup = InlineKeyboardMarkup(inline_keyboard=rows)
        edited = await _safe_edit_text(callback.message, text, reply_markup=markup)
        if edited is None:
            sent = await callback.message.reply(text, reply_markup=markup)
            logger.info('BOT_MESSAGE_SEND route=upgrade_plans_fallback user_id=%s order_id=%s chat_id=%s reply_to=%s sent_message_id=%s plans=%s', user.id, order_id, callback.message.chat.id, callback.message.message_id, getattr(sent, 'message_id', None), len(plans))
        else:
            logger.info('BOT_MESSAGE_EDIT route=upgrade_plans user_id=%s order_id=%s chat_id=%s message_id=%s plans=%s', user.id, order_id, callback.message.chat.id, callback.message.message_id, len(plans))
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('upp:'))
    @dp.callback_query(F.data.startswith('cloud:upgradepay:'))
    async def cb_cloud_upgrade_pay(callback: CallbackQuery, bot: Bot, state: FSMContext):
        await _safe_callback_answer(callback)
        if await _deny_group_high_risk_callback(callback, '修改配置'):
            return
        await state.clear()
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        if callback.data.startswith('upp:'):
            parts = callback.data.split(':', 3)
            _, raw_order_id, raw_plan_id = parts[:3]
            back_callback = compact_callback_path(parts[3]) if len(parts) > 3 else ''
        else:
            parts = callback.data.split(':', 4)
            _, _, raw_order_id, raw_plan_id = parts[:4]
            back_callback = compact_callback_path(parts[4]) if len(parts) > 4 else ''
        is_admin = await _is_admin_chat(callback.message)
        new_order, err = await create_cloud_server_upgrade_order(int(raw_order_id), user.id, int(raw_plan_id), admin=is_admin)
        if err:
            await _safe_callback_answer(callback, err, show_alert=True)
            return
        await callback.message.reply(
            _bot_text_format('bot_cloud_upgrade_submitted', '⚙️ 已提交配置调整任务。\n新订单: {order_no}\n完成后会自动发送新的服务器信息，代理链接保持不变。\n\n后台处理期间，底部菜单和其它按钮可正常使用。', order_no=new_order.order_no),
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='🔙 返回原代理', callback_data=cloud_previous_detail_callback(int(raw_order_id), back_callback))],
            ]) if back_callback else main_menu(),
        )
        asyncio.create_task(_provision_cloud_server_and_notify(bot, callback.from_user.id, new_order.id, new_order.mtproxy_port or MTPROXY_DEFAULT_PORT))

    @dp.callback_query(F.data.startswith('ri:'))
    @dp.callback_query(F.data.startswith('cloud:reinit:'))
    async def cb_cloud_reinit(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        if await _deny_group_high_risk_callback(callback, '重新安装'):
            return
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        if callback.data.startswith('ri:'):
            parts = callback.data.split(':', 2)
            order_id = int(parts[1])
            back_callback = compact_callback_path(parts[2]) if len(parts) > 2 else ''
        else:
            parts = callback.data.split(':', 3)
            order_id = int(parts[2])
            back_callback = compact_callback_path(parts[3]) if len(parts) > 3 else ''
        is_admin = await _is_admin_chat(callback.message)
        order = await get_cloud_server_for_admin(order_id) if is_admin else await get_user_cloud_server(order_id, user.id)
        if not order:
            await _safe_callback_answer(callback, '服务器记录不存在', show_alert=True)
            return
        is_unfinished = order.status in {'paid', 'provisioning', 'failed'}
        if not is_unfinished and (not order.public_ip or not order.login_password):
            logger.warning('CLOUD_REINIT_DENIED user_id=%s order_id=%s order_no=%s status=%s public_ip=%s login_password=%s reason=missing_bootstrap_info', user.id, order.id, order.order_no, order.status, order.public_ip, bool(order.login_password))
            await _safe_callback_answer(callback, '当前服务器缺少公网 IP 或登录密码，暂时无法重新安装；请先在后台补齐实例登录信息', show_alert=True)
            return
        has_main_link = bool(getattr(order, 'mtproxy_link', None) or any(isinstance(item, dict) and item.get('url') and str(item.get('port') or '') == str(order.mtproxy_port or MTPROXY_DEFAULT_PORT) for item in (getattr(order, 'proxy_links', None) or [])))
        if not is_unfinished and not has_main_link:
            await state.update_data(reinstall_order_id=order.id, reinstall_admin=is_admin, reinstall_back=back_callback)
            await state.set_state(CustomServerStates.waiting_reinstall_link)
            await callback.message.reply(_bot_text('bot_reinstall_need_main_link', '当前服务器缺少主代理链接。请直接发送这台服务器的主代理链接，我会先校验 IP、端口和服务器实际密钥，再让你确认是否重新安装。'))
            return
        if is_unfinished:
            token = await _issue_reinstall_confirm_token(state, kind='order', item_id=order.id)
            await state.update_data(reinstall_back=back_callback)
            await callback.message.reply(_bot_text('bot_resume_init_confirm', '⚠️ 确认继续初始化？\n\n系统会重新执行 BBR/MTProxy 安装并生成代理链接。'), reply_markup=_reinstall_confirm_keyboard(order.id, token, back_callback))
            return
        token = await _issue_reinstall_confirm_token(state, kind='order', item_id=order.id)
        await state.update_data(reinstall_back=back_callback)
        await callback.message.reply(_bot_text('bot_reinstall_confirm', '⚠️ 确认重新安装？\n\n重新安装大约需要 5 分钟，期间代理可能会断连。系统会保持主/备用链接不变。'), reply_markup=_reinstall_confirm_keyboard(order.id, token, back_callback))

    @dp.message(CustomServerStates.waiting_retained_ip_renewal_link)
    async def msg_cloud_retained_ip_renewal_link(message: Message, state: FSMContext):
        if await _handle_menu_interrupt(message, state):
            return
        data = await state.get_data()
        asset_id = int(data.get('asset_renewal_asset_id') or 0)
        order_id = int(data.get('retained_ip_renewal_order_id') or 0)
        plan_id = int(data.get('asset_renewal_plan_id') or data.get('retained_ip_renewal_plan_id') or 0)
        is_admin_asset_renewal = bool(data.get('asset_renewal_admin')) and await _is_admin_chat(message)
        is_public_asset_renewal = bool(data.get('asset_renewal_public')) and not is_admin_asset_renewal
        is_admin_renewal = bool(data.get('retained_ip_renewal_admin')) and await _is_admin_chat(message)
        group_renewal_chat_id = int(data.get('retained_ip_renewal_group_chat_id') or 0)
        is_group_renewal = bool(group_renewal_chat_id and _is_group_chat_message(message) and int(message.chat.id) == group_renewal_chat_id)
        back_callback = compact_callback_path(data.get('asset_renewal_back') or data.get('retained_ip_renewal_back'))
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        if asset_id:
            item = await get_proxy_asset_detail_for_admin(asset_id, 'asset') if (is_admin_asset_renewal or is_public_asset_renewal) else await get_user_proxy_asset_detail(asset_id, user.id, 'asset')
        else:
            item = await get_cloud_server_for_admin(order_id) if (is_admin_renewal or is_group_renewal) else await get_user_cloud_server(order_id, user.id)
        if not item:
            await state.clear()
            await message.reply('服务器或代理记录不存在，请重新进入详情。')
            return
        link_data = _parse_proxy_link(message.text or '')
        if not link_data:
            await message.reply(_bot_text('bot_reinstall_invalid_link', '链接格式不对，请发送 tg://proxy?... 或 https://t.me/proxy?... 主代理链接。'))
            return
        ok, reason = await _validate_reinstall_proxy_link(
            item,
            link_data,
            probe_when_possible=False,
            allow_client_port=True,
        )
        if not ok:
            await message.reply(_bot_text_format('bot_reinstall_validate_failed', '校验失败：{reason}', reason=reason))
            return
        if asset_id:
            order, err = await prepare_cloud_asset_renewal_with_link(asset_id, user.id, plan_id, link_data, 31, admin=is_admin_asset_renewal, public=is_public_asset_renewal)
        else:
            order, err = await prepare_retained_ip_renewal_with_link(order_id, user.id, plan_id, link_data, 31, admin=is_admin_renewal or is_group_renewal)
        if err or not order:
            await message.reply(err or '续费订单创建失败，请重新进入详情后再试。')
            return
        await state.clear()
        payment_back_callback = cloud_asset_detail_callback(asset_id, back_callback) if asset_id else back_callback
        await _send_cloud_renewal_payment_prompt(message, order, user, edit=False, back_callback=payment_back_callback)

    @dp.message(CustomServerStates.waiting_reinstall_link)
    async def msg_cloud_reinstall_link(message: Message, state: FSMContext):
        if await _handle_menu_interrupt(message, state):
            return
        data = await state.get_data()
        order_id = int(data.get('reinstall_order_id') or 0)
        asset_id = int(data.get('reinstall_asset_id') or 0)
        is_admin_reinstall = bool(data.get('reinstall_admin')) and await _is_admin_chat(message)
        if _is_group_chat_message(message) and not is_admin_reinstall:
            await state.clear()
            await message.reply('群聊仅支持续费和自动续费，重新安装请在私聊中操作。', reply_markup=main_menu())
            return
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        if asset_id:
            item = await get_proxy_asset_detail_for_admin(asset_id, 'asset') if is_admin_reinstall else await get_user_proxy_asset_detail(asset_id, user.id, 'asset')
        else:
            item = await get_cloud_server_for_admin(order_id) if is_admin_reinstall else await get_user_cloud_server(order_id, user.id)
        if not item:
            await state.clear()
            await message.reply(_bot_text('bot_reinstall_missing_order', '服务器记录不存在，请重新进入云服务器详情。'))
            return
        link_data = _parse_proxy_link(message.text or '')
        if not link_data:
            await message.reply(_bot_text('bot_reinstall_invalid_link', '链接格式不对，请发送 tg://proxy?... 或 https://t.me/proxy?... 主代理链接。'))
            return
        ok, reason = await _validate_reinstall_proxy_link(
            item,
            link_data,
            probe_when_possible=bool(getattr(item, 'login_password', None)),
            allow_client_port=True,
        )
        if not ok:
            await message.reply(_bot_text_format('bot_reinstall_validate_failed', '校验失败：{reason}', reason=reason))
            return
        if asset_id:
            saved = await _save_asset_main_proxy_link(asset_id, None if is_admin_reinstall else user.id, link_data)
            token = await _issue_reinstall_confirm_token(state, kind='asset', item_id=saved.id)
            await message.reply(_bot_text('bot_reinstall_validate_ok', '主代理链接校验通过。\n\n⚠️ 确认重新安装？重新安装大约需要 5 分钟，期间代理可能会断连。'), reply_markup=_asset_reinstall_confirm_keyboard(saved.id, token, data.get('reinstall_back')))
            return
        saved = await _save_user_main_proxy_link(item.id, link_data)
        token = await _issue_reinstall_confirm_token(state, kind='order', item_id=saved.id)
        await message.reply(_bot_text('bot_reinstall_validate_ok', '主代理链接校验通过。\n\n⚠️ 确认重新安装？重新安装大约需要 5 分钟，期间代理可能会断连。'), reply_markup=_reinstall_confirm_keyboard(saved.id, token, data.get('reinstall_back')))

    @dp.callback_query(F.data.startswith('cloud:reinitconfirm:'))
    async def cb_cloud_reinit_confirm(callback: CallbackQuery, bot: Bot, state: FSMContext):
        if await _deny_group_high_risk_callback(callback, '重新安装'):
            return
        parts = callback.data.split(':')
        order_id = int(parts[2])
        token = parts[3] if len(parts) > 3 else ''
        if not await _consume_reinstall_confirm_token(state, kind='order', item_id=order_id, token=token):
            await _safe_callback_answer(callback, '这个确认按钮已过期或已使用，请重新进入详情并重新生成按钮。', show_alert=True)
            return
        await _safe_callback_answer(callback, '已确认，后台处理中')
        await _safe_remove_inline_keyboard(callback.message)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        is_admin = await _is_admin_chat(callback.message)
        order = await mark_cloud_server_reinit_requested(order_id, None if is_admin else user.id)
        if not order:
            await callback.message.reply('服务器记录不存在，请重新进入详情并重新生成按钮。')
            return
        if order is False:
            await callback.message.reply('当前服务器缺少公网 IP 或登录密码，暂时无法继续初始化；请补齐后重新生成按钮。')
            return
        if order == 'missing_main_link':
            await callback.message.reply('当前服务器缺少主代理链接，请先发送主链接完成校验并重新生成按钮。')
            return
        if isinstance(order, str):
            await callback.message.reply(f'{order}\n请重新进入详情并重新生成按钮。')
            return
        is_rebuild = bool(getattr(order, 'replacement_for_id', None))
        action_text = '重建服务器' if is_rebuild else ('继续初始化' if order.status in {'paid', 'provisioning', 'failed'} else '重新安装')
        retry_only = bool(not is_rebuild and order.public_ip and order.login_password)
        work_text = '新建服务器并安装代理，成功后迁移固定 IP，旧机保留 3 天' if is_rebuild else ('重新执行 BBR/MTProxy 安装' if retry_only else '继续创建服务器并完成初始化')
        await callback.message.reply(_bot_text_format('bot_reinstall_submitted', '🛠 已确认{action_text}，后台会{work_text}。预计约 5 分钟，完成后会自动通知你。\n\n后台处理期间，底部菜单和其它按钮可正常使用。', action_text=action_text, work_text=work_text), reply_markup=main_menu())
        await _send_admin_user_action_notice(bot, user, '重装', [
            ('订单号', order.order_no),
            ('IP', getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '-'),
            ('动作', action_text),
        ])
        asyncio.create_task(_provision_cloud_server_and_notify(bot, callback.from_user.id, order.id, order.mtproxy_port or MTPROXY_DEFAULT_PORT, retry_only=retry_only))

    @dp.callback_query(F.data.startswith('exp:'))
    @dp.callback_query(F.data.startswith('cloud:adminexp:'))
    async def cb_cloud_admin_expiry(callback: CallbackQuery, state: FSMContext):
        if not await _is_admin_chat(callback.message):
            await _safe_callback_answer(callback, '仅管理员可使用', show_alert=True)
            return
        if callback.data.startswith('exp:'):
            parts = callback.data.split(':', 3)
            if len(parts) < 3:
                await _safe_callback_answer(callback, '参数无效', show_alert=True)
                return
            item_kind = {'a': 'asset', 'o': 'order'}.get(parts[1], parts[1])
            item_id = int(parts[2])
            back_callback = compact_callback_path(parts[3]) if len(parts) > 3 else 'cloud:querymenu'
        else:
            parts = callback.data.split(':', 4)
            if len(parts) < 4:
                await _safe_callback_answer(callback, '参数无效', show_alert=True)
                return
            item_kind = parts[2]
            item_id = int(parts[3])
            back_callback = compact_callback_path(parts[4]) if len(parts) > 4 else 'cloud:querymenu'
        await state.update_data(admin_expiry_kind=item_kind, admin_expiry_item_id=item_id, admin_expiry_back=back_callback)
        await state.set_state(CustomServerStates.waiting_admin_expiry_time)
        await _safe_callback_answer(callback, '请输入新的到期时间')
        await callback.message.reply(
            '🕒 修改到期时间\n\n请输入新的到期时间，例如：\n2026-06-30 15:00\n或只输入日期：2026-06-30'
        )

    @dp.message(CustomServerStates.waiting_admin_expiry_time)
    async def msg_cloud_admin_expiry_time(message: Message, state: FSMContext):
        if await _handle_menu_interrupt(message, state):
            return
        if not await _is_admin_chat(message):
            await state.clear()
            await message.reply('仅管理员可使用。', reply_markup=main_menu())
            return
        expires_at = _parse_admin_expiry_input(message.text or '')
        if not expires_at:
            await message.reply('时间格式不正确，请发送类似 2026-06-30 15:00 的时间。')
            return
        data = await state.get_data()
        item_kind = str(data.get('admin_expiry_kind') or 'order')
        item_id = int(data.get('admin_expiry_item_id') or 0)
        item, err = await update_cloud_item_expiry_for_admin(item_id, item_kind, expires_at)
        await state.clear()
        if err or not item:
            await message.reply(err or '修改失败，请重新查询后再试。', reply_markup=main_menu())
            return
        label = getattr(item, 'public_ip', None) or getattr(item, 'previous_public_ip', None) or getattr(item, 'order_no', None) or f'{item_kind}:{item_id}'
        back_callback = str(data.get('admin_expiry_back') or 'cloud:querymenu').strip() or 'cloud:querymenu'
        await message.reply(
            f'✅ 已修改到期时间\n\n{label}\n新时间: {_format_local_dt(expires_at)}',
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text='🔙 返回原页面', callback_data=back_callback)],
            ]),
        )

    @dp.callback_query(F.data.startswith('balance:detail:'))
    async def cb_balance_detail(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        raw_item_id = callback.data.split(':', 2)[2]
        item = await get_balance_detail(user.id, raw_item_id)
        if not item:
            await _safe_callback_answer(callback, '明细不存在', show_alert=True)
            return
        await _safe_edit_text(callback.message, _balance_detail_text(item), reply_markup=profile_menu())

    # ══════════════════════════════════════════════════════════════════════
    # 充值回调
    # ══════════════════════════════════════════════════════════════════════

    @dp.callback_query(F.data.startswith('rcur:'))
    async def cb_recharge_currency(callback: CallbackQuery, state: FSMContext):
        currency = callback.data.split(':')[1]
        if currency not in {'USDT', 'TRX'}:
            await _safe_callback_answer(callback, '不支持的充值币种', show_alert=True)
            return
        await state.clear()
        await state.update_data(recharge_currency=currency)
        await state.set_state(RechargeStates.waiting_amount)
        text = f'💰 请输入需要充值的 {currency} 金额：\n\n可随时点击底部菜单打断当前输入。'
        edited = await _safe_edit_text(callback.message, text)
        if edited is None:
            sent = await callback.message.reply(text, reply_markup=main_menu())
            logger.info('BOT_MESSAGE_SEND route=recharge_currency_fallback user_id=%s currency=%s chat_id=%s reply_to=%s sent_message_id=%s', getattr(callback.from_user, 'id', None), currency, callback.message.chat.id, callback.message.message_id, getattr(sent, 'message_id', None))
        await _safe_callback_answer(callback, f'已选择 {currency}')

    @dp.callback_query(F.data.startswith('rpage:'))
    async def cb_recharge_page(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        page = int(callback.data.split(':')[1])
        recharges, total = await list_recharges(user.id, page=page)
        text_out, kb = _recharges_page(recharges, page, total)
        await _safe_edit_text(callback.message, text_out, reply_markup=kb)
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('rdetail:'))
    async def cb_recharge_detail(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        recharge_id = int(callback.data.split(':')[1])
        recharge = await get_recharge(user.id, recharge_id)
        if not recharge:
            await _safe_callback_answer(callback, '充值记录不存在', show_alert=True)
            return
        status_map = {'pending': '待支付', 'completed': '已完成', 'expired': '已过期'}
        chain_trace = _chain_trace_text(recharge)
        text = (
            f'📜 充值详情\n\n'
            f'记录ID: <code>#{recharge.id}</code>\n'
            f'充值金额: <code>{fmt_amount(recharge.amount)} {recharge.currency}</code>\n'
            f'支付金额: <code>{fmt_pay_amount(recharge.pay_amount)} {recharge.currency}</code>\n'
            f'状态: {status_map.get(recharge.status, recharge.status)}\n'
            f'交易哈希: <code>{escape(recharge.tx_hash or "-")}</code>\n'
            f'{chain_trace + chr(10) if chain_trace else ""}'
            f'创建时间: <code>{timezone.localtime(recharge.created_at):%Y-%m-%d %H:%M}</code>'
        )
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text='🔙 返回充值记录', callback_data='profile:recharges'),
        ]])
        await _safe_edit_text(callback.message, text, parse_mode='HTML', reply_markup=kb)
        await _safe_callback_answer(callback)

    # ══════════════════════════════════════════════════════════════════════
    # 监控回调
    # ══════════════════════════════════════════════════════════════════════

    @dp.callback_query(F.data == 'mon:add')
    async def cb_mon_add(callback: CallbackQuery, state: FSMContext):
        await state.set_state(MonitorStates.waiting_address)
        await _safe_edit_text(callback.message, _bot_text('bot_monitor_address_prompt', '请输入要监控的 TRON 地址：\n\n示例：<code>TD7cnQFUwDxPMSxruGELK6hs8YQm83Avco</code>\n\n可随时点击底部菜单打断当前输入。'), parse_mode='HTML')
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'mon:list')
    async def cb_mon_list(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        monitors = await list_monitors(user.id)
        if not monitors:
            await _safe_edit_text(callback.message, _bot_text('bot_monitors_empty', '暂无监控地址。'), reply_markup=monitor_menu())
        else:
            await _safe_edit_text(callback.message, _bot_text('bot_monitors_list', '📋 监控列表：'), reply_markup=kb_monitor_list(monitors))
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:detail:'))
    async def cb_mon_detail(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        mon = await get_monitor(int(callback.data.split(':')[2]), user.id)
        if not mon:
            await _safe_edit_text(callback.message, _bot_text('bot_monitor_missing', '监控不存在。'))
            await _safe_callback_answer(callback)
            return
        await _safe_edit_text(
            callback.message,
            _monitor_detail_text(mon),
            reply_markup=kb_monitor_detail(mon.id, mon.monitor_transfers, mon.monitor_resources),
            parse_mode='HTML',
        )
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:toggle:'))
    async def cb_mon_toggle(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, monitor_id, mode = callback.data.split(':')
        if mode not in {'transfers', 'resources'}:
            await _safe_callback_answer(callback, '不支持的监控开关', show_alert=True)
            return
        field = 'monitor_transfers' if mode == 'transfers' else 'monitor_resources'
        monitor = await toggle_monitor_flag(int(monitor_id), user.id, field)
        if not monitor:
            await _safe_callback_answer(callback, _bot_text('bot_monitor_missing', '监控不存在'), show_alert=True)
            return
        from cloud.cache import update_monitor_flag_in_cache
        await update_monitor_flag_in_cache(monitor.address, field, getattr(monitor, field), monitor.id)
        await _safe_edit_text(
            callback.message,
            _monitor_detail_text(monitor),
            reply_markup=kb_monitor_detail(monitor.id, monitor.monitor_transfers, monitor.monitor_resources),
            parse_mode='HTML',
        )
        await _safe_callback_answer(callback, _bot_text('bot_monitor_updated', '已更新'))

    @dp.callback_query(F.data.startswith('mon:threshold:'))
    async def cb_mon_threshold(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        mid = int(callback.data.split(':')[2])
        mon = await get_monitor(mid, user.id)
        if not mon:
            await _safe_callback_answer(callback, _bot_text('bot_monitor_missing', '监控不存在'), show_alert=True)
            return
        await _safe_edit_text(callback.message, _bot_text('bot_monitor_threshold_currency_prompt', '请选择要修改的阈值币种：'), reply_markup=monitor_threshold_currency(mid))
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:setthr:'))
    async def cb_mon_setthr(callback: CallbackQuery, state: FSMContext):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, mid, currency = callback.data.split(':')
        currency = str(currency or '').upper()
        if currency not in {'USDT', 'TRX', 'ENERGY', 'BANDWIDTH'}:
            await _safe_callback_answer(callback, '不支持的阈值类型', show_alert=True)
            return
        mon = await get_monitor(int(mid), user.id)
        if not mon:
            await _safe_callback_answer(callback, _bot_text('bot_monitor_missing', '监控不存在'), show_alert=True)
            return
        await state.update_data(threshold_monitor_id=int(mid), threshold_currency=currency)
        state_map = {
            'USDT': MonitorStates.waiting_usdt_threshold,
            'TRX': MonitorStates.waiting_trx_threshold,
            'ENERGY': MonitorStates.waiting_energy_threshold,
            'BANDWIDTH': MonitorStates.waiting_bandwidth_threshold,
        }
        prompt_map = {
            'USDT': _bot_text('bot_monitor_threshold_prompt_usdt', '请输入新的 USDT 阈值金额：\n\n可随时点击底部菜单打断当前输入。'),
            'TRX': _bot_text('bot_monitor_threshold_prompt_trx', '请输入新的 TRX 阈值金额：\n\n可随时点击底部菜单打断当前输入。'),
            'ENERGY': _bot_text('bot_monitor_threshold_prompt_energy', '请输入新的能量增加阈值（整数）：\n例如 10000；输入 0 表示只要增加就通知。\n\n可随时点击底部菜单打断当前输入。'),
            'BANDWIDTH': _bot_text('bot_monitor_threshold_prompt_bandwidth', '请输入新的带宽增加阈值（整数）：\n例如 500；输入 0 表示只要增加就通知。\n\n可随时点击底部菜单打断当前输入。'),
        }
        await state.set_state(state_map.get(currency, MonitorStates.waiting_trx_threshold))
        await _safe_edit_text(callback.message, prompt_map.get(currency, prompt_map['TRX']))
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:delete:'))
    async def cb_mon_delete(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        mid = int(callback.data.split(':')[2])
        mon = await get_monitor(mid, user.id)
        if mon:
            from cloud.cache import remove_monitor_from_cache
            await remove_monitor_from_cache(mon.address, mon.id)
        deleted = await delete_monitor(mid, user.id)
        if not deleted:
            await _safe_callback_answer(callback, _bot_text('bot_monitor_missing', '监控不存在'), show_alert=True)
            return
        await _safe_edit_text(callback.message, _bot_text('bot_monitor_deleted', '🗑 监控已删除。'), reply_markup=monitor_menu())
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'mon:back')
    async def cb_mon_back(callback: CallbackQuery):
        await _safe_edit_text(callback.message, _bot_text('bot_monitor_entry', '🔍 地址监控'), reply_markup=monitor_menu())
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:txd:'))
    async def cb_tx_detail(callback: CallbackQuery):
        from orders.runtime import get_tx_detail
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        detail_key = callback.data.split(':')[2]
        detail = get_tx_detail(detail_key)
        if not detail:
            await _safe_callback_answer(callback, _bot_text('bot_tx_detail_expired', '交易详情已过期'), show_alert=True)
            return
        detail_user_id = detail.get('user_id')
        if detail_user_id is not None:
            try:
                detail_user_mismatch = int(detail_user_id) != int(user.id)
            except (TypeError, ValueError):
                detail_user_mismatch = True
            if detail_user_mismatch:
                await _safe_callback_answer(callback, _bot_text('bot_tx_detail_expired_or_forbidden', '交易详情已过期或无权查看'), show_alert=True)
                return
        direction_text = '收入' if detail.get('direction') == 'income' else '支出'
        currency = escape(str(detail.get('currency') or '-'))
        text = (
            f'🔍 交易详情\n\n'
            f'类型: {direction_text}\n'
            f'交易哈希: <code>{escape(str(detail.get("tx_hash") or "-"))}</code>\n'
            f'币种: {currency}\n'
            f'金额: {escape(str(detail.get("amount") or "-"))} {currency}\n'
            f'付款地址: <code>{escape(str(detail.get("from") or "-"))}</code>\n'
            f'收款地址: <code>{escape(str(detail.get("to") or "-"))}</code>\n'
            f'时间: {escape(str(detail.get("time") or "-"))}\n'
        )
        if detail.get("remark"):
            text += f'备注: {escape(str(detail["remark"]))}\n'
        if detail.get("fee_text"):
            text += f'手续费: {escape(str(detail["fee_text"]))}\n'
        await _safe_edit_text(callback.message, text, parse_mode='HTML', disable_web_page_preview=True)
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:resd:'))
    async def cb_resource_detail(callback: CallbackQuery):
        from orders.runtime import get_resource_detail
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        detail_key = callback.data.split(':')[2]
        detail = get_resource_detail(detail_key)
        if not detail:
            await _safe_callback_answer(callback, _bot_text('bot_resource_detail_expired', '资源详情已过期'), show_alert=True)
            return
        detail_user_id = detail.get('user_id')
        if detail_user_id is not None:
            try:
                detail_user_mismatch = int(detail_user_id) != int(user.id)
            except (TypeError, ValueError):
                detail_user_mismatch = True
            if detail_user_mismatch:
                await _safe_callback_answer(callback, _bot_text('bot_resource_detail_expired_or_forbidden', '资源详情已过期或无权查看'), show_alert=True)
                return
        text = (
            f'⚡ 资源详情\n\n'
            f'地址备注: {escape(str(detail.get("remark") or "-"))}\n'
            f'监控地址: <code>{escape(str(detail.get("address") or "-"))}</code>\n'
            f'检测时间: <code>{escape(str(detail.get("time") or "-"))}</code>\n'
            f'可用能量增加: <code>+{escape(str(detail.get("energy_increase", 0)))}</code>（阈值 {escape(str(detail.get("energy_threshold", 1)))}）\n'
            f'可用带宽增加: <code>+{escape(str(detail.get("bandwidth_increase", 0)))}</code>（阈值 {escape(str(detail.get("bandwidth_threshold", 1)))}）\n'
            f'当前可用能量: <code>{escape(str(detail.get("energy", 0)))}</code>\n'
            f'当前可用带宽: <code>{escape(str(detail.get("bandwidth", 0)))}</code>'
        )
        await _safe_edit_text(callback.message, text, parse_mode='HTML')
        await _safe_callback_answer(callback)

    @dp.message()
    async def fallback_message_router(message: Message, state: FSMContext, bot: Bot):
        if await _handle_admin_reply_message(bot, message):
            return
        is_admin_chat = await _is_admin_chat(message)
        raw_text = _message_text_for_router(message)
        content_type = _message_content_type(message)
        kind = _detect_message_kind(raw_text)
        logger.debug(
            '消息分流 sender=%s type=%s kind=%s text_preview=%r',
            getattr(message.from_user, 'id', None),
            content_type,
            kind,
            _safe_preview_text(raw_text),
        )
        if kind in {'empty'} and not _is_admin_forward_media_type(content_type):
            return
        chat = getattr(message, 'chat', None)
        is_group_chat = int(getattr(chat, 'id', 0) or 0) < 0
        if is_group_chat and not is_admin_chat:
            await _forward_plain_text_to_admin(bot, message)
            return
        link_button = _link_button_for_text(raw_text)
        if link_button:
            kb = InlineKeyboardBuilder()
            kb.button(text=link_button.get('button_label') or link_button.get('label') or '打开链接', url=link_button.get('url'))
            await message.answer(
                link_button.get('message') or f'{link_button.get("label")}: {link_button.get("url")}',
                reply_markup=kb.as_markup(),
                disable_web_page_preview=True,
            )
            return
        if kind == 'command':
            await message.answer(_bot_text('bot_unknown_command', '暂不支持这个命令，请使用菜单按钮操作。'), reply_markup=main_menu())
            return
        if kind == 'link':
            await state.clear()
            await _reply_cloud_query_results(message, raw_text, include_start=is_admin_chat)
            return
        if kind == 'address':
            await state.clear()
            addresses = _extract_tron_addresses(raw_text)
            if not addresses:
                return
            try:
                await _reply_tron_address_summary(message, addresses[0])
            except Exception:
                await message.answer(_bot_text('bot_address_query_failed', '地址查询失败，请稍后再试。'), reply_markup=main_menu())
            return
        if is_admin_chat:
            return
        await _forward_plain_text_to_admin(bot, message)
        if _is_admin_forward_media_type(content_type):
            await message.answer(_bot_text('bot_media_received', '已收到你的媒体消息。'), reply_markup=main_menu())
            return
        await message.answer(_bot_text('bot_plain_text_received', '已收到你的消息。若是地址请直接发送地址，若是代理链接请直接发送链接。'), reply_markup=main_menu())

    @dp.callback_query(F.data == 'noop')
    async def cb_noop(callback: CallbackQuery):
        await _safe_callback_answer(callback)


async def create_dispatcher_and_register() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=BOT_TOKEN)
    _install_notice_copy_wrapper(bot)
    storage = await create_fsm_storage()
    dp = Dispatcher(storage=storage)
    dp.message.middleware(RawUserLoggingMiddleware())
    dp.callback_query.middleware(RawUserLoggingMiddleware())
    register_handlers(dp)
    return bot, dp
