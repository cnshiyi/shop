from aiogram.types import InlineKeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from core.formatters import fmt_amount


def main_menu():
    kb = ReplyKeyboardBuilder()
    kb.button(text='✨ 订阅')
    kb.button(text='🛠 定制节点')
    kb.button(text='🔎 到期时间查询')
    kb.button(text='👤 个人中心')
    kb.adjust(2, 2)
    return kb.as_markup(resize_keyboard=True)


def cloud_query_menu():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text='🖥 我的云服务器', callback_data='cloud:list'))
    kb.row(InlineKeyboardButton(text='🔎 IP查询到期', callback_data='cloud:queryip'))
    kb.row(InlineKeyboardButton(text='🔙 返回主菜单', callback_data='profile:back'))
    return kb.as_markup()


def profile_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text='📋 我的订单', callback_data='profile:orders')
    kb.button(text='🛒 购物车', callback_data='profile:cart')
    kb.button(text='💰 充值余额', callback_data='profile:recharge')
    kb.button(text='📜 充值记录', callback_data='profile:recharges')
    kb.button(text='💳 余额明细', callback_data='profile:balance_details')
    kb.button(text='🔍 地址监控', callback_data='profile:monitors')
    kb.button(text='🔙 返回主菜单', callback_data='profile:back')
    kb.adjust(2, 2, 2, 1)
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


def _split_custom_regions(regions):
    preferred_codes = ['ap-southeast-1', 'cn-hongkong', 'ap-northeast-1', 'ap-northeast-2', 'us-east-1']
    name_overrides = {'us-east-1': '美国'}
    region_map = {code: (code, name_overrides.get(code, name)) for code, name in regions}
    popular = []
    seen = set()
    for code in preferred_codes:
        if code not in region_map:
            continue
        popular.append(region_map[code])
        seen.add(code)
        if len(popular) >= 5:
            break
    if len(popular) < 5:
        for code, name in regions:
            if code in seen:
                continue
            popular.append((code, name_overrides.get(code, name)))
            seen.add(code)
            if len(popular) >= 5:
                break
    remaining = [(code, name) for code, name in regions if code not in seen]
    return popular, remaining


def custom_region_menu(regions, expanded: bool = False):
    kb = InlineKeyboardBuilder()
    popular_regions, remaining_regions = _split_custom_regions(regions)
    display_regions = remaining_regions if expanded else popular_regions
    for region_code, region_name in display_regions:
        kb.button(text=region_name, callback_data=f'custom:region:{region_code}')
    if not expanded and remaining_regions:
        kb.button(text='更多', callback_data='custom:regions:more')
        kb.adjust(3, 3)
        kb.button(text='🔙 返回主菜单', callback_data='custom:back')
        kb.adjust(3, 3, 1)
    elif expanded:
        kb.button(text='🔙 返回', callback_data='custom:regions')
        rows = [3] * ((len(display_regions) + 2) // 3)
        kb.adjust(*rows, 1)
    else:
        kb.button(text='🔙 返回主菜单', callback_data='custom:back')
        rows = [3] * ((len(display_regions) + 2) // 3)
        kb.adjust(*rows, 1)
    return kb.as_markup()


def custom_plan_menu(region_code: str, plans):
    kb = InlineKeyboardBuilder()
    labels = ['套餐一', '套餐二', '套餐三', '套餐四', '套餐五', '套餐六', '套餐七', '套餐八', '套餐九']
    for idx, plan in enumerate(plans):
        label = labels[idx] if idx < len(labels) else f'套餐{idx + 1}'
        kb.button(text=label, callback_data=f'custom:plan:{plan.id}')
    kb.button(text='🔙 返回地区', callback_data='custom:regions')
    rows = [3] * ((len(plans) + 2) // 3)
    kb.adjust(*rows, 1)
    return kb.as_markup()


def custom_quantity_keyboard(plan_id: int, quantity: int | None = None):
    kb = InlineKeyboardBuilder()
    selected_quantity = quantity if isinstance(quantity, int) and quantity > 0 else 1
    for qty in [1, 2, 3, 4, 5]:
        text = f'✅ {qty}' if qty == selected_quantity else str(qty)
        kb.button(text=text, callback_data=f'custom:qty:{plan_id}:{qty}')
    kb.button(text='✍️ 自定义', callback_data=f'custom:qty:{plan_id}:custom')
    kb.button(text='🔙 返回地区', callback_data='custom:regions')
    kb.adjust(5, 1, 1)
    return kb.as_markup()


def custom_payment_keyboard(plan_id: int, quantity: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text='💳 钱包支付', callback_data=f'custom:wallet:{plan_id}:{quantity}'),
        InlineKeyboardButton(text='🛒 加入购物车', callback_data=f'custom:qtycart:{plan_id}:{quantity}'),
    )
    kb.row(
        InlineKeyboardButton(text='💳 去购物车支付', callback_data='profile:cart'),
        InlineKeyboardButton(text='🔙 返回数量', callback_data=f'custom:plan:{plan_id}'),
    )
    return kb.as_markup()


def custom_currency_keyboard(plan_id: int | None, usdt_amount=None, trx_amount=None, order_id: int | None = None, quantity: int = 1):
    kb = InlineKeyboardBuilder()
    if plan_id is not None:
        kb.row(InlineKeyboardButton(text='💳 钱包支付', callback_data=f'custom:wallet:{plan_id}:{quantity}'))
        kb.row(InlineKeyboardButton(text='🔙 返回地区', callback_data='custom:regions'))
    else:
        kb.row(InlineKeyboardButton(text='💳 钱包支付', callback_data=f'custom:walletpay:{order_id}'))
        kb.row(InlineKeyboardButton(text='🔙 返回', callback_data='custom:regions'))
    return kb.as_markup()


def custom_wallet_keyboard(plan_id: int, quantity: int, usdt_amount=None, trx_amount=None):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=f'💳 钱包 USDT ({fmt_amount(usdt_amount)} U)', callback_data=f'custom:balance:{plan_id}:{quantity}:USDT'),
        InlineKeyboardButton(text=f'💳 钱包 TRX ({fmt_amount(trx_amount)} TRX)', callback_data=f'custom:balance:{plan_id}:{quantity}:TRX'),
    )
    kb.row(InlineKeyboardButton(text='🔙 返回地区', callback_data='custom:regions'))
    return kb.as_markup()


def custom_order_wallet_keyboard(order_id: int, usdt_amount, trx_amount):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=f'💳 钱包 USDT ({fmt_amount(usdt_amount)} U)', callback_data=f'custom:walletpay:{order_id}:USDT'),
        InlineKeyboardButton(text=f'💳 钱包 TRX ({fmt_amount(trx_amount)} TRX)', callback_data=f'custom:walletpay:{order_id}:TRX'),
    )
    kb.row(InlineKeyboardButton(text='🔙 返回', callback_data='custom:regions'))
    return kb.as_markup()


def custom_port_keyboard(order_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text='✅ 使用默认端口 9528', callback_data=f'custom:port:default:{order_id}'),
        InlineKeyboardButton(text='✍️ 输入自定义端口', callback_data=f'custom:port:custom:{order_id}'),
    )
    kb.row(InlineKeyboardButton(text='🔙 返回主菜单', callback_data='custom:back'))
    return kb.as_markup()


def cart_menu(items, total_amount):
    kb = InlineKeyboardBuilder()
    for item in items:
        title = item.cloud_plan.plan_name if getattr(item, 'cloud_plan', None) else item.product.name
        target_id = item.cloud_plan_id if getattr(item, 'cloud_plan_id', None) else item.product_id
        kb.row(InlineKeyboardButton(text=f'❌ 删除 {title} x{item.quantity}', callback_data=f'cart:remove:{target_id}'))
    if items:
        kb.row(
            InlineKeyboardButton(text='💳 余额结算 USDT', callback_data='cart:checkout:balance:USDT'),
            InlineKeyboardButton(text='🔗 地址结算 USDT', callback_data='cart:checkout:address:USDT'),
        )
        kb.row(InlineKeyboardButton(text='🗑 清空购物车', callback_data='cart:clear'))
    kb.row(InlineKeyboardButton(text='🔙 返回个人中心', callback_data='profile:back_to_menu'))
    return kb.as_markup()


def cloud_server_change_ip_region_menu(order_id: int, regions, expanded: bool = False):
    kb = InlineKeyboardBuilder()
    popular_regions, remaining_regions = _split_custom_regions(regions)
    display_regions = remaining_regions if expanded else popular_regions
    for region_code, region_name in display_regions:
        kb.button(text=region_name, callback_data=f'cloud:ipregion:{order_id}:{region_code}')
    if not expanded and remaining_regions:
        kb.button(text='更多', callback_data=f'cloud:ipregions:more:{order_id}')
        kb.adjust(3, 3)
        kb.button(text='🔙 返回详情', callback_data=f'cloud:detail:{order_id}')
        kb.adjust(3, 3, 1)
    elif expanded:
        kb.button(text='🔙 返回', callback_data=f'cloud:ip:{order_id}')
        rows = [3] * ((len(display_regions) + 2) // 3)
        kb.adjust(*rows, 1)
    else:
        kb.button(text='🔙 返回详情', callback_data=f'cloud:detail:{order_id}')
        rows = [3] * ((len(display_regions) + 2) // 3)
        kb.adjust(*rows, 1)
    return kb.as_markup()


def cloud_server_change_ip_port_keyboard(order_id: int, region_code: str, region_name: str):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text='✅ 使用默认端口 9528', callback_data=f'cloud:ipport:default:{order_id}:{region_code}'),
        InlineKeyboardButton(text='✍️ 输入自定义端口', callback_data=f'cloud:ipport:custom:{order_id}:{region_code}'),
    )
    kb.row(InlineKeyboardButton(text='🔙 返回地区', callback_data=f'cloud:ip:{order_id}'))
    return kb.as_markup()


def cloud_server_list(orders, page: int = 1, total_pages: int = 1, prefix: str = 'cloud:list'):
    kb = InlineKeyboardBuilder()
    for order in orders:
        ip = order.public_ip or order.previous_public_ip or '未分配IP'
        expires = order.service_expires_at.strftime('%Y-%m-%d') if getattr(order, 'service_expires_at', None) else '未设置'
        kb.button(text=f'{ip} | {expires}', callback_data=f'cloud:detail:{order.id}:{prefix}:{page}')
    kb.adjust(1)
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text='⬅️ 上一页', callback_data=f'{prefix}:{page - 1}'))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text='➡️ 下一页', callback_data=f'{prefix}:{page + 1}'))
    if nav:
        kb.row(*nav)
    back_callback = 'profile:orders' if prefix.startswith('profile:orders:cloud') else 'profile:orders'
    kb.row(InlineKeyboardButton(text='🔙 返回订单查询', callback_data=back_callback))
    return kb.as_markup()


def cloud_expiry_actions(order_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text='🔕 关闭提醒3天', callback_data=f'cloud:mute:{order_id}:3'),
        InlineKeyboardButton(text='🕒 延期10天', callback_data=f'cloud:delay:{order_id}:10'),
    )
    return kb.as_markup()


def cloud_server_renew_payment(order_id: int, amount, trx_amount, auto_renew_enabled: bool = False):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text=f'💳 USDT钱包支付 ({fmt_amount(amount)} U)', callback_data=f'cloud:renewpay:{order_id}:USDT'),
        InlineKeyboardButton(text=f'💳 TRX钱包支付 ({fmt_amount(trx_amount)} TRX)', callback_data=f'cloud:renewpay:{order_id}:TRX'),
    )
    if auto_renew_enabled:
        kb.row(InlineKeyboardButton(text='⛔ 关闭钱包自动续费', callback_data=f'cloud:autorenew:off:{order_id}'))
    else:
        kb.row(InlineKeyboardButton(text='⚡ 打开钱包自动续费', callback_data=f'cloud:autorenew:on:{order_id}'))
    kb.row(InlineKeyboardButton(text='🔙 返回详情', callback_data=f'cloud:detail:{order_id}'))
    return kb.as_markup()


def cloud_server_detail(order_id: int, can_renew: bool, can_change_ip: bool, can_reinit: bool = False, can_delay: bool = False, back_callback: str = 'cloud:list'):
    kb = InlineKeyboardBuilder()
    if can_renew:
        kb.button(text='🔄 续费', callback_data=f'cloud:renew:{order_id}')
    if can_delay:
        kb.button(text='🕒 延期', callback_data=f'cloud:delay:{order_id}:10')
    if can_change_ip:
        kb.button(text='🌐 更换IP', callback_data=f'cloud:ip:{order_id}')
    if can_reinit:
        kb.button(text='🛠 重新安装', callback_data=f'cloud:reinit:{order_id}')
    kb.button(text='🔙 返回列表', callback_data=back_callback)
    kb.adjust(2, 2, 1)
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


def wallet_recharge_prompt_menu():
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text='💰 去钱包充值', callback_data='profile:recharge'),
        InlineKeyboardButton(text='🔙 返回个人中心', callback_data='profile:back_to_menu'),
    )
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
    kb.row(InlineKeyboardButton(text='🔙 返回个人中心', callback_data='profile:back_to_menu'))
    return kb.as_markup()


def balance_details_list(items, page: int, total_pages: int):
    kb = InlineKeyboardBuilder()
    for item in items:
        icon = '🟢' if item['direction'] == 'in' else '🔴'
        kb.row(InlineKeyboardButton(
            text=f"{icon} {item['title'][:24]} | {item['amount']} {item['currency']}",
            callback_data=f"balance:detail:{item['id']}",
        ))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text='⬅️ 上一页', callback_data=f'bdpage:{page - 1}'))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text='➡️ 下一页', callback_data=f'bdpage:{page + 1}'))
    if nav:
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text='🔙 返回个人中心', callback_data='profile:back_to_menu'))
    return kb.as_markup()


def order_query_menu():
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text='📦 商品订单', callback_data='profile:orders:product'))
    kb.row(InlineKeyboardButton(text='☁️ 云服务器订单', callback_data='profile:orders:cloud'))
    kb.row(InlineKeyboardButton(text='🔎 IP查询到期', callback_data='cloud:queryip'))
    kb.row(InlineKeyboardButton(text='🔙 返回个人中心', callback_data='profile:back_to_menu'))
    return kb.as_markup()


def cloud_ip_query_result(result_items, renewable_items, page: int = 1, total_pages: int = 1):
    kb = InlineKeyboardBuilder()
    for item in renewable_items:
        ip = item.get('ip') or '未知IP'
        order_id = int(item.get('order_id') or 0)
        if order_id > 0:
            kb.row(InlineKeyboardButton(text=f'🔄 续费IP {ip}', callback_data=f'cloud:renew:{order_id}'))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text='⬅️ 上一页', callback_data=f'cloud:queryip:page:{page - 1}'))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text='➡️ 下一页', callback_data=f'cloud:queryip:page:{page + 1}'))
    if nav:
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text='🔙 返回个人中心', callback_data='profile:back_to_menu'))
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
    kb.row(InlineKeyboardButton(text='🔙 返回个人中心', callback_data='profile:back_to_menu'))
    return kb.as_markup()
