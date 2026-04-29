import asyncio
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
from django.utils import timezone

from orders.ledger import record_balance_ledger
from bot.models import TelegramUser
from cloud.models import AddressMonitor, CloudServerOrder
from orders.models import Order, Product, Recharge
from orders.services import usdt_to_trx
from bot.keyboards import custom_port_keyboard
from cloud.provisioning import provision_cloud_server
from cloud.services import apply_cloud_server_renewal, run_cloud_server_renewal_postcheck
from core.cache import get_config, bump_daily_stats, get_daily_stats
from core.runtime_config import get_runtime_config
from core.persistence import bump_daily_address_stat, record_external_sync_log
from core.trongrid import build_trongrid_headers
from cloud.cache import get_monitor_addresses, maybe_sync_monitors, init_monitor_cache
from orders.tron_parser import parse_trx_transfer, parse_usdt_transfer

logger = logging.getLogger(__name__)

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
    detail_key = tx_hash[:16]
    _recent_tx_details[tx_hash] = detail
    _recent_tx_keys[detail_key] = tx_hash
    if len(_recent_tx_details) > MAX_TX_DETAIL_CACHE:
        old_tx_hash, _ = _recent_tx_details.popitem(last=False)
        old_keys = [key for key, value in _recent_tx_keys.items() if value == old_tx_hash]
        for key in old_keys:
            _recent_tx_keys.pop(key, None)


def _build_tx_detail_keyboard(tx_hash: str) -> InlineKeyboardMarkup:
    detail_key = tx_hash[:16]
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
    amount_units = int((Decimal(str(amount or 0)) * Decimal('1000000')).to_integral_value(rounding=ROUND_DOWN))
    await bump_daily_stats(address, currency, direction, amount=amount_units)
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
    cloud_count = CloudServerOrder.objects.filter(
        pay_method='address',
        status='pending',
        expired_at__isnull=False,
        expired_at__lte=now,
    ).update(status='expired')
    if product_count or recharge_count or cloud_count:
        logger.info('支付订单超时自动关闭: product=%s recharge=%s cloud=%s', product_count, recharge_count, cloud_count)
    return product_count, recharge_count, cloud_count


async def _expire_timed_out_payment_orders_periodically(force: bool = False):
    global _last_pending_expire_check_at
    now = time.time()
    if not force and now - _last_pending_expire_check_at < 60:
        return (0, 0, 0)
    _last_pending_expire_check_at = now
    return await _expire_timed_out_payment_orders()


@sync_to_async
def _get_pending_address_orders(currency: str):
    return list(
        Order.objects.filter(pay_method='address', status='pending', currency=currency)
        .order_by('created_at')
    )


@sync_to_async
def _get_pending_recharges(currency: str):
    return list(
        Recharge.objects.filter(status='pending', currency=currency)
        .order_by('created_at')
    )


@sync_to_async
def _get_pending_cloud_server_orders():
    return list(
        CloudServerOrder.objects.filter(pay_method='address', status='pending')
        .union(
            CloudServerOrder.objects.filter(
                pay_method='address',
                status='renew_pending',
            ).exclude(public_ip__isnull=True).exclude(public_ip='').exclude(status__in=['deleted', 'deleting', 'expired'])
        )
        .order_by('created_at')
    )


@sync_to_async
def _confirm_order_paid(order_id: int, tx_hash: str, payer_address: str = '', receive_address: str = ''):
    from django.db import transaction
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)
        if order.status != 'pending':
            return None
        order.status = 'paid'
        order.tx_hash = tx_hash
        order.payer_address = payer_address or ''
        order.receive_address = receive_address or ''
        order.paid_at = timezone.now()
        order.save(update_fields=['status', 'tx_hash', 'payer_address', 'receive_address', 'paid_at', 'updated_at'])
        product = order.product
        if product.stock != -1:
            product.stock -= order.quantity
            product.save(update_fields=['stock', 'updated_at'])
        order.status = 'delivered'
        order.save(update_fields=['status', 'updated_at'])
    return order


@sync_to_async
def _confirm_recharge(recharge_id: int, tx_hash: str, paid_amount=None, payer_address: str = '', receive_address: str = ''):
    from django.db import transaction
    actual_amount = Decimal(str(paid_amount or 0)).quantize(Decimal('0.000001'), rounding=ROUND_DOWN)
    with transaction.atomic():
        rc = Recharge.objects.select_for_update().get(id=recharge_id)
        if rc.status != 'pending':
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
    with transaction.atomic():
        order = CloudServerOrder.objects.select_for_update().get(id=order_id)
        if order.status not in {'pending', 'renew_pending'}:
            return None
        order.tx_hash = tx_hash
        order.payer_address = payer_address or ''
        order.receive_address = receive_address or ''
        order.paid_at = timezone.now()
        if order.status == 'renew_pending':
            if not str(order.public_ip or '').strip() or order.status in {'deleted', 'deleting', 'expired'}:
                return None
            order.save(update_fields=['tx_hash', 'payer_address', 'receive_address', 'paid_at', 'updated_at'])
            try:
                return apply_cloud_server_renewal.__wrapped__(order.id, order.lifecycle_days or 31, False)
            except Exception as exc:
                logger.warning('云服务器真实续费失败 order=%s err=%s', order.id, exc)
                return None
        order.status = 'paid'
        order.provision_note = '已收款，等待用户确认 MTProxy 端口后进入创建流程。默认端口为 9528。'
        order.save(update_fields=['status', 'tx_hash', 'payer_address', 'receive_address', 'paid_at', 'provision_note', 'updated_at'])
    return order


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


async def _cloud_renewal_postcheck_and_notify(order: CloudServerOrder):
    await _notify_user(order.user_id, '🔎 续费已完成，正在检查服务器运行状态和 MTProxy 链路。')
    checked, err = await run_cloud_server_renewal_postcheck(order.id)
    if getattr(checked, 'replacement_for_id', None) and checked.status in {'paid', 'provisioning', 'failed'}:
        asyncio.create_task(_provision_recovered_cloud_order(checked))
        return
    if err:
        await _notify_user(order.user_id, f'⚠️ 续费后巡检发现异常，已记录并尝试修复。\n订单号: {getattr(checked, "order_no", "-") or "-"}\n请稍后再查看代理状态，或联系人工客服。')
        return
    await _notify_user(order.user_id, f'✅ 续费后巡检完成。\n订单号: {getattr(checked, "order_no", "-") or "-"}\n服务器运行正常，MTProxy 主/备用端口正常。')


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


async def _notify_user(user_id: int, text: str, reply_markup=None, parse_mode: str | None = None):
    if _bot is None:
        return
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

async def _process_payment(transfer: dict) -> bool:
    amount = transfer['amount']
    tx_hash = transfer['tx_hash']
    currency = transfer['currency']

    pending_orders = await _get_pending_address_orders(currency)
    for order in pending_orders:
        if order.pay_amount == amount:
            confirmed = await _confirm_order_paid(order.id, tx_hash, transfer.get('from', ''), transfer.get('to', ''))
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

    now = timezone.now()
    pending_recharges = await _get_pending_recharges(currency)
    active_recharges = [
        rc for rc in pending_recharges
        if (getattr(rc, 'expired_at', None) and rc.expired_at >= now) or (not getattr(rc, 'expired_at', None) and rc.created_at >= now - timezone.timedelta(minutes=30))
    ]
    for rc in active_recharges:
        if rc.pay_amount == amount:
            confirmed = await _confirm_recharge(rc.id, tx_hash, amount, transfer.get('from', ''), transfer.get('to', ''))
            if confirmed:
                logger.info(
                    '💰 充值匹配 → user#%s  %s %s  tx=%s',
                    confirmed.user_id, fmt_amount(amount), currency, tx_hash,
                )
                await _notify_user(confirmed.user_id, f'✅ 充值成功！\n到账金额: {fmt_amount(confirmed.amount)} {currency}\n余额已更新。')
                return True
            return False
    logger.info('充值未匹配：currency=%s chain_amount=%s tx=%s active_pending=%s expired_pending=%s', currency, fmt_amount(amount), tx_hash, [(rc.id, rc.user_id, str(rc.amount), str(rc.pay_amount)) for rc in active_recharges[:10]], [(rc.id, rc.user_id, str(rc.amount), str(rc.pay_amount)) for rc in pending_recharges if rc not in active_recharges][:10])

    pending_cloud_orders = await _get_pending_cloud_server_orders()
    for order in pending_cloud_orders:
        payable = Decimal(str(order.pay_amount or order.total_amount or 0))
        expected_amount = usdt_to_trx.__wrapped__(payable) if currency == 'TRX' else payable
        if expected_amount == amount:
            confirmed = await _confirm_cloud_server_order(order.id, tx_hash, transfer.get('from', ''), transfer.get('to', ''))
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
                await _notify_user(
                    confirmed.user_id,
                    f'✅ 云服务器订单 {confirmed.order_no} 支付成功！\n'
                    f'地区: {confirmed.region_name}\n套餐: {confirmed.plan_name}\n'
                    '请选择 MTProxy 端口：默认端口是 9528，你也可以输入自定义端口。',
                    reply_markup=custom_port_keyboard(confirmed.id),
                )
                return True
            return False
    return False


async def _get_fee_text(tx_hash: str) -> str:
    try:
        headers = await build_trongrid_headers()
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f'{TRONGRID_BASE_URL}/wallet/gettransactioninfobyid',
                json={'value': tx_hash},
                headers=headers,
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

        text = (
            f'{title_icon} {title_word}{currency} 提醒  <code>{amount_prefix}{escape(fmt_amount(amount))} {escape(currency)}</code>\n\n'
            f'🏷️ 地址备注: {escape(remark)}\n\n'
            f'{peer_label}: <code>{escape(peer_addr)}</code>\n'
            f'{main_label}: <code>{escape(main_addr)}</code>\n'
            f'🕒 交易时间: <code>{escape(tx_time)}</code>\n'
            f'💰 交易金额: <code>{amount_prefix}{escape(fmt_amount(amount))} {escape(currency)}</code>\n'
            f'👛 USDT余额: {escape(fmt_amount(user.balance))} USDT\n'
            f'🪙 TRX余额: {escape(fmt_amount(user.balance_trx))} TRX\n'
            f'⛽ 转账消耗: <code>{escape(fee_text)}</code>\n\n'
            f'📈 今日收入: {escape(fmt_amount(income))} {escape(currency)}\n'
            f'📉 今日支出: {escape(fmt_amount(expense))} {escape(currency)}\n'
            f'💹 今日利润: {escape(fmt_amount(profit))} {escape(currency)}'
        )

        _cache_tx_detail(tx_hash, {
            'remark': remark, 'from': from_addr, 'to': to_addr,
            'time': tx_time, 'amount': f'{amount_prefix}{fmt_amount(amount)}',
            'currency': currency, 'tx_hash': tx_hash,
            'raw': transfer.get('raw_tx', ''), 'fee_text': fee_text,
            'direction': direction,
        })
        await _notify_user(mon['user_id'], text, reply_markup=_build_tx_detail_keyboard(tx_hash), parse_mode='HTML')


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
            stats = await _record_daily_stats(to_addr, transfer['currency'], 'income', transfer['amount'], monitor_cache[to_addr][0]['user_id'], monitor_cache[to_addr][0].get('id'))
            await _process_monitor_notification(transfer, monitor_cache[to_addr], stats, 'income')
        if from_addr in monitor_cache and from_addr != to_addr:
            stats = await _record_daily_stats(from_addr, transfer['currency'], 'expense', transfer['amount'], monitor_cache[from_addr][0]['user_id'], monitor_cache[from_addr][0].get('id'))
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
