import logging
import math
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from bot.config import BOT_TOKEN
from bot.exchange import get_exchange_rate_display, usdt_to_trx
from bot.keyboards import (
    main_menu, monitor_menu, monitor_list as kb_monitor_list,
    monitor_detail as kb_monitor_detail, monitor_threshold_currency,
    recharge_currency_menu, product_list, quantity_keyboard,
    pay_method_keyboard, order_list as kb_order_list,
    recharge_list as kb_recharge_list,
)
from bot.services import (
    add_monitor, create_address_order, buy_with_balance, create_recharge,
    delete_monitor, get_or_create_user, get_product, get_monitor,
    list_monitors, list_orders, list_products, list_recharges,
    set_monitor_threshold,
)
from bot.utils import fmt_amount, fmt_pay_amount
from core.models import SiteConfig

logger = logging.getLogger(__name__)


class MonitorStates(StatesGroup):
    waiting_address = State()
    waiting_remark = State()
    waiting_usdt_threshold = State()
    waiting_trx_threshold = State()


class RechargeStates(StatesGroup):
    waiting_amount = State()


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


def _recharges_page(recharges, page: int, total: int):
    total_pages = max(1, math.ceil(total / 5))
    if not recharges:
        return '暂无充值记录。', None
    return '📜 充值记录：', kb_recharge_list(recharges, page, total_pages)


def _receive_address() -> str:
    return SiteConfig.get('receive_address', '')


# ── 辅助：检查是否在 FSM 状态中，如果是则不处理 ──
class _NotInState:
    """仅当用户不在任何 FSM 状态时匹配。"""
    __slots__ = ()

    def __call__(self, obj):
        return True  # 由 aiogram 内部的 StateFilter 机制处理


MENU_BUTTONS = {'🛒 购买商品', '📋 我的订单', '💰 充值余额', '📜 充值记录', '🔍 地址监控', '👤 个人中心'}


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
        from tron.cache import add_monitor_to_cache
        await add_monitor_to_cache(mon.id, user.id, mon.address, remark, mon.usdt_threshold, mon.trx_threshold)
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
        from tron.cache import update_monitor_threshold_in_cache
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
        from tron.cache import update_monitor_threshold_in_cache
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

        if text == '🛒 购买商品':
            products, total = await list_products()
            text_out, kb = _products_page(products, 1, total)
            await message.answer(text_out, reply_markup=kb)

        elif text == '📋 我的订单':
            orders, total = await list_orders(user.id)
            text_out, kb = _orders_page(orders, 1, total)
            await message.answer(text_out, reply_markup=kb)

        elif text == '💰 充值余额':
            await state.clear()
            await message.answer('💰 请选择充值币种：', reply_markup=recharge_currency_menu())

        elif text == '📜 充值记录':
            recharges, total = await list_recharges(user.id)
            text_out, kb = _recharges_page(recharges, 1, total)
            await message.answer(text_out, reply_markup=kb)

        elif text == '🔍 地址监控':
            await message.answer('🔍 地址监控', reply_markup=monitor_menu())

        elif text == '👤 个人中心':
            await message.answer(
                f'👤 个人中心\n用户ID: {user.tg_user_id}\n用户名: @{user.username or "无"}\n'
                f'💵 USDT 余额: {fmt_amount(user.balance)}\n🪙 TRX 余额: {fmt_amount(user.balance_trx)}',
                reply_markup=main_menu(),
            )

    # ══════════════════════════════════════════════════════════════════════
    # 商品回调
    # ══════════════════════════════════════════════════════════════════════

    @dp.callback_query(F.data.startswith('product:'))
    async def cb_product_detail(callback: CallbackQuery):
        product = await get_product(int(callback.data.split(':')[1]))
        if not product:
            await callback.message.edit_text('商品不存在。')
            await callback.answer()
            return
        stock = '无限' if product.stock == -1 else str(product.stock)
        try:
            trx_ref = await usdt_to_trx(product.price)
            rate_info = await get_exchange_rate_display()
            price_block = f'  💵 {fmt_amount(product.price)} USDT\n  🪙 ≈ {fmt_amount(trx_ref)} TRX\n  📊 {rate_info}'
        except Exception:
            price_block = f'  💵 {fmt_amount(product.price)} USDT'
        await callback.message.edit_text(
            f'📦 {product.name}\n📝 {product.description or "无描述"}\n💰 价格:\n{price_block}\n📊 库存: {stock}\n\n请选择购买数量：',
            reply_markup=quantity_keyboard(product.id),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith('ppage:'))
    async def cb_product_page(callback: CallbackQuery):
        page = int(callback.data.split(':')[1])
        products, total = await list_products(page=page)
        text_out, kb = _products_page(products, page, total)
        await callback.message.edit_text(text_out, reply_markup=kb)
        await callback.answer()

    @dp.callback_query(F.data == 'back_to_products')
    async def cb_back_products(callback: CallbackQuery):
        products, total = await list_products()
        text_out, kb = _products_page(products, 1, total)
        await callback.message.edit_text(text_out, reply_markup=kb)
        await callback.answer()

    @dp.callback_query(F.data.startswith('qty:'))
    async def cb_quantity(callback: CallbackQuery):
        _, product_id, quantity = callback.data.split(':')
        product = await get_product(int(product_id))
        if not product:
            await callback.message.edit_text('商品不存在。')
            await callback.answer()
            return
        quantity = int(quantity)
        if product.stock != -1 and product.stock < quantity:
            await callback.answer('库存不足！', show_alert=True)
            return
        usdt_total = product.price * quantity
        try:
            trx_total = await usdt_to_trx(usdt_total)
            rate_info = await get_exchange_rate_display()
        except Exception:
            await callback.message.edit_text('汇率获取失败，请稍后重试。')
            await callback.answer()
            return
        await callback.message.edit_text(
            f'🛒 订单确认\n商品: {product.name}\n数量: {quantity}\n'
            f'💵 {fmt_amount(usdt_total)} USDT  |  🪙 ≈ {fmt_amount(trx_total)} TRX\n'
            f'📊 {rate_info}\n\n请选择支付方式：',
            reply_markup=pay_method_keyboard(product.id, quantity, usdt_total, trx_total),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith('pay:'))
    async def cb_pay(callback: CallbackQuery, bot: Bot):
        _, pay_method, product_id, currency, quantity = callback.data.split(':')
        product_id = int(product_id)
        quantity = int(quantity)
        product = await get_product(product_id)
        if not product:
            await callback.message.edit_text('商品不存在。')
            await callback.answer()
            return
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        usdt_total = product.price * quantity
        try:
            total = await usdt_to_trx(usdt_total) if currency == 'TRX' else usdt_total
        except Exception:
            await callback.message.edit_text('汇率获取失败，请稍后重试。')
            await callback.answer()
            return

        if pay_method == 'balance':
            order, err = await buy_with_balance(user.id, product.id, quantity, total, currency)
            if err:
                await callback.message.edit_text(f'❌ {err}！\n请先充值 {currency} 余额。')
                await callback.answer()
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
        await callback.answer()

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
        await callback.answer()

    @dp.callback_query(F.data.startswith('order_detail:'))
    async def cb_order_detail(callback: CallbackQuery):
        from shopbiz.models import Order
        order = await Order.objects.filter(id=int(callback.data.split(':')[1])).afirst()
        if not order:
            await callback.message.edit_text('订单不存在。')
            await callback.answer()
            return
        sm = {'pending': '待支付', 'paid': '已支付', 'delivered': '已发货', 'cancelled': '已取消', 'expired': '已过期'}
        text = (
            f'📋 订单详情\n订单号: {order.order_no}\n商品: {order.product_name}\n数量: {order.quantity}\n'
            f'总额: {fmt_amount(order.total_amount)} {order.currency}\n'
            f'支付方式: {"余额" if order.pay_method == "balance" else "地址"}\n'
            f'状态: {sm.get(order.status, order.status)}\n创建时间: {order.created_at:%Y-%m-%d %H:%M}'
        )
        if order.tx_hash:
            text += f'\n交易哈希: {order.tx_hash}'
        await callback.message.edit_text(text)
        await callback.answer()

    # ══════════════════════════════════════════════════════════════════════
    # 充值回调
    # ══════════════════════════════════════════════════════════════════════

    @dp.callback_query(F.data.startswith('rcur:'))
    async def cb_recharge_currency(callback: CallbackQuery, state: FSMContext):
        currency = callback.data.split(':')[1]
        await state.update_data(recharge_currency=currency)
        await state.set_state(RechargeStates.waiting_amount)
        await callback.message.edit_text(f'💰 请输入需要充值的 {currency} 金额：')
        await callback.answer()

    @dp.callback_query(F.data.startswith('rpage:'))
    async def cb_recharge_page(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        page = int(callback.data.split(':')[1])
        recharges, total = await list_recharges(user.id, page=page)
        text_out, kb = _recharges_page(recharges, page, total)
        await callback.message.edit_text(text_out, reply_markup=kb)
        await callback.answer()

    # ══════════════════════════════════════════════════════════════════════
    # 监控回调
    # ══════════════════════════════════════════════════════════════════════

    @dp.callback_query(F.data == 'mon:add')
    async def cb_mon_add(callback: CallbackQuery, state: FSMContext):
        await state.set_state(MonitorStates.waiting_address)
        await callback.message.edit_text('请输入要监控的 TRON 地址：')
        await callback.answer()

    @dp.callback_query(F.data == 'mon:list')
    async def cb_mon_list(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        monitors = await list_monitors(user.id)
        if not monitors:
            await callback.message.edit_text('暂无监控地址。', reply_markup=monitor_menu())
        else:
            await callback.message.edit_text('📋 监控列表：', reply_markup=kb_monitor_list(monitors))
        await callback.answer()

    @dp.callback_query(F.data.startswith('mon:detail:'))
    async def cb_mon_detail(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        mon = await get_monitor(int(callback.data.split(':')[2]), user.id)
        if not mon:
            await callback.message.edit_text('监控不存在。')
            await callback.answer()
            return
        icon = '🟢' if mon.is_active else '🔴'
        await callback.message.edit_text(
            f'{icon} 监控详情\n地址: {mon.address}\n备注: {mon.remark or "无"}\n'
            f'USDT 阈值: {fmt_amount(mon.usdt_threshold)}\nTRX 阈值: {fmt_amount(mon.trx_threshold)}',
            reply_markup=kb_monitor_detail(mon.id),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith('mon:threshold:'))
    async def cb_mon_threshold(callback: CallbackQuery):
        mid = int(callback.data.split(':')[2])
        await callback.message.edit_text('请选择要修改的阈值币种：', reply_markup=monitor_threshold_currency(mid))
        await callback.answer()

    @dp.callback_query(F.data.startswith('mon:setthr:'))
    async def cb_mon_setthr(callback: CallbackQuery, state: FSMContext):
        _, _, mid, currency = callback.data.split(':')
        await state.update_data(threshold_monitor_id=int(mid), threshold_currency=currency)
        state_obj = MonitorStates.waiting_usdt_threshold if currency == 'USDT' else MonitorStates.waiting_trx_threshold
        await state.set_state(state_obj)
        await callback.message.edit_text(f'请输入新的 {currency} 阈值金额：')
        await callback.answer()

    @dp.callback_query(F.data.startswith('mon:delete:'))
    async def cb_mon_delete(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        mid = int(callback.data.split(':')[2])
        mon = await get_monitor(mid, user.id)
        if mon:
            from tron.cache import remove_monitor_from_cache
            await remove_monitor_from_cache(mon.address)
        await delete_monitor(mid, user.id)
        await callback.message.edit_text('🗑 监控已删除。', reply_markup=monitor_menu())
        await callback.answer()

    @dp.callback_query(F.data == 'mon:back')
    async def cb_mon_back(callback: CallbackQuery):
        await callback.message.edit_text('🔍 地址监控', reply_markup=monitor_menu())
        await callback.answer()

    @dp.callback_query(F.data.startswith('mon:txdetail:'))
    async def cb_tx_detail(callback: CallbackQuery):
        from tron.scanner import get_tx_detail
        tx_hash = callback.data.split(':')[2]
        detail = get_tx_detail(tx_hash)
        if not detail:
            await callback.answer('交易详情已过期', show_alert=True)
            return
        text = (
            f'🔍 交易详情\n\n'
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
        await callback.answer()

    @dp.callback_query(F.data == 'noop')
    async def cb_noop(callback: CallbackQuery):
        await callback.answer()


def create_dispatcher_and_register() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    register_handlers(dp)
    return bot, dp
