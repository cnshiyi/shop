import re
from datetime import datetime as dt_datetime
from decimal import Decimal
from html import escape

import httpx

from asgiref.sync import sync_to_async
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message
from django.utils import timezone

from bot.cloud_texts import _tronscan_address_url, _tronscan_transfers_url, _tronscan_tx_url
from core.formatters import fmt_amount
from core.runtime_config import get_runtime_config
from core.trongrid import build_trongrid_headers

TRONGRID_BASE_URL = 'https://api.trongrid.io'
USDT_CONTRACT = 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'

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
    base_url = await sync_to_async(get_runtime_config, thread_sensitive=False)('trongrid_base_url', TRONGRID_BASE_URL)
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


