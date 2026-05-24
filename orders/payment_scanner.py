import asyncio
import hashlib
import json
import logging
import os
import time
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal, ROUND_DOWN
from html import escape


import httpx
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from asgiref.sync import sync_to_async
from django.db.models import Q
from django.utils import timezone

from orders.ledger import record_balance_ledger
from bot.models import TelegramUser
from cloud.models import AddressMonitor, CloudAsset, CloudServerOrder
from orders.models import Order, Product, Recharge
from orders.services import usdt_to_trx
from bot.keyboards import custom_port_keyboard
from cloud.provisioning import provision_cloud_server
from cloud.services import apply_cloud_server_renewal, is_cloud_asset_renewal_order, run_cloud_server_renewal_postcheck
from core.cache import get_config, get_redis, bump_daily_stats, get_daily_stats
from core.runtime_config import get_runtime_config
from core.persistence import bump_daily_address_stat, record_external_sync_log
from core.trongrid import build_trongrid_headers
from cloud.cache import get_monitor_addresses, maybe_sync_monitors, init_monitor_cache
from orders.tron_parser import parse_trx_transfer, parse_usdt_transfer

logger = logging.getLogger(__name__)


def _parse_notify_chat_ids(raw_value: str) -> list[int | str]:
    values: list[int | str] = []
    for item in str(raw_value or '').replace('\n', ',').replace(';', ',').split(','):
        text = item.strip()
        if not text:
            continue
        if text.startswith('@'):
            values.append(text)
            continue
        try:
            values.append(int(text))
        except ValueError:
            logger.warning('通知抄送 Chat ID 格式不正确: %s', text)
    return values


def _admin_notice_copy_text(user, text: str) -> str:
    tg_user_id = getattr(user, 'tg_user_id', None) or getattr(user, 'id', None) or '-'
    username = getattr(user, 'primary_username', '') or getattr(user, 'username', '') or ''
    first_name = getattr(user, 'first_name', '') or ''
    user_label = f'{first_name} @{username}'.strip() if username else (first_name or '-')
    return f'📣 通知抄送\n用户: {user_label}\nTG ID: {tg_user_id}\n\n{text}'


# ── 配置 ──────────────────────────────────────────────────────────────────

USDT_CONTRACT = get_runtime_config('usdt_contract', os.getenv('USDT_CONTRACT', 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'))
TRONGRID_BASE_URL = get_runtime_config('trongrid_base_url', os.getenv('TRONGRID_BASE_URL', 'https://api.trongrid.io'))
SCANNER_VERBOSE = get_runtime_config('scanner_verbose', os.getenv('SCANNER_VERBOSE', '0')) == '1'
SCANNER_BLOCK_LOG_ENABLED = get_runtime_config('scanner_block_log_enabled', os.getenv('SCANNER_BLOCK_LOG_ENABLED', '0')) == '1'

# ── 内部状态 ──────────────────────────────────────────────────────────────

_processed_blocks: OrderedDict[str, bool] = OrderedDict()
MAX_CACHE = 200

_last_scan_summary_at: float = time.time()
_SCAN_SUMMARY_INTERVAL = 600
_scan_stats = {'blocks': 0, 'transactions': 0, 'transfers': 0, 'payments': 0, 'monitor_hits': 0}
_last_rate_limit_log_at: float = 0.0
_last_trongrid_unauthorized_log_at: float = 0.0
_last_trongrid_network_error_log_at: float = 0.0
_last_runtime_config_refresh_at: float = 0.0
_last_pending_expire_check_at: float = 0.0
_last_scanned_block_number: int | None = None
_SCANNER_LAST_BLOCK_KEY = 'tron_scanner_last_block_number'
_SCANNER_POLL_INTERVAL = 0.8

_recent_tx_details: OrderedDict[str, dict] = OrderedDict()
_recent_tx_keys: OrderedDict[str, str] = OrderedDict()
_recent_resource_details: OrderedDict[str, dict] = OrderedDict()
_recent_resource_keys: OrderedDict[str, str] = OrderedDict()
MAX_TX_DETAIL_CACHE = 500
ADDRESS_BALANCE_CACHE_TTL = 60
ADDRESS_BALANCE_CACHE_PREFIX = 'address_balance:'

_bot: Bot | None = None


# ── 公开接口 ──────────────────────────────────────────────────────────────

def set_bot(bot: Bot):
    global _bot
    _bot = bot


def get_tx_detail(detail_key: str) -> dict | None:
    tx_hash = _recent_tx_keys.get(detail_key, detail_key)
    return _recent_tx_details.get(tx_hash)


def get_resource_detail(detail_key: str) -> dict | None:
    resource_key = _recent_resource_keys.get(detail_key, detail_key)
    return _recent_resource_details.get(resource_key)


def reload_config():
    """热重载扫描器配置（由外部调用）。"""
    global SCANNER_VERBOSE, SCANNER_BLOCK_LOG_ENABLED, USDT_CONTRACT, TRONGRID_BASE_URL
    SCANNER_VERBOSE = get_runtime_config('scanner_verbose', os.getenv('SCANNER_VERBOSE', '0')) == '1'
    SCANNER_BLOCK_LOG_ENABLED = get_runtime_config('scanner_block_log_enabled', os.getenv('SCANNER_BLOCK_LOG_ENABLED', '0')) == '1'
    USDT_CONTRACT = get_runtime_config('usdt_contract', os.getenv('USDT_CONTRACT', 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'))
    TRONGRID_BASE_URL = get_runtime_config('trongrid_base_url', os.getenv('TRONGRID_BASE_URL', 'https://api.trongrid.io'))


async def _refresh_runtime_flags(force: bool = False):
    global SCANNER_VERBOSE, SCANNER_BLOCK_LOG_ENABLED, USDT_CONTRACT, TRONGRID_BASE_URL, _last_runtime_config_refresh_at
    now = time.time()
    if not force and now - _last_runtime_config_refresh_at < 5:
        return
    from core.cache import refresh_config
    await refresh_config(['scanner_verbose', 'scanner_block_log_enabled', 'usdt_contract', 'trongrid_base_url'])
    SCANNER_VERBOSE = (await get_config('scanner_verbose', os.getenv('SCANNER_VERBOSE', '0'))) == '1'
    SCANNER_BLOCK_LOG_ENABLED = (await get_config('scanner_block_log_enabled', os.getenv('SCANNER_BLOCK_LOG_ENABLED', '0'))) == '1'
    USDT_CONTRACT = await get_config('usdt_contract', os.getenv('USDT_CONTRACT', 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t'))
    TRONGRID_BASE_URL = await get_config('trongrid_base_url', os.getenv('TRONGRID_BASE_URL', 'https://api.trongrid.io'))
    _last_runtime_config_refresh_at = now


# ── 内部辅助 ──────────────────────────────────────────────────────────────

def _cache_tx_detail(tx_hash: str, detail: dict):
    user_id = str(detail.get('user_id') or '').strip()
    cache_key = f'{tx_hash}:{user_id}' if user_id else tx_hash
    detail_key = hashlib.sha1(cache_key.encode('utf-8')).hexdigest()[:16] if user_id else tx_hash[:16]
    _recent_tx_details[cache_key] = detail
    _recent_tx_keys[detail_key] = cache_key
    if len(_recent_tx_details) > MAX_TX_DETAIL_CACHE:
        old_cache_key, _ = _recent_tx_details.popitem(last=False)
        old_keys = [key for key, value in _recent_tx_keys.items() if value == old_cache_key]
        for key in old_keys:
            _recent_tx_keys.pop(key, None)
    return detail_key


def _build_tx_detail_keyboard(detail_key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='查看交易详情', callback_data=f'mon:txd:{detail_key}')]]
    )


def _cache_resource_detail(detail_id: str, detail: dict):
    detail_key = detail_id[:16]
    _recent_resource_details[detail_id] = detail
    _recent_resource_keys[detail_key] = detail_id
    if len(_recent_resource_details) > MAX_TX_DETAIL_CACHE:
        old_id, _ = _recent_resource_details.popitem(last=False)
        old_keys = [key for key, value in _recent_resource_keys.items() if value == old_id]
        for key in old_keys:
            _recent_resource_keys.pop(key, None)


def _build_resource_detail_keyboard(detail_id: str) -> InlineKeyboardMarkup:
    detail_key = detail_id[:16]
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='查看资源详情', callback_data=f'mon:resd:{detail_key}')]]
    )


def fmt_amount(value) -> str:
    if value is None:
        return ''
    amount = Decimal(str(value)).quantize(Decimal('0.001'), rounding=ROUND_DOWN)
    text = format(amount, 'f')
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def _now_str() -> str:
    return datetime.now().strftime('%H:%M:%S')


def _short_addr(addr: str) -> str:
    if len(addr) > 14:
        return f'{addr[:6]}...{addr[-4:]}'
    return addr


async def _record_daily_stats(address: str, currency: str, direction: str, amount: Decimal, user_id: int, monitor_id: int | None = None):
    return await _record_daily_stats_for_monitors(
        address,
        currency,
        direction,
        amount,
        [{'user_id': user_id, 'id': monitor_id}],
    )


async def _record_daily_stats_for_monitors(address: str, currency: str, direction: str, amount: Decimal, monitors: list[dict]):
    amount_units = int((Decimal(str(amount or 0)) * Decimal('1000000')).to_integral_value(rounding=ROUND_DOWN))
    await bump_daily_stats(address, currency, direction, amount=amount_units)
    seen = set()
    for mon in monitors or []:
        user_id = mon.get('user_id')
        monitor_id = mon.get('id')
        if not user_id:
            continue
        key = (int(user_id), int(monitor_id or 0))
        if key in seen:
            continue
        seen.add(key)
        await sync_to_async(bump_daily_address_stat)(
            user_id=user_id,
            address=address,
            currency=currency,
            direction=direction,
            amount=amount,
            monitor_id=monitor_id,
            account_scope='platform',
            account_key='default',
        )
    income_units = await get_daily_stats(f'{address}:{currency}:income')
    expense_units = await get_daily_stats(f'{address}:{currency}:expense')
    return {
        'income': str(Decimal(income_units) / Decimal('1000000')),
        'expense': str(Decimal(expense_units) / Decimal('1000000')),
    }


# ── DB access (sync → async) ────────────────────────────────────────────

def _cloud_status_after_renewal_payment_expired(order: CloudServerOrder, now=None) -> str:
    now = now or timezone.now()
    retained_ip = bool((order.public_ip or order.previous_public_ip) and not str(order.instance_id or '').strip() and order.ip_recycle_at and order.ip_recycle_at > now)
    if retained_ip:
        return 'deleted'
    if order.delete_at and order.delete_at <= now:
        return 'deleting'
    if order.suspend_at and order.suspend_at <= now:
        return 'suspended'
    if order.service_expires_at and order.service_expires_at <= now:
        return 'expiring'
    return 'completed'


@sync_to_async
def _expire_timed_out_payment_orders():
    now = timezone.now()
    product_count = Order.objects.filter(
        pay_method='address',
        status='pending',
        expired_at__isnull=False,
        expired_at__lte=now,
    ).update(status='expired')
    recharge_count = Recharge.objects.filter(
        status='pending',
        expired_at__isnull=False,
        expired_at__lte=now,
    ).update(status='expired')
    expired_cloud_payment_qs = CloudServerOrder.objects.filter(
        pay_method='address',
        status='pending',
        expired_at__isnull=False,
        expired_at__lte=now,
    )
    expired_asset_renewal_order_ids = [
        order.id for order in expired_cloud_payment_qs
        if is_cloud_asset_renewal_order(order)
    ]
    cloud_count = expired_cloud_payment_qs.update(status='expired')
    if expired_asset_renewal_order_ids:
        for asset in CloudAsset.objects.filter(order_id__in=expired_asset_renewal_order_ids).order_by('id'):
            asset.order = None
            asset.save(update_fields=['order', 'updated_at'])
    renewal_expired_count = 0
    renewal_orders = list(CloudServerOrder.objects.filter(
        pay_method='address',
        status='renew_pending',
        expired_at__isnull=False,
        expired_at__lte=now,
    ).order_by('id'))
    for order in renewal_orders:
        order.status = _cloud_status_after_renewal_payment_expired(order, now)
        order.expired_at = None
        order.tx_hash = None
        order.payer_address = None
        order.receive_address = None
        order.paid_at = None
        order.provision_note = '\n'.join(filter(None, [str(order.provision_note or '').strip(), f'续费地址支付窗口已于 {now:%Y-%m-%d %H:%M} 超时关闭，主服务器订单状态已恢复为 {order.status}。']))
        order.save(update_fields=['status', 'expired_at', 'tx_hash', 'payer_address', 'receive_address', 'paid_at', 'provision_note', 'updated_at'])
        renewal_expired_count += 1
    if product_count or recharge_count or cloud_count or renewal_expired_count:
        logger.info('支付订单超时自动关闭: product=%s recharge=%s cloud=%s cloud_renewal=%s', product_count, recharge_count, cloud_count, renewal_expired_count)
    return product_count, recharge_count, cloud_count, renewal_expired_count


async def _expire_timed_out_payment_orders_periodically(force: bool = False):
    global _last_pending_expire_check_at
    now = time.time()
    if not force and now - _last_pending_expire_check_at < 60:
        return (0, 0, 0, 0)
    _last_pending_expire_check_at = now
    return await _expire_timed_out_payment_orders()


def _active_payment_q(now=None):
    now = now or timezone.now()
    return Q(expired_at__isnull=True) | Q(expired_at__gt=now)


@sync_to_async
def _get_pending_address_orders(currency: str):
    return list(
        Order.objects.filter(_active_payment_q(), pay_method='address', status='pending', currency=currency)
        .order_by('created_at')
    )


@sync_to_async
def _get_pending_recharges(currency: str):
    return list(
        Recharge.objects.filter(_active_payment_q(), status='pending', currency=currency)
        .order_by('created_at')
    )


@sync_to_async
def _get_pending_cloud_server_orders(currency: str):
    currency_filter = Q(currency=currency)
    return list(
        CloudServerOrder.objects.filter(
            _active_payment_q(),
            currency_filter,
            pay_method='address',
            status__in=['pending', 'renew_pending'],
        )
        .filter(
            Q(status='pending')
            | (Q(status='renew_pending') & (Q(public_ip__isnull=False, public_ip__gt='') | Q(previous_public_ip__isnull=False, previous_public_ip__gt='')))
        )
        .order_by('created_at')
    )


def _tx_hash_already_confirmed(tx_hash: str) -> bool:
    if not str(tx_hash or '').strip():
        return False
    return bool(
        Order.objects.filter(tx_hash=tx_hash).exists()
        or Recharge.objects.filter(tx_hash=tx_hash).exists()
        or CloudServerOrder.objects.filter(tx_hash=tx_hash).exists()
    )


@sync_to_async
def _confirm_order_paid(order_id: int, tx_hash: str, payer_address: str = '', receive_address: str = ''):
    from django.db import transaction
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)
        if order.status != 'pending':
            return None
        if _tx_hash_already_confirmed(tx_hash):
            logger.warning('商品订单链上支付拒绝: order=%s tx=%s reason=duplicate_tx_hash', order_id, tx_hash)
            return None
        order.status = 'paid'
        order.tx_hash = tx_hash
        order.payer_address = payer_address or ''
        order.receive_address = receive_address or ''
        order.paid_at = timezone.now()
        product = Product.objects.select_for_update().get(id=order.product_id)
        if product.stock != -1 and product.stock < order.quantity:
            logger.warning(
                '商品订单链上支付拒绝: order=%s tx=%s reason=stock_insufficient stock=%s quantity=%s',
                order_id,
                tx_hash,
                product.stock,
                order.quantity,
            )
            return None
        order.save(update_fields=['status', 'tx_hash', 'payer_address', 'receive_address', 'paid_at'])
        if product.stock != -1:
            product.stock -= order.quantity
            product.save(update_fields=['stock', 'updated_at'])
        order.status = 'delivered'
        order.save(update_fields=['status'])
    return order


@sync_to_async
def _confirm_recharge(recharge_id: int, tx_hash: str, paid_amount=None, payer_address: str = '', receive_address: str = ''):
    from django.db import transaction
    actual_amount = Decimal(str(paid_amount or 0)).quantize(Decimal('0.000001'), rounding=ROUND_DOWN)
    with transaction.atomic():
        rc = Recharge.objects.select_for_update().get(id=recharge_id)
        if rc.status != 'pending':
            return None
        if _tx_hash_already_confirmed(tx_hash):
            logger.warning('充值链上支付拒绝: recharge=%s tx=%s reason=duplicate_tx_hash', recharge_id, tx_hash)
            return None
        if actual_amount <= 0:
            actual_amount = Decimal(str(rc.pay_amount or rc.amount or 0)).quantize(Decimal('0.000001'), rounding=ROUND_DOWN)
        rc.amount = actual_amount
        rc.status = 'completed'
        rc.tx_hash = tx_hash
        rc.payer_address = payer_address or ''
        rc.receive_address = receive_address or rc.receive_address or ''
        rc.completed_at = timezone.now()
        rc.save(update_fields=['amount', 'status', 'tx_hash', 'payer_address', 'receive_address', 'completed_at'])
        user = TelegramUser.objects.select_for_update().get(id=rc.user_id)
        field = 'balance_trx' if rc.currency == 'TRX' else 'balance'
        old_balance = getattr(user, field)
        setattr(user, field, old_balance + actual_amount)
        user.save(update_fields=[field, 'updated_at'])
        record_balance_ledger(
            user,
            ledger_type='recharge',
            currency=rc.currency,
            old_balance=old_balance,
            new_balance=getattr(user, field),
            related_type='recharge',
            related_id=rc.id,
            description=f'充值订单 #{rc.id} 完成入账，链上实付 {actual_amount} {rc.currency}',
        )
    return rc


@sync_to_async
def _confirm_cloud_server_order(order_id: int, tx_hash: str, payer_address: str = '', receive_address: str = ''):
    from django.db import transaction
    try:
        with transaction.atomic():
            order = CloudServerOrder.objects.select_for_update().get(id=order_id)
            if order.status not in {'pending', 'renew_pending'}:
                return None
            if _tx_hash_already_confirmed(tx_hash):
                logger.warning('云服务器链上支付拒绝: order=%s tx=%s reason=duplicate_tx_hash', order_id, tx_hash)
                return None
            order.tx_hash = tx_hash
            order.payer_address = payer_address or ''
            order.receive_address = receive_address or ''
            order.paid_at = timezone.now()
            asset_recovery_order = is_cloud_asset_renewal_order(order)
            if order.status == 'renew_pending' and not asset_recovery_order:
                if not str(order.public_ip or order.previous_public_ip or '').strip() or order.status in {'deleted', 'deleting', 'expired'}:
                    return None
                order.save(update_fields=['tx_hash', 'payer_address', 'receive_address', 'paid_at', 'updated_at'])
                return apply_cloud_server_renewal.__wrapped__(order.id, order.lifecycle_days or 31, False)
            order.status = 'paid'
            if asset_recovery_order:
                order.service_expires_at = None
            payment_note = '已收款，正在恢复未绑定代理资产固定 IP。' if asset_recovery_order else '已收款，等待用户确认 MTProxy 端口后进入创建流程。默认端口为 9528。'
            order.provision_note = '\n'.join(part for part in [str(order.provision_note or '').strip(), payment_note] if part)
            update_fields = ['status', 'tx_hash', 'payer_address', 'receive_address', 'paid_at', 'provision_note', 'updated_at']
            if asset_recovery_order:
                update_fields.append('service_expires_at')
            order.save(update_fields=update_fields)
        return order
    except Exception as exc:
        logger.warning('云服务器真实支付确认失败 order=%s err=%s', order_id, exc)
        return None


@sync_to_async
def _get_user(user_id: int):
    return TelegramUser.objects.filter(id=user_id).first()


@sync_to_async
def _get_product(product_id: int):
    return Product.objects.filter(id=product_id).first()


# ── 通知 ──────────────────────────────────────────────────────────────────

def _format_local_dt(value) -> str:
    if not value:
        return '未设置'
    try:
        return timezone.localtime(value).strftime('%Y-%m-%d %H:%M')
    except Exception:
        return str(value)


def _cloud_order_ip_text(order) -> str:
    return getattr(order, 'public_ip', None) or getattr(order, 'previous_public_ip', None) or '未分配'


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
    return '\n'.join(lines)


async def _cloud_renewal_postcheck_and_notify(order: CloudServerOrder):
    await _notify_user(order.user_id, '🔎 续费已完成，正在检查服务器运行状态和 MTProxy 链路。')
    checked, err = await run_cloud_server_renewal_postcheck(order.id)
    if getattr(checked, 'replacement_for_id', None) and checked.status in {'paid', 'provisioning', 'failed'}:
        asyncio.create_task(_provision_recovered_cloud_order(checked))
        return
    if err:
        await _notify_user(order.user_id, f'⚠️ 续费后巡检发现异常，已记录并尝试修复。\n订单号: {getattr(checked, "order_no", "-") or "-"}\n请稍后再查看代理状态，或联系人工客服。')
        return
    await _notify_user(order.user_id, f'IP: {_cloud_order_ip_text(checked)}\n{_cloud_order_plan_text(checked)}')


async def _provision_recovered_cloud_order(order: CloudServerOrder):
    try:
        await _notify_user(order.user_id, f'✅ 云服务器续费成功，正在恢复固定 IP 服务器。\n订单号: {order.order_no}\n系统会保持旧 IP / 旧端口 / 旧密钥不变，完成后自动发送代理链接。')
        provisioned = await provision_cloud_server(order.id)
        if provisioned and provisioned.status == 'completed':
            await _notify_user(provisioned.user_id, f'✅ 固定 IP 服务器恢复完成！\n订单号: {provisioned.order_no}\nIP: {provisioned.public_ip or provisioned.previous_public_ip}\n新的到期时间: {_format_local_dt(provisioned.service_expires_at)}')
        else:
            await _notify_user(order.user_id, f'⚠️ 固定 IP 服务器恢复暂未完成。\n订单号: {order.order_no}\n请稍后在查询中心查看，或联系人工客服。')
    except Exception as exc:
        logger.exception('固定 IP 续费恢复任务异常 order=%s error=%s', getattr(order, 'id', None), exc)
        await _notify_user(order.user_id, f'⚠️ 固定 IP 服务器恢复任务异常。\n订单号: {order.order_no}\n请联系人工客服处理。')


async def _copy_notice_to_admins(user, text: str, parse_mode: str | None = None):
    if _bot is None or not user:
        return
    copy_chat_ids = _parse_notify_chat_ids(await get_config('bot_notice_copy_chat_ids', ''))
    if not copy_chat_ids:
        return
    copy_text = _admin_notice_copy_text(user, text)
    for copy_chat_id in copy_chat_ids:
        if str(copy_chat_id) == str(getattr(user, 'tg_user_id', '')):
            continue
        try:
            await _bot.send_message(chat_id=copy_chat_id, text=copy_text, parse_mode=parse_mode)
        except Exception as exc:
            logger.warning('用户通知抄送失败 copy_chat_id=%s user_id=%s err=%s', copy_chat_id, getattr(user, 'id', None), exc)


async def _notify_user(user_id: int, text: str, reply_markup=None, parse_mode: str | None = None):
    if _bot is None:
        return
    user = None
    try:
        user = await _get_user(user_id)
        if user:
            await _bot.send_message(
                chat_id=user.tg_user_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
    except Exception as e:
        logger.error('通知用户失败 user_id=%s: %s', user_id, e)
    if user:
        await _copy_notice_to_admins(user, text, parse_mode=parse_mode)


async def _deliver_product(user_id: int, product, quantity: int = 1):
    if _bot is None:
        logger.warning('Bot 未初始化，无法发货')
        return
    try:
        user = await _get_user(user_id)
        if not user:
            return
        for _ in range(quantity):
            if product.content_type == 'text':
                await _bot.send_message(chat_id=user.tg_user_id, text=product.content_text or '')
            elif product.content_type == 'image' and product.content_image:
                await _bot.send_photo(chat_id=user.tg_user_id, photo=product.content_image, caption=product.content_text or '')
            elif product.content_type == 'video' and product.content_video:
                await _bot.send_video(chat_id=user.tg_user_id, video=product.content_video, caption=product.content_text or '')
    except Exception as e:
        logger.error('发货失败 user_id=%s: %s', user_id, e)


# ── 支付匹配 ──────────────────────────────────────────────────────────────

def _cloud_expected_transfer_amount(order: CloudServerOrder, currency: str) -> Decimal | None:
    payable = Decimal(str(order.pay_amount or order.total_amount or 0))
    if order.currency == currency:
        return payable
    return None


async def _process_payment(transfer: dict) -> bool:
    amount = transfer['amount']
    tx_hash = transfer['tx_hash']
    currency = transfer['currency']

    matches = []
    pending_orders = await _get_pending_address_orders(currency)
    for order in pending_orders:
        if order.pay_amount == amount:
            matches.append(('product', order))

    pending_recharges = await _get_pending_recharges(currency)
    for rc in pending_recharges:
        if rc.pay_amount == amount:
            matches.append(('recharge', rc))

    pending_cloud_orders = await _get_pending_cloud_server_orders(currency)
    for order in pending_cloud_orders:
        expected_amount = _cloud_expected_transfer_amount(order, currency)
        if expected_amount == amount:
            matches.append(('cloud', order))

    if len(matches) > 1:
        logger.error(
            '链上支付金额匹配冲突，已拒绝自动入账: currency=%s amount=%s tx=%s matches=%s',
            currency,
            fmt_amount(amount),
            tx_hash,
            [(kind, getattr(item, 'id', None), getattr(item, 'order_no', None) or getattr(item, 'amount', None), str(getattr(item, 'pay_amount', ''))) for kind, item in matches],
        )
        return False

    if not matches:
        logger.info(
            '链上支付未匹配：currency=%s chain_amount=%s tx=%s pending_products=%s pending_recharges=%s pending_cloud=%s',
            currency,
            fmt_amount(amount),
            tx_hash,
            [(order.id, order.user_id, str(order.total_amount), str(order.pay_amount)) for order in pending_orders[:10]],
            [(rc.id, rc.user_id, str(rc.amount), str(rc.pay_amount)) for rc in pending_recharges[:10]],
            [(order.id, order.user_id, order.order_no, order.currency, str(order.total_amount), str(order.pay_amount)) for order in pending_cloud_orders[:10]],
        )
        return False

    kind, item = matches[0]
    if kind == 'product':
        confirmed = await _confirm_order_paid(item.id, tx_hash, transfer.get('from', ''), transfer.get('to', ''))
        if confirmed:
            logger.info(
                '💰 订单匹配 → %s  %s %s  tx=%s',
                confirmed.order_no, fmt_amount(amount), currency, tx_hash,
            )
            product = await _get_product(confirmed.product_id)
            if product:
                await _deliver_product(confirmed.user_id, product, confirmed.quantity)
            await _notify_user(confirmed.user_id, f'✅ 订单 {confirmed.order_no} 支付成功！\n商品已发送，请查收。')
            return True
        return False

    if kind == 'recharge':
        confirmed = await _confirm_recharge(item.id, tx_hash, amount, transfer.get('from', ''), transfer.get('to', ''))
        if confirmed:
            logger.info(
                '💰 充值匹配 → user#%s  %s %s  tx=%s',
                confirmed.user_id, fmt_amount(amount), currency, tx_hash,
            )
            await _notify_user(confirmed.user_id, f'✅ 充值成功！\n到账金额: {fmt_amount(confirmed.amount)} {currency}\n余额已更新。')
            return True
        return False

    confirmed = await _confirm_cloud_server_order(item.id, tx_hash, transfer.get('from', ''), transfer.get('to', ''))
    if confirmed:
        logger.info(
            '💰 云服务器订单匹配 → %s  %s %s  tx=%s',
            confirmed.order_no, fmt_amount(amount), currency, tx_hash,
        )
        if confirmed.status == 'completed':
            await _notify_user(
                confirmed.user_id,
                f'✅ 云服务器订单 {confirmed.order_no} 续费成功！\n新的到期时间: {_format_local_dt(confirmed.service_expires_at)}',
            )
            asyncio.create_task(_cloud_renewal_postcheck_and_notify(confirmed))
            return True
        if getattr(confirmed, 'replacement_for_id', None) and confirmed.status in {'paid', 'provisioning', 'failed'}:
            asyncio.create_task(_provision_recovered_cloud_order(confirmed))
            return True
        if is_cloud_asset_renewal_order(confirmed) and confirmed.status in {'paid', 'provisioning', 'failed'}:
            asyncio.create_task(_provision_recovered_cloud_order(confirmed))
            return True
        await _notify_user(
            confirmed.user_id,
            f'✅ 云服务器订单 {confirmed.order_no} 支付成功！\n'
            f'地区: {confirmed.region_name}\n套餐: {confirmed.plan_name}\n'
            '请选择 MTProxy 端口：默认端口是 9528，你也可以输入自定义端口。',
            reply_markup=custom_port_keyboard(confirmed.id),
        )
        return True
    return False


async def _get_address_chain_balances(address: str) -> tuple[Decimal | None, Decimal | None]:
    cache_key = f'{ADDRESS_BALANCE_CACHE_PREFIX}{address}'
    redis_client = await get_redis()
    if redis_client is not None:
        try:
            cached = await redis_client.get(cache_key)
            if cached:
                payload = json.loads(cached)
                return Decimal(str(payload.get('usdt', '0'))), Decimal(str(payload.get('trx', '0')))
        except Exception:
            pass
    try:
        headers = await build_trongrid_headers()
        async with httpx.AsyncClient(timeout=10) as client:
            trx_resp = await _trongrid_get_with_key_fallback(
                client,
                f'{TRONGRID_BASE_URL}/v1/accounts/{address}',
                headers,
            )
            trx_resp.raise_for_status()
            trx_data = trx_resp.json() or {}
            account_items = trx_data.get('data') or []
            account = account_items[0] if account_items else {}
            trx_balance = Decimal(str(account.get('balance', 0) or 0)) / Decimal('1000000')
            usdt_balance = Decimal('0')
            for item in account.get('trc20') or []:
                if not isinstance(item, dict):
                    continue
                for contract, value in item.items():
                    if str(contract).lower() == str(USDT_CONTRACT).lower():
                        usdt_balance = Decimal(str(value or '0')) / Decimal('1000000')
                        break
        if redis_client is not None:
            try:
                await redis_client.setex(
                    cache_key,
                    ADDRESS_BALANCE_CACHE_TTL,
                    json.dumps({'usdt': str(usdt_balance), 'trx': str(trx_balance)}),
                )
            except Exception:
                pass
        return usdt_balance, trx_balance
    except Exception as exc:
        logger.warning('监控地址余额查询失败 address=%s error=%s', address, exc)
        return None, None


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


def _format_chain_balance(value: Decimal | None, currency: str) -> str:
    if value is None:
        return f'查询失败 {currency}'
    return f'{fmt_amount(value)} {currency}'


async def _get_fee_text(tx_hash: str) -> str:
    try:
        headers = await build_trongrid_headers()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await _trongrid_post_with_key_fallback(
                client,
                f'{TRONGRID_BASE_URL}/wallet/gettransactioninfobyid',
                {'value': tx_hash},
                headers,
            )
            resp.raise_for_status()
            info = resp.json() or {}
        receipt = info.get('receipt', {}) or {}
        fee_sun = int(info.get('fee', 0) or 0)
        net_fee_sun = int(receipt.get('net_fee', 0) or 0)
        energy_fee_sun = int(receipt.get('energy_fee', 0) or 0)
        net_usage = int(receipt.get('net_usage', 0) or 0)
        energy_usage = int(receipt.get('energy_usage_total', 0) or 0)
        fee_trx = (Decimal(fee_sun) / Decimal('1000000')).normalize() if fee_sun else Decimal('0')
        return f'{fee_trx} TRX; {net_usage} 带宽; {energy_usage} 能量'
    except Exception:
        return '0 TRX; 0 带宽; 0 能量'


# ── 监控通知 ──────────────────────────────────────────────────────────────

async def _process_monitor_notification(transfer: dict, monitors: list[dict], daily_stats: dict[str, str], direction: str):
    amount = transfer['amount']
    currency = transfer['currency']
    from_addr = transfer['from']
    to_addr = transfer['to']
    tx_hash = transfer['tx_hash']
    tx_time = transfer.get('timestamp') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    is_income = direction == 'income'
    title_icon = '🟢' if is_income else '🔴'
    title_word = '收入' if is_income else '支出'
    amount_prefix = '+' if is_income else '-'
    main_addr = to_addr if is_income else from_addr
    peer_addr = from_addr if is_income else to_addr
    peer_label = '💸 付款地址' if is_income else '📤 收款地址'
    main_label = '📥 收款地址' if is_income else '💳 支出地址'

    for mon in monitors:
        if not mon.get('monitor_transfers', True):
            continue
        threshold = Decimal(mon.get('usdt_threshold', '1') if currency == 'USDT' else mon.get('trx_threshold', '1'))
        if threshold and amount < threshold:
            continue

        _scan_stats['monitor_hits'] += 1
        user = await _get_user(mon['user_id'])
        if not user:
            continue

        remark = mon.get('remark') or '(无备注)'
        fee_text = transfer.get('fee_text') or await _get_fee_text(tx_hash)

        logger.info(
            '\n'
            '  ┌─ 监控命中 ─────────────────────────────\n'
            '  │ 类型     : %s\n'
            '  │ 地址备注 : %s\n'
            '  │ 监控地址 : %s\n'
            '  │ 对手方   : %s\n'
            '  │ 交易时间 : %s\n'
            '  │ 币种     : %s\n'
            '  │ 金额     : %s%s %s\n'
            '  │ 手续费   : %s\n'
            '  │ TX Hash  : %s\n'
            '  └────────────────────────────────────────',
            title_word, remark, main_addr, _short_addr(peer_addr),
            tx_time, currency, amount_prefix, fmt_amount(amount), currency,
            fee_text, tx_hash,
        )

        income = Decimal(str(daily_stats.get('income', '0')))
        expense = Decimal(str(daily_stats.get('expense', '0')))
        profit = income - expense
        address_usdt_balance, address_trx_balance = await _get_address_chain_balances(main_addr)

        text = (
            f'{title_icon} {title_word}{currency} 提醒  <code>{amount_prefix}{escape(fmt_amount(amount))} {escape(currency)}</code>\n\n'
            f'🏷️ 地址备注: {escape(remark)}\n\n'
            f'{peer_label}: <code>{escape(peer_addr)}</code>\n'
            f'{main_label}: <code>{escape(main_addr)}</code>\n'
            f'🕒 交易时间: <code>{escape(tx_time)}</code>\n'
            f'💰 交易金额: <code>{amount_prefix}{escape(fmt_amount(amount))} {escape(currency)}</code>\n'
            f'👛 地址USDT余额: {escape(_format_chain_balance(address_usdt_balance, "USDT"))}\n'
            f'🪙 地址TRX余额: {escape(_format_chain_balance(address_trx_balance, "TRX"))}\n'
            f'⛽ 转账消耗: <code>{escape(fee_text)}</code>\n\n'
            f'📈 今日收入: {escape(fmt_amount(income))} {escape(currency)}\n'
            f'📉 今日支出: {escape(fmt_amount(expense))} {escape(currency)}\n'
            f'💹 今日利润: {escape(fmt_amount(profit))} {escape(currency)}'
        )

        detail_key = _cache_tx_detail(tx_hash, {
            'user_id': mon['user_id'],
            'remark': remark, 'from': from_addr, 'to': to_addr,
            'time': tx_time, 'amount': f'{amount_prefix}{fmt_amount(amount)}',
            'currency': currency, 'tx_hash': tx_hash,
            'raw': transfer.get('raw_tx', ''), 'fee_text': fee_text,
            'direction': direction,
        })
        await _notify_user(mon['user_id'], text, reply_markup=_build_tx_detail_keyboard(detail_key), parse_mode='HTML')


# ── 摘要日志 ──────────────────────────────────────────────────────────────

async def _log_scan_summary(force: bool = False):
    global _last_scan_summary_at
    now = time.time()
    if not force and now - _last_scan_summary_at < _SCAN_SUMMARY_INTERVAL:
        return
    s = _scan_stats
    if s['blocks'] > 0:
        logger.info(
            '📊 10min: %d 块 | %d tx | %d 转账 | %d 支付 | %d 监控',
            s['blocks'], s['transactions'], s['transfers'],
            s['payments'], s['monitor_hits'],
        )
    _last_scan_summary_at = now
    for key in _scan_stats:
        _scan_stats[key] = 0


# ── 主扫描循环 ─────────────────────────────────────────────────────────────

@sync_to_async
def _load_saved_block_number() -> int:
    try:
        from core.models import SiteConfig
        return int(SiteConfig.get(_SCANNER_LAST_BLOCK_KEY, '0') or 0)
    except Exception:
        return 0


@sync_to_async
def _save_scanned_block_number(block_number: int):
    try:
        from core.models import SiteConfig
        SiteConfig.set(_SCANNER_LAST_BLOCK_KEY, str(int(block_number or 0)))
    except Exception as exc:
        logger.warning('保存 TRON 扫块进度失败 block=%s error=%s', block_number, exc)


def _block_number(block_data: dict) -> int:
    try:
        return int(((block_data.get('block_header') or {}).get('raw_data') or {}).get('number') or 0)
    except Exception:
        return 0


async def _handle_block_data(block_data: dict) -> bool:
    block_id = block_data.get('blockID', '')
    block_number = _block_number(block_data)
    if not block_id:
        if SCANNER_BLOCK_LOG_ENABLED:
            logger.warning('[scan] %s block=UNKNOWN number=%s skip=missing_block_id', _now_str(), block_number or '-')
        return False
    if block_id in _processed_blocks:
        if SCANNER_BLOCK_LOG_ENABLED:
            logger.info('[scan] %s block=%s number=%s skip=duplicate', _now_str(), block_id[:16], block_number or '-')
        return False

    _processed_blocks[block_id] = True
    _scan_stats['blocks'] += 1
    if len(_processed_blocks) > MAX_CACHE:
        _processed_blocks.popitem(last=False)

    transactions = block_data.get('transactions', []) or []
    _scan_stats['transactions'] += len(transactions)
    if SCANNER_BLOCK_LOG_ENABLED:
        logger.info('[scan] %s block=%s number=%s txs=%d', _now_str(), block_id[:16], block_number or '-', len(transactions))

    if not transactions:
        return True

    await maybe_sync_monitors()
    monitor_cache = await get_monitor_addresses()
    receive_address = await get_config('receive_address', '')

    for tx in transactions:
        transfer = parse_usdt_transfer(tx, USDT_CONTRACT)
        if transfer is None:
            transfer = parse_trx_transfer(tx)
        if transfer is None:
            continue

        _scan_stats['transfers'] += 1
        from_addr = transfer['from']
        to_addr = transfer['to']

        if receive_address and to_addr == receive_address:
            matched = await _process_payment(transfer)
            if matched:
                _scan_stats['payments'] += 1

        if to_addr in monitor_cache:
            stats = await _record_daily_stats_for_monitors(to_addr, transfer['currency'], 'income', transfer['amount'], monitor_cache[to_addr])
            await _process_monitor_notification(transfer, monitor_cache[to_addr], stats, 'income')
        if from_addr in monitor_cache and from_addr != to_addr:
            stats = await _record_daily_stats_for_monitors(from_addr, transfer['currency'], 'expense', transfer['amount'], monitor_cache[from_addr])
            await _process_monitor_notification(transfer, monitor_cache[from_addr], stats, 'expense')
    return True


async def _post_trongrid(client: httpx.AsyncClient, endpoint: str, payload: dict, headers: dict, *, context: str) -> dict | None:
    global _last_rate_limit_log_at, _last_trongrid_unauthorized_log_at
    resp = await client.post(f'{TRONGRID_BASE_URL}{endpoint}', json=payload, headers=headers)
    if resp.status_code == 401 and headers.get('TRON-PRO-API-KEY'):
        now = time.time()
        if now - _last_trongrid_unauthorized_log_at >= 60:
            logger.warning('TRONGrid API Key 未授权或已失效，%s 已降级为无 Key 请求；请在后台更新有效 TRON API Key', context)
            _last_trongrid_unauthorized_log_at = now
        fallback_headers = {key: value for key, value in headers.items() if key.lower() != 'tron-pro-api-key'}
        resp = await client.post(f'{TRONGRID_BASE_URL}{endpoint}', json=payload, headers=fallback_headers)
    if resp.status_code == 429:
        now = time.time()
        if now - _last_rate_limit_log_at >= 60:
            logger.warning('TRON 扫块触发 429 限流，%s 已跳过本轮并等待下次调度', context)
            _last_rate_limit_log_at = now
        return None
    resp.raise_for_status()
    return resp.json() or {}


async def _fetch_current_block(client: httpx.AsyncClient, headers: dict) -> dict | None:
    return await _post_trongrid(client, '/wallet/getnowblock', {'detail': True}, headers, context='获取当前块')


async def _fetch_block_by_number(client: httpx.AsyncClient, headers: dict, block_number: int) -> dict | None:
    return await _post_trongrid(client, '/wallet/getblockbynum', {'num': int(block_number)}, headers, context=f'获取块 {block_number}')


async def scan_block():
    global _last_scanned_block_number
    try:
        await _refresh_runtime_flags()
        headers = await build_trongrid_headers()

        if _last_scanned_block_number is None:
            saved_number = await _load_saved_block_number()
            _last_scanned_block_number = saved_number or 0

        async with httpx.AsyncClient(timeout=10) as client:
            current_block = await _fetch_current_block(client, headers)
            if not current_block:
                return
            current_number = _block_number(current_block)
            if not current_number:
                logger.warning('TRON 扫块无法读取当前块号')
                return

            if not _last_scanned_block_number:
                handled = await _handle_block_data(current_block)
                if handled:
                    _last_scanned_block_number = current_number
                    await _save_scanned_block_number(current_number)
                await _log_scan_summary()
                return

            if current_number <= _last_scanned_block_number:
                await _handle_block_data(current_block)
                await _log_scan_summary()
                return

            pending_count = current_number - _last_scanned_block_number
            if pending_count > 1:
                logger.warning('TRON 顺序扫块发现积压，将按顺序逐块追赶: last=%s current=%s pending=%s', _last_scanned_block_number, current_number, pending_count)

            for number in range(_last_scanned_block_number + 1, current_number + 1):
                block_data = current_block if number == current_number else await _fetch_block_by_number(client, headers, number)
                if not block_data:
                    break
                handled = await _handle_block_data(block_data)
                block_number = _block_number(block_data) or number
                if handled and block_number:
                    _last_scanned_block_number = max(_last_scanned_block_number or 0, block_number)
                    await _save_scanned_block_number(_last_scanned_block_number)

        await _log_scan_summary()
    except (httpx.TimeoutException, httpx.TransportError) as exc:
        global _last_trongrid_network_error_log_at
        now = time.time()
        if now - _last_trongrid_network_error_log_at >= 60:
            logger.warning('TRON 扫块网络暂不可用，已跳过本轮并等待下次重试: %s', exc.__class__.__name__)
            _last_trongrid_network_error_log_at = now
    except Exception as e:
        logger.exception('扫块异常: %s', e)


async def scan_forever(stop_event: asyncio.Event | None = None):
    logger.info('TRON 顺序扫块器已启动 poll_interval=%ss mode=sequential_no_skip', _SCANNER_POLL_INTERVAL)
    while stop_event is None or not stop_event.is_set():
        started = time.time()
        await _expire_timed_out_payment_orders_periodically()
        await scan_block()
        elapsed = time.time() - started
        delay = max(_SCANNER_POLL_INTERVAL - elapsed, 0.05)
        try:
            if stop_event is None:
                await asyncio.sleep(delay)
            else:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
        except asyncio.TimeoutError:
            pass
