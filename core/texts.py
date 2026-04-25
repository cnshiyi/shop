from django.apps import apps


BOT_TEXTS = {
    'bot_welcome': ('欢迎使用商城机器人！请选择操作：', '机器人欢迎语'),
    'bot_removed_products_entry': ('商品购买入口已移除，请使用“🛠 定制节点”或“🔎 到期时间查询”。', '商品入口已移除提示'),
    'bot_custom_region_entry': ('🛠 云服务器定制\n\n请选择热门地区：', '定制节点入口文案'),
    'bot_query_center_entry': ('🔎 查询中心\n\n请选择查询方式：', '查询中心入口文案'),
    'bot_profile_entry_suffix': ('请选择要进入的功能：', '个人中心尾部提示'),
    'bot_query_ip_prompt': ('🔎 IP查询到期\n\n请输入要查询的 IP 地址：\n\n可随时点击底部菜单打断当前输入。', 'IP 查询输入提示'),
    'bot_query_ip_invalid': ('请输入包含 IP 或代理链接的文本内容。', 'IP 查询非法输入提示'),
    'bot_query_ip_empty': ('🔎 IP查询到期\n\n未查询到可续费的有效 IP 记录。', 'IP 查询无结果提示'),
    'bot_query_ip_expired': ('🔎 IP查询到期\n\n查询结果已失效，请重新输入 IP。', 'IP 查询结果失效提示'),
    'bot_orders_entry': ('📋 订单查询\n\n请选择要查看的订单类型：', '订单查询入口文案'),
    'bot_cloud_orders_empty': ('☁️ 云服务器订单\n\n暂无云服务器订单。', '云服务器订单为空提示'),
    'bot_cloud_orders_entry': ('☁️ 云服务器订单\n\n请选择要查看的订单：', '云服务器订单入口文案'),
    'bot_cart_removed': ('商品/购物车入口已移除，请使用云服务器相关功能。', '购物车入口已移除提示'),
    'bot_recharge_currency_prompt': ('💰 请选择充值币种：\n\n可随时点击底部菜单打断当前输入。', '充值币种选择提示'),
    'bot_monitor_entry': ('🔍 地址监控', '地址监控首页标题'),
    'bot_back_to_menu': ('已返回主菜单，请使用底部按钮继续操作。', '返回主菜单提示'),
    'bot_monitor_invalid_address': ('❌ 无效 TRON 地址，请重新输入：', '监控地址非法提示'),
    'bot_monitor_remark_prompt': ('请输入备注（可选，输入 - 跳过）：\n\n可随时点击底部菜单打断当前输入。', '监控备注输入提示'),
    'bot_monitor_invalid_usdt_threshold': ('❌ 请输入有效金额。', 'USDT 阈值非法提示'),
    'bot_monitor_invalid_trx_threshold': ('❌ 请输入有效金额。', 'TRX 阈值非法提示'),
    'bot_recharge_invalid_amount': ('❌ 请输入有效的正数金额。', '充值金额非法提示'),
    'bot_custom_quantity_invalid': ('请输入 1-99 的购买数量：\n\n可随时点击底部菜单打断当前输入。', '自定义数量非法提示'),
    'bot_custom_plan_missing': ('套餐不存在或已下架，请重新选择。', '套餐不存在提示'),
    'bot_custom_port_invalid': ('端口格式不正确，请输入 1025-65535 之间的数字。\n\n可随时点击底部菜单打断当前输入。', '自定义端口非法提示'),
    'bot_custom_context_missing': ('订单上下文已失效，请重新下单。', '自定义下单上下文失效提示'),
    'bot_change_ip_failed': ('更换IP失败，请返回详情页重试。', '更换 IP 失败提示'),
    'bot_set_port_failed': ('订单不存在，无法设置端口。', '设置端口失败提示'),
    'bot_monitor_address_prompt': ('请输入要监控的 TRON 地址：\n\n示例：<code>TD7cnQFUwDxPMSxruGELK6hs8YQm83Avco</code>\n\n可随时点击底部菜单打断当前输入。', '监控地址输入提示'),
    'bot_monitors_empty': ('暂无监控地址。', '监控列表为空提示'),
    'bot_monitors_list': ('📋 监控列表：', '监控列表标题'),
    'bot_monitor_missing': ('监控不存在。', '监控不存在提示'),
    'bot_monitor_threshold_currency_prompt': ('请选择要修改的阈值币种：', '监控阈值币种选择提示'),
    'bot_monitor_deleted': ('🗑 监控已删除。', '监控删除成功提示'),
    'bot_unknown_command': ('暂不支持这个命令，请使用菜单按钮操作。', '未知命令提示'),
    'bot_address_query_failed': ('地址查询失败，请稍后再试。', '地址查询失败提示'),
    'bot_plain_text_received': ('已收到你的消息。若是地址请直接发送地址，若是代理链接请直接发送链接。', '普通文本默认回复'),
    'bot_no_orders': ('暂无订单记录。', '订单列表为空提示'),
    'bot_orders_list_title': ('📋 我的订单：', '订单列表标题'),
    'bot_balance_details_empty': ('💳 余额明细\n\n暂无余额流水。', '余额明细为空提示'),
    'bot_recharges_empty': ('暂无充值记录。', '充值记录为空提示'),
    'bot_recharges_title': ('📜 充值记录：', '充值记录标题'),
    'bot_cloud_unassigned_pending': ('未分配IP说明: 订单未付款', '云服务器未分配 IP 提示-待支付'),
    'bot_cloud_unassigned_paid': ('未分配IP说明: 已支付但尚未完成，请联系人工处理', '云服务器未分配 IP 提示-处理中'),
    'bot_cloud_unassigned_failed': ('未分配IP说明: 创建失败，请联系人工处理', '云服务器未分配 IP 提示-失败'),
}

TEXT_GROUPS = {
    'custom_text': list(BOT_TEXTS.keys()),
}


def _site_config_model():
    return apps.get_model('core', 'SiteConfig')


def text_default(key: str, default: str = '') -> str:
    item = BOT_TEXTS.get(key)
    if item:
        return item[0]
    return default


def text_description(key: str, default: str = '') -> str:
    item = BOT_TEXTS.get(key)
    if item:
        return item[1]
    return default


def site_text(key: str, default: str = '') -> str:
    SiteConfig = _site_config_model()
    fallback = text_default(key, default)
    value = SiteConfig.get(key, fallback)
    return value if value else fallback


def all_text_keys() -> list[str]:
    return list(BOT_TEXTS.keys())


def init_texts(mode: str = 'missing_only') -> dict[str, int]:
    SiteConfig = _site_config_model()
    created = 0
    updated = 0
    mode = (mode or 'missing_only').strip() or 'missing_only'
    for key, (default_value, _) in BOT_TEXTS.items():
        item, was_created = SiteConfig.objects.get_or_create(
            key=key,
            defaults={'value': default_value, 'is_sensitive': False},
        )
        created += int(was_created)
        if was_created:
            continue
        if mode == 'reset_defaults':
            if (item.value or '') != default_value:
                SiteConfig.set(key, default_value, sensitive=item.is_sensitive)
                updated += 1
        elif not (item.value or '').strip():
            SiteConfig.set(key, default_value, sensitive=item.is_sensitive)
            updated += 1
    return {'created': created, 'updated': updated}
