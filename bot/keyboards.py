from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from core.formatters import fmt_amount


def main_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text='✨ 订阅')
    kb.button(text='🛠 定制')
    kb.button(text='🔎 查询')
    kb.button(text='👤 个人中心')
    kb.adjust(2, 2)
    return kb.as_markup(resize_keyboard=True)


def profile_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text='📋 我的订单', callback_data='profile:orders')
    kb.button(text='💰 充值余额', callback_data='profile:recharge')
    kb.button(text='📜 充值记录', callback_data='profile:recharges')
    kb.button(text='🔍 地址监控', callback_data='profile:monitors')
    kb.button(text='🔙 返回主菜单', callback_data='profile:back')
    kb.adjust(2, 2, 1)
    return kb.as_markup()


def monitor_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text='➕ 添加监控地址', callback_data='mon:add')
    kb.button(text='📋 我的监控列表', callback_data='mon:list')
    kb.adjust(2)
    return kb.as_markup()


def monitor_list(monitors):
    kb = InlineKeyboardBuilder()
    for m in monitors:
        remark = f' ({m.remark})' if m.remark else ''
        short = f'{m.address[:6]}...{m.address[-4:]}'
        icon = '🟢' if m.is_active else '🔴'
        kb.button(text=f'{icon} {short}{remark}', callback_data=f'mon:detail:{m.id}')
    kb.button(text='🔙 返回', callback_data='mon:back')
    kb.adjust(2, *([2] * (len(monitors) // 2)), 1)
    return kb.as_markup()


def monitor_detail(monitor_id: int, monitor_transfers: bool = True, monitor_resources: bool = False):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(
            text=f'💸 监控转账: {"开" if monitor_transfers else "关"}',
            callback_data=f'mon:toggle:{monitor_id}:transfers'
        ),
        InlineKeyboardButton(
            text=f'⚡ 监控资源: {"开" if monitor_resources else "关"}',
            callback_data=f'mon:toggle:{monitor_id}:resources'
        ),
    )
    kb.row(
        InlineKeyboardButton(text='⚙️ 设置阈值', callback_data=f'mon:threshold:{monitor_id}'),
        InlineKeyboardButton(text='🗑 删除监控', callback_data=f'mon:delete:{monitor_id}'),
    )
    kb.row(InlineKeyboardButton(text='🔙 返回列表', callback_data='mon:list'))
    return kb.as_markup()


def monitor_threshold_currency(monitor_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text='💵 USDT', callback_data=f'mon:setthr:{monitor_id}:USDT'),
        InlineKeyboardButton(text='🪙 TRX', callback_data=f'mon:setthr:{monitor_id}:TRX'),
    )
    kb.row(InlineKeyboardButton(text='🔙 返回', callback_data=f'mon:detail:{monitor_id}'))
    return kb.as_markup()


def custom_region_menu(regions):
    kb = InlineKeyboardBuilder()
    for region_code, region_name in regions:
        kb.button(text=region_name, callback_data=f'custom:region:{region_code}')
    kb.button(text='🔙 返回主菜单', callback_data='custom:back')
    kb.adjust(2, *([2] * (max(len(regions) - 2, 0) // 2)), 1)
    return kb.as_markup()


def custom_plan_menu(region_code: str, plans):
    kb = InlineKeyboardBuilder()
    for plan in plans:
        provider_name = '光帆服务器' if plan.provider == 'aws_lightsail' else '轻量云'
        kb.button(text=f'{provider_name} | {plan.plan_name} | {fmt_amount(plan.price)} USDT', callback_data=f'custom:plan:{plan.id}')
    kb.button(text='🔙 返回地区', callback_data='custom:regions')
    kb.adjust(1)
    return kb.as_markup()


def custom_pay_keyboard(order_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text='✅ 使用默认端口 9528', callback_data=f'custom:port:default:{order_id}'),
        InlineKeyboardButton(text='✍️ 输入自定义端口', callback_data=f'custom:port:custom:{order_id}'),
    )
    kb.row(InlineKeyboardButton(text='🔙 返回主菜单', callback_data='custom:back'))
    return kb.as_markup()


def cloud_server_list(orders):
    kb = InlineKeyboardBuilder()
    for order in orders:
        status = order.get_status_display() if hasattr(order, 'get_status_display') else order.status
        kb.button(text=f'{order.region_name} | {order.public_ip or order.previous_public_ip or "未分配IP"} | {status}', callback_data=f'cloud:detail:{order.id}')
    kb.adjust(1)
    kb.row(InlineKeyboardButton(text='🔙 返回主菜单', callback_data='custom:back'))
    return kb.as_markup()


def cloud_server_detail(order_id: int, can_renew: bool, can_change_ip: bool):
    kb = InlineKeyboardBuilder()
    if can_renew:
        kb.button(text='🔄 续费31天', callback_data=f'cloud:renew:{order_id}')
    if can_change_ip:
        kb.button(text='🌐 更换IP', callback_data=f'cloud:ip:{order_id}')
    kb.button(text='🔙 返回列表', callback_data='cloud:list')
    kb.adjust(2, 1)
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
