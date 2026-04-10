from aiogram.types import InlineKeyboardButton, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder
from aiogram.utils.keyboard import InlineKeyboardBuilder


def main_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text='🛒 购买商品')
    kb.button(text='📋 我的订单')
    kb.button(text='💰 充值余额')
    kb.button(text='📜 充值记录')
    kb.button(text='🔍 地址监控')
    kb.button(text='👤 个人中心')
    kb.adjust(2, 2, 2)
    return kb.as_markup(resize_keyboard=True)


def monitor_menu():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text='➕ 添加监控地址', callback_data='mon:add'))
    kb.row(InlineKeyboardButton(text='📋 我的监控列表', callback_data='mon:list'))
    return kb.as_markup()


def monitor_list(monitors):
    kb = InlineKeyboardBuilder()
    for m in monitors:
        remark = f' ({m.remark})' if m.remark else ''
        short = f'{m.address[:6]}...{m.address[-4:]}'
        icon = '🟢' if m.is_active else '🔴'
        kb.row(InlineKeyboardButton(text=f'{icon} {short}{remark}', callback_data=f'mon:detail:{m.id}'))
    kb.row(InlineKeyboardButton(text='🔙 返回', callback_data='mon:back'))
    return kb.as_markup()


def monitor_detail(monitor_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text='⚙️ 设置阈值', callback_data=f'mon:threshold:{monitor_id}'))
    kb.row(InlineKeyboardButton(text='🗑 删除监控', callback_data=f'mon:delete:{monitor_id}'))
    kb.row(InlineKeyboardButton(text='🔙 返回列表', callback_data='mon:list'))
    return kb.as_markup()


def monitor_threshold_currency(monitor_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text='💵 USDT', callback_data=f'mon:setthr:{monitor_id}:USDT'))
    kb.row(InlineKeyboardButton(text='🪙 TRX', callback_data=f'mon:setthr:{monitor_id}:TRX'))
    kb.row(InlineKeyboardButton(text='🔙 返回', callback_data=f'mon:detail:{monitor_id}'))
    return kb.as_markup()


def recharge_currency_menu():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text='💵 USDT', callback_data='rcur:USDT'))
    kb.row(InlineKeyboardButton(text='🪙 TRX', callback_data='rcur:TRX'))
    return kb.as_markup()


def product_list(products, page: int, total_pages: int):
    kb = InlineKeyboardBuilder()
    for p in products:
        stock = '无限' if p.stock == -1 else str(p.stock)
        kb.row(InlineKeyboardButton(
            text=f'{p.name} - {p.price} USDT (库存:{stock})',
            callback_data=f'product:{p.id}',
        ))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text='⬅️ 上一页', callback_data=f'ppage:{page - 1}'))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text='➡️ 下一页', callback_data=f'ppage:{page + 1}'))
    if nav:
        kb.row(*nav)
    return kb.as_markup()


def quantity_keyboard(product_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text='1', callback_data=f'qty:{product_id}:1'),
        InlineKeyboardButton(text='2', callback_data=f'qty:{product_id}:2'),
        InlineKeyboardButton(text='3', callback_data=f'qty:{product_id}:3'),
    )
    kb.row(
        InlineKeyboardButton(text='5', callback_data=f'qty:{product_id}:5'),
        InlineKeyboardButton(text='10', callback_data=f'qty:{product_id}:10'),
    )
    kb.row(InlineKeyboardButton(text='🔙 返回商品列表', callback_data='back_to_products'))
    return kb.as_markup()


def pay_method_keyboard(product_id: int, quantity: int, usdt_total, trx_total):
    from bot.utils import fmt_amount
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f'💳 余额 USDT ({fmt_amount(usdt_total)} U)', callback_data=f'pay:balance:{product_id}:USDT:{quantity}'))
    kb.row(InlineKeyboardButton(text=f'💳 余额 TRX ({fmt_amount(trx_total)} TRX)', callback_data=f'pay:balance:{product_id}:TRX:{quantity}'))
    kb.row(InlineKeyboardButton(text=f'🔗 地址 USDT ({fmt_amount(usdt_total)} U)', callback_data=f'pay:address:{product_id}:USDT:{quantity}'))
    kb.row(InlineKeyboardButton(text=f'🔗 地址 TRX ({fmt_amount(trx_total)} TRX)', callback_data=f'pay:address:{product_id}:TRX:{quantity}'))
    kb.row(InlineKeyboardButton(text='🔙 返回', callback_data=f'product:{product_id}'))
    return kb.as_markup()


def order_list(orders, page: int, total_pages: int):
    kb = InlineKeyboardBuilder()
    sm = {'pending': '⏳', 'paid': '✅', 'delivered': '📦', 'cancelled': '❌', 'expired': '⏰'}
    for o in orders:
        kb.row(InlineKeyboardButton(
            text=f'{sm.get(o.status, "")} {o.order_no} | {o.product_name} | {o.total_amount} {o.currency}',
            callback_data=f'order_detail:{o.id}',
        ))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text='⬅️ 上一页', callback_data=f'opage:{page - 1}'))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text='➡️ 下一页', callback_data=f'opage:{page + 1}'))
    if nav:
        kb.row(*nav)
    return kb.as_markup()


def recharge_list(recharges, page: int, total_pages: int):
    kb = InlineKeyboardBuilder()
    sm = {'pending': '⏳', 'completed': '✅', 'expired': '⏰'}
    for r in recharges:
        kb.row(InlineKeyboardButton(
            text=f'{sm.get(r.status, "")} {r.amount} {r.currency} | {r.created_at:%m-%d %H:%M}',
            callback_data='noop',
        ))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text='⬅️ 上一页', callback_data=f'rpage:{page - 1}'))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text='➡️ 下一页', callback_data=f'rpage:{page + 1}'))
    if nav:
        kb.row(*nav)
    return kb.as_markup()
