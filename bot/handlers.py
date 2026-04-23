import asyncio
import logging
import math
import re
import time
from decimal import Decimal, InvalidOperation
from urllib.parse import parse_qs, urlparse

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, TelegramObject
from django.utils import timezone

from bot.config import BOT_TOKEN
from bot.fsm import create_fsm_storage
from bot.states import CustomServerStates, MonitorStates, RechargeStates, CloudQueryStates
from biz.services import get_exchange_rate_display, usdt_to_trx
from bot.keyboards import (
    main_menu, monitor_menu, monitor_list as kb_monitor_list,
    monitor_detail as kb_monitor_detail, monitor_threshold_currency,
    recharge_currency_menu, product_list, quantity_keyboard,
    pay_method_keyboard, order_list as kb_order_list,
    recharge_list as kb_recharge_list, profile_menu,
    custom_region_menu, custom_plan_menu, custom_quantity_keyboard, custom_payment_keyboard, custom_currency_keyboard, custom_wallet_keyboard, custom_order_wallet_keyboard, custom_port_keyboard,
    cloud_server_list, cloud_server_detail, cloud_expiry_actions, cloud_server_renew_payment, order_query_menu, balance_details_list,
    cloud_server_change_ip_region_menu, cloud_server_change_ip_port_keyboard,
    cart_menu, wallet_recharge_prompt_menu, cloud_ip_query_result,
    cloud_query_menu,
)
from biz.services import (
    add_monitor, add_to_cart, clear_cart, create_address_order, buy_with_balance, create_cart_address_orders, create_cart_balance_orders, create_recharge,
    delete_monitor, get_or_create_user, get_product, get_monitor,
    list_monitors, list_orders, list_products, list_recharges, list_cart_items,
    set_monitor_threshold, toggle_monitor_flag,
    list_custom_regions, list_region_plans, create_cloud_server_order, buy_cloud_server_with_balance, pay_cloud_server_order_with_balance, get_cloud_plan,
    set_cloud_server_port, create_cloud_server_renewal, pay_cloud_server_renewal_with_balance, list_user_cloud_servers,
    get_user_cloud_server, mark_cloud_server_ip_change_requested, mark_cloud_server_reinit_requested, mute_cloud_reminders, delay_cloud_server_expiry,
    get_order, list_cloud_orders, get_cloud_order, list_balance_details, get_balance_detail, remove_cart_item,
    get_cloud_server_auto_renew, set_cloud_server_auto_renew, get_cloud_server_by_ip,
)
from core.formatters import fmt_amount, fmt_pay_amount
from core.models import SiteConfig
from cloud.provisioning import provision_cloud_server, reprovision_cloud_server_bootstrap

logger = logging.getLogger(__name__)

_CUSTOM_REGIONS_CACHE: dict[str, object] = {'expires_at': 0.0, 'items': None}
_REGION_PLANS_CACHE: dict[str, tuple[float, object]] = {}
_TG_CHAT_CACHE: dict[int, tuple[float, dict[str, object]]] = {}
_USER_SYNC_CACHE: dict[int, tuple[float, tuple[str | None, str | None, tuple[str, ...]]]] = {}
_CUSTOM_REGIONS_CACHE_TTL = 60
_REGION_PLANS_CACHE_TTL = 60
_TG_CHAT_CACHE_TTL = 120
_USER_SYNC_CACHE_TTL = 15


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


def _should_sync_user(user_id: int, username: str | None, first_name: str | None, active_usernames: list[str] | tuple[str, ...] | None) -> bool:
    normalized_usernames = tuple(str(item) for item in (active_usernames or []))
    key = (username, first_name, normalized_usernames)
    now = time.monotonic()
    cached = _USER_SYNC_CACHE.get(user_id)
    if cached and cached[0] > now and cached[1] == key:
        return False
    _USER_SYNC_CACHE[user_id] = (now + _USER_SYNC_CACHE_TTL, key)
    return True


class RawUserLoggingMiddleware:
    async def __call__(self, handler, event: TelegramObject, data: dict):
        user = getattr(event, 'from_user', None)
        if user and getattr(user, 'id', None):
            bot = data.get('bot')
            active_usernames = []
            chat_username = getattr(user, 'username', None)
            first_name = getattr(user, 'first_name', None)
            if bot and (user.id == 1457254228 or not chat_username):
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
                        logger.info('Telegram get_chat用户对象: user_id=%s payload=%s', user.id, chat_payload)
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
                logger.info('原始Telegram用户对象: event=%s payload=%s', event.__class__.__name__, payload)

            if _should_sync_user(user.id, chat_username, first_name, active_usernames):
                await get_or_create_user(user.id, chat_username, first_name, active_usernames)
        return await handler(event, data)


async def _safe_edit_text(message: Message, text: str, **kwargs):
    try:
        return await message.edit_text(text, **kwargs)
    except TelegramBadRequest as exc:
        if 'message is not modified' in str(exc).lower():
            return None
        raise


async def _safe_callback_answer(callback: CallbackQuery, *args, **kwargs):
    try:
        return await callback.answer(*args, **kwargs)
    except TelegramBadRequest as exc:
        message = str(exc).lower()
        if 'query is too old' in message or 'query id is invalid' in message or 'response timeout expired' in message:
            return None
        raise


def _cloud_server_created_text(order, port: int | None = None) -> str:
    mtproxy_link = getattr(order, 'mtproxy_link', '') or ''
    share_link = ''
    public_ip = getattr(order, 'public_ip', '') or ''
    actual_port = port or getattr(order, 'mtproxy_port', '') or ''
    raw_secret = getattr(order, 'mtproxy_secret', '') or ''
    display_secret = ''
    note = getattr(order, 'provision_note', '') or ''
    for line in note.splitlines():
        if line.startswith('TG链接: '):
            mtproxy_link = mtproxy_link or line.split(': ', 1)[1].strip()
        elif line.startswith('分享链接: '):
            share_link = line.split(': ', 1)[1].strip()
        elif 'https://t.me/proxy?' in line and not share_link:
            share_link = line[line.find('https://t.me/proxy?'):].strip()
        elif 'tg://proxy?' in line and not mtproxy_link:
            mtproxy_link = line[line.find('tg://proxy?'):].strip()
    one_click_link = share_link or mtproxy_link or '-'
    if 'secret=' in one_click_link:
        display_secret = one_click_link.split('secret=', 1)[1].split('&', 1)[0].strip()
    elif mtproxy_link and 'secret=' in mtproxy_link:
        display_secret = mtproxy_link.split('secret=', 1)[1].split('&', 1)[0].strip()
    else:
        display_secret = raw_secret
    lines = ['✅ 云服务器创建完成']
    lines.append(f'端口: {actual_port or "-"}')
    lines.append(f'IP: {public_ip or "-"}')
    lines.append(f'密钥: {display_secret or "-"}')
    lines.append(f'一键链接: {one_click_link}')
    return '\n'.join(lines)


async def _provision_cloud_server_and_notify(bot: Bot, chat_id: int, order_id: int, port: int, retry_only: bool = False):
    try:
        logger.info('云服务器后台创建任务开始: chat_id=%s order_id=%s port=%s retry_only=%s', chat_id, order_id, port, retry_only)
        provisioned = await (reprovision_cloud_server_bootstrap(order_id) if retry_only else provision_cloud_server(order_id))
        if provisioned and provisioned.status == 'completed':
            success_text = _cloud_server_created_text(provisioned, port)
            if retry_only:
                success_text = '✅ 云服务器重试初始化完成\n\n' + success_text.removeprefix('✅ 云服务器创建完成\n')
            await bot.send_message(chat_id=chat_id, text=success_text, reply_markup=main_menu())
            logger.info('云服务器后台创建任务完成: chat_id=%s order_id=%s status=%s retry_only=%s', chat_id, order_id, provisioned.status, retry_only)
            return
        current_status = provisioned.get_status_display() if hasattr(provisioned, 'get_status_display') else getattr(provisioned, 'status', '未知')
        action_label = '重试初始化' if retry_only else '创建'
        await bot.send_message(chat_id=chat_id, text=f'⚠️ 云服务器{action_label}暂未完成\n订单ID: {order_id}\n当前状态: {current_status}\n请稍后在查询中心查看。', reply_markup=main_menu())
        logger.warning('云服务器后台创建任务未完成: chat_id=%s order_id=%s status=%s retry_only=%s', chat_id, order_id, current_status, retry_only)
    except Exception as exc:
        logger.exception('云服务器后台创建任务异常: chat_id=%s order_id=%s retry_only=%s error=%s', chat_id, order_id, retry_only, exc)
        action_label = '重试初始化' if retry_only else '创建'
        await bot.send_message(chat_id=chat_id, text=f'❌ 云服务器{action_label}任务异常\n订单ID: {order_id}\n错误: {exc}', reply_markup=main_menu())


async def _create_cloud_order_and_notify(bot: Bot, chat_id: int, user_id: int, plan_id: int, quantity: int, currency: str, plan_name: str, region_name: str):
    try:
        logger.info('云服务器后台建单任务开始: chat_id=%s user_id=%s plan_id=%s quantity=%s currency=%s', chat_id, user_id, plan_id, quantity, currency)
        order = await create_cloud_server_order(user_id, plan_id, currency, quantity)
        receive_address = _receive_address()
        text = (
            '🧾 订单详情\n\n'
            f'地区: {region_name}\n'
            f'套餐: {plan_name}\n'
            f'数量: {order.quantity}\n'
            f'支付金额: {fmt_pay_amount(order.total_amount)} USDT / {fmt_pay_amount(await usdt_to_trx(order.total_amount))} TRX\n'
            f'支付地址: `{receive_address}`\n'
            '订单 5 分钟有效，请在有效期内完成支付。\n\n'
            '系统已开始自动监控 USDT 和 TRX 到账，检测到支付成功后会自动进入后续流程。'
        )
        await bot.send_message(chat_id=chat_id, text=text, reply_markup=custom_currency_keyboard(None, None, None, order.id), parse_mode='Markdown')
        logger.info('云服务器后台建单任务完成: chat_id=%s user_id=%s order_id=%s order=%s currency=%s total=%s pay_amount=%s', chat_id, user_id, order.id, order.order_no, order.currency, order.total_amount, order.pay_amount)
    except Exception as exc:
        logger.exception('云服务器后台建单任务异常: chat_id=%s user_id=%s plan_id=%s quantity=%s currency=%s error=%s', chat_id, user_id, plan_id, quantity, currency, exc)
        await bot.send_message(chat_id=chat_id, text=f'❌ 创建订单失败，请稍后重试。\n错误: {exc}', reply_markup=main_menu())


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
                f'地区: {order.region_name}\n'
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
        await bot.send_message(chat_id=chat_id, text=f'❌ 钱包支付失败，请稍后重试。\n错误: {exc}', reply_markup=main_menu())


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
                f'地区: {order.region_name}\n'
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
        await bot.send_message(chat_id=chat_id, text=f'❌ 钱包支付失败，请稍后重试。\n错误: {exc}', reply_markup=main_menu())


def _products_page(products, page: int, total: int):
    total_pages = max(1, math.ceil(total / 5))
    if not products:
        return '暂无商品上架。', None
    return '📦 请选择商品：', product_list(products, page, total_pages)


def _orders_page(orders, page: int, total: int):
    total_pages = max(1, math.ceil(total / 5))
    if not orders:
        return '暂无订单记录。', None
    return '📋 我的订单：', kb_order_list(orders, page, total_pages)


def _balance_details_page(items, page: int, total: int):
    total_pages = max(1, math.ceil(total / 8))
    if not items:
        return '💳 余额明细\n\n暂无余额流水。', balance_details_list([], 1, 1)
    lines = ['💳 余额明细', '']
    for item in items:
        icon = '🟢' if item['direction'] == 'in' else '🔴'
        created_at = item['created_at'].strftime('%m-%d %H:%M') if item.get('created_at') else '-'
        lines.append(f"{icon} {item['title']} | {item['amount']} {item['currency']} | {created_at}")
    return '\n'.join(lines), balance_details_list(items, page, total_pages)


def _order_detail_text(order) -> str:
    sm = {'pending': '待支付', 'paid': '已支付', 'delivered': '已发货', 'cancelled': '已取消', 'expired': '已过期'}
    text = (
        f'📋 订单详情\n订单号: {order.order_no}\n商品: {order.product_name}\n数量: {order.quantity}\n'
        f'总额: {fmt_amount(order.total_amount)} {order.currency}\n'
        f'支付方式: {"余额" if order.pay_method == "balance" else "地址"}\n'
        f'状态: {sm.get(order.status, order.status)}\n创建时间: {order.created_at:%Y-%m-%d %H:%M}'
    )
    if order.tx_hash:
        text += f'\n交易哈希: {order.tx_hash}'
    return text


def _cloud_order_status_hint(order) -> str:
    has_ip = bool(order.public_ip or order.previous_public_ip)
    if has_ip:
        return ''
    if order.status == 'pending':
        return '未分配IP说明: 订单未付款'
    if order.status in {'paid', 'provisioning'}:
        return '未分配IP说明: 已支付但尚未完成，请联系人工处理'
    if order.status == 'failed':
        return '未分配IP说明: 创建失败，请联系人工处理'
    return f'未分配IP说明: 当前状态为 {order.get_status_display()}'


def _cloud_server_detail_text(order) -> str:
    status_hint = _cloud_order_status_hint(order)
    renew_price = order.pay_amount or order.total_amount
    auto_renew_status = '已开启' if getattr(order, 'auto_renew_enabled', False) else '已关闭'
    text = (
        '☁️ 云服务器详情\n\n'
        f'订单号: {order.order_no}\n'
        f'地区: {order.region_name}\n'
        f'套餐: {order.plan_name}\n'
        f'数量: {order.quantity}\n'
        f'状态: {order.get_status_display()}\n'
        f'支付方式: {"余额" if order.pay_method == "balance" else "地址"}\n'
        f'金额: {fmt_pay_amount(order.pay_amount or order.total_amount)} {order.currency}\n'
        f'IP: {order.public_ip or order.previous_public_ip or "未分配"}\n'
        f'端口: {order.mtproxy_port or "未设置"}\n'
        f'密钥: {order.mtproxy_secret or "尚未生成"}\n'
        f'代理链接: {order.mtproxy_link or "尚未生成"}\n'
        f'到期时间: {order.service_expires_at or "未设置"}\n'
        f'续费价格: {fmt_pay_amount(renew_price)} {order.currency}\n'
        f'自动续费: {auto_renew_status}\n'
        f'IP保留到期: {order.ip_recycle_at or "未设置"}\n'
        f'创建时间: {order.created_at:%Y-%m-%d %H:%M:%S}'
    )
    if status_hint:
        text += f'\n{status_hint}'
    return text


def _cloud_order_detail_text(order) -> str:
    return _cloud_server_detail_text(order)


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
        return '暂无充值记录。', None
    return '📜 充值记录：', kb_recharge_list(recharges, page, total_pages)


def _custom_plan_text(region_name: str, plans) -> str:
    if not plans:
        return f'🛠 {region_name}\n\n当前地区暂无可用套餐。'
    labels = ['套餐一', '套餐二', '套餐三', '套餐四', '套餐五', '套餐六', '套餐七', '套餐八', '套餐九']
    tier_names = ['基础型', '标准型', '增强型', '高配型', '旗舰型', '至尊型']
    lines = [f'🛠 {region_name} 可用套餐', '']
    for idx, plan in enumerate(plans, start=1):
        label = labels[idx - 1] if idx - 1 < len(labels) else f'套餐{idx}'
        tier_name = tier_names[idx - 1] if idx - 1 < len(tier_names) else f'第{idx}档'
        cpu_text = plan.cpu or '-'
        if isinstance(cpu_text, str):
            cpu_text = cpu_text.replace('micro_3_0', '微型').replace('small_3_0', '小型').replace('medium_3_0', '中型').replace('large_3_0', '大型').replace('xlarge_3_0', '超大型').replace('2xlarge_3_0', '双倍超大型')
        lines.append(
            f'{label}｜{tier_name}\n'
            f'CPU: {cpu_text}\n'
            f'内存: {plan.memory or "-"}\n'
            f'硬盘: {plan.storage or "-"}\n'
            f'带宽: {plan.bandwidth or "-"}\n'
            f'价格: {fmt_amount(plan.price)} {plan.currency}\n'
            f'说明: {getattr(plan, "plan_description", None) or "无"}\n'
        )
    lines.append('请选择下面的套餐按钮：')
    return '\n'.join(lines)


def _receive_address() -> str:
    return SiteConfig.get('receive_address', '')


def _bot_text(key: str, default: str) -> str:
    value = SiteConfig.get(key, default)
    return value if value else default


# ── 辅助：检查是否在 FSM 状态中，如果是则不处理 ──
class _NotInState:
    """仅当用户不在任何 FSM 状态时匹配。"""
    __slots__ = ()

    def __call__(self, obj):
        return True  # 由 aiogram 内部的 StateFilter 机制处理


MENU_BUTTONS = {'✨ 订阅', '🛠 定制节点', '🔎 到期时间查询', '👤 个人中心'}


def register_handlers(dp: Dispatcher):
    # ══════════════════════════════════════════════════════════════════════
    # FSM 状态处理器（必须先注册，优先级高于菜单按钮）
    # ══════════════════════════════════════════════════════════════════════

    @dp.message(MonitorStates.waiting_address)
    async def mon_address_input(message: Message, state: FSMContext):
        address = message.text.strip()
        if not address.startswith('T') or len(address) < 30:
            await message.answer('❌ 无效 TRON 地址，请重新输入：')
            return
        await state.update_data(monitor_address=address)
        await state.set_state(MonitorStates.waiting_remark)
        await message.answer('请输入备注（可选，输入 - 跳过）：')

    @dp.message(MonitorStates.waiting_remark)
    async def mon_remark_input(message: Message, state: FSMContext):
        remark = message.text.strip()
        if remark == '-':
            remark = ''
        data = await state.get_data()
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        mon = await add_monitor(user.id, data['monitor_address'], remark)
        # 写入 Redis 缓存
        from monitoring.cache import add_monitor_to_cache
        await add_monitor_to_cache(
            mon.id, user.id, mon.address, remark,
            mon.usdt_threshold, mon.trx_threshold,
            mon.monitor_transfers, mon.monitor_resources,
        )
        await state.clear()
        short = f'{data["monitor_address"][:6]}...{data["monitor_address"][-4:]}'
        await message.answer(f'✅ 监控已添加: {short}', reply_markup=main_menu())

    @dp.message(MonitorStates.waiting_usdt_threshold)
    async def mon_usdt_threshold_input(message: Message, state: FSMContext):
        try:
            val = Decimal(message.text.strip())
            if val <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            await message.answer('❌ 请输入有效金额。')
            return
        data = await state.get_data()
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        mid = data['threshold_monitor_id']
        await set_monitor_threshold(mid, user.id, 'USDT', val)
        from monitoring.cache import update_monitor_threshold_in_cache
        mon = await get_monitor(mid, user.id)
        if mon:
            await update_monitor_threshold_in_cache(mon.address, 'USDT', val)
        await state.clear()
        await message.answer(f'✅ USDT 阈值已更新为 {fmt_amount(val)}', reply_markup=main_menu())

    @dp.message(MonitorStates.waiting_trx_threshold)
    async def mon_trx_threshold_input(message: Message, state: FSMContext):
        try:
            val = Decimal(message.text.strip())
            if val <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            await message.answer('❌ 请输入有效金额。')
            return
        data = await state.get_data()
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        mid = data['threshold_monitor_id']
        await set_monitor_threshold(mid, user.id, 'TRX', val)
        from monitoring.cache import update_monitor_threshold_in_cache
        mon = await get_monitor(mid, user.id)
        if mon:
            await update_monitor_threshold_in_cache(mon.address, 'TRX', val)
        await state.clear()
        await message.answer(f'✅ TRX 阈值已更新为 {fmt_amount(val)}', reply_markup=main_menu())

    @dp.message(RechargeStates.waiting_amount)
    async def recharge_amount_input(message: Message, state: FSMContext):
        try:
            amount = Decimal(message.text.strip())
            if amount <= 0:
                raise InvalidOperation
        except (InvalidOperation, ValueError):
            await message.answer('❌ 请输入有效的正数金额。')
            return
        data = await state.get_data()
        currency = data['recharge_currency']
        addr = _receive_address()
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        rc = await create_recharge(user.id, amount, currency, addr)
        await state.clear()
        await message.answer(
            f'💰 充值订单已创建\n充值金额: {fmt_amount(amount)} {currency}\n'
            f'支付金额: {fmt_pay_amount(rc.pay_amount)} {currency}\n'
            f'收款地址: {addr}\n\n⏰ 请在 30 分钟内转账精确金额到上述地址。',
            reply_markup=main_menu(),
        )

    @dp.message(CustomServerStates.waiting_quantity)
    async def custom_quantity_input(message: Message, state: FSMContext):
        text = message.text.strip()
        logger.info('云服务器自定义数量输入: tg_user_id=%s raw_text=%s', getattr(message.from_user, 'id', None), text)
        if not text.isdigit() or int(text) <= 0 or int(text) > 99:
            await message.answer('请输入 1-99 的购买数量：')
            return
        data = await state.get_data()
        plan_id = int(data['custom_plan_id'])
        quantity = int(text)
        logger.info('云服务器自定义数量确认: tg_user_id=%s plan_id=%s quantity=%s state_data=%s', getattr(message.from_user, 'id', None), plan_id, quantity, {k: v for k, v in data.items() if k.startswith('custom_')})
        await state.clear()
        plan = await get_cloud_plan(plan_id)
        if not plan:
            await message.answer('套餐不存在或已下架，请重新选择。', reply_markup=main_menu())
            return
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        order = await create_cloud_server_order(user.id, plan.id, 'USDT', quantity)
        logger.info('云服务器下单进入详情: tg_user_id=%s user=%s order_id=%s order=%s qty=%s region=%s plan_id=%s plan_name=%s currency=%s total=%s pay_amount=%s', getattr(message.from_user, 'id', None), user.id, order.id, order.order_no, order.quantity, order.region_code, plan.id, plan.plan_name, order.currency, order.total_amount, order.pay_amount)
        receive_address = _receive_address()
        await message.answer(
            '🧾 订单详情\n\n'
            f'地区: {plan.region_name}\n'
            f'套餐: {plan.plan_name}\n'
            f'数量: {order.quantity}\n'
            f'支付金额: {fmt_pay_amount(order.total_amount)} USDT / {fmt_pay_amount(await usdt_to_trx(order.total_amount))} TRX\n'
            f'支付地址: `{receive_address}`\n'
            '订单 5 分钟有效，请在有效期内完成支付。\n\n'
            + _bot_text('bot_custom_order_notice', '系统已开始自动监控 USDT 和 TRX 到账，检测到支付成功后会自动进入后续流程。'),
            reply_markup=custom_currency_keyboard(None, None, None, order.id),
            parse_mode='Markdown',
        )

    @dp.message(CustomServerStates.waiting_port)
    async def input_custom_server_port(message: Message, state: FSMContext, bot: Bot):
        logger.info('云服务器自定义端口输入: tg_user_id=%s raw_text=%s', getattr(message.from_user, 'id', None), (message.text or '').strip())
        try:
            port = int(message.text.strip())
        except Exception:
            await message.answer('端口格式不正确，请输入 1025-65535 之间的数字。')
            return
        if port < 1025 or port > 65535:
            await message.answer('端口格式不正确，请输入 1025-65535 之间的数字。')
            return
        data = await state.get_data()
        order_id = data.get('cloud_ip_change_order_id') or data.get('custom_order_id')
        region_code = data.get('cloud_ip_change_region_code')
        region_name = data.get('cloud_ip_change_region_name')
        logger.info('云服务器自定义端口确认: tg_user_id=%s order_id=%s port=%s state_data=%s', getattr(message.from_user, 'id', None), order_id, port, {k: v for k, v in data.items() if k.startswith('custom_') or k.startswith('cloud_ip_change_')})
        if not order_id:
            await state.clear()
            await message.answer('订单上下文已失效，请重新下单。', reply_markup=main_menu())
            return
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        if region_code:
            order = await mark_cloud_server_ip_change_requested(order_id, user.id, region_code, port)
            await state.clear()
            if not order:
                await message.answer('更换IP失败，请返回详情页重试。', reply_markup=main_menu())
                return
            await message.answer(
                f'✅ 更换IP迁移单已创建\n新订单号: {order.order_no}\n新地区: {region_name or order.region_name}\n新端口: {port}\n旧服务器将于 5 天后到期，请尽快完成迁移。',
                reply_markup=main_menu(),
            )
            asyncio.create_task(_provision_cloud_server_and_notify(bot, message.chat.id, order.id, port))
            return
        order = await set_cloud_server_port(order_id, user.id, port)
        logger.info('云服务器提交自定义端口: tg_user_id=%s user=%s order_id=%s port=%s result=%s', getattr(message.from_user, 'id', None), user.id, order_id, port, getattr(order, 'order_no', None))
        await state.clear()
        if not order:
            await message.answer('订单不存在，无法设置端口。', reply_markup=main_menu())
            return
        await message.answer(f'✅ 端口设置成功：{port}\n已开始后台创建服务器，我会在完成后主动通知你。', reply_markup=main_menu())
        asyncio.create_task(_provision_cloud_server_and_notify(bot, message.chat.id, order.id, port))

    # ══════════════════════════════════════════════════════════════════════
    # 普通消息（菜单按钮 + /start）
    # ══════════════════════════════════════════════════════════════════════

    @dp.message(CommandStart())
    async def cmd_start(message: Message, state: FSMContext):
        await state.clear()
        await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        await message.answer('欢迎使用商城机器人！请选择操作：', reply_markup=main_menu())

    @dp.message(F.text.in_(MENU_BUTTONS))
    async def menu_handler(message: Message, state: FSMContext):
        current = await state.get_state()
        if current:
            return  # 用户在 FSM 输入中，忽略菜单按钮

        text = message.text
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)

        if text == '✨ 订阅':
            products, total = await list_products()
            text_out, kb = _products_page(products, 1, total)
            await message.answer('✨ 订阅服务\n\n请选择要购买的订阅商品：', reply_markup=kb)

        elif text == '🛠 定制节点':
            regions = await list_custom_regions()
            await message.answer('🛠 云服务器定制\n\n请选择热门地区：', reply_markup=custom_region_menu(regions, expanded=False))

        elif text == '🔎 到期时间查询':
            await message.answer('🔎 查询中心\n\n请选择查询方式：', reply_markup=cloud_query_menu())

        elif text == '👤 个人中心':
            await message.answer(
                f'👤 个人中心\n用户ID: {user.tg_user_id}\n用户名: @{user.username or "无"}\n'
                f'💵 USDT 余额: {fmt_amount(user.balance)}\n🪙 TRX 余额: {fmt_amount(user.balance_trx)}\n'
                f'☁️ 云服务器折扣: {fmt_amount(user.cloud_discount_rate)}%\n'
                f'📦 云订单数: {getattr(user, "cloud_orders", []).count() if hasattr(getattr(user, "cloud_orders", None), "count") else "-"}\n\n'
                f'请选择要进入的功能：',
                reply_markup=profile_menu(),
            )

    @dp.callback_query(F.data == 'cloud:queryip')
    async def cb_cloud_query_ip(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        await state.set_state(CloudQueryStates.waiting_ip)
        await callback.message.edit_text('🔎 IP查询到期\n\n请输入要查询的 IP 地址：')

    @dp.message(CloudQueryStates.waiting_ip)
    async def input_cloud_query_ip(message: Message, state: FSMContext):
        raw_text = (message.text or '').strip()
        query_ips = _extract_query_ips(raw_text)
        if not query_ips:
            await message.answer('请输入包含 IP 或代理链接的文本内容。')
            return
        results = []
        for ip in query_ips:
            order = await get_cloud_server_by_ip(ip)
            if not order:
                continue
            can_renew = bool(order.public_ip and order.status not in {'deleted', 'deleting', 'expired'})
            renew_text = '可续费' if can_renew else '不可续费'
            results.append({
                'ip': ip,
                'text': f'IP: {ip}\n到期时间: {order.service_expires_at or "未设置"}\n状态: {renew_text}',
                'renewable': can_renew,
                'order_id': order.id,
            })
        await state.update_data(cloud_query_results=results)
        await state.set_state(CloudQueryStates.waiting_ip)
        page = 1
        per_page = 5
        total_pages = max(1, math.ceil(len(results) / per_page))
        page_items = results[(page - 1) * per_page: page * per_page]
        text = '🔎 IP批量查询结果\n\n' + '\n\n'.join(item['text'] for item in page_items)
        renewable_items = [{'ip': item['ip'], 'order_id': item['order_id']} for item in page_items if item['renewable'] and item['order_id']]
        await message.answer(text, reply_markup=cloud_ip_query_result(page_items, renewable_items, page, total_pages))

    @dp.callback_query(F.data.startswith('cloud:queryip:page:'))
    async def cb_cloud_query_ip_page(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        data = await state.get_data()
        results = data.get('cloud_query_results') or []
        if not results:
            await callback.message.edit_text('🔎 IP查询到期\n\n查询结果已失效，请重新输入 IP。', reply_markup=order_query_menu())
            return
        page = max(1, int(callback.data.split(':')[3]))
        per_page = 5
        total_pages = max(1, math.ceil(len(results) / per_page))
        page = min(page, total_pages)
        page_items = results[(page - 1) * per_page: page * per_page]
        text = '🔎 IP批量查询结果\n\n' + '\n\n'.join(item['text'] for item in page_items)
        renewable_items = [{'ip': item['ip'], 'order_id': item['order_id']} for item in page_items if item['renewable'] and item['order_id']]
        await callback.message.edit_text(text, reply_markup=cloud_ip_query_result(page_items, renewable_items, page, total_pages))

    @dp.callback_query(F.data == 'profile:orders')
    async def cb_profile_orders(callback: CallbackQuery):
        await callback.message.edit_text('📋 订单查询\n\n请选择要查看的订单类型：', reply_markup=order_query_menu())
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'profile:orders:product')
    async def cb_profile_product_orders(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        orders, total = await list_orders(user.id)
        text_out, kb = _orders_page(orders, 1, total)
        if not orders:
            await callback.message.edit_text('📦 商品订单\n\n暂无商品订单。', reply_markup=order_query_menu())
        else:
            await callback.message.edit_text(text_out, reply_markup=kb)
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'profile:orders:cloud')
    async def cb_profile_cloud_orders(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        page = 1
        orders, total = await list_cloud_orders(user.id, page=page)
        total_pages = max(1, math.ceil(total / 5))
        if not orders:
            await callback.message.edit_text('☁️ 云服务器订单\n\n暂无云服务器订单。', reply_markup=order_query_menu())
        else:
            await callback.message.edit_text('☁️ 云服务器订单\n\n请选择要查看的订单：', reply_markup=cloud_server_list(orders, page, total_pages, 'profile:orders:cloud:page'))
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'profile:cart')
    async def cb_profile_cart(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        items, total_amount = await list_cart_items(user.id)
        cloud_items = [item for item in items if item.item_type == 'cloud_plan' and item.cloud_plan]
        if not cloud_items:
            await callback.message.edit_text('🛒 购物车\n\n购物车还是空的。', reply_markup=cart_menu([], 0))
        else:
            lines = ['🛒 购物车', '']
            for idx, item in enumerate(cloud_items, start=1):
                lines.append(f'{idx}. {item.cloud_plan.region_name} / {item.cloud_plan.plan_name} x {item.quantity} = {fmt_amount(item.cloud_plan.price * item.quantity)} USDT')
            lines.append('')
            lines.append(f'合计: {fmt_amount(sum(item.cloud_plan.price * item.quantity for item in cloud_items))} USDT')
            await callback.message.edit_text('\n'.join(lines), reply_markup=cart_menu(cloud_items, total_amount))
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('cart:add:'))
    async def cb_cart_add(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        product_id = int(callback.data.split(':')[2])
        item = await add_to_cart(user.id, product_id, 1, item_type='cloud_plan')
        if not item:
            await _safe_callback_answer(callback, '套餐不存在或已下架', show_alert=True)
            return
        await _safe_callback_answer(callback, '已加入购物车')

    @dp.callback_query(F.data.startswith('cart:remove:'))
    async def cb_cart_remove(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        product_id = int(callback.data.split(':')[2])
        await remove_cart_item(user.id, product_id, item_type='cloud_plan')
        items, total_amount = await list_cart_items(user.id)
        cloud_items = [item for item in items if item.item_type == 'cloud_plan' and item.cloud_plan]
        if not cloud_items:
            await callback.message.edit_text('🛒 购物车\n\n购物车还是空的。', reply_markup=cart_menu([], 0))
        else:
            lines = ['🛒 购物车', '']
            for idx, item in enumerate(cloud_items, start=1):
                lines.append(f'{idx}. {item.cloud_plan.region_name} / {item.cloud_plan.plan_name} x {item.quantity} = {fmt_amount(item.cloud_plan.price * item.quantity)} USDT')
            lines.append('')
            lines.append(f'合计: {fmt_amount(sum(item.cloud_plan.price * item.quantity for item in cloud_items))} USDT')
            await callback.message.edit_text('\n'.join(lines), reply_markup=cart_menu(cloud_items, total_amount))
        await _safe_callback_answer(callback, '已删除')

    @dp.callback_query(F.data == 'cart:clear')
    async def cb_cart_clear(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        await clear_cart(user.id, item_type='cloud_plan')
        await callback.message.edit_text('🛒 购物车\n\n已清空购物车。', reply_markup=cart_menu([], 0))
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('cart:checkout:'))
    async def cb_cart_checkout(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, pay_method, currency = callback.data.split(':')
        items, _ = await list_cart_items(user.id)
        cloud_items = [item for item in items if item.item_type == 'cloud_plan' and item.cloud_plan]
        if not cloud_items:
            await _safe_callback_answer(callback, '购物车为空', show_alert=True)
            return
        if len(cloud_items) != 1:
            await _safe_callback_answer(callback, '当前仅支持单个云套餐直接结算，请先保留一个套餐', show_alert=True)
            return
        cart_item = cloud_items[0]
        plan = cart_item.cloud_plan
        quantity = cart_item.quantity
        if pay_method == 'balance':
            order, err = await buy_cloud_server_with_balance(user.id, plan.id, currency, quantity)
            if err:
                await callback.message.edit_text(
                    f'❌ 余额不足，请先充值\n\n当前支付币种: {currency}',
                    reply_markup=profile_menu(),
                )
                await _safe_callback_answer(callback, '余额不足，请先充值', show_alert=True)
                return
            await clear_cart(user.id, item_type='cloud_plan')
            await state.update_data(custom_order_id=order.id, custom_currency=currency)
            await callback.message.edit_text(
                '✅ 钱包支付成功\n\n'
                f'订单号: {order.order_no}\n'
                f'地区: {order.region_name}\n'
                f'套餐: {order.plan_name}\n'
                f'数量: {order.quantity}\n'
                f'支付金额: {fmt_pay_amount(order.pay_amount)} {order.currency}\n\n'
                '请选择 MTProxy 端口：默认端口是 9528，你也可以输入自定义端口。',
                reply_markup=custom_port_keyboard(order.id),
            )
            return
        order = await create_cloud_server_order(user.id, plan.id, currency, quantity)
        await clear_cart(user.id, item_type='cloud_plan')
        await state.update_data(custom_order_id=order.id, custom_quantity=quantity, custom_currency=currency)
        receive_address = _receive_address()
        await callback.message.edit_text(
            '🧾 购物车订单详情\n\n'
            f'订单号: {order.order_no}\n'
            f'地区: {plan.region_name}\n'
            f'套餐: {plan.plan_name}\n'
            f'数量: {order.quantity}\n'
            f'支付金额: {fmt_pay_amount(order.total_amount)} USDT / {fmt_pay_amount(await usdt_to_trx(order.total_amount))} TRX\n'
            f'支付地址: `{receive_address}`\n'
            '订单 5 分钟有效，请在有效期内完成支付。\n\n'
            '系统已开始自动监控 USDT 和 TRX 到账，检测到支付成功后会自动进入后续流程。',
            reply_markup=custom_currency_keyboard(None, None, None, order.id),
            parse_mode='Markdown',
        )

    @dp.callback_query(F.data == 'profile:balance_details')
    async def cb_profile_balance_details(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        items, total = await list_balance_details(user.id)
        text_out, kb = _balance_details_page(items, 1, total)
        await callback.message.edit_text(text_out, reply_markup=kb)
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'profile:back_to_menu')
    async def cb_profile_back_to_menu(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        await callback.message.edit_text(
            f'👤 个人中心\n用户ID: {user.tg_user_id}\n用户名: @{user.username or "无"}\n'
            f'💵 USDT 余额: {fmt_amount(user.balance)}\n🪙 TRX 余额: {fmt_amount(user.balance_trx)}\n'
            f'☁️ 云服务器折扣: {fmt_amount(user.cloud_discount_rate)}%\n'
            f'📦 云订单数: {getattr(user, "cloud_orders", []).count() if hasattr(getattr(user, "cloud_orders", None), "count") else "-"}\n\n'
            f'请选择要进入的功能：',
            reply_markup=profile_menu(),
        )
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'profile:recharge')
    async def cb_profile_recharge(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await callback.message.edit_text('💰 请选择充值币种：', reply_markup=recharge_currency_menu())
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'profile:recharges')
    async def cb_profile_recharges(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        recharges, total = await list_recharges(user.id)
        text_out, kb = _recharges_page(recharges, 1, total)
        await callback.message.edit_text(text_out, reply_markup=kb)
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'profile:monitors')
    async def cb_profile_monitors(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await callback.message.edit_text('🔍 地址监控', reply_markup=monitor_menu())
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'profile:back')
    async def cb_profile_back(callback: CallbackQuery):
        await callback.message.edit_text('已返回主菜单，请使用底部按钮继续操作。')
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'custom:back')
    async def cb_custom_back(callback: CallbackQuery):
        await callback.message.edit_text('已返回主菜单，请使用底部按钮继续操作。')
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'custom:regions')
    async def cb_custom_regions(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        regions = await _get_cached_custom_regions()
        await _safe_edit_text(callback.message, '🛠 云服务器定制\n\n请选择热门地区：', reply_markup=custom_region_menu(regions, expanded=False))

    @dp.callback_query(F.data == 'custom:regions:more')
    async def cb_custom_regions_more(callback: CallbackQuery):
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
        discount_rate = Decimal(str(getattr(user, 'cloud_discount_rate', 100) or 100))
        display_price = (Decimal(str(plan.price)) * discount_rate / Decimal('100')).quantize(Decimal('0.01'))
        await state.update_data(custom_plan_id=plan.id, custom_plan_name=plan.plan_name, custom_plan_price=str(display_price), custom_region_code=plan.region_code, custom_region_name=plan.region_name)
        logger.info('云服务器套餐已记录: tg_user_id=%s plan_id=%s plan_name=%s region=%s price=%s', getattr(callback.from_user, 'id', None), plan.id, plan.plan_name, plan.region_code, display_price)
        text = (
            _bot_text('bot_custom_quantity_title', '🧾 请选择购买数量') + '\n\n'
            f'地区: {plan.region_name}\n'
            f'套餐: {plan.plan_name}\n'
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
            await state.update_data(custom_plan_id=plan_id, custom_plan_name=plan.plan_name, custom_plan_price=str(plan.price), custom_region_code=plan.region_code, custom_region_name=plan.region_name)
            logger.info('云服务器进入自定义数量输入: tg_user_id=%s plan_id=%s plan_name=%s', getattr(callback.from_user, 'id', None), plan_id, plan.plan_name)
            await state.set_state(CustomServerStates.waiting_quantity)
            await _safe_edit_text(callback.message, '请输入购买数量（1-99）：')
            return
        quantity = int(qty_text)
        await state.update_data(custom_plan_id=plan.id, custom_plan_name=plan.plan_name, custom_plan_price=str(plan.price), custom_region_code=plan.region_code, custom_region_name=plan.region_name, custom_quantity=quantity)
        usdt_amount = Decimal(str(getattr(plan, 'price', 0))) * quantity
        trx_amount = await usdt_to_trx(usdt_amount)
        receive_address = _receive_address()
        text = (
            _bot_text('bot_custom_payment_title', '🧾 支付页面') + '\n\n'
            f'地区: {plan.region_name}\n'
            f'套餐: {plan.plan_name}\n'
            f'数量: {quantity}\n'
            f'USDT金额: {fmt_pay_amount(usdt_amount)} USDT\n'
            f'TRX金额: {fmt_pay_amount(trx_amount)} TRX\n'
            f'支付地址: `{receive_address}`\n\n'
            + _bot_text('bot_custom_order_notice', '系统已开始自动监控 USDT 和 TRX 到账，检测到支付成功后会自动进入后续流程。')
        )
        await _safe_callback_answer(callback)
        await _safe_edit_text(callback.message, text, reply_markup=custom_payment_keyboard(plan.id, quantity), parse_mode='Markdown')


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
        await state.update_data(custom_plan_id=plan.id, custom_plan_name=plan.plan_name, custom_plan_price=str(plan.price), custom_region_code=plan.region_code, custom_region_name=plan.region_name, custom_quantity=quantity)
        usdt_amount = Decimal(str(getattr(plan, 'price', 0))) * quantity
        trx_amount = await usdt_to_trx(usdt_amount)
        receive_address = _receive_address()
        text = (
            _bot_text('bot_custom_payment_title', '🧾 支付页面') + '\n\n'
            f'地区: {plan.region_name}\n'
            f'套餐: {plan.plan_name}\n'
            f'数量: {quantity}\n'
            f'USDT金额: {fmt_pay_amount(usdt_amount)} USDT\n'
            f'TRX金额: {fmt_pay_amount(trx_amount)} TRX\n'
            f'支付地址: `{receive_address}`\n\n'
            + _bot_text('bot_custom_order_notice', '系统已开始自动监控 USDT 和 TRX 到账，检测到支付成功后会自动进入后续流程。')
        )
        await _safe_edit_text(callback.message, text, reply_markup=custom_payment_keyboard(plan.id, quantity), parse_mode='Markdown')


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
        await _safe_callback_answer(callback, '钱包支付处理中，完成后将主动通知你')
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, plan_id_text, quantity_text, currency = callback.data.split(':')
        plan_id = int(plan_id_text)
        quantity = int(quantity_text)
        logger.info('云服务器钱包直付开始: tg_user_id=%s user=%s plan_id=%s quantity=%s currency=%s callback=%s', getattr(callback.from_user, 'id', None), user.id, plan_id, quantity, currency, callback.data)
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
            from biz.models import CloudServerOrder
            order = await asyncio.to_thread(lambda: CloudServerOrder.objects.filter(id=order_id, user_id=user.id).first())
            if not order:
                await _safe_callback_answer(callback, '订单不存在', show_alert=True)
                return
            trx_amount = await usdt_to_trx(order.total_amount)
            logger.info('云服务器订单钱包币种页准备完成: tg_user_id=%s user=%s order_id=%s total=%s trx=%s', getattr(callback.from_user, 'id', None), user.id, order.id, order.total_amount, trx_amount)
            await callback.message.edit_text(
                _bot_text('bot_custom_wallet_title', '请选择钱包支付币种：'),
                reply_markup=custom_order_wallet_keyboard(order.id, order.total_amount, trx_amount),
            )
            return
        currency = parts[3]
        logger.info('云服务器订单钱包补付开始: tg_user_id=%s user=%s order_id=%s currency=%s', getattr(callback.from_user, 'id', None), user.id, order_id, currency)
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
        await state.update_data(custom_order_id=order_id, custom_port=9528)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order = await set_cloud_server_port(order_id, user.id, 9528)
        logger.info('云服务器使用默认端口: tg_user_id=%s user=%s order_id=%s port=9528 result=%s', getattr(callback.from_user, 'id', None), user.id, order_id, getattr(order, 'order_no', None))
        if not order:
            await _safe_callback_answer(callback, '订单不存在', show_alert=True)
            return
        await bot.send_message(
            chat_id=callback.from_user.id,
            text='✅ 已选择默认端口 9528。\n服务器创建任务已提交，正在后台处理，完成后会自动发送创建结果。',
            reply_markup=main_menu(),
        )
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
            text='✍️ 已选择自定义端口。\n请发送 1025-65535 之间的端口号，发送后我会立即提交服务器创建任务。',
        )

    @dp.callback_query(F.data == 'cloud:list')
    async def cb_cloud_list(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        servers = await list_user_cloud_servers(user.id)
        visible_servers = [item for item in servers if (item.public_ip or item.previous_public_ip)]
        page = 1
        per_page = 5
        total_visible = len(visible_servers)
        total_pages = max(1, math.ceil(total_visible / per_page))
        page_items = visible_servers[(page - 1) * per_page: page * per_page]
        if not page_items:
            await callback.message.delete()
            await callback.message.answer('🔎 查询中心\n\n暂无可查询的云服务器记录。', reply_markup=main_menu())
        else:
            await _safe_edit_text(callback.message, '🔎 我的云服务器\n\n请选择要查看的服务器：', reply_markup=cloud_server_list(page_items, page, total_pages, 'cloud:list:page'))

    @dp.callback_query(F.data.startswith('cloud:list:page:'))
    async def cb_cloud_list_page(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        page = max(1, int(callback.data.split(':')[3]))
        servers = await list_user_cloud_servers(user.id)
        visible_servers = [item for item in servers if (item.public_ip or item.previous_public_ip)]
        per_page = 5
        total_visible = len(visible_servers)
        total_pages = max(1, math.ceil(total_visible / per_page))
        page = min(page, total_pages)
        page_items = visible_servers[(page - 1) * per_page: page * per_page]
        if not page_items:
            await _safe_edit_text(callback.message, '🔎 查询中心\n\n暂无可查询的云服务器记录。', reply_markup=main_menu())
            return
        await _safe_edit_text(
            callback.message,
            '🔎 我的云服务器\n\n请选择要查看的服务器：',
            reply_markup=cloud_server_list(page_items, page, total_pages, 'cloud:list:page'),
        )

    @dp.callback_query(F.data.startswith('profile:orders:cloud:page:'))
    async def cb_profile_cloud_orders_page(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        page = max(1, int(callback.data.split(':')[4]))
        orders, total = await list_cloud_orders(user.id, page=page)
        total_pages = max(1, math.ceil(total / 5))
        if not orders:
            await _safe_edit_text(callback.message, '☁️ 云服务器订单\n\n暂无云服务器订单。', reply_markup=order_query_menu())
            return
        await _safe_edit_text(
            callback.message,
            '☁️ 云服务器订单\n\n请选择要查看的订单：',
            reply_markup=cloud_server_list(orders, page, total_pages, 'profile:orders:cloud:page'),
        )

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
            await _safe_callback_answer(callback, '服务器记录不存在', show_alert=True)
            return
        can_renew = bool(order.public_ip and order.status not in {'deleted', 'deleting', 'expired'})
        can_change_ip = order.status in {'completed', 'expiring', 'suspended'}
        can_reinit = bool(order.public_ip and order.login_password and order.status in {'completed', 'failed'})
        now = timezone.now()
        expires_at = getattr(order, 'service_expires_at', None)
        delay_quota = max(int(getattr(order, 'delay_quota', 0) or 0), 0)
        can_delay = bool(
            can_renew
            and expires_at
            and expires_at >= now
            and expires_at <= now + timezone.timedelta(days=5)
            and delay_quota > 0
        )
        await _safe_edit_text(
            callback.message,
            _cloud_server_detail_text(order),
            reply_markup=cloud_server_detail(order.id, can_renew, can_change_ip, can_reinit, can_delay, back_callback),
        )

    @dp.callback_query(F.data.startswith('cloud:mute:'))
    async def cb_cloud_mute(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, raw_order_id, raw_days = callback.data.split(':')
        updated = await mute_cloud_reminders(user.id, int(raw_days))
        if not updated:
            await _safe_callback_answer(callback, '关闭提醒失败', show_alert=True)
            return

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
        await callback.message.reply(f'🕒 已为订单 {order.order_no} 延期 {raw_days} 天，系统将自动顺延删机前宽限时间。')

    @dp.callback_query(F.data.startswith('cloud:renew:'))
    async def cb_cloud_renew(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(callback.data.split(':')[2])
        order = await create_cloud_server_renewal(order_id, user.id, 31)
        if order is False:
            await _safe_callback_answer(callback, '该服务器IP已删除，禁止续费', show_alert=True)
            return
        if not order:
            await _safe_callback_answer(callback, '续费订单创建失败', show_alert=True)
            return
        trx_amount = await usdt_to_trx(order.pay_amount)
        receive_address = _receive_address()
        auto_renew_enabled = await get_cloud_server_auto_renew(order.id, user.id)
        await callback.message.edit_text(
            '🔄 云服务器续费\n\n'
            f'订单号: {order.order_no}\n'
            '续费时长: 31天\n'
            f'续费价格: {fmt_pay_amount(order.pay_amount)} {order.currency}\n'
            f'自动续费: {"已开启" if auto_renew_enabled else "已关闭"}\n'
            f'收款地址: `{receive_address}`\n\n'
            '可直接地址支付，或使用下方钱包续费与自动续费开关。',
            parse_mode='Markdown',
            reply_markup=cloud_server_renew_payment(order.id, order.pay_amount, trx_amount, bool(auto_renew_enabled)),
        )

    @dp.callback_query(F.data.startswith('cloud:autorenew:'))
    async def cb_cloud_auto_renew_toggle(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, action, order_id_text = callback.data.split(':')
        order_id = int(order_id_text)
        enabled = action == 'on'
        order = await set_cloud_server_auto_renew(order_id, user.id, enabled)
        if not order:
            await _safe_callback_answer(callback, '服务器记录不存在', show_alert=True)
            return
        trx_amount = await usdt_to_trx(order.pay_amount or order.total_amount)
        receive_address = _receive_address()
        await callback.message.edit_text(
            '🔄 云服务器续费\n\n'
            f'订单号: {order.order_no}\n'
            '续费时长: 31天\n'
            f'续费价格: {fmt_pay_amount(order.pay_amount or order.total_amount)} {order.currency}\n'
            f'自动续费: {"已开启" if enabled else "已关闭"}\n'
            f'收款地址: `{receive_address}`\n\n'
            '可直接地址支付，或使用下方钱包续费与自动续费开关。',
            parse_mode='Markdown',
            reply_markup=cloud_server_renew_payment(order.id, order.pay_amount or order.total_amount, trx_amount, enabled),
        )

    @dp.callback_query(F.data.startswith('cloud:renewwallet:'))
    async def cb_cloud_renew_wallet(callback: CallbackQuery):
        await _safe_callback_answer(callback, '钱包自动续费处理中')
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(callback.data.split(':')[2])
        order, err = await pay_cloud_server_renewal_with_balance(order_id, user.id, 'USDT', 31)
        if err:
            await callback.message.edit_text(
                f'❌ 钱包自动续费失败：{err}。\n请先充值余额后再试，或使用下方地址支付。',
                reply_markup=wallet_recharge_prompt_menu(),
            )
            return
        await callback.message.edit_text(
            '✅ 云服务器钱包自动续费成功\n\n'
            f'订单号: {order.order_no}\n'
            '续费时长: 31天\n'
            '支付方式: 钱包自动续费\n'
            '支付币种: USDT\n'
            f'新的到期时间: {order.service_expires_at or "未设置"}',
            reply_markup=cloud_server_detail(
                order.id,
                True,
                bool(order.public_ip and order.status in {"completed", "expiring", "suspended"}),
                bool(order.public_ip and order.login_password and order.status in {"completed", "failed"}),
                bool(
                    order.public_ip
                    and getattr(order, 'service_expires_at', None)
                    and getattr(order, 'service_expires_at', None) >= timezone.now()
                    and getattr(order, 'service_expires_at', None) <= timezone.now() + timezone.timedelta(days=5)
                    and max(int(getattr(order, 'delay_quota', 0) or 0), 0) > 0
                ),
                'cloud:list',
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
            await callback.message.edit_text(f'❌ {err}。', reply_markup=wallet_recharge_prompt_menu())
            return
        await callback.message.edit_text(
            '✅ 云服务器续费成功\n\n'
            f'订单号: {order.order_no}\n'
            '续费时长: 31天\n'
            f'支付币种: {currency}\n'
            f'新的到期时间: {order.service_expires_at or "未设置"}',
            reply_markup=cloud_server_detail(
                order.id,
                True,
                bool(order.public_ip and order.status in {"completed", "expiring", "suspended"}),
                bool(order.public_ip and order.login_password and order.status in {"completed", "failed"}),
                bool(
                    order.public_ip
                    and getattr(order, 'service_expires_at', None)
                    and getattr(order, 'service_expires_at', None) >= timezone.now()
                    and getattr(order, 'service_expires_at', None) <= timezone.now() + timezone.timedelta(days=5)
                    and max(int(getattr(order, 'delay_quota', 0) or 0), 0) > 0
                ),
                'cloud:list',
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
        regions = await _get_cached_custom_regions()
        await callback.message.edit_text(
            '🌐 更换IP\n\n请选择新的地区：',
            reply_markup=cloud_server_change_ip_region_menu(order.id, regions, expanded=False),
        )

    @dp.callback_query(F.data.startswith('cloud:ipregions:more:'))
    async def cb_cloud_change_ip_regions_more(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        order_id = int(callback.data.split(':')[3])
        regions = await _get_cached_custom_regions()
        await callback.message.edit_text(
            '🌐 更换IP\n\n请选择新的地区：',
            reply_markup=cloud_server_change_ip_region_menu(order_id, regions, expanded=True),
        )

    @dp.callback_query(F.data.startswith('cloud:ipregion:'))
    async def cb_cloud_change_ip_region(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback)
        _, _, raw_order_id, region_code = callback.data.split(':')
        order_id = int(raw_order_id)
        regions = await _get_cached_custom_regions()
        region_name = next((name for code, name in regions if code == region_code), region_code)
        await state.update_data(cloud_ip_change_order_id=order_id, cloud_ip_change_region_code=region_code, cloud_ip_change_region_name=region_name)
        await callback.message.edit_text(
            f'🌐 更换IP\n\n已选择地区：{region_name}\n请选择端口：',
            reply_markup=cloud_server_change_ip_port_keyboard(order_id, region_code, region_name),
        )

    @dp.callback_query(F.data.startswith('cloud:ipport:default:'))
    async def cb_cloud_change_ip_port_default(callback: CallbackQuery, state: FSMContext, bot: Bot):
        await _safe_callback_answer(callback, '已选择默认端口 9528，正在创建迁移服务器')
        _, _, _, raw_order_id, region_code = callback.data.split(':')
        order_id = int(raw_order_id)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        new_order = await mark_cloud_server_ip_change_requested(order_id, user.id, region_code, 9528)
        await state.clear()
        if new_order is False:
            await _safe_callback_answer(callback, '当前状态不可更换 IP', show_alert=True)
            return
        if not new_order:
            await _safe_callback_answer(callback, '创建迁移单失败', show_alert=True)
            return
        await callback.message.reply(
            f'🌐 已为你创建新的换 IP 服务器\n新订单号: {new_order.order_no}\n新地区: {new_order.region_name}\n新端口: {new_order.mtproxy_port or 9528}\n旧服务器将于 5 天后到期，请尽快完成迁移。'
        )
        asyncio.create_task(_provision_cloud_server_and_notify(bot, callback.from_user.id, new_order.id, new_order.mtproxy_port or 9528))

    @dp.callback_query(F.data.startswith('cloud:ipport:custom:'))
    async def cb_cloud_change_ip_port_custom(callback: CallbackQuery, state: FSMContext):
        await _safe_callback_answer(callback, '已选择自定义端口')
        _, _, _, raw_order_id, region_code = callback.data.split(':')
        order_id = int(raw_order_id)
        regions = await _get_cached_custom_regions()
        region_name = next((name for code, name in regions if code == region_code), region_code)
        await state.update_data(cloud_ip_change_order_id=order_id, cloud_ip_change_region_code=region_code, cloud_ip_change_region_name=region_name)
        await state.set_state(CustomServerStates.waiting_port)
        await callback.message.reply(
            f'✍️ 已选择更换IP自定义端口。\n地区：{region_name}\n请发送 1025-65535 之间的端口号。'
        )


    @dp.callback_query(F.data.startswith('cloud:reinit:'))
    async def cb_cloud_reinit(callback: CallbackQuery, bot: Bot):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(callback.data.split(':')[2])
        order = await mark_cloud_server_reinit_requested(order_id, user.id)
        if not order:
            await _safe_callback_answer(callback, '服务器记录不存在', show_alert=True)
            return
        if order is False:
            await _safe_callback_answer(callback, '当前服务器缺少公网 IP 或登录密码，暂时无法重试初始化', show_alert=True)
            return
        await _safe_callback_answer(callback, '已提交重试初始化任务')
        await callback.message.reply('🛠 已提交重试初始化任务，后台会重新执行 BBR/MTProxy 安装，完成后会自动通知你。')
        asyncio.create_task(_provision_cloud_server_and_notify(bot, callback.from_user.id, order.id, order.mtproxy_port or 9528, retry_only=True))

    @dp.callback_query(F.data.startswith('ppage:'))
    async def cb_product_page(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        page = int(callback.data.split(':')[1])
        products, total = await list_products(page=page)
        text_out, kb = _products_page(products, page, total)
        await callback.message.edit_text(text_out, reply_markup=kb)

    @dp.callback_query(F.data.startswith('balance:detail:'))
    async def cb_balance_detail(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        raw_item_id = callback.data.split(':', 2)[2]
        item = await get_balance_detail(user.id, raw_item_id)
        if not item:
            await _safe_callback_answer(callback, '明细不存在', show_alert=True)
            return
        await callback.message.edit_text(_balance_detail_text(item), reply_markup=profile_menu())

    @dp.callback_query(F.data == 'back_to_products')
    async def cb_back_products(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        products, total = await list_products()
        text_out, kb = _products_page(products, 1, total)
        await callback.message.edit_text(text_out, reply_markup=kb)

    @dp.callback_query(F.data.startswith('qty:'))
    async def cb_quantity(callback: CallbackQuery):
        await _safe_callback_answer(callback)
        _, product_id, quantity = callback.data.split(':')
        product = await get_product(int(product_id))
        if not product:
            await callback.message.edit_text('商品不存在。')
            return
        quantity = int(quantity)
        if product.stock != -1 and product.stock < quantity:
            await _safe_callback_answer(callback, '库存不足！', show_alert=True)
            return
        usdt_total = product.price * quantity
        try:
            trx_total = await usdt_to_trx(usdt_total)
            rate_info = await get_exchange_rate_display()
        except Exception:
            await callback.message.edit_text('汇率获取失败，请稍后重试。')
            return
        await callback.message.edit_text(
            f'🛒 订单确认\n商品: {product.name}\n数量: {quantity}\n'
            f'💵 {fmt_amount(usdt_total)} USDT  |  🪙 ≈ {fmt_amount(trx_total)} TRX\n'
            f'📊 {rate_info}\n\n请选择支付方式：',
            reply_markup=pay_method_keyboard(product.id, quantity, usdt_total, trx_total),
        )

    @dp.callback_query(F.data.startswith('pay:'))
    async def cb_pay(callback: CallbackQuery, bot: Bot):
        await _safe_callback_answer(callback)
        _, pay_method, product_id, currency, quantity = callback.data.split(':')
        product_id = int(product_id)
        quantity = int(quantity)
        product = await get_product(product_id)
        if not product:
            await callback.message.edit_text('商品不存在。')
            return
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        usdt_total = product.price * quantity
        try:
            total = await usdt_to_trx(usdt_total) if currency == 'TRX' else usdt_total
        except Exception:
            await callback.message.edit_text('汇率获取失败，请稍后重试。')
            return

        if pay_method == 'balance':
            order, err = await buy_with_balance(user.id, product.id, quantity, total, currency)
            if err:
                await callback.message.edit_text(f'❌ {err}！\n请先充值 {currency} 余额。')
                return
            await callback.message.edit_text(f'✅ 购买成功！\n订单号: {order.order_no}\n商品正在发送...')
            for _ in range(quantity):
                if product.content_type == 'text':
                    await bot.send_message(chat_id=callback.from_user.id, text=product.content_text or '')
                elif product.content_type == 'image' and product.content_image:
                    await bot.send_photo(chat_id=callback.from_user.id, photo=product.content_image, caption=product.content_text or '')
                elif product.content_type == 'video' and product.content_video:
                    await bot.send_video(chat_id=callback.from_user.id, video=product.content_video, caption=product.content_text or '')
        else:
            order = await create_address_order(user.id, product.id, quantity, total, currency)
            addr = _receive_address()
            await callback.message.edit_text(
                f'📋 订单已创建\n订单号: {order.order_no}\n支付币种: {currency}\n'
                f'支付金额: {fmt_pay_amount(order.pay_amount)} {currency}\n'
                f'收款地址: {addr}\n\n⏰ 请在 15 分钟内转账精确金额到上述地址。\n系统将自动确认并发货。'
            )

    # ══════════════════════════════════════════════════════════════════════
    # 订单回调
    # ══════════════════════════════════════════════════════════════════════

    @dp.callback_query(F.data.startswith('opage:'))
    async def cb_order_page(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        page = int(callback.data.split(':')[1])
        orders, total = await list_orders(user.id, page=page)
        text_out, kb = _orders_page(orders, page, total)
        await callback.message.edit_text(text_out, reply_markup=kb)
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('order_detail:'))
    async def cb_order_detail(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order = await get_order(int(callback.data.split(':')[1]))
        if not order or order.user_id != user.id:
            await callback.message.edit_text('订单不存在。')
            await _safe_callback_answer(callback)
            return
        await callback.message.edit_text(_order_detail_text(order), reply_markup=order_query_menu())
        await _safe_callback_answer(callback)

    # ══════════════════════════════════════════════════════════════════════
    # 充值回调
    # ══════════════════════════════════════════════════════════════════════

    @dp.callback_query(F.data.startswith('rcur:'))
    async def cb_recharge_currency(callback: CallbackQuery, state: FSMContext):
        currency = callback.data.split(':')[1]
        await state.update_data(recharge_currency=currency)
        await state.set_state(RechargeStates.waiting_amount)
        await callback.message.edit_text(f'💰 请输入需要充值的 {currency} 金额：')
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('rpage:'))
    async def cb_recharge_page(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        page = int(callback.data.split(':')[1])
        recharges, total = await list_recharges(user.id, page=page)
        text_out, kb = _recharges_page(recharges, page, total)
        await callback.message.edit_text(text_out, reply_markup=kb)
        await _safe_callback_answer(callback)

    # ══════════════════════════════════════════════════════════════════════
    # 监控回调
    # ══════════════════════════════════════════════════════════════════════

    @dp.callback_query(F.data == 'mon:add')
    async def cb_mon_add(callback: CallbackQuery, state: FSMContext):
        await state.set_state(MonitorStates.waiting_address)
        await callback.message.edit_text('请输入要监控的 TRON 地址：')
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'mon:list')
    async def cb_mon_list(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        monitors = await list_monitors(user.id)
        if not monitors:
            await callback.message.edit_text('暂无监控地址。', reply_markup=monitor_menu())
        else:
            await callback.message.edit_text('📋 监控列表：', reply_markup=kb_monitor_list(monitors))
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:detail:'))
    async def cb_mon_detail(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        mon = await get_monitor(int(callback.data.split(':')[2]), user.id)
        if not mon:
            await callback.message.edit_text('监控不存在。')
            await _safe_callback_answer(callback)
            return
        icon = '🟢' if mon.is_active else '🔴'
        await callback.message.edit_text(
            f'{icon} 监控详情\n地址: <code>{mon.address}</code>\n备注: {mon.remark or "无"}\n'
            f'💸 监控转账: {"开启" if mon.monitor_transfers else "关闭"}\n'
            f'⚡ 监控资源: {"开启" if mon.monitor_resources else "关闭"}\n'
            f'USDT 阈值: {fmt_amount(mon.usdt_threshold)}\nTRX 阈值: {fmt_amount(mon.trx_threshold)}\n\n'
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
            await _safe_callback_answer(callback, '监控不存在', show_alert=True)
            return
        from monitoring.cache import update_monitor_flag_in_cache
        await update_monitor_flag_in_cache(monitor.address, field, getattr(monitor, field))
        await callback.message.edit_text(
            f'{"🟢" if monitor.is_active else "🔴"} 监控详情\n地址: <code>{monitor.address}</code>\n备注: {monitor.remark or "无"}\n'
            f'💸 监控转账: {"开启" if monitor.monitor_transfers else "关闭"}\n'
            f'⚡ 监控资源: {"开启" if monitor.monitor_resources else "关闭"}\n'
            f'USDT 阈值: {fmt_amount(monitor.usdt_threshold)}\nTRX 阈值: {fmt_amount(monitor.trx_threshold)}\n\n'
            f'📘 使用说明:\n'
            f'1. 监控转账：地址收到 USDT/TRX 转账时通知。\n'
            f'2. 监控资源：地址可用能量/带宽增加时通知；正常转账消耗不通知。',
            reply_markup=kb_monitor_detail(monitor.id, monitor.monitor_transfers, monitor.monitor_resources),
            parse_mode='HTML',
        )
        await _safe_callback_answer(callback, '已更新')

    @dp.callback_query(F.data.startswith('mon:threshold:'))
    async def cb_mon_threshold(callback: CallbackQuery):
        mid = int(callback.data.split(':')[2])
        await callback.message.edit_text('请选择要修改的阈值币种：', reply_markup=monitor_threshold_currency(mid))
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:setthr:'))
    async def cb_mon_setthr(callback: CallbackQuery, state: FSMContext):
        _, _, mid, currency = callback.data.split(':')
        await state.update_data(threshold_monitor_id=int(mid), threshold_currency=currency)
        state_obj = MonitorStates.waiting_usdt_threshold if currency == 'USDT' else MonitorStates.waiting_trx_threshold
        await state.set_state(state_obj)
        await callback.message.edit_text(f'请输入新的 {currency} 阈值金额：')
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:delete:'))
    async def cb_mon_delete(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        mid = int(callback.data.split(':')[2])
        mon = await get_monitor(mid, user.id)
        if mon:
            from monitoring.cache import remove_monitor_from_cache
            await remove_monitor_from_cache(mon.address)
        await delete_monitor(mid, user.id)
        await callback.message.edit_text('🗑 监控已删除。', reply_markup=monitor_menu())
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data == 'mon:back')
    async def cb_mon_back(callback: CallbackQuery):
        await callback.message.edit_text('🔍 地址监控', reply_markup=monitor_menu())
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:txd:'))
    async def cb_tx_detail(callback: CallbackQuery):
        from tron.scanner import get_tx_detail
        detail_key = callback.data.split(':')[2]
        detail = get_tx_detail(detail_key)
        if not detail:
            await _safe_callback_answer(callback, '交易详情已过期', show_alert=True)
            return
        text = (
            f'🔍 交易详情\n\n'
            f'类型: {"收入" if detail.get("direction") == "income" else "支出"}\n'
            f'交易哈希: {detail["tx_hash"]}\n'
            f'币种: {detail["currency"]}\n'
            f'金额: {detail["amount"]} {detail["currency"]}\n'
            f'付款地址: {detail["from"]}\n'
            f'收款地址: {detail["to"]}\n'
            f'时间: {detail["time"]}\n'
        )
        if detail.get("remark"):
            text += f'备注: {detail["remark"]}\n'
        if detail.get("fee_text"):
            text += f'手续费: {detail["fee_text"]}\n'
        await callback.message.edit_text(text)
        await _safe_callback_answer(callback)

    @dp.callback_query(F.data.startswith('mon:resd:'))
    async def cb_resource_detail(callback: CallbackQuery):
        from tron.resource_checker import get_resource_detail
        detail_key = callback.data.split(':')[2]
        detail = get_resource_detail(detail_key)
        if not detail:
            await _safe_callback_answer(callback, '资源详情已过期', show_alert=True)
            return
        text = (
            f'⚡ 资源详情\n\n'
            f'地址备注: {detail["remark"]}\n'
            f'监控地址: <code>{detail["address"]}</code>\n'
            f'检测时间: <code>{detail["time"]}</code>\n'
            f'可用能量增加: <code>+{detail["energy_increase"]}</code>\n'
            f'可用带宽增加: <code>+{detail["bandwidth_increase"]}</code>\n'
            f'当前可用能量: <code>{detail["energy"]}</code>\n'
            f'当前可用带宽: <code>{detail["bandwidth"]}</code>'
        )
        await callback.message.edit_text(text, parse_mode='HTML')
        await _safe_callback_answer(callback)

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
