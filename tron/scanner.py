import json
import logging
import os
import time
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal

import httpx
from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from asgiref.sync import sync_to_async
from django.utils import timezone

from monitors.models import AddressMonitor
from payments.models import Recharge
from shopbiz.models import Order, Product
from tron.monitor_cache import get_monitor_addresses, maybe_sync, init_monitor_cache
from tron.parser import parse_usdt_transfer, parse_trx_transfer
from users.models import TelegramUser

logger = logging.getLogger(__name__)

# ── 配置 ──────────────────────────────────────────────────────────────────

USDT_CONTRACT = os.getenv('USDT_CONTRACT', 'TR7NHqjeKQxGTCi8q8ZY4pL8otSzgjLj6t')
TRONGRID_BASE_URL = os.getenv('TRONGRID_BASE_URL', 'https://api.trongrid.io')
SCANNER_VERBOSE = os.getenv('SCANNER_VERBOSE', '0') == '1'

# ── 内部状态 ──────────────────────────────────────────────────────────────

_processed_blocks: OrderedDict[str, bool] = OrderedDict()
MAX_CACHE = 200

_last_scan_summary_at: float = 0
_SCAN_SUMMARY_INTERVAL = 600
_scan_stats = {'blocks': 0, 'transactions': 0, 'transfers': 0, 'payments': 0, 'monitor_hits': 0}

_recent_tx_details: OrderedDict[str, dict] = OrderedDict()
MAX_TX_DETAIL_CACHE = 500

_bot: Bot | None = None


# ── 公开接口 ──────────────────────────────────────────────────────────────

def set_bot(bot: Bot):
    global _bot
    _bot = bot


def get_tx_detail(tx_hash: str) -> dict | None:
    return _recent_tx_details.get(tx_hash)


def reload_config():
    """热重载扫描器配置（由外部调用）。"""
    global SCANNER_VERBOSE
    SCANNER_VERBOSE = os.getenv('SCANNER_VERBOSE', '0') == '1'


# ── 内部辅助 ──────────────────────────────────────────────────────────────

def _receive_address() -> str:
    from core.models import SiteConfig
    return SiteConfig.get('receive_address', '')


def _trongrid_api_key() -> str:
    from core.models import SiteConfig
    return SiteConfig.get('trongrid_api_key', '')


def _cache_tx_detail(tx_hash: str, detail: dict):
    _recent_tx_details[tx_hash] = detail
    if len(_recent_tx_details) > MAX_TX_DETAIL_CACHE:
        _recent_tx_details.popitem(last=False)


def _build_tx_detail_keyboard(tx_hash: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='查看交易详情', callback_data=f'mon:txdetail:{tx_hash}')]]
    )


def fmt_amount(value) -> str:
    if value is None:
        return ''
    text = str(Decimal(value))
    if '.' in text:
        text = text.rstrip('0').rstrip('.')
    return text or '0'


def _now_str() -> str:
    return datetime.now().strftime('%H:%M:%S')


def _short_addr(addr: str) -> str:
    if len(addr) > 14:
        return f'{addr[:6]}...{addr[-4:]}'
    return addr


# ── DB access (sync → async) ────────────────────────────────────────────

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
def _confirm_order_paid(order_id: int, tx_hash: str):
    from django.db import transaction
    with transaction.atomic():
        order = Order.objects.select_for_update().get(id=order_id)
        if order.status != 'pending':
            return None
        order.status = 'paid'
        order.tx_hash = tx_hash
        order.paid_at = timezone.now()
        order.save(update_fields=['status', 'tx_hash', 'paid_at', 'updated_at'])
        product = order.product
        if product.stock != -1:
            product.stock -= order.quantity
            product.save(update_fields=['stock', 'updated_at'])
        order.status = 'delivered'
        order.save(update_fields=['status', 'updated_at'])
    return order


@sync_to_async
def _confirm_recharge(recharge_id: int, tx_hash: str):
    from django.db import transaction
    with transaction.atomic():
        rc = Recharge.objects.select_for_update().get(id=recharge_id)
        if rc.status != 'pending':
            return None
        rc.status = 'completed'
        rc.tx_hash = tx_hash
        rc.completed_at = timezone.now()
        rc.save(update_fields=['status', 'tx_hash', 'completed_at', 'updated_at'])
        user = TelegramUser.objects.select_for_update().get(id=rc.user_id)
        field = 'balance_trx' if rc.currency == 'TRX' else 'balance'
        setattr(user, field, getattr(user, field) + rc.amount)
        user.save(update_fields=[field, 'updated_at'])
    return rc


@sync_to_async
def _get_user(user_id: int):
    return TelegramUser.objects.filter(id=user_id).first()


@sync_to_async
def _get_product(product_id: int):
    return Product.objects.filter(id=product_id).first()


# ── 通知 ──────────────────────────────────────────────────────────────────

async def _notify_user(user_id: int, text: str, reply_markup=None):
    if _bot is None:
        return
    try:
        user = await _get_user(user_id)
        if user:
            await _bot.send_message(chat_id=user.tg_user_id, text=text, reply_markup=reply_markup)
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
            confirmed = await _confirm_order_paid(order.id, tx_hash)
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

    pending_recharges = await _get_pending_recharges(currency)
    for rc in pending_recharges:
        if rc.pay_amount == amount:
            confirmed = await _confirm_recharge(rc.id, tx_hash)
            if confirmed:
                logger.info(
                    '💰 充值匹配 → user#%s  %s %s  tx=%s',
                    confirmed.user_id, fmt_amount(amount), currency, tx_hash,
                )
                await _notify_user(rc.user_id, f'✅ 充值成功！\n金额: {fmt_amount(rc.amount)} {currency}\n余额已更新。')
                return True
            return False
    return False


# ── 监控通知 ──────────────────────────────────────────────────────────────

async def _process_monitor_notification(transfer: dict, monitors: list[dict]):
    amount = transfer['amount']
    currency = transfer['currency']
    from_addr = transfer['from']
    to_addr = transfer['to']
    tx_hash = transfer['tx_hash']
    tx_time = transfer.get('timestamp') or datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for mon in monitors:
        threshold = Decimal(mon.get('usdt_threshold', '1') if currency == 'USDT' else mon.get('trx_threshold', '1'))
        if threshold and amount < threshold:
            continue

        _scan_stats['monitor_hits'] += 1
        user = await _get_user(mon['user_id'])
        if not user:
            continue

        remark = mon.get('remark') or '(无备注)'
        fee_text = transfer.get('fee_text', '未知')

        # 控制台日志：仅命中有格式化的详情
        logger.info(
            '\n'
            '  ┌─ 监控命中 ─────────────────────────────\n'
            '  │ 地址备注 : %s\n'
            '  │ 监控地址 : %s\n'
            '  │ 付款方   : %s\n'
            '  │ 交易时间 : %s\n'
            '  │ 币种     : %s\n'
            '  │ 金额     : +%s %s\n'
            '  │ 手续费   : %s\n'
            '  │ TX Hash  : %s\n'
            '  └────────────────────────────────────────',
            remark, to_addr, _short_addr(from_addr),
            tx_time, currency, fmt_amount(amount), currency,
            fee_text, tx_hash,
        )

        # Telegram 通知卡片
        text = (
            f'🟢 收入{currency} +{fmt_amount(amount)} {currency}\n\n'
            f'备注 : {remark}\n'
            f'来自 : {from_addr}\n'
            f'收款 : {to_addr}\n'
            f'时间 : {tx_time}\n'
            f'金额 : +{fmt_amount(amount)} {currency}\n'
            f'手续费 : {fee_text}\n\n'
            f'USDT 余额 : {fmt_amount(user.balance)}\n'
            f'TRX  余额 : {fmt_amount(user.balance_trx)}'
        )

        _cache_tx_detail(tx_hash, {
            'remark': remark, 'from': from_addr, 'to': to_addr,
            'time': tx_time, 'amount': fmt_amount(amount),
            'currency': currency, 'tx_hash': tx_hash,
            'raw': transfer.get('raw_tx', ''), 'fee_text': fee_text,
        })
        await _notify_user(mon['user_id'], text, reply_markup=_build_tx_detail_keyboard(tx_hash))


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

async def scan_block():
    try:
        headers = {'accept': 'application/json', 'content-type': 'application/json'}
        api_key = _trongrid_api_key()
        if api_key:
            headers['TRON-PRO-API-KEY'] = api_key

        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f'{TRONGRID_BASE_URL}/wallet/getnowblock', json={'detail': True}, headers=headers)
            resp.raise_for_status()
            block_data = resp.json()

        block_id = block_data.get('blockID', '')
        if not block_id or block_id in _processed_blocks:
            await _log_scan_summary()
            return

        _processed_blocks[block_id] = True
        _scan_stats['blocks'] += 1
        if len(_processed_blocks) > MAX_CACHE:
            _processed_blocks.popitem(last=False)

        transactions = block_data.get('transactions', [])
        _scan_stats['transactions'] += len(transactions)

        # 静默模式：只打印时间和区块号
        if SCANNER_VERBOSE:
            logger.info('[scan] %s block=%s txs=%d', _now_str(), block_id[:16], len(transactions))

        if not transactions:
            await _log_scan_summary()
            return

        # 定时同步 Redis 缓存
        await maybe_sync()
        monitor_cache = await get_monitor_addresses()

        receive_address = _receive_address()

        for tx in transactions:
            transfer = parse_usdt_transfer(tx, USDT_CONTRACT)
            if transfer is None:
                transfer = parse_trx_transfer(tx)
            if transfer is None:
                continue

            _scan_stats['transfers'] += 1
            to_addr = transfer['to']

            # 支付匹配
            if receive_address and to_addr == receive_address:
                matched = await _process_payment(transfer)
                if matched:
                    _scan_stats['payments'] += 1

            # 监控通知（有格式化详情日志）
            if to_addr in monitor_cache:
                await _process_monitor_notification(transfer, monitor_cache[to_addr])

        await _log_scan_summary()
    except Exception as e:
        logger.error('扫块异常: %s', e)
