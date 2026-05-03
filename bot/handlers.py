import asyncio
import contextlib
import logging
import math
import re
import secrets
import time
from decimal import Decimal, InvalidOperation
from html import escape
from urllib.parse import parse_qs, urlparse, unquote

import httpx

from asgiref.sync import sync_to_async
from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, TelegramObject
from aiogram.utils.keyboard import InlineKeyboardBuilder
from django.db.models import Q
from django.utils import timezone

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
    custom_region_menu, custom_plan_menu, custom_quantity_keyboard, custom_payment_keyboard, custom_currency_keyboard, custom_wallet_keyboard, custom_order_wallet_keyboard, custom_port_keyboard,
    cloud_server_list, cloud_auto_renew_server_list, cloud_server_detail, cloud_order_list, cloud_order_readonly_detail, cloud_expiry_actions, cloud_server_renew_payment, order_query_menu, balance_details_list, support_contact_button, cloud_lifecycle_notice_actions,
    cloud_server_change_ip_region_menu, cloud_server_change_ip_port_keyboard,
    cart_menu, wallet_recharge_prompt_menu, cloud_ip_query_result,
    cloud_query_menu, configured_link_for_label, configured_link_menu,
)
from bot.services import create_admin_reply_link, get_admin_reply_link, get_admin_reply_link_by_id, get_or_create_user, get_admin_forward_mute_status, is_admin_forward_muted, mute_admin_forward_for_days, record_bot_operation_log, record_telegram_message, should_forward_telegram_group
from cloud.services import (
    RenewalPriceMissingError,
    buy_cloud_server_with_balance,
    create_cloud_server_order,
    create_cloud_server_renewal,
    create_cloud_server_renewal_by_public_query,
    create_cloud_server_renewal_for_user,
    delay_cloud_server_expiry,
    disable_all_cloud_server_auto_renew,
    disable_all_cloud_server_auto_renew_admin,
    enable_all_cloud_server_auto_renew,
    enable_all_cloud_server_auto_renew_admin,
    get_cloud_plan,
    get_cloud_order_group_balance_lines,
    get_cloud_server_auto_renew,
    get_user_reminder_summary,
    get_cloud_server_by_ip,
    get_user_cloud_server,
    get_user_proxy_asset_detail,
    ensure_cloud_asset_operation_order,
    initialize_proxy_asset,
    list_all_auto_renew_cloud_servers,
    list_custom_regions,
    list_region_plans,
    list_user_auto_renew_cloud_servers,
    list_user_cloud_servers,
    list_cloud_server_upgrade_plans,
    create_cloud_server_upgrade_order,
    refund_cloud_server_to_balance,
    mark_cloud_server_ip_change_requested,
    mark_cloud_server_reinit_requested,
    mute_all_user_reminders,
    mute_cloud_order_reminders,
    mute_cloud_reminders,
    pay_cloud_server_order_with_balance,
    pay_cloud_server_renewal_with_balance,
    prepare_cloud_server_order_instances,
    run_cloud_server_renewal_postcheck,
    set_cloud_order_reminder,
    set_cloud_server_auto_renew,
    set_cloud_server_auto_renew_admin,
    start_cloud_server_from_admin,
    unmute_all_user_reminders,
    unmute_cloud_reminders,
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
from core.formatters import fmt_amount, fmt_pay_amount
from core.models import SiteConfig
from core.texts import site_text
from core.trongrid import build_trongrid_headers
from cloud.provisioning import get_provision_progress, provision_cloud_server, reprovision_cloud_server_bootstrap
from cloud.bootstrap import _probe_mtproxy_state, build_mtproxy_links
from cloud.ports import is_valid_mtproxy_main_port, mtproxy_port_validation_hint

logger = logging.getLogger(__name__)

_CUSTOM_REGIONS_CACHE: dict[str, object] = {'expires_at': 0.0, 'items': None}
_REGION_PLANS_CACHE: dict[str, tuple[float, object]] = {}
_TG_CHAT_CACHE: dict[int, tuple[float, dict[str, object]]] = {}
_USER_SYNC_CACHE: dict[int, tuple[float, tuple[str | None, str | None, tuple[str, ...]]]] = {}
_ASSET_REINIT_INFLIGHT: set[int] = set()
_REINSTALL_CONFIRM_TTL = 600
_CUSTOM_REGIONS_CACHE_TTL = 60
_REGION_PLANS_CACHE_TTL = 60
_TG_CHAT_CACHE_TTL = 120
_USER_SYNC_CACHE_TTL = 15
TRONGRID_BASE_URL = 'https://api.trongrid.io'
USDT_CONTRACT = 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'


def _extract_query_ip(raw_text: str) -> str:
    text = (raw_text or '').strip()
    if not text:
        return ''
    if '://' in text:
        parsed = urlparse(text)
        hostname = parsed.hostname or ''
        if hostname:
            return hostname.strip('[]')
        query_server = (parse_qs(parsed.query).get('server') or [''])[0]
        if query_server:
            return query_server.strip().strip('[]')
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


async def _fetch_tron_address_summary(address: str) -> dict:
    async with httpx.AsyncClient(timeout=15) as client:
        account_resp = await client.post(
            f'{TRONGRID_BASE_URL}/wallet/getaccount',
            json={'address': address, 'visible': True},
            headers=await build_trongrid_headers(),
        )
        account_resp.raise_for_status()
        account_data = account_resp.json() or {}
        resource_resp = await client.post(
            f'{TRONGRID_BASE_URL}/wallet/getaccountresource',
            json={'address': address, 'visible': True},
            headers=await build_trongrid_headers(),
        )
        resource_resp.raise_for_status()
        resource_data = resource_resp.json() or {}
        account_v1_resp = await client.get(f'{TRONGRID_BASE_URL}/v1/accounts/{address}', headers=await build_trongrid_headers())
        account_v1_resp.raise_for_status()
        account_v1_data = account_v1_resp.json() or {}
        trc20_resp = await client.get(
            f'{TRONGRID_BASE_URL}/v1/accounts/{address}/transactions/trc20?limit=20&only_confirmed=true&order_by=block_timestamp,desc',
            headers=headers,
        )
        trc20_resp.raise_for_status()
        trc20_data = trc20_resp.json() or {}
        trx_resp = await client.get(
            f'{TRONGRID_BASE_URL}/v1/accounts/{address}/transactions?limit=20&only_confirmed=true&order_by=block_timestamp,desc',
            headers=headers,
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
    results = []
    for index, ip in enumerate(query_ips):
        order = await get_cloud_server_by_ip(ip)
        if not order:
            continue
        display_ip = str(order.public_ip or order.previous_public_ip or ip).strip()
        is_deleted = order.status in {'deleted', 'deleting', 'expired'} or not display_ip
        if is_deleted:
            continue
        expires_at = getattr(order, 'service_expires_at', None)
        expires_text = _format_local_dt(expires_at).split(' ', 1)[0] if expires_at else '今天到期'
        auto_renew_text = '已开启' if getattr(order, 'auto_renew_enabled', False) else '未开启'
        group_balance_lines = await get_cloud_order_group_balance_lines(order.id)
        balance_block = ''
        if group_balance_lines:
            balance_block = '\n多用户余额:\n' + '\n'.join(escape(line) for line in group_balance_lines)
        results.append({
            'ip': display_ip,
            'text': f'IP: <code>{escape(display_ip)}</code>\n到期时间: {expires_text}\n自动续费: {auto_renew_text}\n状态: 可续费{balance_block}',
            'renewable': True,
            'order_id': order.id,
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
        await message.answer(_bot_text('bot_query_ip_empty', '🔎 IP查询到期\n\n未查询到可续费的有效 IP 记录。'), reply_markup=order_query_menu())
        return
    page = 1
    per_page = 8
    total_pages = max(1, math.ceil(len(results) / per_page))
    page_items = results[(page - 1) * per_page: page * per_page]
    text = '🔎 IP批量查询结果\n\n' + '\n\n'.join(item['text'] for item in page_items)
    renewable_items = [{'ip': item['ip'], 'order_id': item['order_id']} for item in page_items if item['renewable'] and item['order_id']]
    await message.answer(text, reply_markup=cloud_ip_query_result(page_items, renewable_items, page, total_pages, include_start=include_start), parse_mode='HTML')


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
        'noop': 'noop 无操作',
    }
    if data in exact:
        return exact[data]
    prefixes = [
        ('cloud:orderdetail:', 'cloud.orderdetail 云订单详情'),
        ('cloud:assetreinitconfirm:', 'cloud.assetreinitconfirm 确认资产重建'),
        ('cloud:assetinit:', 'cloud.assetinit 资产重新安装'),
        ('cloud:assetaction:', 'cloud.assetaction 资产操作'),
        ('cloud:assetdetail:', 'cloud.assetdetail 人工代理详情'),
        ('cloud:detail:', 'cloud.detail 代理详情'),
        ('cloud:list:page:', 'cloud.list.page 代理列表分页'),
        ('cloud:autorenewlist:all:', 'cloud.autorenewlist.all 自动续费批量开关'),
        ('cloud:autorenewlist:page:', 'cloud.autorenewlist.page 自动续费列表分页'),
        ('cloud:autorenewlist:on:', 'cloud.autorenewlist.on 开启自动续费'),
        ('cloud:autorenewlist:off:', 'cloud.autorenewlist.off 关闭自动续费'),
        ('cloud:queryip:page:', 'cloud.queryip.page IP查询分页'),
        ('cloud:renewpay:', 'cloud.renewpay 续费钱包支付'),
        ('cloud:renewwallet:', 'cloud.renewwallet 自动续费钱包支付'),
        ('cloud:renew:', 'cloud.renew 续费'),
        ('cloud:start:', 'cloud.start 管理员开机'),
        ('cloud:autorenew:', 'cloud.autorenew 自动续费开关'),
        ('cloud:delay:', 'cloud.delay 延期'),
        ('cloud:mute:', 'cloud.mute 关闭提醒'),
        ('cloud:ipport:default:', 'cloud.ipport.default 更换IP默认端口'),
        ('cloud:ipport:custom:', 'cloud.ipport.custom 更换IP自定义端口'),
        ('cloud:ipregions:more:', 'cloud.ipregions.more 更换IP更多地区'),
        ('cloud:ipregion:', 'cloud.ipregion 更换IP选地区'),
        ('cloud:ip:', 'cloud.ip 更换IP'),
        ('cloud:upgradepay:', 'cloud.upgradepay 升级支付'),
        ('cloud:upgrade:', 'cloud.upgrade 升级配置'),
        ('cloud:refundyes:', 'cloud.refundyes 确认退款'),
        ('cloud:refund:', 'cloud.refund 退款确认'),
        ('cloud:reinitconfirm:', 'cloud.reinitconfirm 确认重新初始化'),
        ('cloud:reinit:', 'cloud.reinit 重新安装/继续初始化'),
        ('profile:orders:cloud:page:', 'profile.orders.cloud.page 云服务器订单分页'),
        ('profile:reminders:ip:', 'profile.reminders.ip IP提醒详情'),
        ('profile:reminders:order:', 'profile.reminders.order 单IP生命周期提醒开关'),
        ('profile:reminders:auto:', 'profile.reminders.auto 单IP自动续费开关'),
        ('profile:reminders:page:', 'profile.reminders.page 提醒列表分页'),
        ('custom:region:', 'custom.region 选择地区'),
        ('custom:plan:', 'custom.plan 选择套餐'),
        ('custom:qty:', 'custom.qty 选择数量'),
        ('custom:paypage:', 'custom.paypage 支付页'),
        ('custom:qtycart:', 'custom.qtycart 加入购物车'),
        ('custom:walletpay:', 'custom.walletpay 钱包补付'),
        ('custom:wallet:', 'custom.wallet 钱包支付币种'),
        ('custom:currency:', 'custom.currency 支付币种'),
        ('custom:balance:', 'custom.balance 钱包支付'),
        ('custom:port:default:', 'custom.port.default 默认端口'),
        ('custom:port:custom:', 'custom.port.custom 自定义端口'),
        ('balance:detail:', 'balance.detail 余额明细详情'),
        ('rcur:', 'recharge.currency 充值币种'),
        ('rpage:', 'recharge.page 充值分页'),
        ('rdetail:', 'recharge.detail 充值详情'),
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
                'BOT_UPDATE_IN event=%s route="%s" handler=%s user_id=%s username=%s first_name=%s chat_id=%s message_id=%s callback_data=%s text=%s',
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
            if bot and not is_group_chat and (user.id == 1457254228 or not chat_username):
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
                        'ADMIN_REPLY_MIDDLEWARE_HANDLED user_id=%s chat_id=%s message_id=%s route=%s',
                        getattr(user, 'id', None),
                        chat_id,
                        message_id,
                        route_label,
                    )
                    return None
            except Exception as exc:
                logger.warning('ADMIN_REPLY_MIDDLEWARE_ERROR user_id=%s chat_id=%s message_id=%s error=%s', getattr(user, 'id', None), chat_id, message_id, exc)
        try:
            result = await handler(event, data)
            logger.info(
                'BOT_UPDATE_DONE event=%s route="%s" handler=%s user_id=%s chat_id=%s message_id=%s callback_data=%s elapsed_ms=%.1f',
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
                'BOT_UPDATE_ERROR event=%s route="%s" handler=%s user_id=%s chat_id=%s message_id=%s callback_data=%s elapsed_ms=%.1f error=%s',
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
        return sent
    except TelegramBadRequest as exc:
        if 'message is not modified' in str(exc).lower():
            logger.info('BOT_MESSAGE_EDIT_NOT_MODIFIED chat_id=%s message_id=%s text_preview=%s', getattr(getattr(message, 'chat', None), 'id', None), getattr(message, 'message_id', None), str(text or '').replace('\n', ' ')[:180])
            return None
        logger.warning('BOT_MESSAGE_EDIT_FAILED chat_id=%s message_id=%s error=%s text_preview=%s', getattr(getattr(message, 'chat', None), 'id', None), getattr(message, 'message_id', None), exc, str(text or '').replace('\n', ' ')[:180])
        raise


async def _safe_callback_answer(callback: CallbackQuery, *args, **kwargs):
    try:
        return await callback.answer(*args, **kwargs)
    except TelegramBadRequest as exc:
        message = str(exc).lower()
        if 'query is too old' in message or 'query id is invalid' in message or 'response timeout expired' in message:
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


def _cloud_server_created_text(order, port: int | None = None, title: str | None = None) -> str:
    mtproxy_link = getattr(order, 'mtproxy_link', '') or ''
    share_link = ''
    extra_links = []
    seen_links = set()
    public_ip = getattr(order, 'public_ip', '') or ''
    actual_port = port or getattr(order, 'mtproxy_port', '') or ''
    raw_secret = getattr(order, 'mtproxy_secret', '') or ''
    display_secret = ''
    for item in getattr(order, 'proxy_links', None) or []:
        link = item.get('url') if isinstance(item, dict) else ''
        if link and link not in seen_links:
            extra_links.append(link)
            seen_links.add(link)
            if not mtproxy_link:
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
            if link and link not in seen_links:
                extra_links.append(link)
                seen_links.add(link)
            if not mtproxy_link:
                mtproxy_link = link
    one_click_link = share_link or mtproxy_link or '-'
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
        for index, link in enumerate(additional_links[:8], start=1):
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
            await bot.send_message(chat_id=chat_id, text=error_text, reply_markup=_cloud_notice_keyboard_for_order(getattr(asset, 'order_id', None) or asset_id, 'cloud_asset_init_failed'))
            logger.warning('同步资产代理初始化失败: chat_id=%s user_id=%s asset_id=%s error=%s', chat_id, user_id, asset_id, err)
            return
        success_text = '✅ 同步资产代理初始化完成\n\n' + _cloud_server_created_text(asset, getattr(asset, 'mtproxy_port', None))
        _log_bot_cloud_notice('asset_init_completed', chat_id=chat_id, order=asset, text=success_text, keyboard='cloud_lifecycle_notice_actions')
        await bot.send_message(chat_id=chat_id, text=success_text, reply_markup=_cloud_notice_keyboard_for_order(getattr(asset, 'order_id', None) or asset_id, 'cloud_asset_init_completed'), parse_mode='HTML', disable_web_page_preview=True)
        logger.info('同步资产代理初始化完成: chat_id=%s user_id=%s asset_id=%s ip=%s', chat_id, user_id, asset_id, getattr(asset, 'public_ip', None))
    except Exception as exc:
        logger.exception('同步资产代理初始化异常: chat_id=%s user_id=%s asset_id=%s error=%s', chat_id, user_id, asset_id, exc)
        await bot.send_message(chat_id=chat_id, text=f'❌ 同步资产代理初始化任务异常\n错误: {_public_cloud_error_text(exc)}', reply_markup=main_menu())


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
            await bot.send_message(chat_id=chat_id, text=success_text, reply_markup=_cloud_notice_keyboard_for_order(provisioned.id, 'cloud_provision_completed'), parse_mode='HTML', disable_web_page_preview=True)
            logger.info('云服务器后台创建任务完成: chat_id=%s order_id=%s status=%s retry_only=%s ip=%s', chat_id, order_id, provisioned.status, retry_only, getattr(provisioned, 'public_ip', None) or getattr(provisioned, 'previous_public_ip', None))
            return
        current_status = provisioned.get_status_display() if hasattr(provisioned, 'get_status_display') else getattr(provisioned, 'status', '未知')
        action_label = '重试初始化' if retry_only else '创建'
        current_ip = getattr(provisioned, 'public_ip', None) or getattr(provisioned, 'previous_public_ip', None) or '未分配'
        incomplete_text = _bot_text_format('bot_async_task_incomplete', '⚠️ 云服务器{action_label}暂未完成\n\nIP: {ip}\n订单号: {order_no}\n当前状态: {current_status}\n\n请稍后在查询中心查看；不需要提醒可点击下方关闭提醒。', action_label=action_label, ip=current_ip, order_no=getattr(provisioned, 'order_no', '-') or '-', current_status=current_status)
        _log_bot_cloud_notice('provision_incomplete', chat_id=chat_id, order=provisioned, text=incomplete_text, keyboard='cloud_lifecycle_notice_actions')
        await bot.send_message(chat_id=chat_id, text=incomplete_text, reply_markup=_cloud_notice_keyboard_for_order(order_id, 'cloud_provision_incomplete'))
        logger.warning('云服务器后台创建任务未完成: chat_id=%s order_id=%s status=%s retry_only=%s', chat_id, order_id, current_status, retry_only)
    except Exception as exc:
        logger.exception('云服务器后台创建任务异常: chat_id=%s order_id=%s retry_only=%s error=%s', chat_id, order_id, retry_only, exc)
        action_label = '重试初始化' if retry_only else '创建'
        error_text = _bot_text_format('bot_async_task_error', '❌ 云服务器{action_label}任务异常\n\nIP: {ip}\n错误: {error}\n\n不需要提醒可点击下方关闭提醒；如需处理请联系人工客服。', action_label=action_label, ip='未分配', error=_public_cloud_error_text(exc))
        _log_bot_cloud_notice('provision_error', chat_id=chat_id, order=type('OrderNotice', (), {'id': order_id, 'order_no': None, 'public_ip': None, 'previous_public_ip': None, 'status': 'error'})(), text=error_text, keyboard='cloud_lifecycle_notice_actions')
        await bot.send_message(chat_id=chat_id, text=error_text, reply_markup=_cloud_notice_keyboard_for_order(order_id, 'cloud_provision_error'))
    finally:
        if progress_task:
            progress_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await progress_task


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
    await bot.send_message(chat_id=chat_id, text='🔎 续费已完成，正在检查服务器运行状态和 MTProxy 链路。')
    checked, err = await run_cloud_server_renewal_postcheck(order_id)
    if getattr(checked, 'replacement_for_id', None) and checked.status in {'paid', 'provisioning', 'failed'}:
        await bot.send_message(chat_id=chat_id, text='🛠 固定 IP 保留期续费已进入自动恢复流程。\n系统会保持旧 IP / 旧端口 / 旧密钥不变，完成后自动发送代理链接。')
        asyncio.create_task(_provision_cloud_server_and_notify(bot, chat_id, checked.id, checked.mtproxy_port or 9528))
        return
    if err:
        await bot.send_message(chat_id=chat_id, text=f'⚠️ 续费后巡检发现异常，已记录并尝试修复。\n订单号: {getattr(checked, "order_no", "-") or "-"}\n请稍后再查看代理状态，或联系人工客服。')
        return
    balance_text = _renew_balance_change_text(balance_change)
    plan_text = _cloud_order_plan_text(checked) if checked else ''
    await bot.send_message(
        chat_id=chat_id,
        text='\n'.join(filter(None, [
            '✅ 续费后巡检完成。',
            f'订单号: {getattr(checked, "order_no", "-") or "-"}',
            plan_text,
            balance_text,
            '服务器运行正常，MTProxy 主/备用端口正常。',
        ])),
    )


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
            f'支付金额: {fmt_pay_amount(order.pay_amount or order.total_amount)} USDT / {fmt_pay_amount(await usdt_to_trx(order.pay_amount or order.total_amount))} TRX\n'
            f'支付地址: <code>{escape(receive_address)}</code>\n'
            '订单 5 分钟有效，请在有效期内完成支付。\n\n'
            '系统已开始自动监控 USDT 和 TRX 到账，检测到支付成功后会自动进入后续流程。'
        )
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=custom_currency_keyboard(None, None, None, order.id), parse_mode='HTML', disable_web_page_preview=True)
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
                reply_markup=wallet_recharge_prompt_menu(),
            )
            return
        await bot.send_message(
            chat_id=chat_id,
            text=(
                '✅ 钱包支付成功\n\n'
                f'{_public_region_line(order.region_name)}'
                f'套餐: {order.plan_name}\n'
                f'数量: {order.quantity}\n'
                f'支付金额: {fmt_pay_amount(order.pay_amount)} {order.currency}\n\n'
                + _bot_text('bot_custom_port_hint', '请选择 MTProxy 端口：默认端口是 9528，你也可以输入自定义端口。')
            ),
            reply_markup=custom_port_keyboard(order.id),
        )
        logger.info('云服务器后台钱包直付任务完成: chat_id=%s user_id=%s order_id=%s order=%s currency=%s qty=%s pay_amount=%s', chat_id, user_id, order.id, order.order_no, currency, order.quantity, order.pay_amount)
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
                reply_markup=wallet_recharge_prompt_menu(),
            )
            return
        await bot.send_message(
            chat_id=chat_id,
            text=(
                '✅ 钱包支付成功\n\n'
                f'{_public_region_line(order.region_name)}'
                f'套餐: {order.plan_name}\n'
                f'数量: {order.quantity}\n'
                f'支付金额: {fmt_pay_amount(order.pay_amount)} {order.currency}\n\n'
                + _bot_text('bot_custom_port_hint', '请选择 MTProxy 端口：默认端口是 9528，你也可以输入自定义端口。')
            ),
            reply_markup=custom_port_keyboard(order.id),
        )
        logger.info('云服务器后台钱包补付任务完成: chat_id=%s user_id=%s order_id=%s order=%s currency=%s qty=%s pay_amount=%s', chat_id, user_id, order.id, order.order_no, currency, order.quantity, order.pay_amount)
    except Exception as exc:
        logger.exception('云服务器后台钱包补付任务异常: chat_id=%s user_id=%s order_id=%s currency=%s error=%s', chat_id, user_id, order_id, currency, exc)
        await bot.send_message(chat_id=chat_id, text=_bot_text_format('bot_wallet_pay_failed', '❌ 钱包支付失败，请稍后重试。\n错误: {error}', error=_public_cloud_error_text(exc)), reply_markup=main_menu())


def _orders_page(orders, page: int, total: int):
    total_pages = max(1, math.ceil(total / 5))
    if not orders:
        return _bot_text('bot_no_orders', '暂无订单记录。'), None
    return _bot_text('bot_orders_list_title', '📋 我的订单：'), kb_order_list(orders, page, total_pages)


def _balance_details_page(items, page: int, total: int):
    total_pages = max(1, math.ceil(total / 8))
    if not items:
        return _bot_text('bot_balance_details_empty', '💳 余额明细\n\n暂无余额流水。'), balance_details_list([], 1, 1)
    lines = [_bot_text('bot_balance_detail_title', '💳 余额明细'), '']
    for item in items:
        icon = '🟢' if item['direction'] == 'in' else '🔴'
        created_at = item['created_at'].strftime('%m-%d %H:%M') if item.get('created_at') else '-'
        lines.append(f"{icon} {item['title']} | {item['amount']} {item['currency']} | {created_at}")
    return '\n'.join(lines), balance_details_list(items, page, total_pages)


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


async def _validate_reinstall_proxy_link(order, link_data: dict[str, str], probe_when_possible: bool = True) -> tuple[bool, str]:
    order_ip = str(order.public_ip or order.previous_public_ip or '').strip()
    order_port = str(order.mtproxy_port or link_data['port'] or 9528)
    parsed_secret = _normalize_proxy_secret(link_data.get('secret', ''))
    logger.info(
        'CLOUD_REINSTALL_LINK_PARSED item_id=%s ip_expected=%s port_expected=%s parsed_server=%s parsed_port=%s parsed_secret=%s probe_when_possible=%s has_login_password=%s',
        getattr(order, 'id', None),
        order_ip,
        order_port,
        link_data.get('server'),
        link_data.get('port'),
        _secret_log_hint(link_data.get('secret', '')),
        probe_when_possible,
        bool(getattr(order, 'login_password', None)),
    )
    if link_data['server'] != order_ip:
        logger.warning('CLOUD_REINSTALL_LINK_COMPARE_FAIL reason=ip item_id=%s expected_ip=%s parsed_ip=%s', getattr(order, 'id', None), order_ip, link_data['server'])
        return False, f'链接 IP 不匹配。当前服务器 IP 是 {order_ip}，你发的是 {link_data["server"]}'
    if link_data['port'] != order_port:
        logger.warning('CLOUD_REINSTALL_LINK_COMPARE_FAIL reason=port item_id=%s expected_port=%s parsed_port=%s', getattr(order, 'id', None), order_port, link_data['port'])
        return False, f'链接端口不匹配。当前主代理端口是 {order_port}，你发的是 {link_data["port"]}'
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
        ok, probe = await _probe_mtproxy_state(order_ip, candidate, order.login_password, int(order_port))
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
        order_port,
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
            logger.warning('CLOUD_REINSTALL_LINK_COMPARE_PROBE_FAILED_BUT_STORED_SECRET_MATCH item_id=%s ip=%s port=%s users=%s', getattr(order, 'id', None), order_ip, order_port, ','.join(probe_users))
            return True, '主链接格式、IP 和已记录密钥校验通过'
        return False, '无法登录服务器确认代理状态，请稍后再试或联系后台检查 SSH/代理服务'
    if remote_secret_normalized != parsed_secret:
        logger.warning('CLOUD_REINSTALL_LINK_COMPARE_FAIL reason=secret item_id=%s remote_secret=%s parsed_secret=%s', getattr(order, 'id', None), _secret_log_hint(remote_secret_normalized), _secret_log_hint(parsed_secret))
        return False, '链接密钥和服务器实际运行密钥不一致，请检查后重新发送主链接'
    logger.info('CLOUD_REINSTALL_LINK_COMPARE_OK item_id=%s ip=%s port=%s secret=%s', getattr(order, 'id', None), order_ip, order_port, _secret_log_hint(parsed_secret))
    return True, '主链接校验通过'


@sync_to_async
def _save_asset_main_proxy_link(asset_id: int, user_id: int, link_data: dict[str, str]):
    from cloud.models import CloudAsset
    asset = CloudAsset.objects.get(id=asset_id, user_id=user_id)
    asset.mtproxy_link = link_data['url']
    asset.mtproxy_secret = link_data['secret']
    asset.mtproxy_host = link_data['server']
    asset.mtproxy_port = int(link_data['port'])
    links = list(asset.proxy_links or [])
    links = [item for item in links if not (isinstance(item, dict) and str(item.get('port') or '') == str(asset.mtproxy_port))]
    links.insert(0, {'name': '主代理 mtg', 'server': link_data['server'], 'port': link_data['port'], 'secret': link_data['secret'], 'url': link_data['url']})
    asset.proxy_links = links
    asset.note = '\n'.join(filter(None, [asset.note, '用户补充主代理链接，准备重新安装。']))
    asset.save(update_fields=['mtproxy_link', 'mtproxy_secret', 'mtproxy_host', 'mtproxy_port', 'proxy_links', 'note', 'updated_at'])
    return asset


@sync_to_async
def _save_user_main_proxy_link(order_id: int, link_data: dict[str, str]):
    from cloud.models import CloudAsset, CloudServerOrder
    order = CloudServerOrder.objects.get(id=order_id)
    order.mtproxy_link = link_data['url']
    order.mtproxy_secret = link_data['secret']
    order.mtproxy_host = link_data['server']
    order.mtproxy_port = int(link_data['port'])
    links = list(order.proxy_links or [])
    links = [item for item in links if not (isinstance(item, dict) and str(item.get('port') or '') == str(order.mtproxy_port))]
    links.insert(0, {'name': '主代理 mtg', 'server': link_data['server'], 'port': link_data['port'], 'secret': link_data['secret'], 'url': link_data['url']})
    order.proxy_links = links
    order.provision_note = '\n'.join(filter(None, [order.provision_note, '用户补充并校验主代理链接，准备重新安装。']))
    order.save(update_fields=['mtproxy_link', 'mtproxy_secret', 'mtproxy_host', 'mtproxy_port', 'proxy_links', 'provision_note', 'updated_at'])
    CloudAsset.objects.filter(order=order).update(mtproxy_link=order.mtproxy_link, mtproxy_secret=order.mtproxy_secret, mtproxy_host=order.mtproxy_host, mtproxy_port=order.mtproxy_port, proxy_links=links, updated_at=timezone.now())
    return order


def _reinstall_confirm_keyboard(order_id: int, token: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='确认重新安装', callback_data=f'cloud:reinitconfirm:{order_id}:{token}')],
        [InlineKeyboardButton(text='取消', callback_data=f'cloud:detail:{order_id}')],
    ])


def _asset_reinstall_confirm_keyboard(asset_id: int, token: str):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text='确认重新安装', callback_data=f'cloud:assetreinitconfirm:{asset_id}:{token}')],
        [InlineKeyboardButton(text='取消', callback_data=f'cloud:assetdetail:asset:{asset_id}:cloud:list:page:1')],
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


def _cloud_can_refund(order, now=None) -> bool:
    if order.status not in {'paid', 'provisioning', 'failed', 'completed', 'expiring', 'suspended'}:
        return False
    expires_at = getattr(order, 'service_expires_at', None)
    if expires_at and expires_at < (now or timezone.now()) + timezone.timedelta(days=10):
        return False
    return True


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


def _cloud_order_plan_text(order) -> str:
    expires_at = getattr(order, 'service_expires_at', None)
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
    if suspend_at:
        lines.append(f'请务必在 {_format_local_dt(suspend_at)} 之前完成续费，避免关机。')
    if delete_at:
        lines.append(f'如已关机，请务必在 {_format_local_dt(delete_at)} 之前完成续费，避免实例删除。')
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
            lines.append(f'- {escape(str(ip))} | 到期 {_format_local_dt(order.service_expires_at)} | 提醒 {reminder_count}/4 | {auto}')
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
        f'到期时间: {_format_local_dt(order.service_expires_at)}',
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
        f'到期时间: {_format_local_dt(getattr(item, "service_expires_at", None))}\n'
        f'创建时间: {_format_local_dt(getattr(item, "created_at", None))}'
    )


def _cloud_server_detail_text(order) -> str:
    status_hint = _cloud_order_status_hint(order)
    service_expires_at = _format_local_dt(order.service_expires_at) if order.service_expires_at else '今天到期'
    renew_price = getattr(order, 'renewal_price', None) or order.pay_amount or order.total_amount
    auto_renew_status = '已开启' if getattr(order, 'auto_renew_enabled', False) else '已关闭'
    proxy_links_text = _proxy_links_text(order)
    text = (
        '☁️ 云服务器详情\n\n'
        f'订单号: {order.order_no}\n'
        f'{_public_region_line(order.region_name)}'
        f'套餐: {order.plan_name}\n'
        f'数量: {order.quantity}\n'
        f'状态: {order.get_status_display()}\n'
        f'支付方式: {"余额" if order.pay_method == "balance" else "地址"}\n'
        f'金额: {fmt_pay_amount(order.pay_amount or order.total_amount)} {order.currency}\n'
        f'IP: <code>{escape(order.public_ip or order.previous_public_ip or "未分配")}</code>\n'
        f'端口: <code>{escape(str(order.mtproxy_port or "未设置"))}</code>\n'
        f'密钥: <code>{escape(str(order.mtproxy_secret or "尚未生成"))}</code>\n'
        f'{proxy_links_text}\n'
        f'到期时间: {service_expires_at}\n'
        f'续费价格: {fmt_pay_amount(renew_price)} {order.currency}\n'
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
        asset = CloudAsset.objects.filter(order_id=order.id).order_by('-updated_at', '-id').first()
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
            candidates.extend(CloudAsset.objects.filter(
                Q(instance_id__in=lookup_values) | Q(provider_resource_id__in=lookup_values) | Q(asset_name__in=lookup_values) | Q(public_ip__in=lookup_values) | Q(previous_public_ip__in=lookup_values)
            ).order_by('-updated_at', '-id')[:5])
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
    service_expires_at = _format_local_dt(order.service_expires_at) if order.service_expires_at else '未设置'
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
        f'到期时间: {service_expires_at}\n'
        f'支付时间: {paid_at_text}'
    )
    if chain_trace:
        text += f'\n{chain_trace}'
    text += f'\n创建时间: {order.created_at:%Y-%m-%d %H:%M:%S}'
    if status_hint:
        text += f'\n{status_hint}'
    text += '\n\n此处仅用于查询订单，不提供自助操作。如需续费、退款、初始化或其他处理，请联系人工客服。'
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


def _receive_address() -> str:
    from core.cache import _cached_config
    return _cached_config.get('receive_address', '')


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
        if not address.startswith('T') or len(address) < 30:
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
        await set_monitor_threshold(mid, user.id, 'USDT', val)
        from cloud.cache import update_monitor_threshold_in_cache
        mon = await get_monitor(mid, user.id)
        if mon:
            await update_monitor_threshold_in_cache(mon.address, 'USDT', val)
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
        await set_monitor_threshold(mid, user.id, 'TRX', val)
        from cloud.cache import update_monitor_threshold_in_cache
        mon = await get_monitor(mid, user.id)
        if mon:
            await update_monitor_threshold_in_cache(mon.address, 'TRX', val)
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
        await set_monitor_threshold(mid, user.id, 'ENERGY', val)
        from cloud.cache import update_monitor_threshold_in_cache
        mon = await get_monitor(mid, user.id)
        if mon:
            await update_monitor_threshold_in_cache(mon.address, 'ENERGY', val)
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
        await set_monitor_threshold(mid, user.id, 'BANDWIDTH', val)
        from cloud.cache import update_monitor_threshold_in_cache
        mon = await get_monitor(mid, user.id)
        if mon:
            await update_monitor_threshold_in_cache(mon.address, 'BANDWIDTH', val)
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

    @dp.message(CustomServerStates.waiting_quantity)
    async def custom_quantity_input(message: Message, state: FSMContext):
        if await _handle_menu_interrupt(message, state):
            return
        text = message.text.strip()
        logger.info('云服务器自定义数量输入: tg_user_id=%s raw_text=%s', getattr(message.from_user, 'id', None), text)
        if not text.isdigit() or int(text) <= 0 or int(text) > 99:
            await message.answer(_bot_text('bot_custom_quantity_invalid', '请输入 1-99 的购买数量：\n\n可随时点击底部菜单打断当前输入。'))
            return
        data = await state.get_data()
        plan_id = int(data['custom_plan_id'])
        quantity = int(text)
        logger.info('云服务器自定义数量确认: tg_user_id=%s plan_id=%s quantity=%s state_data=%s', getattr(message.from_user, 'id', None), plan_id, quantity, {k: v for k, v in data.items() if k.startswith('custom_')})
        await state.clear()
        plan = await get_cloud_plan(plan_id)
        if not plan:
            await message.answer(_bot_text('bot_custom_plan_missing', '套餐不存在或已下架，请重新选择。'), reply_markup=main_menu())
            return
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        order = await create_cloud_server_order(user.id, plan.id, 'USDT', quantity)
        logger.info('云服务器下单进入详情: tg_user_id=%s user=%s order_id=%s order=%s qty=%s region=%s plan_id=%s plan_name=%s currency=%s total=%s pay_amount=%s', getattr(message.from_user, 'id', None), user.id, order.id, order.order_no, order.quantity, order.region_code, plan.id, plan.plan_name, order.currency, order.total_amount, order.pay_amount)
        receive_address = _receive_address()
        await message.answer(
            '🧾 订单详情\n\n'
            f'{_public_region_line(plan.region_name)}'
            f'套餐: {display_name}\n'
            f'数量: {order.quantity}\n'
            f'支付金额: {fmt_pay_amount(order.pay_amount or order.total_amount)} USDT / {fmt_pay_amount(await usdt_to_trx(order.pay_amount or order.total_amount))} TRX\n'
            f'支付地址: <code>{escape(receive_address)}</code>\n'
            '订单 5 分钟有效，请在有效期内完成支付。\n\n'
            + _bot_text('bot_custom_order_notice', '系统已开始自动监控 USDT 和 TRX 到账，检测到支付成功后会自动进入后续流程。'),
            reply_markup=custom_currency_keyboard(None, None, None, order.id),
            parse_mode='HTML',
            disable_web_page_preview=True,
        )

    @dp.message(CustomServerStates.waiting_port)
    async def input_custom_server_port(message: Message, state: FSMContext, bot: Bot):
        if await _handle_menu_interrupt(message, state):
            return
        logger.info('云服务器自定义端口输入: tg_user_id=%s raw_text=%s', getattr(message.from_user, 'id', None), (message.text or '').strip())
        try:
            port = int(message.text.strip())
        except Exception:
            await message.answer(_bot_text('bot_custom_port_invalid', mtproxy_port_validation_hint() + '\n\n可随时点击底部菜单打断当前输入。'))
            return
        if not is_valid_mtproxy_main_port(port):
            await message.answer(_bot_text('bot_custom_port_invalid', mtproxy_port_validation_hint() + '\n\n可随时点击底部菜单打断当前输入。'))
            return
        data = await state.get_data()
        order_id = data.get('cloud_ip_change_order_id') or data.get('custom_order_id')
        region_code = data.get('cloud_ip_change_region_code')
        region_name = data.get('cloud_ip_change_region_name')
        logger.info('云服务器自定义端口确认: tg_user_id=%s order_id=%s port=%s state_data=%s', getattr(message.from_user, 'id', None), order_id, port, {k: v for k, v in data.items() if k.startswith('custom_') or k.startswith('cloud_ip_change_')})
        if not order_id:
            await state.clear()
            await message.answer(_bot_text('bot_custom_context_missing', '订单上下文已失效，请重新下单。'), reply_markup=main_menu())
            return
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        if region_code:
            order = await mark_cloud_server_ip_change_requested(order_id, user.id, region_code, port)
            await state.clear()
            if not order:
                await message.answer(_bot_text('bot_change_ip_failed', '更换IP失败，请返回详情页重试。'), reply_markup=main_menu())
                return
            await message.answer(
                _bot_text_format(
                    'bot_ip_change_order_created',
                    '✅ 更换IP新服务器已创建\n新订单号: {order_no}\n新节点: {region_name}\n新端口: {port}\n系统会新建同配置服务器并绑定新的固定 IP，请在 5 天内切换使用。',
                    order_no=order.order_no,
                    region_name=_public_region_text(region_name or order.region_name) or '默认节点',
                    port=port,
                ),
                reply_markup=main_menu(),
            )
            asyncio.create_task(_provision_cloud_server_and_notify(bot, message.chat.id, order.id, port))
            return
        orders = await prepare_cloud_server_order_instances(order_id, user.id, port)
        logger.info('云服务器提交自定义端口: tg_user_id=%s user=%s order_id=%s port=%s orders=%s', getattr(message.from_user, 'id', None), user.id, order_id, port, [getattr(item, 'order_no', None) for item in orders])
        await state.clear()
        if not orders:
            await message.answer(_bot_text('bot_set_port_failed', '订单不存在，无法设置端口。'), reply_markup=main_menu())
            return
        task_count = len(orders)
        await message.answer(_bot_text_format('bot_custom_port_success', '✅ 端口设置成功：{port}\n已开始后台创建 {count} 台服务器，我会在完成后主动通知你。', port=port, count=task_count), reply_markup=main_menu())
        for order in orders:
            asyncio.create_task(_provision_cloud_server_and_notify(bot, message.chat.id, order.id, port))

    # ══════════════════════════════════════════════════════════════════════
    # 普通消息（菜单按钮 + /start）
    # ══════════════════════════════════════════════════════════════════════

    @dp.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext):
        await state.clear()
        await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        sent = await message.answer(_bot_text('bot_welcome', '欢迎使用商城机器人！请选择操作：'), reply_markup=main_menu())
        logger.info('BOT_MESSAGE_SEND route=start user_id=%s chat_id=%s reply_to=%s sent_message_id=%s', getattr(message.from_user, 'id', None), message.chat.id, message.message_id, getattr(sent, 'message_id', None))

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
            logger.info('BOT_MESSAGE_SEND route=menu_link label=%s user_id=%s chat_id=%s reply_to=%s sent_message_id=%s', text, getattr(message.from_user, 'id', None), message.chat.id, message.message_id, getattr(sent, 'message_id', None))
            return

        if text == '✨ 订阅':
            sent = await message.answer(_bot_text('bot_removed_products_entry', '商品购买入口已移除，请使用“🛠 购买节点”或“🔎 到期时间查询”。'), reply_markup=main_menu())
            logger.info('BOT_MESSAGE_SEND route=menu_removed label=%s user_id=%s chat_id=%s reply_to=%s sent_message_id=%s', text, getattr(message.from_user, 'id', None), message.chat.id, message.message_id, getattr(sent, 'message_id', None))

        elif text == '🛠 购买节点':
            regions = await list_custom_regions()
            sent = await message.answer(_bot_text('bot_custom_region_entry', '🛠 购买节点\n\n请选择热门地区：'), reply_markup=custom_region_menu(regions, expanded=False))
            logger.info('BOT_MESSAGE_SEND route=menu_custom user_id=%s chat_id=%s reply_to=%s sent_message_id=%s', getattr(message.from_user, 'id', None), message.chat.id, message.message_id, getattr(sent, 'message_id', None))

        elif text == '🔎 到期时间查询':
            sent = await message.answer(_bot_text('bot_query_center_entry', '🔎 查询中心\n\n请选择查询方式：'), reply_markup=cloud_query_menu())
            logger.info('BOT_MESSAGE_SEND route=menu_query user_id=%s chat_id=%s reply_to=%s sent_message_id=%s', getattr(message.from_user, 'id', None), message.chat.id, message.message_id, getattr(sent, 'message_id', None))

        elif text == '👤 个人中心':
            sent = await message.answer(
                f'👤 个人中心\n用户ID: {user.tg_user_id}\n用户名: {_display_username(user)}\n'
                f'💵 USDT 余额: {fmt_amount(user.balance)}\n🪙 TRX 余额: {fmt_amount(user.balance_trx)}\n\n'
                f'请选择要进入的功能：',
                reply_markup=profile_menu(),
            )
            logger.info('BOT_MESSAGE_SEND route=menu_profile user_id=%s chat_id=%s reply_to=%s sent_message_id=%s', getattr(message.from_user, 'id', None), message.chat.id, message.message_id, getattr(sent, 'message_id', None))

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
        text = '🔎 IP批量查询结果\n\n' + '\n\n'.join(item['text'] for item in page_items)
        renewable_items = [{'ip': item['ip'], 'order_id': item['order_id']} for item in page_items if item['renewable'] and item['order_id']]
        await _safe_edit_text(callback.message, text, reply_markup=cloud_ip_query_result(page_items, renewable_items, page, total_pages, include_start=await _is_admin_chat(callback.message)), parse_mode='HTML')

    async def _render_profile_cloud_orders(callback: CallbackQuery, page: int = 1):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        per_page = 8
        orders, total = await list_cloud_orders(user.id, page=page, per_page=per_page)
        total_pages = max(1, math.ceil(total / per_page))
        if not orders:
            await _safe_edit_text(callback.message, _bot_text('bot_cloud_orders_empty', '☁️ 云服务器订单\n\n暂无云服务器订单。'), reply_markup=profile_menu())
            return
        await _safe_edit_text(
            callback.message,
            _bot_text('bot_cloud_orders_entry', '☁️ 云服务器订单\n\n请选择要查看的订单：'),
            reply_markup=cloud_order_list(orders, page, total_pages, 'profile:orders:cloud:page'),
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

    @dp.callback_query(F.data == 'profile:cart')
    async def cb_profile_cart(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        await _safe_edit_text(callback.message, _bot_text('bot_cart_removed', '商品/购物车入口已移除，请使用云服务器相关功能。'), reply_markup=profile_menu())

    @dp.callback_query(F.data == 'profile:balance_details')
    async def cb_profile_balance_details(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        items, total = await list_balance_details(user.id)
        text_out, kb = _balance_details_page(items, 1, total)
        await _safe_edit_text(callback.message, text_out, reply_markup=kb)
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
        quantity = int(qty_text)
        display_name = _plan_display_name(plan)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        pending_order = await create_cloud_server_order(user.id, plan.id, 'USDT', quantity)
        await state.update_data(custom_plan_id=plan.id, custom_plan_name=display_name, custom_plan_price=str(plan.price), custom_region_code=plan.region_code, custom_region_name=plan.region_name, custom_quantity=quantity, custom_order_id=pending_order.id)
        usdt_amount = Decimal(str(pending_order.pay_amount or pending_order.total_amount or 0))
        trx_amount = await usdt_to_trx(usdt_amount)
        receive_address = _receive_address()
        text = (
            _bot_text('bot_custom_payment_title', '🧾 支付页面') + '\n\n'
            f'订单号: {pending_order.order_no}\n'
            f'{_public_region_line(plan.region_name)}'
            f'套餐: {display_name}\n'
            f'数量: {quantity}\n'
            f'USDT金额: {fmt_pay_amount(usdt_amount)} USDT\n'
            f'TRX金额: {fmt_pay_amount(trx_amount)} TRX\n'
            f'支付地址: <code>{escape(receive_address)}</code>\n\n'
            + _bot_text('bot_custom_order_notice', '系统已开始自动监控 USDT 和 TRX 到账，检测到支付成功后会自动进入后续流程。')
        )
        await _safe_callback_answer(callback)
        await _safe_edit_text(callback.message, text, reply_markup=custom_payment_keyboard(pending_order.id, plan.id, quantity), parse_mode='HTML', disable_web_page_preview=True)


    @dp.callback_query(F.data.startswith('custom:paypage:'))
    async def cb_custom_paypage(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        _, _, plan_id_text, quantity_text = callback.data.split(':')
        plan_id = int(plan_id_text)
        quantity = int(quantity_text)
        plan = await get_cloud_plan(plan_id)
        if not plan:
            await _safe_callback_answer(callback, '套餐不存在或已下架', show_alert=True)
            return
        display_name = _plan_display_name(plan)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        pending_order = await create_cloud_server_order(user.id, plan.id, 'USDT', quantity)
        await state.update_data(custom_plan_id=plan.id, custom_plan_name=display_name, custom_plan_price=str(plan.price), custom_region_code=plan.region_code, custom_region_name=plan.region_name, custom_quantity=quantity, custom_order_id=pending_order.id)
        usdt_amount = Decimal(str(pending_order.pay_amount or pending_order.total_amount or 0))
        trx_amount = await usdt_to_trx(usdt_amount)
        receive_address = _receive_address()
        text = (
            _bot_text('bot_custom_payment_title', '🧾 支付页面') + '\n\n'
            f'订单号: {pending_order.order_no}\n'
            f'{_public_region_line(plan.region_name)}'
            f'套餐: {display_name}\n'
            f'数量: {quantity}\n'
            f'USDT金额: {fmt_pay_amount(usdt_amount)} USDT\n'
            f'TRX金额: {fmt_pay_amount(trx_amount)} TRX\n'
            f'支付地址: <code>{escape(receive_address)}</code>\n\n'
            + _bot_text('bot_custom_order_notice', '系统已开始自动监控 USDT 和 TRX 到账，检测到支付成功后会自动进入后续流程。')
        )
        await _safe_edit_text(callback.message, text, reply_markup=custom_payment_keyboard(pending_order.id, plan.id, quantity), parse_mode='HTML', disable_web_page_preview=True)


    @dp.callback_query(F.data.startswith('custom:qtycart:'))
    async def cb_custom_quantity_add_to_cart(callback: CallbackQuery):
        _, _, plan_id_text, qty_text = callback.data.split(':')
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        item = await add_to_cart(user.id, int(plan_id_text), int(qty_text), item_type='cloud_plan')
        if not item:
            await _safe_callback_answer(callback, '套餐不存在或已下架', show_alert=True)
            return
        await _safe_callback_answer(callback, '已加入购物车')

    @dp.callback_query(F.data.startswith('custom:wallet:'))
    async def cb_custom_wallet(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        _, _, plan_id_text, quantity_text = callback.data.split(':')
        plan_id = int(plan_id_text)
        quantity = int(quantity_text)
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
        usdt_amount = Decimal(str(getattr(plan, 'price', 0))) * quantity
        trx_amount = await usdt_to_trx(usdt_amount)
        logger.info('云服务器钱包币种页准备完成: tg_user_id=%s plan_id=%s quantity=%s usdt=%s trx=%s', getattr(callback.from_user, 'id', None), plan_id, quantity, usdt_amount, trx_amount)
        await _safe_edit_text(
            callback.message,
            _bot_text('bot_custom_wallet_title', '请选择钱包支付币种：'),
            reply_markup=custom_wallet_keyboard(plan.id, quantity, usdt_amount, trx_amount),
        )

    @dp.callback_query(F.data.startswith('custom:currency:'))
    async def cb_custom_currency(callback: CallbackQuery, state: FSMContext, bot: Bot):
        await _safe_callback_answer(callback, '订单创建中，完成后将主动通知你')
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, plan_id_text, quantity_text, currency = callback.data.split(':')
        plan_id = int(plan_id_text)
        quantity = int(quantity_text)
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
        plan_id = int(plan_id_text)
        quantity = int(quantity_text)
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
            await _safe_edit_text(
                callback.message,
                f"{_bot_text('bot_custom_balance_insufficient', '❌ 余额不足，请先充值')}\n\n当前支付币种: {currency}",
                reply_markup=wallet_recharge_prompt_menu(),
            )
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
            payable_amount = Decimal(str(order.pay_amount or order.total_amount or 0))
            trx_amount = await usdt_to_trx(payable_amount)
            logger.info('云服务器订单钱包币种页准备完成: tg_user_id=%s user=%s order_id=%s total=%s pay=%s trx=%s', getattr(callback.from_user, 'id', None), user.id, order.id, order.total_amount, order.pay_amount, trx_amount)
            await _safe_edit_text(callback.message, 
                _bot_text('bot_custom_wallet_title', '请选择钱包支付币种：'),
                reply_markup=custom_order_wallet_keyboard(order.id, payable_amount, trx_amount),
            )
            return
        currency = parts[3]
        logger.info('云服务器订单钱包补付开始: tg_user_id=%s user=%s order_id=%s currency=%s', getattr(callback.from_user, 'id', None), user.id, order_id, currency)
        from cloud.models import CloudServerOrder
        order = await asyncio.to_thread(lambda: CloudServerOrder.objects.filter(id=order_id, user_id=user.id).first())
        if not order:
            await _safe_callback_answer(callback, '订单不存在', show_alert=True)
            return
        payable_amount = Decimal(str(order.pay_amount or order.total_amount or 0))
        total_amount = await usdt_to_trx(payable_amount) if currency == 'TRX' else payable_amount
        current_balance = Decimal(str(getattr(user, 'balance_trx' if currency == 'TRX' else 'balance', 0) or 0))
        if current_balance < total_amount:
            await _safe_callback_answer(callback, f'{currency} 余额不足', show_alert=True)
            await _safe_edit_text(
                callback.message,
                f"{_bot_text('bot_custom_balance_insufficient', '❌ 余额不足，请先充值')}\n\n当前支付币种: {currency}",
                reply_markup=wallet_recharge_prompt_menu(),
            )
            return
        await _safe_edit_text(
            callback.message,
            _bot_text('bot_custom_pending_wallet', '⏳ 正在后台处理钱包支付，请稍候…\n\n处理完成后会主动把结果发给你。'),
        )
        asyncio.create_task(_pay_cloud_server_order_with_balance_and_notify(bot, callback.from_user.id, user.id, order_id, currency))

    @dp.callback_query(F.data.startswith('custom:port:default:'))
    async def cb_custom_port_default(callback: CallbackQuery, state: FSMContext, bot: Bot):
        await _safe_callback_answer(callback, '默认端口设置成功，已开始后台创建服务器')
        order_id = int(callback.data.split(':')[3])
        logger.info('云服务器选择默认端口: tg_user_id=%s order_id=%s port=9528 callback=%s', getattr(callback.from_user, 'id', None), order_id, callback.data)
        await state.clear()
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        orders = await prepare_cloud_server_order_instances(order_id, user.id, 9528)
        logger.info('云服务器使用默认端口: tg_user_id=%s user=%s order_id=%s port=9528 orders=%s', getattr(callback.from_user, 'id', None), user.id, order_id, [getattr(item, 'order_no', None) for item in orders])
        if not orders:
            await _safe_callback_answer(callback, '订单不存在', show_alert=True)
            return
        task_count = len(orders)
        await bot.send_message(
            chat_id=callback.from_user.id,
            text=f'✅ 已选择默认端口 9528。\n{task_count} 台服务器创建任务已提交，正在后台处理，完成后会自动发送创建结果。',
            reply_markup=main_menu(),
        )
        for order in orders:
            asyncio.create_task(_provision_cloud_server_and_notify(bot, callback.from_user.id, order.id, 9528))

    @dp.callback_query(F.data.startswith('custom:port:custom:'))
    async def cb_custom_port_custom(callback: CallbackQuery, state: FSMContext, bot: Bot):
        await _safe_callback_answer(callback, '已选择自定义端口')
        order_id = int(callback.data.split(':')[3])
        logger.info('云服务器进入自定义端口输入: tg_user_id=%s order_id=%s callback=%s', getattr(callback.from_user, 'id', None), order_id, callback.data)
        await state.update_data(custom_order_id=order_id)
        await state.set_state(CustomServerStates.waiting_port)
        await bot.send_message(
            chat_id=callback.from_user.id,
            text='✍️ 已选择自定义端口。\n请发送 443 或 1025-65530 之间的端口号，发送后我会立即提交服务器创建任务。',
        )

    @dp.callback_query(F.data == 'cloud:list')
    async def cb_cloud_list(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        visible_servers = await list_user_cloud_servers(user.id)
        page = 1
        per_page = 8
        total_visible = len(visible_servers)
        total_pages = max(1, math.ceil(total_visible / per_page))
        page_items = visible_servers[(page - 1) * per_page: page * per_page]
        if not page_items:
            await callback.message.delete()
            await callback.message.answer(_bot_text('bot_query_cloud_empty', '🔎 查询中心\n\n暂无可查询的代理记录。'), reply_markup=main_menu())
        else:
            await _safe_edit_text(callback.message, '🔎 代理列表\n\n请选择要查看的代理：', reply_markup=cloud_server_list(page_items, page, total_pages, 'cloud:list:page'))

    @dp.callback_query(F.data.startswith('cloud:list:page:'))
    async def cb_cloud_list_page(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        page = max(1, int(callback.data.split(':')[3]))
        visible_servers = await list_user_cloud_servers(user.id)
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
            '🔎 代理列表\n\n请选择要查看的代理：',
            reply_markup=cloud_server_list(page_items, page, total_pages, 'cloud:list:page'),
        )

    async def _render_cloud_auto_renew_list(callback: CallbackQuery, page: int = 1):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        is_admin = await _is_admin_chat(callback.message)
        visible_servers = await list_all_auto_renew_cloud_servers() if is_admin else await list_user_auto_renew_cloud_servers(user.id)
        per_page = 8
        total_visible = len(visible_servers)
        total_pages = max(1, math.ceil(total_visible / per_page))
        page = min(max(1, page), total_pages)
        page_items = visible_servers[(page - 1) * per_page: page * per_page]
        if not page_items:
            await _safe_edit_text(callback.message, '⚡ 自动续费列表\n\n暂无可设置自动续费的代理。', reply_markup=cloud_query_menu())
            return
        enabled_count = sum(1 for item in visible_servers if getattr(item, 'auto_renew_enabled', False))
        title = '⚡ 自动续费列表'
        scope = '我的代理'
        text = f'{title}\n\n{scope}\n已开启 {enabled_count}/{total_visible}。\n✅=已开启，❌=已关闭；点击每行可开启/关闭。'
        await _safe_edit_text(
            callback.message,
            text,
            reply_markup=cloud_auto_renew_server_list(page_items, page, total_pages, is_admin=is_admin),
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
        if enabled:
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
        page = max(1, int(raw_page or 1))
        if await _is_admin_chat(callback.message):
            order = await set_cloud_server_auto_renew_admin(int(raw_order_id), enabled)
        else:
            order = await set_cloud_server_auto_renew(int(raw_order_id), user.id, enabled)
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
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        page = max(1, int(callback.data.split(':')[4]))
        per_page = 8
        orders, total = await list_cloud_orders(user.id, page=page, per_page=per_page)
        total_pages = max(1, math.ceil(total / per_page))
        if not orders:
            await _safe_edit_text(callback.message, '☁️ 云服务器订单\n\n暂无云服务器订单。', reply_markup=profile_menu())
            return
        await _safe_edit_text(
            callback.message,
            '☁️ 云服务器订单\n\n请选择要查看的订单：',
            reply_markup=cloud_order_list(orders, page, total_pages, 'profile:orders:cloud:page'),
        )

    @dp.callback_query(F.data.startswith('cloud:orderdetail:'))
    async def cb_cloud_order_detail(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        parts = callback.data.split(':')
        order_id = int(parts[2])
        back_callback = 'profile:orders:cloud:page:1'
        if len(parts) >= 6:
            prefix = ':'.join(parts[3:-1])
            page = parts[-1]
            back_callback = f'{prefix}:{page}'
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
    async def cb_cloud_asset_detail(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        parts = callback.data.split(':')
        item_kind = parts[2]
        item_id = int(parts[3])
        back_callback = 'cloud:list'
        if len(parts) >= 7:
            prefix = ':'.join(parts[4:-1])
            page = parts[-1]
            back_callback = f'{prefix}:{page}'
        item = await get_user_proxy_asset_detail(item_id, user.id, item_kind)
        if not item:
            logger.warning('CLOUD_ASSET_DETAIL_DENIED user_id=%s item_id=%s callback_data=%s', user.id, item_id, callback.data)
            await _safe_callback_answer(callback, '代理记录不存在', show_alert=True)
            return
        has_link = bool(getattr(item, 'mtproxy_link', None) or getattr(item, 'proxy_links', None))
        order_id = getattr(item, 'order_id', None) if getattr(item, 'order_user_id', None) == user.id else None
        logger.info('CLOUD_ASSET_DETAIL_RENDER user_id=%s item_id=%s kind=%s ip=%s back=%s order_id=%s has_link=%s', user.id, item_id, getattr(item, '_proxy_item_kind', None), item.public_ip, back_callback, order_id, has_link)
        rows = [
            [InlineKeyboardButton(text='🔄 续费', callback_data=f'cloud:assetaction:renew:{item_id}'), InlineKeyboardButton(text='🌐 更换IP', callback_data=f'cloud:assetaction:changeip:{item_id}')],
            [InlineKeyboardButton(text='🛠 重新安装', callback_data=f'cloud:assetinit:{item_id}:{back_callback}'), InlineKeyboardButton(text='⬆️ 升级配置', callback_data=f'cloud:assetaction:upgrade:{item_id}')],
        ]
        rows.append([support_contact_button('cloud_asset', item_id)])
        rows.append([InlineKeyboardButton(text='🔙 返回代理列表', callback_data=back_callback)])
        await _safe_edit_text(callback.message, _cloud_asset_detail_text(item), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows), parse_mode='HTML')

    @dp.callback_query(F.data.startswith('cloud:assetaction:'))
    async def cb_cloud_asset_action(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        parts = callback.data.split(':')
        action = parts[2]
        asset_id = int(parts[3])
        item = await get_user_proxy_asset_detail(asset_id, user.id, 'asset')
        if not item:
            await _safe_callback_answer(callback, '代理记录不存在', show_alert=True)
            return
        order, err = await ensure_cloud_asset_operation_order(asset_id, user.id)
        if err:
            await _safe_callback_answer(callback, err, show_alert=True)
            return
        logger.info('CLOUD_ASSET_ACTION_START user_id=%s asset_id=%s order_id=%s action=%s ip=%s', user.id, asset_id, order.id, action, getattr(item, 'public_ip', None))
        if action == 'renew':
            try:
                renewal = await create_cloud_server_renewal_for_user(order.id, user.id, 31)
            except RenewalPriceMissingError as exc:
                await _safe_callback_answer(callback, str(exc), show_alert=True)
                return
            if renewal is False:
                await _safe_callback_answer(callback, '该服务器IP已删除，禁止续费', show_alert=True)
                return
            if not renewal:
                await _safe_callback_answer(callback, '续费订单创建失败', show_alert=True)
                return
            trx_amount = await usdt_to_trx(renewal.pay_amount)
            receive_address = _receive_address()
            auto_renew_enabled = await get_cloud_server_auto_renew(renewal.id, user.id)
            group_balance_lines = await get_cloud_order_group_balance_lines(renewal.id)
            balance_text = '\n'.join(['多用户余额：', *group_balance_lines]) if group_balance_lines else ''
            await _safe_edit_text(callback.message,
                '🔄 云服务器续费\n\n'
                f'订单号: {renewal.order_no}\n'
                '续费时长: 31天\n'
                f'续费价格: {fmt_pay_amount(renewal.pay_amount)} {renewal.currency}\n'
                f'自动续费: {"已开启" if auto_renew_enabled else "已关闭"}\n'
                f'收款地址: <code>{escape(receive_address)}</code>'
                f'{("\n\n" + balance_text) if balance_text else ""}\n\n'
                '可直接地址支付，或使用下方钱包续费与自动续费开关。',
                parse_mode='HTML',
                disable_web_page_preview=True,
                reply_markup=cloud_server_renew_payment(renewal.id, renewal.pay_amount, trx_amount, bool(auto_renew_enabled)),
            )
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
                        [InlineKeyboardButton(text='🔙 返回代理详情', callback_data=f'cloud:assetdetail:asset:{asset_id}:cloud:list:page:1')],
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
                        [InlineKeyboardButton(text='🔄 去续费', callback_data=f'cloud:assetaction:renew:{asset_id}')],
                        [support_contact_button('cloud_asset_changeip_quota', asset_id)],
                        [InlineKeyboardButton(text='🔙 返回代理详情', callback_data=f'cloud:assetdetail:asset:{asset_id}:cloud:list:page:1')],
                    ]),
                )
                return
            regions = [(code, name) for code, name in await _get_cached_custom_regions() if code != 'cn-hongkong']
            text = '🌐 更换IP\n\n请选择新的地区：'
            markup = cloud_server_change_ip_region_menu(order.id, regions, expanded=False)
            edited = await _safe_edit_text(callback.message, text, reply_markup=markup)
            if edited is None:
                sent = await callback.message.reply(text, reply_markup=markup)
                logger.info('BOT_MESSAGE_SEND route=asset_change_ip_regions_fallback user_id=%s asset_id=%s order_id=%s chat_id=%s reply_to=%s sent_message_id=%s regions=%s', user.id, asset_id, order.id, callback.message.chat.id, callback.message.message_id, getattr(sent, 'message_id', None), regions)
            else:
                logger.info('BOT_MESSAGE_EDIT route=asset_change_ip_regions user_id=%s asset_id=%s order_id=%s chat_id=%s message_id=%s regions=%s', user.id, asset_id, order.id, callback.message.chat.id, callback.message.message_id, regions)
            return
        if action == 'upgrade':
            plans, err = await list_cloud_server_upgrade_plans(order.id, user.id)
            if err:
                await _safe_callback_answer(callback, err, show_alert=True)
                return
            if not plans:
                await _safe_callback_answer(callback, '暂无可升级配置', show_alert=True)
                return
            rows = []
            text_lines = ['⬆️ 升级配置', '', '请选择目标配置，系统会从 USDT 余额扣除差价，并创建更高规格服务器；主/备用代理链接保持不变。']
            for plan in plans[:10]:
                text_lines.append(f"- {plan['name']}：补 {plan['diff']} U，到期补足 {plan['target_days']} 天")
                rows.append([InlineKeyboardButton(text=f"{plan['name']} +{plan['diff']}U", callback_data=f"cloud:upgradepay:{order.id}:{plan['id']}")])
            rows.append([InlineKeyboardButton(text='🔙 返回详情', callback_data=f'cloud:assetdetail:asset:{asset_id}:cloud:list:page:1')])
            await _safe_edit_text(callback.message, '\n'.join(text_lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
            return
        await _safe_callback_answer(callback, '未知操作', show_alert=True)

    @dp.callback_query(F.data.startswith('cloud:assetinit:'))
    async def cb_cloud_asset_init(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        parts = callback.data.split(':')
        asset_id = int(parts[2])
        item = await get_user_proxy_asset_detail(asset_id, user.id, 'asset')
        if not item:
            await _safe_callback_answer(callback, '代理记录不存在', show_alert=True)
            return
        await state.update_data(reinstall_asset_id=asset_id, reinstall_order_id=0)
        await state.set_state(CustomServerStates.waiting_reinstall_link)
        logger.info('CLOUD_ASSET_REINSTALL_LINK_WAIT user_id=%s asset_id=%s ip=%s', user.id, asset_id, getattr(item, 'public_ip', None))
        await callback.message.reply(_bot_text('bot_reinstall_need_main_link', '当前服务器缺少主代理链接。请直接发送这台服务器的主代理链接，我会先校验 IP、端口和服务器实际密钥，再让你确认是否重新安装。'))

    @dp.callback_query(F.data.startswith('cloud:assetreinitconfirm:'))
    async def cb_cloud_asset_reinit_confirm(callback: CallbackQuery, bot: Bot, state: FSMContext):
        parts = callback.data.split(':')
        asset_id = int(parts[2])
        token = parts[3] if len(parts) > 3 else ''
        if not await _consume_reinstall_confirm_token(state, kind='asset', item_id=asset_id, token=token):
            await _safe_callback_answer(callback, '这个确认按钮已过期或已使用，请重新进入详情并重新生成按钮。', show_alert=True)
            return
        await _safe_callback_answer(callback, '已确认，后台处理中')
        await _safe_remove_inline_keyboard(callback.message)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        item = await get_user_proxy_asset_detail(asset_id, user.id, 'asset')
        if not item:
            await callback.message.reply('代理记录不存在，请重新进入详情并重新生成按钮。')
            return
        if asset_id in _ASSET_REINIT_INFLIGHT:
            logger.info('CLOUD_ASSET_REINIT_DUPLICATE user_id=%s asset_id=%s ip=%s', user.id, asset_id, getattr(item, 'public_ip', None))
            await callback.message.reply('这台代理正在重新安装，请勿重复点击；如需再次操作，请等待当前任务结束后重新生成按钮。')
            return
        order, err = await ensure_cloud_asset_operation_order(asset_id, user.id)
        if err or not order:
            logger.warning('CLOUD_ASSET_REINIT_DENIED user_id=%s asset_id=%s reason=%s', user.id, asset_id, err or '无法创建代理操作订单')
            await callback.message.reply(err or '无法创建代理操作订单，请重新进入详情并重新生成按钮。')
            return
        rebuild_order = await mark_cloud_server_reinit_requested(order.id, user.id)
        if not rebuild_order:
            logger.warning('CLOUD_ASSET_REINIT_DENIED user_id=%s asset_id=%s order_id=%s reason=missing_order', user.id, asset_id, order.id)
            await callback.message.reply('服务器记录不存在，请重新进入详情并重新生成按钮。')
            return
        if isinstance(rebuild_order, str):
            logger.warning('CLOUD_ASSET_REINIT_DENIED user_id=%s asset_id=%s order_id=%s reason=%s', user.id, asset_id, order.id, rebuild_order)
            await callback.message.reply(f'{rebuild_order}\n请重新进入详情并重新生成按钮。')
            return
        _ASSET_REINIT_INFLIGHT.add(asset_id)
        logger.info('CLOUD_ASSET_REINIT_SUBMIT user_id=%s asset_id=%s order_id=%s rebuild_order_id=%s ip=%s', user.id, asset_id, order.id, rebuild_order.id, getattr(item, 'public_ip', None))
        await callback.message.reply('🛠 已确认重建服务器：后台会新建干净服务器并安装代理，成功后迁移固定 IP，旧机保留 3 天。预计约 5 分钟，完成后会自动通知你。\n\n后台处理期间，底部菜单和其它按钮可正常使用。', reply_markup=main_menu())
        task = asyncio.create_task(_provision_cloud_server_and_notify(bot, callback.from_user.id, rebuild_order.id, rebuild_order.mtproxy_port or 9528, retry_only=False))
        task.add_done_callback(lambda _task, _asset_id=asset_id: _ASSET_REINIT_INFLIGHT.discard(_asset_id))

    @dp.callback_query(F.data.startswith('cloud:detail:'))
    async def cb_cloud_detail(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        parts = callback.data.split(':')
        order_id = int(parts[2])
        back_callback = 'cloud:list'
        if len(parts) >= 6:
            prefix = ':'.join(parts[3:-1])
            page = parts[-1]
            back_callback = f'{prefix}:{page}'
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
        can_renew = bool(order.public_ip and order.status in {'completed', 'expiring', 'suspended', 'renew_pending', 'provisioning', 'paid'})
        can_change_ip = bool(order.provider == 'aws_lightsail' and order.status in {'completed', 'expiring', 'suspended'} and max(int(getattr(order, 'ip_change_quota', 0) or 0), 0) > 0)
        can_resume_init = bool(order.status in {'paid', 'provisioning', 'failed'} and (order.public_ip or not order.mtproxy_secret or not order.mtproxy_link or not order.login_password))
        can_reinit = bool(order.public_ip and order.login_password and order.status == 'completed')
        can_upgrade = bool(order.provider == 'aws_lightsail' and order.status in {'completed', 'expiring', 'suspended'})
        can_refund = _cloud_can_refund(order, now)
        expires_at = getattr(order, 'service_expires_at', None)
        delay_quota = max(int(getattr(order, 'delay_quota', 0) or 0), 0)
        can_delay = bool(
            can_renew
            and expires_at
            and expires_at >= now
            and expires_at <= now + timezone.timedelta(days=5)
            and delay_quota > 0
        )
        logger.info(
            'CLOUD_DETAIL_RENDER user_id=%s order_id=%s order_no=%s status=%s provider=%s public_ip=%s login_password=%s mtproxy_secret=%s mtproxy_link=%s buttons={renew:%s,delay:%s,change_ip:%s,resume_init:%s,reinit:%s,upgrade:%s,refund:%s} back=%s',
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
            can_delay,
            can_change_ip,
            can_resume_init,
            can_reinit,
            can_upgrade,
            can_refund,
            back_callback,
        )
        await _safe_edit_text(
            callback.message,
            _cloud_server_detail_text(order),
            reply_markup=cloud_server_detail(order.id, can_renew, can_change_ip, can_reinit, can_delay, back_callback, can_upgrade, can_refund, can_resume_init),
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

    @dp.callback_query(F.data.startswith('cloud:delay:'))
    async def cb_cloud_delay(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, raw_order_id, raw_days = callback.data.split(':')
        order, err = await delay_cloud_server_expiry(int(raw_order_id), user.id, int(raw_days))
        if err:
            await _safe_callback_answer(callback, err, show_alert=True)
            return
        if not order:
            await _safe_callback_answer(callback, '延期失败', show_alert=True)
            return
        await callback.message.reply(_bot_text_format('bot_cloud_extend_success', '🕒 已为订单 {order_no} 延期 {days} 天，系统将自动顺延删机前宽限时间。', order_no=order.order_no, days=raw_days))

    @dp.callback_query(F.data.startswith('cloud:renew:'))
    async def cb_cloud_renew(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(callback.data.split(':')[2])
        try:
            order = await create_cloud_server_renewal_for_user(order_id, user.id, 31)
            if not order:
                order = await create_cloud_server_renewal_by_public_query(order_id, 31)
        except RenewalPriceMissingError as exc:
            await _safe_callback_answer(callback, str(exc), show_alert=True)
            return
        if order is False:
            await _safe_callback_answer(callback, '该服务器IP已删除，禁止续费', show_alert=True)
            return
        if not order:
            await _safe_callback_answer(callback, '续费订单创建失败', show_alert=True)
            return
        trx_amount = await usdt_to_trx(order.pay_amount)
        receive_address = _receive_address()
        auto_renew_enabled = await get_cloud_server_auto_renew(order.id, getattr(order, 'user_id', user.id))
        group_balance_lines = await get_cloud_order_group_balance_lines(order.id)
        balance_text = '\n'.join(['多用户余额：', *group_balance_lines]) if group_balance_lines else ''
        await _safe_edit_text(callback.message, 
            '🔄 云服务器续费\n\n'
            f'订单号: {order.order_no}\n'
            '续费时长: 31天\n'
            f'续费价格: {fmt_pay_amount(order.pay_amount)} {order.currency}\n'
            f'自动续费: {"已开启" if auto_renew_enabled else "已关闭"}\n'
            f'收款地址: <code>{escape(receive_address)}</code>'
            f'{("\n\n" + balance_text) if balance_text else ""}\n\n'
            '可直接地址支付，或使用下方钱包续费与自动续费开关。',
            parse_mode='HTML',
            disable_web_page_preview=True,
            reply_markup=cloud_server_renew_payment(order.id, order.pay_amount, trx_amount, bool(auto_renew_enabled)),
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
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, action, order_id_text = callback.data.split(':')
        order_id = int(order_id_text)
        enabled = action == 'on'
        order = await set_cloud_server_auto_renew(order_id, user.id, enabled)
        if order is False:
            await _safe_callback_answer(callback, '该服务器IP已删除，禁止续费', show_alert=True)
            return
        if not order:
            await _safe_callback_answer(callback, '服务器记录不存在', show_alert=True)
            return
        trx_amount = await usdt_to_trx(order.pay_amount or order.total_amount)
        receive_address = _receive_address()
        await _safe_edit_text(callback.message, 
            '🔄 云服务器续费\n\n'
            f'订单号: {order.order_no}\n'
            '续费时长: 31天\n'
            f'续费价格: {fmt_pay_amount(order.pay_amount or order.total_amount)} {order.currency}\n'
            f'自动续费: {"已开启" if enabled else "已关闭"}\n'
            f'收款地址: <code>{escape(receive_address)}</code>\n\n'
            '可直接地址支付，或使用下方钱包续费与自动续费开关。',
            parse_mode='HTML',
            disable_web_page_preview=True,
            reply_markup=cloud_server_renew_payment(order.id, order.pay_amount or order.total_amount, trx_amount, enabled),
        )

    @dp.callback_query(F.data.startswith('cloud:renewwallet:'))
    async def cb_cloud_renew_wallet(callback: CallbackQuery):
        await _safe_callback_answer(callback, '钱包自动续费处理中')
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(callback.data.split(':')[2])
        order, err = await pay_cloud_server_renewal_with_balance(order_id, user.id, 'USDT', 31)
        if err:
            existing = await get_cloud_order(order_id, user.id)
            if existing and existing.status == 'completed' and existing.paid_at:
                asyncio.create_task(_cloud_renewal_postcheck_and_notify(callback.bot, callback.from_user.id, existing.id))
                await _safe_edit_text(callback.message, f'✅ 这笔续费已完成。\n\n订单号: {existing.order_no}\n{_cloud_order_plan_text(existing)}\n\n我会继续执行续费后巡检。')
                return
            await _safe_edit_text(callback.message, 
                f'❌ 钱包自动续费失败：{_public_cloud_error_text(err)}。\n请先充值余额后再试，或使用下方地址支付。',
                reply_markup=wallet_recharge_prompt_menu(),
            )
            return
        if getattr(order, 'replacement_for_id', None) and order.status in {'paid', 'provisioning', 'failed'}:
            await _safe_edit_text(callback.message, '✅ 云服务器钱包续费成功，正在自动恢复固定 IP 服务器。\n\n系统会保持旧 IP / 旧端口 / 旧密钥不变，完成后自动发送代理链接。')
            asyncio.create_task(_provision_cloud_server_and_notify(callback.bot, callback.from_user.id, order.id, order.mtproxy_port or 9528))
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
                can_delay=bool(
                    order.public_ip
                    and getattr(order, 'service_expires_at', None)
                    and getattr(order, 'service_expires_at', None) >= timezone.now()
                    and getattr(order, 'service_expires_at', None) <= timezone.now() + timezone.timedelta(days=5)
                    and max(int(getattr(order, 'delay_quota', 0) or 0), 0) > 0
                ),
                back_callback='cloud:list',
                can_upgrade=bool(order.provider == 'aws_lightsail' and order.status in {"completed", "expiring", "suspended"}),
                can_refund=_cloud_can_refund(order),
                can_resume_init=bool(order.status in {"paid", "provisioning", "failed"} and (order.public_ip or not order.mtproxy_secret or not order.mtproxy_link or not order.login_password)),
            ),
        )

    @dp.callback_query(F.data.startswith('cloud:renewpay:'))
    async def cb_cloud_renew_pay(callback: CallbackQuery):
        await _safe_callback_answer(callback, '续费钱包支付处理中')
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, order_id_text, currency = callback.data.split(':')
        order_id = int(order_id_text)
        order, err = await pay_cloud_server_renewal_with_balance(order_id, user.id, currency, 31)
        if err:
            existing = await get_cloud_order(order_id, user.id)
            if existing and existing.status == 'completed' and existing.paid_at:
                asyncio.create_task(_cloud_renewal_postcheck_and_notify(callback.bot, callback.from_user.id, existing.id))
                await _safe_edit_text(callback.message, f'✅ 这笔续费已完成。\n\n订单号: {existing.order_no}\n{_cloud_order_plan_text(existing)}\n\n我会继续执行续费后巡检。')
                return
            await _safe_edit_text(callback.message, f'❌ {_public_cloud_error_text(err)}。', reply_markup=wallet_recharge_prompt_menu())
            return
        if getattr(order, 'replacement_for_id', None) and order.status in {'paid', 'provisioning', 'failed'}:
            await _safe_edit_text(callback.message, '✅ 云服务器续费成功，正在自动恢复固定 IP 服务器。\n\n系统会保持旧 IP / 旧端口 / 旧密钥不变，完成后自动发送代理链接。')
            asyncio.create_task(_provision_cloud_server_and_notify(callback.bot, callback.from_user.id, order.id, order.mtproxy_port or 9528))
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
                can_delay=bool(
                    order.public_ip
                    and getattr(order, 'service_expires_at', None)
                    and getattr(order, 'service_expires_at', None) >= timezone.now()
                    and getattr(order, 'service_expires_at', None) <= timezone.now() + timezone.timedelta(days=5)
                    and max(int(getattr(order, 'delay_quota', 0) or 0), 0) > 0
                ),
                back_callback='cloud:list',
                can_upgrade=bool(order.provider == 'aws_lightsail' and order.status in {"completed", "expiring", "suspended"}),
                can_refund=_cloud_can_refund(order),
                can_resume_init=bool(order.status in {"paid", "provisioning", "failed"} and (order.public_ip or not order.mtproxy_secret or not order.mtproxy_link or not order.login_password)),
            ),
        )

    @dp.callback_query(F.data.startswith('cloud:ip:'))
    async def cb_cloud_change_ip(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(callback.data.split(':')[2])
        order = await get_user_cloud_server(order_id, user.id)
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
            reply_markup=cloud_server_change_ip_region_menu(order.id, regions, expanded=False),
        )

    @dp.callback_query(F.data.startswith('cloud:ipregions:more:'))
    async def cb_cloud_change_ip_regions_more(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        order_id = int(callback.data.split(':')[3])
        regions = [(code, name) for code, name in await _get_cached_custom_regions() if code != 'cn-hongkong']
        await _safe_edit_text(callback.message, 
            '🌐 更换IP\n\n请选择新的地区：',
            reply_markup=cloud_server_change_ip_region_menu(order_id, regions, expanded=True),
        )

    @dp.callback_query(F.data.startswith('cloud:ipregion:'))
    async def cb_cloud_change_ip_region(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        _, _, raw_order_id, region_code = callback.data.split(':')
        order_id = int(raw_order_id)
        if region_code == 'cn-hongkong':
            await _safe_callback_answer(callback, '当前节点暂不支持更换 IP', show_alert=True)
            return
        regions = [(code, name) for code, name in await _get_cached_custom_regions() if code != 'cn-hongkong']
        region_name = next((name for code, name in regions if code == region_code), region_code)
        await state.update_data(cloud_ip_change_order_id=order_id, cloud_ip_change_region_code=region_code, cloud_ip_change_region_name=region_name)
        await _safe_edit_text(callback.message, 
            f'🌐 更换IP\n\n已选择节点：{_public_region_text(region_name) or "默认节点"}\n请选择端口：',
            reply_markup=cloud_server_change_ip_port_keyboard(order_id, region_code, region_name),
        )

    @dp.callback_query(F.data.startswith('cloud:ipport:default:'))
    async def cb_cloud_change_ip_port_default(callback: CallbackQuery, state: FSMContext, bot: Bot):
        await _safe_callback_answer(callback, '已选择默认端口 9528，正在创建同配置新服务器')
        _, _, _, raw_order_id, region_code = callback.data.split(':')
        order_id = int(raw_order_id)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        new_order = await mark_cloud_server_ip_change_requested(order_id, user.id, region_code, 9528)
        await state.clear()
        if new_order is False:
            await _safe_callback_answer(callback, '当前状态不可更换 IP', show_alert=True)
            return
        if not new_order:
            await _safe_callback_answer(callback, '创建更换 IP 新服务器失败', show_alert=True)
            return
        await callback.message.reply(
            f'🌐 已为你创建同配置换 IP 新服务器\n新订单号: {new_order.order_no}\n新节点: {_public_region_text(new_order.region_name) or "默认节点"}\n新端口: {new_order.mtproxy_port or 9528}\n系统会绑定新的固定 IP，请在 5 天内切换使用。\n\n后台创建期间，底部菜单和其它按钮可正常使用。',
            reply_markup=main_menu(),
        )
        asyncio.create_task(_provision_cloud_server_and_notify(bot, callback.from_user.id, new_order.id, new_order.mtproxy_port or 9528))

    @dp.callback_query(F.data.startswith('cloud:ipport:custom:'))
    async def cb_cloud_change_ip_port_custom(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback, '已选择自定义端口')
        _, _, _, raw_order_id, region_code = callback.data.split(':')
        order_id = int(raw_order_id)
        if region_code == 'cn-hongkong':
            await _safe_callback_answer(callback, '当前节点暂不支持更换 IP', show_alert=True)
            return
        regions = [(code, name) for code, name in await _get_cached_custom_regions() if code != 'cn-hongkong']
        region_name = next((name for code, name in regions if code == region_code), region_code)
        await state.update_data(cloud_ip_change_order_id=order_id, cloud_ip_change_region_code=region_code, cloud_ip_change_region_name=region_name)
        await state.set_state(CustomServerStates.waiting_port)
        await callback.message.reply(
            f'✍️ 已选择更换IP自定义端口。\n节点：{_public_region_text(region_name) or "默认节点"}\n请发送 443 或 1025-65530 之间的端口号。'
        )


    @dp.callback_query(F.data.startswith('cloud:upgrade:'))
    async def cb_cloud_upgrade(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(callback.data.split(':')[2])
        plans, err = await list_cloud_server_upgrade_plans(order_id, user.id)
        if err:
            await _safe_callback_answer(callback, err, show_alert=True)
            return
        if not plans:
            await _safe_callback_answer(callback, '暂无可升级配置', show_alert=True)
            return
        rows = []
        text_lines = ['⬆️ 升级配置', '', '请选择目标配置，系统会从 USDT 余额扣除差价，并创建更高规格服务器；主/备用代理链接保持不变。']
        for plan in plans[:10]:
            text_lines.append(f"- {plan['name']}：补 {plan['diff']} U，到期补足 {plan['target_days']} 天")
            rows.append([InlineKeyboardButton(text=f"{plan['name']} +{plan['diff']}U", callback_data=f"cloud:upgradepay:{order_id}:{plan['id']}")])
        rows.append([InlineKeyboardButton(text='🔙 返回详情', callback_data=f'cloud:detail:{order_id}')])
        await _safe_edit_text(callback.message, '\n'.join(text_lines), reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @dp.callback_query(F.data.startswith('cloud:upgradepay:'))
    async def cb_cloud_upgrade_pay(callback: CallbackQuery, bot: Bot, state: FSMContext):
        await _safe_callback_answer(callback)
        await state.clear()
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, raw_order_id, raw_plan_id = callback.data.split(':')
        new_order, err = await create_cloud_server_upgrade_order(int(raw_order_id), user.id, int(raw_plan_id))
        if err:
            await _safe_callback_answer(callback, err, show_alert=True)
            return
        await callback.message.reply(_bot_text_format('bot_cloud_upgrade_submitted', '⬆️ 已扣除升级差价并提交升级任务。\n新订单: {order_no}\n升级完成后会自动发送新的服务器信息，代理链接保持不变。\n\n后台升级期间，底部菜单和其它按钮可正常使用。', order_no=new_order.order_no), reply_markup=main_menu())
        asyncio.create_task(_provision_cloud_server_and_notify(bot, callback.from_user.id, new_order.id, new_order.mtproxy_port or 9528))

    @dp.callback_query(F.data.startswith('cloud:refund:'))
    async def cb_cloud_refund(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        order_id = int(callback.data.split(':')[2])
        logger.info('CLOUD_REFUND_CONFIRM_PAGE user_id=%s order_id=%s callback_data=%s', getattr(callback.from_user, 'id', None), order_id, callback.data)
        rows = [[InlineKeyboardButton(text='确认退款并改为3天后到期', callback_data=f'cloud:refundyes:{order_id}')], [InlineKeyboardButton(text='🔙 返回详情', callback_data=f'cloud:detail:{order_id}')]]
        await _safe_edit_text(callback.message, '💸 退款确认\n\n到期时间少于 10 天的订单禁止退款。确认后会把退款金额退回余额，并把服务到期时间改为 3 天后；订单状态不变，后续仍可续费。确认继续？', reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))

    @dp.callback_query(F.data.startswith('cloud:refundyes:'))
    async def cb_cloud_refund_yes(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(callback.data.split(':')[2])
        logger.info('CLOUD_REFUND_EXEC_START user_id=%s order_id=%s callback_data=%s', user.id, order_id, callback.data)
        result, err = await refund_cloud_server_to_balance(order_id, user.id)
        if err:
            logger.warning('CLOUD_REFUND_EXEC_DENIED user_id=%s order_id=%s reason=%s', user.id, order_id, err)
            await _safe_callback_answer(callback, err, show_alert=True)
            return
        logger.info('CLOUD_REFUND_EXEC_OK user_id=%s order_id=%s amount=%s currency=%s', user.id, order_id, result['amount'], result['currency'])
        await _safe_edit_text(callback.message, f"✅ 已退款 {fmt_amount(result['amount'])} {result['currency']} 至余额，服务到期时间已改为 3 天后，订单状态保持不变。", reply_markup=main_menu())

    @dp.callback_query(F.data.startswith('cloud:reinit:'))
    async def cb_cloud_reinit(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(callback.data.split(':')[2])
        order = await get_user_cloud_server(order_id, user.id)
        if not order:
            await _safe_callback_answer(callback, '服务器记录不存在', show_alert=True)
            return
        is_unfinished = order.status in {'paid', 'provisioning', 'failed'}
        if not is_unfinished and (not order.public_ip or not order.login_password):
            logger.warning('CLOUD_REINIT_DENIED user_id=%s order_id=%s order_no=%s status=%s public_ip=%s login_password=%s reason=missing_bootstrap_info', user.id, order.id, order.order_no, order.status, order.public_ip, bool(order.login_password))
            await _safe_callback_answer(callback, '当前服务器缺少公网 IP 或登录密码，暂时无法重新安装；请先在后台补齐实例登录信息', show_alert=True)
            return
        has_main_link = bool(getattr(order, 'mtproxy_link', None) or any(isinstance(item, dict) and item.get('url') and str(item.get('port') or '') == str(order.mtproxy_port or 9528) for item in (getattr(order, 'proxy_links', None) or [])))
        if not is_unfinished and not has_main_link:
            await state.update_data(reinstall_order_id=order.id)
            await state.set_state(CustomServerStates.waiting_reinstall_link)
            await callback.message.reply(_bot_text('bot_reinstall_need_main_link', '当前服务器缺少主代理链接。请直接发送这台服务器的主代理链接，我会先校验 IP、端口和服务器实际密钥，再让你确认是否重新安装。'))
            return
        if is_unfinished:
            token = await _issue_reinstall_confirm_token(state, kind='order', item_id=order.id)
            await callback.message.reply(_bot_text('bot_resume_init_confirm', '⚠️ 确认继续初始化？\n\n系统会重新执行 BBR/MTProxy 安装并生成代理链接。'), reply_markup=_reinstall_confirm_keyboard(order.id, token))
            return
        token = await _issue_reinstall_confirm_token(state, kind='order', item_id=order.id)
        await callback.message.reply(_bot_text('bot_reinstall_confirm', '⚠️ 确认重新安装？\n\n重新安装大约需要 5 分钟，期间代理可能会断连。系统会保持主/备用链接不变。'), reply_markup=_reinstall_confirm_keyboard(order.id, token))

    @dp.message(CustomServerStates.waiting_reinstall_link)
    async def msg_cloud_reinstall_link(message: Message, state: FSMContext):
        if await _handle_menu_interrupt(message, state):
            return
        data = await state.get_data()
        order_id = int(data.get('reinstall_order_id') or 0)
        asset_id = int(data.get('reinstall_asset_id') or 0)
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        item = await get_user_proxy_asset_detail(asset_id, user.id, 'asset') if asset_id else await get_user_cloud_server(order_id, user.id)
        if not item:
            await state.clear()
            await message.reply(_bot_text('bot_reinstall_missing_order', '服务器记录不存在，请重新进入云服务器详情。'))
            return
        link_data = _parse_proxy_link(message.text or '')
        if not link_data:
            await message.reply(_bot_text('bot_reinstall_invalid_link', '链接格式不对，请发送 tg://proxy?... 或 https://t.me/proxy?... 主代理链接。'))
            return
        ok, reason = await _validate_reinstall_proxy_link(item, link_data, probe_when_possible=bool(getattr(item, 'login_password', None)))
        if not ok:
            await message.reply(_bot_text_format('bot_reinstall_validate_failed', '校验失败：{reason}', reason=reason))
            return
        if asset_id:
            saved = await _save_asset_main_proxy_link(asset_id, user.id, link_data)
            token = await _issue_reinstall_confirm_token(state, kind='asset', item_id=saved.id)
            await message.reply(_bot_text('bot_reinstall_validate_ok', '主代理链接校验通过。\n\n⚠️ 确认重新安装？重新安装大约需要 5 分钟，期间代理可能会断连。'), reply_markup=_asset_reinstall_confirm_keyboard(saved.id, token))
            return
        saved = await _save_user_main_proxy_link(item.id, link_data)
        token = await _issue_reinstall_confirm_token(state, kind='order', item_id=saved.id)
        await message.reply(_bot_text('bot_reinstall_validate_ok', '主代理链接校验通过。\n\n⚠️ 确认重新安装？重新安装大约需要 5 分钟，期间代理可能会断连。'), reply_markup=_reinstall_confirm_keyboard(saved.id, token))

    @dp.callback_query(F.data.startswith('cloud:reinitconfirm:'))
    async def cb_cloud_reinit_confirm(callback: CallbackQuery, bot: Bot, state: FSMContext):
        parts = callback.data.split(':')
        order_id = int(parts[2])
        token = parts[3] if len(parts) > 3 else ''
        if not await _consume_reinstall_confirm_token(state, kind='order', item_id=order_id, token=token):
            await _safe_callback_answer(callback, '这个确认按钮已过期或已使用，请重新进入详情并重新生成按钮。', show_alert=True)
            return
        await _safe_callback_answer(callback, '已确认，后台处理中')
        await _safe_remove_inline_keyboard(callback.message)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order = await mark_cloud_server_reinit_requested(order_id, user.id)
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
        asyncio.create_task(_provision_cloud_server_and_notify(bot, callback.from_user.id, order.id, order.mtproxy_port or 9528, retry_only=retry_only))

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
        icon = '🟢' if mon.is_active else '🔴'
        await _safe_edit_text(callback.message, 
            f'{icon} 监控详情\n监控地址: <code>{mon.address}</code>\n备注: {mon.remark or "无"}\n'
            f'💸 监控转账: {"开启" if mon.monitor_transfers else "关闭"}\n'
            f'⚡ 监控资源: {"开启" if mon.monitor_resources else "关闭"}\n'
            f'USDT 阈值: {fmt_amount(mon.usdt_threshold)}\nTRX 阈值: {fmt_amount(mon.trx_threshold)}\n'
            f'能量增加阈值: {int(mon.energy_threshold or 0)}\n带宽增加阈值: {int(mon.bandwidth_threshold or 0)}\n\n'
            f'📘 使用说明:\n'
            f'1. 监控转账：地址收到 USDT/TRX 转账时通知。\n'
            f'2. 监控资源：地址可用能量/带宽增加时通知；正常转账消耗不通知。',
            reply_markup=kb_monitor_detail(mon.id, mon.monitor_transfers, mon.monitor_resources),
            parse_mode='HTML',
        )
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:toggle:'))
    async def cb_mon_toggle(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, monitor_id, mode = callback.data.split(':')
        field = 'monitor_transfers' if mode == 'transfers' else 'monitor_resources'
        monitor = await toggle_monitor_flag(int(monitor_id), user.id, field)
        if not monitor:
            await _safe_callback_answer(callback, _bot_text('bot_monitor_missing', '监控不存在'), show_alert=True)
            return
        from cloud.cache import update_monitor_flag_in_cache
        await update_monitor_flag_in_cache(monitor.address, field, getattr(monitor, field))
        await _safe_edit_text(callback.message, 
            f'{"🟢" if monitor.is_active else "🔴"} 监控详情\n监控地址: <code>{monitor.address}</code>\n备注: {monitor.remark or "无"}\n'
            f'💸 监控转账: {"开启" if monitor.monitor_transfers else "关闭"}\n'
            f'⚡ 监控资源: {"开启" if monitor.monitor_resources else "关闭"}\n'
            f'USDT 阈值: {fmt_amount(monitor.usdt_threshold)}\nTRX 阈值: {fmt_amount(monitor.trx_threshold)}\n'
            f'能量增加阈值: {int(monitor.energy_threshold or 0)}\n带宽增加阈值: {int(monitor.bandwidth_threshold or 0)}\n\n'
            f'📘 使用说明:\n'
            f'1. 监控转账：地址收到 USDT/TRX 转账时通知。\n'
            f'2. 监控资源：地址可用能量/带宽增加时通知；正常转账消耗不通知。',
            reply_markup=kb_monitor_detail(monitor.id, monitor.monitor_transfers, monitor.monitor_resources),
            parse_mode='HTML',
        )
        await _safe_callback_answer(callback, _bot_text('bot_monitor_updated', '已更新'))

    @dp.callback_query(F.data.startswith('mon:threshold:'))
    async def cb_mon_threshold(callback: CallbackQuery):
        mid = int(callback.data.split(':')[2])
        await _safe_edit_text(callback.message, _bot_text('bot_monitor_threshold_currency_prompt', '请选择要修改的阈值币种：'), reply_markup=monitor_threshold_currency(mid))
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:setthr:'))
    async def cb_mon_setthr(callback: CallbackQuery, state: FSMContext):
        _, _, mid, currency = callback.data.split(':')
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
            await remove_monitor_from_cache(mon.address)
        await delete_monitor(mid, user.id)
        await _safe_edit_text(callback.message, _bot_text('bot_monitor_deleted', '🗑 监控已删除。'), reply_markup=monitor_menu())
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'mon:back')
    async def cb_mon_back(callback: CallbackQuery):
        await _safe_edit_text(callback.message, _bot_text('bot_monitor_entry', '🔍 地址监控'), reply_markup=monitor_menu())
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:txd:'))
    async def cb_tx_detail(callback: CallbackQuery):
        from orders.runtime import get_tx_detail
        detail_key = callback.data.split(':')[2]
        detail = get_tx_detail(detail_key)
        if not detail:
            await _safe_callback_answer(callback, _bot_text('bot_tx_detail_expired', '交易详情已过期'), show_alert=True)
            return
        text = (
            f'🔍 交易详情\n\n'
            f'类型: {"收入" if detail.get("direction") == "income" else "支出"}\n'
            f'交易哈希: <code>{detail["tx_hash"]}</code>\n'
            f'币种: {detail["currency"]}\n'
            f'金额: {detail["amount"]} {detail["currency"]}\n'
            f'付款地址: <code>{detail["from"]}</code>\n'
            f'收款地址: <code>{detail["to"]}</code>\n'
            f'时间: {detail["time"]}\n'
        )
        if detail.get("remark"):
            text += f'备注: {detail["remark"]}\n'
        if detail.get("fee_text"):
            text += f'手续费: {detail["fee_text"]}\n'
        await _safe_edit_text(callback.message, text, parse_mode='HTML', disable_web_page_preview=True)
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:resd:'))
    async def cb_resource_detail(callback: CallbackQuery):
        from orders.runtime import get_resource_detail
        detail_key = callback.data.split(':')[2]
        detail = get_resource_detail(detail_key)
        if not detail:
            await _safe_callback_answer(callback, _bot_text('bot_resource_detail_expired', '资源详情已过期'), show_alert=True)
            return
        text = (
            f'⚡ 资源详情\n\n'
            f'地址备注: {detail["remark"]}\n'
            f'监控地址: <code>{detail["address"]}</code>\n'
            f'检测时间: <code>{detail["time"]}</code>\n'
            f'可用能量增加: <code>+{detail["energy_increase"]}</code>（阈值 {detail.get("energy_threshold", 1)}）\n'
            f'可用带宽增加: <code>+{detail["bandwidth_increase"]}</code>（阈值 {detail.get("bandwidth_threshold", 1)}）\n'
            f'当前可用能量: <code>{detail["energy"]}</code>\n'
            f'当前可用带宽: <code>{detail["bandwidth"]}</code>'
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
    storage = await create_fsm_storage()
    dp = Dispatcher(storage=storage)
    dp.message.middleware(RawUserLoggingMiddleware())
    dp.callback_query.middleware(RawUserLoggingMiddleware())
    register_handlers(dp)
    return bot, dp
