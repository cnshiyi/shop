import logging
import hashlib
from datetime import datetime
from html import escape
from collections import OrderedDict

import httpx
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from asgiref.sync import sync_to_async
from django.apps import apps
from django.utils import timezone

from core.persistence import record_external_sync_log, save_resource_snapshot
from core.runtime_config import get_runtime_config
from core.trongrid import build_trongrid_headers
from cloud.cache import get_monitor_addresses

logger = logging.getLogger(__name__)

TRONGRID_BASE_URL = 'https://api.trongrid.io'
_bot: Bot | None = None
_recent_resource_details: OrderedDict[str, dict] = OrderedDict()
_recent_resource_keys: OrderedDict[str, str] = OrderedDict()
MAX_RESOURCE_DETAIL_CACHE = 300


def set_bot(bot: Bot):
    global _bot
    _bot = bot


def get_resource_detail(detail_key: str) -> dict | None:
    real_key = _recent_resource_keys.get(detail_key, detail_key)
    return _recent_resource_details.get(real_key)


def _cache_resource_detail(detail_id: str, detail: dict):
    user_id = str(detail.get('user_id') or '').strip()
    cache_key = f'{detail_id}:{user_id}' if user_id else detail_id
    short_key = hashlib.sha1(cache_key.encode('utf-8')).hexdigest()[:16] if user_id else detail_id[:16]
    _recent_resource_details[cache_key] = detail
    _recent_resource_keys[short_key] = cache_key
    if len(_recent_resource_details) > MAX_RESOURCE_DETAIL_CACHE:
        old_key, _ = _recent_resource_details.popitem(last=False)
        old_keys = [key for key, value in _recent_resource_keys.items() if value == old_key]
        for key in old_keys:
            _recent_resource_keys.pop(key, None)
    return short_key


def _trongrid_headers_without_key(headers: dict | None) -> dict:
    return {key: value for key, value in dict(headers or {}).items() if key.lower() != 'tron-pro-api-key'}


async def _trongrid_post_with_key_fallback(client: httpx.AsyncClient, url: str, payload: dict, headers: dict):
    resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code == 401 and headers.get('TRON-PRO-API-KEY'):
        resp = await client.post(url, json=payload, headers=_trongrid_headers_without_key(headers))
    return resp


@sync_to_async
def _get_user(user_id: int):
    TelegramUser = apps.get_model('bot', 'TelegramUser')
    return TelegramUser.objects.filter(id=user_id).first()


@sync_to_async
def _update_resource_snapshot(monitor_id: int, address: str, energy: int, bandwidth: int, delta_energy: int, delta_bandwidth: int):
    AddressMonitor = apps.get_model('cloud', 'AddressMonitor')
    AddressMonitor.objects.filter(id=monitor_id).update(
        last_energy=energy,
        last_bandwidth=bandwidth,
        resource_checked_at=timezone.now(),
    )
    save_resource_snapshot(
        monitor_id=monitor_id,
        address=address,
        energy=energy,
        bandwidth=bandwidth,
        delta_energy=delta_energy,
        delta_bandwidth=delta_bandwidth,
        account_scope='platform',
        account_key='default',
    )


async def _notify(user_id: int, text: str, reply_markup=None):
    if _bot is None:
        return
    user = await _get_user(user_id)
    if not user:
        return
    await _bot.send_message(chat_id=user.tg_user_id, text=text, parse_mode='HTML', reply_markup=reply_markup)


async def _fetch_account_resource(address: str) -> tuple[int, int]:
    headers = await build_trongrid_headers()
    base_url = await sync_to_async(get_runtime_config, thread_sensitive=False)('trongrid_base_url', TRONGRID_BASE_URL)
    base_url = str(base_url or TRONGRID_BASE_URL).rstrip('/')
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await _trongrid_post_with_key_fallback(client, f'{base_url}/wallet/getaccountresource', {'address': address, 'visible': True}, headers)
        resp.raise_for_status()
        data = resp.json() or {}
        await sync_to_async(record_external_sync_log)(
            source='trongrid',
            action='get_account_resource',
            target=address,
            request_payload={'address': address},
            response_payload=data,
            is_success=True,
        )
    free_net_limit = int(data.get('freeNetLimit', 0) or 0)
    free_net_used = int(data.get('freeNetUsed', 0) or 0)
    net_limit = int(data.get('NetLimit', 0) or 0)
    net_used = int(data.get('NetUsed', 0) or 0)
    energy_limit = int(data.get('EnergyLimit', 0) or 0)
    energy_used = int(data.get('EnergyUsed', 0) or 0)
    available_bandwidth = max((free_net_limit - free_net_used) + (net_limit - net_used), 0)
    available_energy = max(energy_limit - energy_used, 0)
    return available_energy, available_bandwidth


async def check_resources():
    try:
        monitor_cache = await get_monitor_addresses()
        for address, monitors in monitor_cache.items():
            resource_watchers = [mon for mon in monitors if mon.get('monitor_resources')]
            if not resource_watchers:
                continue
            energy, bandwidth = await _fetch_account_resource(address)
            for mon in resource_watchers:
                old_energy = int(mon.get('last_energy', 0) or 0)
                old_bandwidth = int(mon.get('last_bandwidth', 0) or 0)
                energy_increase = energy - old_energy
                bandwidth_increase = bandwidth - old_bandwidth
                await _update_resource_snapshot(mon['id'], address, energy, bandwidth, energy_increase, bandwidth_increase)
                energy_threshold = max(int(mon.get('energy_threshold', 1) or 0), 0)
                bandwidth_threshold = max(int(mon.get('bandwidth_threshold', 1) or 0), 0)
                energy_hit = energy_increase > 0 and energy_increase >= energy_threshold
                bandwidth_hit = bandwidth_increase > 0 and bandwidth_increase >= bandwidth_threshold
                if not energy_hit and not bandwidth_hit:
                    logger.info(
                        'RESOURCE_MONITOR_THRESHOLD_SKIP monitor_id=%s address=%s energy_delta=%s energy_threshold=%s bandwidth_delta=%s bandwidth_threshold=%s',
                        mon.get('id'), address, energy_increase, energy_threshold, bandwidth_increase, bandwidth_threshold,
                    )
                    continue
                remark = mon.get('remark') or '(无备注)'
                now_text = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
                lines = [
                    '⚡ 资源变动提醒',
                    '',
                    f'🏷️ 地址备注: {escape(remark)}',
                    f'📍 监控地址: <code>{escape(address)}</code>',
                    f'🕒 检测时间: <code>{escape(now_text)}</code>',
                ]
                if energy_hit:
                    lines.append(f'⚡ 可用能量增加: <code>+{energy_increase}</code>（阈值 {energy_threshold}）')
                if bandwidth_hit:
                    lines.append(f'📶 可用带宽增加: <code>+{bandwidth_increase}</code>（阈值 {bandwidth_threshold}）')
                lines.extend([
                    '',
                    f'当前可用能量: <code>{energy}</code>',
                    f'当前可用带宽: <code>{bandwidth}</code>',
                    '',
                    '📘 说明: 仅在资源增加时通知，正常转账消耗不通知。',
                ])
                detail_id = f"{address}:{now_text}"
                detail_key = _cache_resource_detail(detail_id, {
                    'user_id': mon['user_id'],
                    'address': address,
                    'remark': remark,
                    'time': now_text,
                    'energy_increase': energy_increase,
                    'bandwidth_increase': bandwidth_increase,
                    'energy': energy,
                    'bandwidth': bandwidth,
                    'energy_threshold': energy_threshold,
                    'bandwidth_threshold': bandwidth_threshold,
                })
                await _notify(
                    mon['user_id'],
                    '\n'.join(lines),
                    reply_markup=InlineKeyboardMarkup(
                        inline_keyboard=[[InlineKeyboardButton(text='查看资源详情', callback_data=f'mon:resd:{detail_key}')]]
                    ),
                )
    except Exception as exc:
        logger.error('资源巡检异常: %s', exc)
