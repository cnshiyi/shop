import logging

from django.utils import timezone
from aiogram.types import InlineKeyboardButton, KeyboardButton
from aiogram.utils.keyboard import InlineKeyboardBuilder, ReplyKeyboardBuilder

from core.formatters import fmt_amount

logger = logging.getLogger(__name__)


def _log_inline_keyboard(name: str, markup, **context):
    rows = []
    for row in getattr(markup, 'inline_keyboard', None) or []:
        rows.append([
            {
                'text': getattr(button, 'text', None),
                'callback_data': getattr(button, 'callback_data', None),
                'url': getattr(button, 'url', None),
            }
            for button in row
        ])
    logger.info('BOT_KEYBOARD_LOAD name=%s context=%s rows=%s', name, context, rows)
    return markup


def _format_local_date(value):
    if not value:
        return '未设置'
    try:
        return timezone.localtime(value).strftime('%Y-%m-%d')
    except Exception:
        return str(value)


def main_menu():
    from core.button_config import load_button_config

    config = load_button_config()
    row_size = config.get('row_size') or 2
    kb = ReplyKeyboardBuilder()
    items = [item for item in config.get('items', []) if item.get('enabled', True)]
    for item in items:
        label = str(item.get('label') or '').strip()
        if not label:
            continue
        url = str(item.get('url') or '').strip() if item.get('type') == 'link' else ''
        if url:
            try:
                kb.add(KeyboardButton(text=label, web_app=None))
            except TypeError:
                kb.button(text=label)
        else:
            kb.button(text=label)
    kb.adjust(*([row_size] * max(1, len(items))))
    return kb.as_markup(resize_keyboard=True)


def configured_link_for_label(label: str) -> dict | None:
    from core.button_config import load_button_config

    target = str(label or '').strip()
    for item in load_button_config().get('items', []):
        if not item.get('enabled', True) or item.get('type') != 'link':
            continue
        if str(item.get('label') or '').strip() == target:
            return item
    return None


def configured_link_menu(label: str):
    item = configured_link_for_label(label)
    if not item:
        return None
    url = str(item.get('url') or '').strip()
    if not url:
        return None
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=str(item.get('button_label') or item.get('label') or '打开链接'), url=url))
    kb.row(InlineKeyboardButton(text='🔙 返回主菜单', callback_data='profile:back'))
    return _log_inline_keyboard('configured_link_menu', kb.as_markup(), label=label, url=url)




def cloud_query_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text='🖥 代理列表', callback_data='cloud:list')
    kb.button(text='⚡ 自动续费查询', callback_data='cloud:autorenewlist')
    kb.button(text='🔎 IP查询到期', callback_data='cloud:queryip')
    kb.button(text='🔙 返回主菜单', callback_data='profile:back')
    kb.adjust(2, 2)
    return kb.as_markup()


def profile_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text='📋 我的订单', callback_data='profile:orders')
    kb.button(text='💰 充值余额', callback_data='profile:recharge')
    kb.button(text='📜 充值记录', callback_data='profile:recharges')
    kb.button(text='💳 余额明细', callback_data='profile:balance_details')
    kb.button(text='🔔 提醒列表', callback_data='profile:reminders')
    kb.button(text='🔍 地址监控', callback_data='profile:monitors')
    kb.button(text='🔙 返回主菜单', callback_data='profile:back')
    kb.adjust(2, 2, 2, 1)
    return kb.as_markup()


def reminder_list_menu(orders=None, is_muted: bool = False, page: int = 1, total_pages: int = 1):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text='🔔 一键开启全部提醒', callback_data='profile:reminders:unmuteall'),
        InlineKeyboardButton(text='🔕 一键关闭全部提醒', callback_data='profile:reminders:muteall'),
    )
    for order in orders or []:
        label = order.public_ip or order.previous_public_ip or order.order_no
        if len(str(label)) > 22:
            label = f'{str(label)[:19]}...'
        enabled_count = sum(1 for field in ('cloud_reminder_enabled', 'suspend_reminder_enabled', 'delete_reminder_enabled', 'ip_recycle_reminder_enabled') if getattr(order, field, True))
        auto = '⚡' if getattr(order, 'auto_renew_enabled', False) else '⏸'
        kb.row(InlineKeyboardButton(text=f'🌐 {label}  提醒{enabled_count}/4 {auto}', callback_data=f'profile:reminders:ip:{order.id}:{page}'))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text='⬅️ 上一页', callback_data=f'profile:reminders:page:{page - 1}'))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text='➡️ 下一页', callback_data=f'profile:reminders:page:{page + 1}'))
    nav.append(InlineKeyboardButton(text='🔙 返回个人中心', callback_data='profile:back_to_menu'))
    kb.row(*nav)
    return _log_inline_keyboard('reminder_list_menu', kb.as_markup(), is_muted=is_muted, order_count=len(orders or []), page=page, total_pages=total_pages)


def reminder_ip_detail_menu(order, page: int = 1):
    kb = InlineKeyboardBuilder()
    reminder_enabled = bool(getattr(order, 'cloud_reminder_enabled', True))
    suspend_enabled = bool(getattr(order, 'suspend_reminder_enabled', True))
    delete_enabled = bool(getattr(order, 'delete_reminder_enabled', True))
    ip_recycle_enabled = bool(getattr(order, 'ip_recycle_reminder_enabled', True))
    auto_enabled = bool(getattr(order, 'auto_renew_enabled', False))
    for reminder_type, label, enabled in (
        ('expiry', '到期提醒', reminder_enabled),
        ('suspend', '停机提醒', suspend_enabled),
        ('delete', '删机提醒', delete_enabled),
        ('ip_recycle', 'IP保留期提醒', ip_recycle_enabled),
    ):
        kb.row(InlineKeyboardButton(
            text=f'{"🔕 关闭" if enabled else "🔔 开启"}{label}',
            callback_data=f'profile:reminders:order:{reminder_type}:{"off" if enabled else "on"}:{order.id}:{page}',
        ))
    kb.row(InlineKeyboardButton(
        text='⏸ 关闭自动续费提醒/续费' if auto_enabled else '⚡ 开启自动续费提醒/续费',
        callback_data=f'profile:reminders:auto:{"off" if auto_enabled else "on"}:{order.id}:{page}',
    ))
    kb.row(InlineKeyboardButton(text='🔙 返回提醒列表', callback_data=f'profile:reminders:page:{page}'))
    return _log_inline_keyboard('reminder_ip_detail_menu', kb.as_markup(), order_id=getattr(order, 'id', None), page=page, reminder_enabled=reminder_enabled, suspend_enabled=suspend_enabled, delete_enabled=delete_enabled, ip_recycle_enabled=ip_recycle_enabled, auto_enabled=auto_enabled)


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
        InlineKeyboardButton(text='💵 USDT转账', callback_data=f'mon:setthr:{monitor_id}:USDT'),
        InlineKeyboardButton(text='🪙 TRX转账', callback_data=f'mon:setthr:{monitor_id}:TRX'),
    )
    kb.row(
        InlineKeyboardButton(text='⚡ 能量增加', callback_data=f'mon:setthr:{monitor_id}:ENERGY'),
        InlineKeyboardButton(text='📶 带宽增加', callback_data=f'mon:setthr:{monitor_id}:BANDWIDTH'),
    )
    kb.row(InlineKeyboardButton(text='🔙 返回', callback_data=f'mon:detail:{monitor_id}'))
    return kb.as_markup()


def _split_custom_regions(regions):
    preferred_codes = ['ap-southeast-1', 'cn-hongkong', 'ap-northeast-1', 'ap-northeast-2', 'us-east-1']
    name_overrides = {
        'ap-southeast-1': '新加坡',
        'cn-hongkong': '香港',
        'ap-northeast-1': '日本',
        'ap-northeast-2': '韩国',
        'us-east-1': '美国',
    }
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


def custom_payment_keyboard(order_id: int, plan_id: int, quantity: int):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text='💳 钱包支付', callback_data=f'custom:walletpay:{order_id}'))
    kb.row(InlineKeyboardButton(text='🔙 返回数量', callback_data=f'custom:plan:{plan_id}'))
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
    has_switch_rows = False
    for order in orders:
        ip = order.public_ip or order.previous_public_ip
        label = ip or getattr(order, 'order_no', None) or f'订单 {order.id}'
        expires_at = getattr(order, 'service_expires_at', None) or getattr(order, 'actual_expires_at', None) or getattr(order, 'expires_at', None)
        expires = _format_local_date(expires_at)
        auto_enabled = bool(getattr(order, 'auto_renew_enabled', False))
        auto_icon = '✅' if auto_enabled else '❌'
        item_kind = getattr(order, '_proxy_item_kind', '')
        if prefix.startswith('cloud:list') and item_kind in {'asset', 'server'}:
            detail_data = f'cloud:assetdetail:{item_kind}:{order.id}:{prefix}:{page}'
            toggle_action = 'off' if auto_enabled else 'on'
            toggle_data = f'cloud:list:autorenew:{toggle_action}:{order.id}:{page}'
            kb.row(
                InlineKeyboardButton(text=f'{label} | {expires} | {auto_icon}', callback_data=detail_data),
                InlineKeyboardButton(text='🔔 自动续费' if not auto_enabled else '🔕 自动续费', callback_data=toggle_data),
            )
            has_switch_rows = True
            continue
        if item_kind in {'asset', 'server'}:
            callback_data = f'cloud:assetdetail:{item_kind}:{order.id}:{prefix}:{page}'
        else:
            callback_data = f'cloud:detail:{order.id}:{prefix}:{page}'
        kb.button(text=f'{label} | {expires}', callback_data=callback_data)
    if not has_switch_rows:
        kb.adjust(1)
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text='⬅️ 上一页', callback_data=f'{prefix}:{page - 1}'))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text='➡️ 下一页', callback_data=f'{prefix}:{page + 1}'))
    if prefix.startswith('profile:orders:cloud'):
        back_callback = 'profile:orders'
        back_text = '🔙 返回订单查询'
    elif prefix.startswith('cloud:'):
        back_callback = 'cloud:querymenu'
        back_text = '🔙 返回到期时间查询'
    else:
        back_callback = 'profile:back_to_menu'
        back_text = '🔙 返回个人中心'
    nav.append(InlineKeyboardButton(text=back_text, callback_data=back_callback))
    kb.row(*nav)
    return _log_inline_keyboard(
        'cloud_server_list',
        kb.as_markup(),
        page=page,
        total_pages=total_pages,
        prefix=prefix,
        order_ids=[getattr(order, 'id', None) for order in orders],
    )


def cloud_auto_renew_server_list(orders, page: int = 1, total_pages: int = 1, *, is_admin: bool = False):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text='✅ 一键开启', callback_data=f'cloud:autorenewlist:all:on:{page}'),
        InlineKeyboardButton(text='❌ 一键关闭', callback_data=f'cloud:autorenewlist:all:off:{page}'),
    )
    for order in orders:
        order_id = getattr(order, 'order_id', None) or getattr(order, 'id', None)
        if not order_id:
            continue
        ip = order.public_ip or order.previous_public_ip
        label = ip or getattr(order, 'order_no', None) or f'订单 {order_id}'
        user_label = ''
        if is_admin:
            username = getattr(order, 'username', '') or ''
            first_name = getattr(order, 'first_name', '') or ''
            tg_user_id = getattr(order, 'user_tg_id', None)
            user_label = username and f' @{username}' or first_name or (str(tg_user_id) if tg_user_id else '')
            user_label = f' | {user_label}' if user_label else ''
        expires_at = getattr(order, 'service_expires_at', None) or getattr(order, 'actual_expires_at', None) or getattr(order, 'expires_at', None)
        expires = _format_local_date(expires_at)
        enabled = bool(getattr(order, 'auto_renew_enabled', False))
        bell = '🔔' if enabled else '🔕'
        icon = '✅' if enabled else '❌'
        status_text = '已开启' if enabled else '已关闭'
        action = 'off' if enabled else 'on'
        kb.row(InlineKeyboardButton(text=f'{bell} {label}{user_label} | 到期 {expires} | {icon} {status_text}', callback_data=f'cloud:autorenewlist:{action}:{order_id}:{page}'))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text='⬅️ 上一页', callback_data=f'cloud:autorenewlist:page:{page - 1}'))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text='➡️ 下一页', callback_data=f'cloud:autorenewlist:page:{page + 1}'))
    nav.append(InlineKeyboardButton(text='🔙 返回到期时间查询', callback_data='cloud:querymenu'))
    kb.row(*nav)
    return _log_inline_keyboard(
        'cloud_auto_renew_server_list',
        kb.as_markup(),
        page=page,
        total_pages=total_pages,
        is_admin=is_admin,
        order_ids=[getattr(order, 'order_id', None) or getattr(order, 'id', None) for order in orders],
    )


def _support_contact_url() -> str:
    try:
        from core.button_config import load_button_config
        items = load_button_config().get('items', [])
    except Exception:
        return ''
    preferred_keys = {'contact_support', 'support_contact', 'customer_service', 'support'}
    for item in items:
        if not item.get('enabled', True) or item.get('type') != 'link':
            continue
        key = str(item.get('key') or '').strip()
        label = str(item.get('label') or item.get('button_label') or '').strip()
        if key in preferred_keys or '客服' in label or '联系人工' in label or '联系' in label and '人工' in label:
            return str(item.get('url') or '').strip()
    return ''


def support_contact_button(context: str, target_id: int | str | None = None, text: str = '👩‍💻 联系客服') -> InlineKeyboardButton:
    url = _support_contact_url()
    if url:
        return InlineKeyboardButton(text=text, url=url)
    suffix = f':{target_id}' if target_id not in (None, '') else ''
    return InlineKeyboardButton(text=text, callback_data=f'support:contact:{context}{suffix}')


def cloud_lifecycle_notice_actions(order_id: int, context: str = 'cloud_lifecycle'):
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text='🔕 关闭提醒', callback_data=f'cloud:mute:{order_id}'),
        support_contact_button(context, order_id),
    )
    return _log_inline_keyboard('cloud_lifecycle_notice_actions', kb.as_markup(), order_id=order_id, context=context)


def cloud_expiry_actions(order_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text='🔄 立即续费', callback_data=f'cloud:renew:{order_id}'))
    kb.row(
        InlineKeyboardButton(text='⚡ 开启自动续费', callback_data=f'cloud:autorenew:on:{order_id}'),
        InlineKeyboardButton(text='⛔ 关闭自动续费', callback_data=f'cloud:autorenew:off:{order_id}'),
    )
    kb.row(
        InlineKeyboardButton(text='🔕 关闭提醒', callback_data=f'cloud:mute:{order_id}'),
        support_contact_button('cloud_expiry', order_id),
    )
    return _log_inline_keyboard('cloud_expiry_actions', kb.as_markup(), order_id=order_id)


def cloud_auto_renew_notice_actions(order_id: int):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text='⛔ 关闭自动续费', callback_data=f'cloud:autorenew:off:{order_id}'))
    kb.row(
        InlineKeyboardButton(text='🔕 关闭提醒', callback_data=f'cloud:mute:{order_id}'),
        support_contact_button('cloud_autorenew', order_id),
    )
    return _log_inline_keyboard('cloud_auto_renew_notice_actions', kb.as_markup(), order_id=order_id)


def cloud_server_renew_payment(order_id: int, amount, trx_amount, auto_renew_enabled: bool = False):
    kb = InlineKeyboardBuilder()
    kb.row(InlineKeyboardButton(text=f'💳 USDT钱包支付 ({fmt_amount(amount)} U)', callback_data=f'cloud:renewpay:{order_id}:USDT'))
    kb.row(InlineKeyboardButton(text=f'💳 TRX钱包支付 ({fmt_amount(trx_amount)} TRX)', callback_data=f'cloud:renewpay:{order_id}:TRX'))
    if auto_renew_enabled:
        kb.row(InlineKeyboardButton(text='⛔ 关闭钱包自动续费', callback_data=f'cloud:autorenew:off:{order_id}'))
    else:
        kb.row(InlineKeyboardButton(text='⚡ 打开钱包自动续费', callback_data=f'cloud:autorenew:on:{order_id}'))
    kb.row(InlineKeyboardButton(text='🔙 返回详情', callback_data=f'cloud:detail:{order_id}'))
    return _log_inline_keyboard(
        'cloud_server_renew_payment',
        kb.as_markup(),
        order_id=order_id,
        amount=str(amount),
        trx_amount=str(trx_amount),
        auto_renew_enabled=auto_renew_enabled,
    )


def cloud_server_detail(order_id: int, can_renew: bool, can_change_ip: bool, can_reinit: bool = False, can_delay: bool = False, back_callback: str = 'cloud:list', can_upgrade: bool = False, can_refund: bool = False, can_resume_init: bool = False):
    kb = InlineKeyboardBuilder()
    if can_renew:
        kb.button(text='🔄 续费', callback_data=f'cloud:renew:{order_id}')
    if can_delay:
        kb.button(text='🕒 延期', callback_data=f'cloud:delay:{order_id}:10')
    if can_change_ip:
        kb.button(text='🌐 更换IP', callback_data=f'cloud:ip:{order_id}')
    if can_resume_init:
        kb.button(text='🛠 继续初始化', callback_data=f'cloud:reinit:{order_id}')
    elif can_reinit:
        kb.button(text='🛠 重新安装', callback_data=f'cloud:reinit:{order_id}')
    if can_upgrade:
        kb.button(text='⬆️ 升级配置', callback_data=f'cloud:upgrade:{order_id}')
    if can_refund:
        kb.button(text='💸 退款', callback_data=f'cloud:refund:{order_id}')
    kb.button(text='🔙 返回列表', callback_data=back_callback)
    kb.adjust(2, 2, 2, 1)
    return _log_inline_keyboard(
        'cloud_server_detail',
        kb.as_markup(),
        order_id=order_id,
        can_renew=can_renew,
        can_change_ip=can_change_ip,
        can_reinit=can_reinit,
        can_delay=can_delay,
        can_upgrade=can_upgrade,
        can_refund=can_refund,
        can_resume_init=can_resume_init,
        back_callback=back_callback,
    )


def cloud_order_list(orders, page: int = 1, total_pages: int = 1, prefix: str = 'profile:orders:cloud:page'):
    kb = InlineKeyboardBuilder()
    status_labels = {
        'pending': '待支付',
        'paid': '已支付',
        'provisioning': '开通中',
        'completed': '已完成',
        'expiring': '即将到期',
        'suspended': '已暂停',
        'renew_pending': '续费待支付',
        'failed': '开通失败',
        'cancelled': '已取消',
        'deleted': '已删除',
        'expired': '已过期',
    }
    for order in orders:
        status = status_labels.get(getattr(order, 'status', '') or '') or getattr(order, 'status', '') or '-'
        amount = fmt_amount(getattr(order, 'pay_amount', None) or getattr(order, 'total_amount', None) or 0)
        kb.row(InlineKeyboardButton(
            text=f'{getattr(order, "order_no", "-")} | {status} | {amount} {getattr(order, "currency", "")}',
            callback_data=f'cloud:orderdetail:{order.id}:{prefix}:{page}',
        ))
    nav = []
    if page > 1:
        nav.append(InlineKeyboardButton(text='⬅️ 上一页', callback_data=f'{prefix}:{page - 1}'))
    if page < total_pages:
        nav.append(InlineKeyboardButton(text='➡️ 下一页', callback_data=f'{prefix}:{page + 1}'))
    if nav:
        kb.row(*nav)
    kb.row(InlineKeyboardButton(text='🔙 返回个人中心', callback_data='profile:back_to_menu'))
    return _log_inline_keyboard(
        'cloud_order_list',
        kb.as_markup(),
        page=page,
        total_pages=total_pages,
        prefix=prefix,
        order_ids=[getattr(order, 'id', None) for order in orders],
    )


def cloud_order_readonly_detail(order_id: int, back_callback: str = 'profile:orders:cloud:page:1'):
    kb = InlineKeyboardBuilder()
    kb.row(support_contact_button('cloud_order', order_id))
    kb.row(InlineKeyboardButton(text='🔙 返回订单列表', callback_data=back_callback))
    return _log_inline_keyboard('cloud_order_readonly_detail', kb.as_markup(), order_id=order_id, back_callback=back_callback)


def recharge_currency_menu():
    kb = InlineKeyboardBuilder()
    kb.row(
        InlineKeyboardButton(text='💵 USDT', callback_data='rcur:USDT'),
        InlineKeyboardButton(text='🪙 TRX', callback_data='rcur:TRX'),
    )
    return kb.as_markup()


def product_list(products, page: int, total_pages: int):
    kb = InlineKeyboardBuilder()
    for p in products:
        stock = '无限' if p.stock == -1 else str(p.stock)
        kb.row(InlineKeyboardButton(
            text=f'{p.name} - {fmt_amount(p.price)} USDT (库存:{stock})',
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
            text=f'{sm.get(o.status, "")} {o.order_no} | {o.product_name} | {fmt_amount(o.total_amount)} {o.currency}',
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
    kb.row(
        InlineKeyboardButton(text='☁️ 云服务器订单', callback_data='profile:orders:cloud'),
        InlineKeyboardButton(text='🔎 IP查询到期', callback_data='cloud:queryip'),
    )
    kb.row(InlineKeyboardButton(text='🔙 返回个人中心', callback_data='profile:back_to_menu'))
    return kb.as_markup()


def cloud_ip_query_result(result_items, renewable_items, page: int = 1, total_pages: int = 1, include_start: bool = False):
    kb = InlineKeyboardBuilder()
    for item in renewable_items:
        ip = item.get('ip') or '未知IP'
        order_id = int(item.get('order_id') or 0)
        if order_id > 0:
            row = [InlineKeyboardButton(text=f'🔄 续费IP {ip}', callback_data=f'cloud:renew:{order_id}')]
            if include_start:
                row.append(InlineKeyboardButton(text='▶️ 开机', callback_data=f'cloud:start:{order_id}'))
            kb.row(*row)
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
            text=f'{sm.get(r.status, "")} {fmt_amount(r.amount)} {r.currency} | {r.created_at:%m-%d %H:%M}',
            callback_data=f'rdetail:{r.id}',
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
