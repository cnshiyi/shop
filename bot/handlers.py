import logging
import math
from decimal import Decimal, InvalidOperation

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from bot.config import BOT_TOKEN
from bot.fsm import create_fsm_storage
from bot.states import CustomServerStates, MonitorStates, RechargeStates
from biz.services import get_exchange_rate_display, usdt_to_trx
from bot.keyboards import (
    main_menu, monitor_menu, monitor_list as kb_monitor_list,
    monitor_detail as kb_monitor_detail, monitor_threshold_currency,
    recharge_currency_menu, product_list, quantity_keyboard,
    pay_method_keyboard, order_list as kb_order_list,
    recharge_list as kb_recharge_list, profile_menu,
    custom_region_menu, custom_plan_menu, custom_quantity_keyboard, custom_currency_keyboard, custom_wallet_keyboard, custom_order_wallet_keyboard, custom_port_keyboard,
    cloud_server_list, cloud_server_detail,
)
from biz.services import (
    add_monitor, create_address_order, buy_with_balance, create_recharge,
    delete_monitor, get_or_create_user, get_product, get_monitor,
    list_monitors, list_orders, list_products, list_recharges,
    set_monitor_threshold, toggle_monitor_flag,
    list_custom_regions, list_region_plans, create_cloud_server_order, buy_cloud_server_with_balance, pay_cloud_server_order_with_balance, get_cloud_plan,
    set_cloud_server_port, create_cloud_server_renewal, list_user_cloud_servers,
    get_user_cloud_server, mark_cloud_server_ip_change_requested,
)
from core.formatters import fmt_amount, fmt_pay_amount
from core.models import SiteConfig
from cloud.provisioning import provision_cloud_server

logger = logging.getLogger(__name__)


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


def _custom_plan_text(region_name: str, plans) -> str:
    if not plans:
        return f'🛠 {region_name}\n\n当前地区暂无可用套餐。'
    labels = ['套餐一', '套餐二', '套餐三', '套餐四', '套餐五', '套餐六']
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
            f'价格: {fmt_amount(plan.price)} {plan.currency}\n'
        )
    lines.append('请选择下面的套餐按钮：')
    return '\n'.join(lines)


def _receive_address() -> str:
    return SiteConfig.get('receive_address', '')


# ── 辅助：检查是否在 FSM 状态中，如果是则不处理 ──
class _NotInState:
    """仅当用户不在任何 FSM 状态时匹配。"""
    __slots__ = ()

    def __call__(self, obj):
        return True  # 由 aiogram 内部的 StateFilter 机制处理


MENU_BUTTONS = {'✨ 订阅', '🛠 定制', '🔎 查询', '👤 个人中心'}


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
        if not text.isdigit() or int(text) <= 0 or int(text) > 99:
            await message.answer('请输入 1-99 的购买数量：')
            return
        data = await state.get_data()
        plan_id = int(data['custom_plan_id'])
        await state.clear()
        plan = await get_cloud_plan(plan_id)
        if not plan:
            await message.answer('套餐不存在或已下架，请重新选择。', reply_markup=main_menu())
            return
        quantity = int(text)
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        order = await create_cloud_server_order(user.id, plan.id, 'USDT', quantity)
        logger.info('云服务器下单进入详情: user=%s order=%s qty=%s region=%s', user.id, order.order_no, order.quantity, order.region_code)
        receive_address = _receive_address()
        await message.answer(
            '🧾 订单详情\n\n'
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

    @dp.message(CustomServerStates.waiting_port)
    async def input_custom_server_port(message: Message, state: FSMContext):
        try:
            port = int(message.text.strip())
        except Exception:
            await message.answer('端口格式不正确，请输入 1025-65535 之间的数字。')
            return
        if port < 1025 or port > 65535:
            await message.answer('端口格式不正确，请输入 1025-65535 之间的数字。')
            return
        data = await state.get_data()
        order_id = data.get('custom_order_id')
        if not order_id:
            await state.clear()
            await message.answer('订单上下文已失效，请重新下单。', reply_markup=main_menu())
            return
        user = await get_or_create_user(message.from_user.id, message.from_user.username, message.from_user.first_name)
        order = await set_cloud_server_port(order_id, user.id, port)
        logger.info('云服务器提交自定义端口: user=%s order_id=%s port=%s', user.id, order_id, port)
        await state.clear()
        if not order:
            await message.answer('订单不存在，无法设置端口。', reply_markup=main_menu())
            return
        provisioned = await provision_cloud_server(order.id)
        if provisioned and provisioned.status == 'completed':
            await message.answer(
                f'✅ 已设置自定义端口：{port}\n{provisioned.provision_note or "MTProxy 已创建完成。"}',
                reply_markup=main_menu(),
            )
            return
        await message.answer(f'✅ 已设置自定义端口：{port}\n已进入创建流程。', reply_markup=main_menu())

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

        elif text == '🛠 定制':
            regions = await list_custom_regions()
            await message.answer('🛠 云服务器定制\n\n请选择热门地区：', reply_markup=custom_region_menu(regions, expanded=False))

        elif text == '🔎 查询':
            servers = await list_user_cloud_servers(user.id)
            if not servers:
                await message.answer('🔎 查询中心\n\n你还没有云服务器记录。', reply_markup=main_menu())
            else:
                await message.answer('🔎 我的云服务器\n\n请选择要查看的服务器：', reply_markup=cloud_server_list(servers))

        elif text == '👤 个人中心':
            await message.answer(
                f'👤 个人中心\n用户ID: {user.tg_user_id}\n用户名: @{user.username or "无"}\n'
                f'💵 USDT 余额: {fmt_amount(user.balance)}\n🪙 TRX 余额: {fmt_amount(user.balance_trx)}\n\n'
                f'请选择要进入的功能：',
                reply_markup=profile_menu(),
            )

    @dp.callback_query(F.data == 'profile:orders')
    async def cb_profile_orders(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        orders, total = await list_orders(user.id)
        text_out, kb = _orders_page(orders, 1, total)
        await callback.message.edit_text(text_out, reply_markup=kb)
        await callback.answer()

    @dp.callback_query(F.data == 'profile:recharge')
    async def cb_profile_recharge(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await callback.message.edit_text('💰 请选择充值币种：', reply_markup=recharge_currency_menu())
        await callback.answer()

    @dp.callback_query(F.data == 'profile:recharges')
    async def cb_profile_recharges(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        recharges, total = await list_recharges(user.id)
        text_out, kb = _recharges_page(recharges, 1, total)
        await callback.message.edit_text(text_out, reply_markup=kb)
        await callback.answer()

    @dp.callback_query(F.data == 'profile:monitors')
    async def cb_profile_monitors(callback: CallbackQuery, state: FSMContext):
        await state.clear()
        await callback.message.edit_text('🔍 地址监控', reply_markup=monitor_menu())
        await callback.answer()

    @dp.callback_query(F.data == 'profile:back')
    async def cb_profile_back(callback: CallbackQuery):
        await callback.message.edit_text('已返回主菜单，请使用底部按钮继续操作。')
        await callback.answer()

    @dp.callback_query(F.data == 'custom:back')
    async def cb_custom_back(callback: CallbackQuery):
        await callback.message.edit_text('已返回主菜单，请使用底部按钮继续操作。')
        await callback.answer()

    @dp.callback_query(F.data == 'custom:regions')
    async def cb_custom_regions(callback: CallbackQuery):
        regions = await list_custom_regions()
        await callback.message.edit_text('🛠 云服务器定制\n\n请选择热门地区：', reply_markup=custom_region_menu(regions, expanded=False))
        await callback.answer()

    @dp.callback_query(F.data == 'custom:regions:more')
    async def cb_custom_regions_more(callback: CallbackQuery):
        regions = await list_custom_regions()
        await callback.message.edit_text('🛠 云服务器定制\n\n请选择地区：', reply_markup=custom_region_menu(regions, expanded=True))
        await callback.answer()

    @dp.callback_query(F.data.startswith('custom:region:'))
    async def cb_custom_region(callback: CallbackQuery):
        region_code = callback.data.split(':', 2)[2]
        plans = await list_region_plans(region_code)
        region_name = plans[0].region_name if plans else region_code
        await callback.message.edit_text(_custom_plan_text(region_name, plans), reply_markup=custom_plan_menu(region_code, plans))
        await callback.answer()

    @dp.callback_query(F.data.startswith('custom:plan:'))
    async def cb_custom_plan(callback: CallbackQuery):
        plan_id = int(callback.data.split(':')[2])
        plan = await get_cloud_plan(plan_id)
        if not plan:
            await callback.answer('套餐不存在或已下架', show_alert=True)
            return
        text = (
            '🧾 请选择购买数量\n\n'
            f'地区: {plan.region_name}\n'
            f'套餐: {plan.plan_name}\n'
            f'单价: {fmt_amount(plan.price)} USDT\n\n'
            '请选择数量，或输入自定义数量。'
        )
        await callback.message.edit_text(text, reply_markup=custom_quantity_keyboard(plan.id))
        await callback.answer()

    @dp.callback_query(F.data.startswith('custom:qty:'))
    async def cb_custom_quantity(callback: CallbackQuery, state: FSMContext):
        _, _, plan_id_text, qty_text = callback.data.split(':')
        plan_id = int(plan_id_text)
        plan = await get_cloud_plan(plan_id)
        if not plan:
            await callback.answer('套餐不存在或已下架', show_alert=True)
            return
        if qty_text == 'custom':
            await state.update_data(custom_plan_id=plan_id)
            await state.set_state(CustomServerStates.waiting_quantity)
            await callback.message.edit_text('请输入购买数量（1-99）：')
            await callback.answer()
            return
        quantity = int(qty_text)
        await state.clear()
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order = await create_cloud_server_order(user.id, plan.id, 'USDT', quantity)
        logger.info('云服务器下单进入详情: user=%s order=%s qty=%s region=%s', user.id, order.order_no, order.quantity, order.region_code)
        receive_address = _receive_address()
        await callback.message.edit_text(
            '🧾 订单详情\n\n'
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
        await callback.answer()

    @dp.callback_query(F.data.startswith('custom:wallet:'))
    async def cb_custom_wallet(callback: CallbackQuery, state: FSMContext):
        _, _, plan_id_text, quantity_text = callback.data.split(':')
        plan_id = int(plan_id_text)
        quantity = int(quantity_text)
        plan = await get_cloud_plan(plan_id)
        if not plan:
            await callback.answer('套餐不存在或已下架', show_alert=True)
            return
        usdt_amount = plan.price * quantity
        trx_amount = await usdt_to_trx(usdt_amount)
        await state.clear()
        await callback.message.edit_text(
            '请选择钱包支付币种：',
            reply_markup=custom_wallet_keyboard(plan.id, quantity, usdt_amount, trx_amount),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith('custom:currency:'))
    async def cb_custom_currency(callback: CallbackQuery, state: FSMContext):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, plan_id_text, quantity_text, currency = callback.data.split(':')
        plan_id = int(plan_id_text)
        quantity = int(quantity_text)
        plan = await get_cloud_plan(plan_id)
        if not plan:
            await callback.answer('套餐不存在或已下架', show_alert=True)
            return
        order = await create_cloud_server_order(user.id, plan.id, currency, quantity)
        receive_address = _receive_address()
        text = (
            '🧾 订单详情\n\n'
            f'地区: {plan.region_name}\n'
            f'套餐: {plan.plan_name}\n'
            f'数量: {order.quantity}\n'
            f'支付金额: {fmt_pay_amount(order.total_amount)} USDT / {fmt_pay_amount(await usdt_to_trx(order.total_amount))} TRX\n'
            f'支付地址: `{receive_address}`\n'
            '订单 5 分钟有效，请在有效期内完成支付。\n\n'
            '系统已开始自动监控 USDT 和 TRX 到账，检测到支付成功后会自动进入后续流程。'
        )
        await state.clear()
        await callback.message.edit_text(text, reply_markup=custom_currency_keyboard(None, None, None, order.id), parse_mode='Markdown')
        await callback.answer()

    @dp.callback_query(F.data.startswith('custom:balance:'))
    async def cb_custom_balance(callback: CallbackQuery, state: FSMContext):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, plan_id_text, quantity_text, currency = callback.data.split(':')
        plan_id = int(plan_id_text)
        quantity = int(quantity_text)
        order, err = await buy_cloud_server_with_balance(user.id, plan_id, currency, quantity)
        if not err and order:
            logger.info('云服务器钱包支付成功: user=%s order=%s currency=%s qty=%s', user.id, order.order_no, currency, order.quantity)
        if err:
            await callback.answer(err, show_alert=True)
            return
        text = (
            '✅ 钱包支付成功\n\n'
            f'地区: {order.region_name}\n'
            f'套餐: {order.plan_name}\n'
            f'数量: {order.quantity}\n'
            f'支付金额: {fmt_pay_amount(order.pay_amount)} {order.currency}\n\n'
            '请选择 MTProxy 端口：默认端口是 9528，你也可以输入自定义端口。'
        )
        await state.clear()
        await callback.message.edit_text(text, reply_markup=custom_port_keyboard(order.id))
        await callback.answer()

    @dp.callback_query(F.data.startswith('custom:walletpay:'))
    async def cb_custom_walletpay(callback: CallbackQuery, state: FSMContext):
        parts = callback.data.split(':')
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(parts[2])
        if len(parts) == 3:
            from biz.models import CloudServerOrder
            order = await asyncio.to_thread(lambda: CloudServerOrder.objects.filter(id=order_id, user_id=user.id).first())
            if not order:
                await callback.answer('订单不存在', show_alert=True)
                return
            trx_amount = await usdt_to_trx(order.total_amount)
            await callback.message.edit_text(
                '请选择钱包支付币种：',
                reply_markup=custom_order_wallet_keyboard(order.id, order.total_amount, trx_amount),
            )
            await callback.answer()
            return
        currency = parts[3]
        order, err = await pay_cloud_server_order_with_balance(order_id, user.id, currency)
        if not err and order:
            logger.info('云服务器订单钱包补付成功: user=%s order=%s currency=%s qty=%s', user.id, order.order_no, currency, order.quantity)
        if err:
            await callback.answer(err, show_alert=True)
            return
        await state.clear()
        await callback.message.edit_text(
            '✅ 钱包支付成功\n\n'
            f'地区: {order.region_name}\n'
            f'套餐: {order.plan_name}\n'
            f'数量: {order.quantity}\n'
            f'支付金额: {fmt_pay_amount(order.pay_amount)} {order.currency}\n\n'
            '请选择 MTProxy 端口：默认端口是 9528，你也可以输入自定义端口。',
            reply_markup=custom_port_keyboard(order.id),
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith('custom:port:default:'))
    async def cb_custom_port_default(callback: CallbackQuery):
        order_id = int(callback.data.split(':')[3])
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order = await set_cloud_server_port(order_id, user.id, 9528)
        logger.info('云服务器使用默认端口: user=%s order_id=%s port=9528', user.id, order_id)
        if not order:
            await callback.answer('订单不存在', show_alert=True)
            return
        provisioned = await provision_cloud_server(order.id)
        if provisioned and provisioned.status == 'completed':
            await callback.message.reply(
                f'✅ 已使用默认端口 9528，并已完成创建。\n{provisioned.provision_note or "MTProxy 已创建完成。"}'
            )
        else:
            await callback.message.reply(f'✅ 已使用默认端口 9528，已进入创建流程。\n当前状态: {getattr(provisioned, "status", "unknown")}')
        await callback.answer('已使用默认端口 9528')

    @dp.callback_query(F.data.startswith('custom:port:custom:'))
    async def cb_custom_port_custom(callback: CallbackQuery, state: FSMContext):
        order_id = int(callback.data.split(':')[3])
        await state.update_data(custom_order_id=order_id)
        await state.set_state(CustomServerStates.waiting_port)
        await callback.message.reply('✍️ 请输入自定义端口（1025-65535）：')
        await callback.answer()

    @dp.callback_query(F.data == 'cloud:list')
    async def cb_cloud_list(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        servers = await list_user_cloud_servers(user.id)
        if not servers:
            await callback.message.edit_text('你还没有云服务器记录。')
        else:
            await callback.message.edit_text('🔎 我的云服务器\n\n请选择要查看的服务器：', reply_markup=cloud_server_list(servers))
        await callback.answer()

    @dp.callback_query(F.data.startswith('cloud:detail:'))
    async def cb_cloud_detail(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(callback.data.split(':')[2])
        order = await get_user_cloud_server(order_id, user.id)
        if not order:
            await callback.answer('服务器记录不存在', show_alert=True)
            return
        can_renew = bool(order.public_ip or order.previous_public_ip)
        can_change_ip = order.status in {'completed', 'expiring', 'suspended'}
        text = (
            '☁️ 云服务器详情\n\n'
            f'订单号: {order.order_no}\n'
            f'地区: {order.region_name}\n'
            f'套餐: {order.plan_name}\n'
            f'状态: {order.get_status_display()}\n'
            f'MTProxy 链接: {order.mtproxy_link or "尚未生成"}\n'
            f'当前IP: {order.public_ip or "无"}\n'
            f'历史IP: {order.previous_public_ip or "无"}\n'
            f'到期时间: {order.service_expires_at or "未设置"}\n'
            f'IP保留到期: {order.ip_recycle_at or "未设置"}'
        )
        await callback.message.edit_text(text, reply_markup=cloud_server_detail(order.id, can_renew, can_change_ip))
        await callback.answer()

    @dp.callback_query(F.data.startswith('cloud:renew:'))
    async def cb_cloud_renew(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(callback.data.split(':')[2])
        order = await create_cloud_server_renewal(order_id, user.id, 31)
        if not order:
            await callback.answer('续费订单创建失败', show_alert=True)
            return
        receive_address = _receive_address()
        await callback.message.edit_text(
            '🔄 云服务器续费订单已创建\n\n'
            f'订单号: {order.order_no}\n'
            '续费时长: 31天\n'
            f'支付金额: {fmt_pay_amount(order.pay_amount)} {order.currency}\n'
            f'收款地址: `{receive_address}`\n\n'
            '只要 IP 仍在保留期内，到账后即可恢复服务。',
            parse_mode='Markdown',
        )
        await callback.answer()

    @dp.callback_query(F.data.startswith('cloud:ip:'))
    async def cb_cloud_change_ip(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        order_id = int(callback.data.split(':')[2])
        order = await get_user_cloud_server(order_id, user.id)
        if not order:
            await callback.answer('服务器记录不存在', show_alert=True)
            return
        await mark_cloud_server_ip_change_requested(order.id)
        await callback.answer('已记录更换 IP 请求')
        await callback.message.reply('🌐 已提交更换 IP 请求，后台处理后会同步给你新的 MTProxy 链接。')

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
        order = await get_order(int(callback.data.split(':')[1]))
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
        await callback.answer()

    @dp.callback_query(F.data.startswith('mon:toggle:'))
    async def cb_mon_toggle(callback: CallbackQuery):
        user = await get_or_create_user(callback.from_user.id, callback.from_user.username, callback.from_user.first_name)
        _, _, monitor_id, mode = callback.data.split(':')
        field = 'monitor_transfers' if mode == 'transfers' else 'monitor_resources'
        monitor = await toggle_monitor_flag(int(monitor_id), user.id, field)
        if not monitor:
            await callback.answer('监控不存在', show_alert=True)
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
        await callback.answer('已更新')

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
            from monitoring.cache import remove_monitor_from_cache
            await remove_monitor_from_cache(mon.address)
        await delete_monitor(mid, user.id)
        await callback.message.edit_text('🗑 监控已删除。', reply_markup=monitor_menu())
        await callback.answer()

    @dp.callback_query(F.data == 'mon:back')
    async def cb_mon_back(callback: CallbackQuery):
        await callback.message.edit_text('🔍 地址监控', reply_markup=monitor_menu())
        await callback.answer()

    @dp.callback_query(F.data.startswith('mon:txd:'))
    async def cb_tx_detail(callback: CallbackQuery):
        from tron.scanner import get_tx_detail
        detail_key = callback.data.split(':')[2]
        detail = get_tx_detail(detail_key)
        if not detail:
            await callback.answer('交易详情已过期', show_alert=True)
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
        await callback.answer()

    @dp.callback_query(F.data.startswith('mon:resd:'))
    async def cb_resource_detail(callback: CallbackQuery):
        from tron.resource_checker import get_resource_detail
        detail_key = callback.data.split(':')[2]
        detail = get_resource_detail(detail_key)
        if not detail:
            await callback.answer('资源详情已过期', show_alert=True)
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
        await callback.answer()

    @dp.callback_query(F.data == 'noop')
    async def cb_noop(callback: CallbackQuery):
        await callback.answer()


async def create_dispatcher_and_register() -> tuple[Bot, Dispatcher]:
    bot = Bot(token=BOT_TOKEN)
    storage = await create_fsm_storage()
    dp = Dispatcher(storage=storage)
    register_handlers(dp)
    return bot, dp
