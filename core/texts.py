import asyncio
import threading
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
    'bot_media_received': ('已收到你的媒体消息。', '收到媒体消息默认回复'),
    'bot_monitor_detail_instruction': ('📘 使用说明：\n1. 监控转账：地址收到 USDT/TRX 转账时通知。\n2. 监控资源：地址可用能量/带宽增加时通知；正常转账消耗不通知。', '监控使用说明'),
    'bot_monitor_updated': ('已更新', '监控更新成功提示'),
    'bot_monitor_threshold_prompt_usdt': ('请输入新的 USDT 阈值金额：\n\n可随时点击底部菜单打断当前输入。', 'USDT 阈值输入提示'),
    'bot_monitor_threshold_prompt_trx': ('请输入新的 TRX 阈值金额：\n\n可随时点击底部菜单打断当前输入。', 'TRX 阈值输入提示'),
    'bot_recharge_amount_prompt_usdt': ('💰 请输入需要充值的 USDT 金额：\n\n可随时点击底部菜单打断当前输入。', 'USDT 充值金额输入提示'),
    'bot_recharge_amount_prompt_trx': ('💰 请输入需要充值的 TRX 金额：\n\n可随时点击底部菜单打断当前输入。', 'TRX 充值金额输入提示'),
    'bot_tx_detail_expired': ('交易详情已过期', '交易详情过期提示'),
    'bot_resource_detail_expired': ('资源详情已过期', '资源详情过期提示'),
    'bot_cloud_create_success': ('✅ 云服务器创建完成', '云服务器创建成功标题'),
    'bot_cloud_retry_success': ('✅ 云服务器重试初始化完成', '云服务器重试初始化成功标题'),
    'bot_cloud_order_payment_note': ('订单 5 分钟有效，请在有效期内完成支付。\n\n系统已开始自动监控 USDT 和 TRX 到账，检测到支付成功后会自动进入后续流程。', '云服务器下单支付说明'),
    'bot_balance_detail_title': ('💳 余额明细', '余额明细标题'),
    'bot_custom_quantity_title': ('请选择购买数量', '云服务器数量页标题'),
    'bot_custom_quantity_hint': ('请选择数量，或输入自定义数量。', '云服务器数量页提示'),
    'bot_custom_payment_title': ('🧾 支付页面', '云服务器支付页标题'),
    'bot_custom_order_notice': ('系统已开始自动监控 USDT 和 TRX 到账，检测到支付成功后会自动进入后续流程。', '云服务器订单到账监控提示'),
    'bot_custom_wallet_title': ('请选择钱包支付币种：', '云服务器钱包支付币种页标题'),
    'bot_custom_pending_order': ('⏳ 正在后台创建订单，请稍候…\n\n创建完成后会主动把支付信息发给你。', '云服务器后台创建订单提示'),
    'bot_custom_balance_insufficient': ('❌ 余额不足，请先充值', '云服务器钱包余额不足提示'),
    'bot_custom_pending_wallet': ('⏳ 正在后台处理钱包支付，请稍候…\n\n处理完成后会主动把结果发给你。', '云服务器后台钱包支付处理中提示'),
    'bot_custom_port_hint': ('请选择 MTProxy 端口：默认端口是 9528，你也可以输入自定义端口。', '云服务器端口选择提示'),
    'bot_async_task_incomplete': ('⚠️ 云服务器{action_label}暂未完成\n订单ID: {order_id}\n当前状态: {current_status}\n请稍后在查询中心查看。', '云服务器异步任务未完成提示'),
    'bot_async_task_error': ('❌ 云服务器{action_label}任务异常\n订单ID: {order_id}\n错误: {error}', '云服务器异步任务异常提示'),
    'bot_create_order_failed': ('❌ 创建订单失败，请稍后重试。\n错误: {error}', '云服务器创建订单失败提示'),
    'bot_wallet_pay_failed': ('❌ 钱包支付失败，请稍后重试。\n错误: {error}', '钱包支付失败提示'),
    'bot_monitor_added': ('✅ 监控已添加: {address}', '监控添加成功提示'),
    'bot_monitor_usdt_threshold_updated': ('✅ USDT 阈值已更新为 {amount}', 'USDT 阈值更新成功提示'),
    'bot_monitor_trx_threshold_updated': ('✅ TRX 阈值已更新为 {amount}', 'TRX 阈值更新成功提示'),
    'bot_recharge_order_created': ('💰 充值订单已创建\n充值金额: {amount} {currency}\n支付金额: {pay_amount} {currency}\n收款地址: <code>{address}</code>\n\n⏰ 请在 30 分钟内转账精确金额到上述地址。', '充值订单创建成功提示'),
    'bot_custom_order_detail_title': ('🧾 订单详情', '云服务器订单详情标题'),
    'bot_custom_port_success': ('✅ 端口设置成功：{port}\n已开始后台创建服务器，我会在完成后主动通知你。', '云服务器端口设置成功提示'),
    'bot_ip_change_order_created': ('✅ 更换IP迁移单已创建\n新订单号: {order_no}\n新地区: {region_name}\n新端口: {port}\n旧服务器将于 5 天后到期，请尽快完成迁移。', '更换 IP 迁移单创建成功提示'),
    'bot_query_cloud_empty': ('🔎 查询中心\n\n暂无可查询的代理记录。', '查询中心无代理记录提示'),
    'bot_cloud_extend_success': ('🕒 已为订单 {order_no} 延期 {days} 天，系统将自动顺延删机前宽限时间。', '云服务器延期成功提示'),
    'bot_cloud_upgrade_submitted': ('⬆️ 已扣除升级差价并提交升级任务。\n新订单: {order_no}\n升级完成后会自动发送新的服务器信息，代理链接保持不变。', '云服务器升级提交成功提示'),
    'bot_reinstall_need_main_link': ('当前服务器缺少主代理链接。请直接发送这台服务器的主代理链接，我会先校验 IP、端口和服务器实际密钥，再让你确认是否重新安装。', '重装缺少主代理链接提示'),
    'bot_resume_init_confirm': ('⚠️ 确认继续初始化？\n\n系统会重新执行 BBR/MTProxy 安装并生成代理链接。', '继续初始化确认提示'),
    'bot_reinstall_confirm': ('⚠️ 确认重新安装？\n\n重新安装大约需要 5 分钟，期间代理可能会断连。系统会保持主/备用链接不变。', '重新安装确认提示'),
    'bot_reinstall_missing_order': ('服务器记录不存在，请重新进入云服务器详情。', '重装服务器记录不存在提示'),
    'bot_reinstall_invalid_link': ('链接格式不对，请发送 tg://proxy?... 或 https://t.me/proxy?... 主代理链接。', '重装主代理链接格式错误提示'),
    'bot_reinstall_validate_failed': ('校验失败：{reason}', '重装主代理链接校验失败提示'),
    'bot_reinstall_validate_ok': ('主代理链接校验通过。\n\n⚠️ 确认重新安装？重新安装大约需要 5 分钟，期间代理可能会断连。', '重装主代理链接校验成功提示'),
    'bot_reinstall_submitted': ('🛠 已确认{action_text}，后台会{work_text}。预计约 5 分钟，完成后会自动通知你。', '重装/继续初始化已提交提示'),
    'cloud_auto_renew_failed': ('❌ 自动续费失败\n\nIP: {ip}\n\n到期时间: {expires_at}\n\n失败原因: {error}\n\n请联系人工客服处理。', '自动续费失败通知'),
    'cloud_delete_notice': ('⚠️ 云服务器删机提醒\n订单号: {order_no}\n计划删机时间: {delete_at}\n如需保留，请尽快处理。', '云服务器删机提醒'),
    'cloud_ip_recycle_notice': ('📦 固定IP删除提醒\n订单号: {order_no}\n计划删除IP时间: {ip_recycle_at}\n如需保留，请尽快处理。', '固定 IP 删除提醒'),
    'cloud_expiring_notice': ('⏰ 云服务器即将到期\n订单号: {order_no}\n请尽快续费，未续费将按规则关机/删机。', '云服务器即将到期提醒'),
    'cloud_suspended_notice': ('⚠️ 云服务器已关机\n订单号: {order_no}\n如需继续使用，请尽快续费。', '云服务器已关机通知'),
    'cloud_instance_deleted_notice': ('🗑 云服务器实例已删除\n订单号: {order_no}\n固定 IP 仍保留，可在保留期内续费恢复。', '云服务器实例已删除通知'),
    'cloud_ip_retention_ended_notice': ('📦 云服务器固定 IP 保留期已结束\n订单号: {order_no}', '固定 IP 保留期结束通知'),
    'cloud_migration_old_deleted_notice': ('🧹 迁移期已结束，旧服务器已删除\n订单号: {order_no}', '迁移期旧服务器删除通知'),
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


def _threaded_site_config_get(key: str, fallback: str) -> str:
    result = {'value': fallback}

    def _read():
        SiteConfig = _site_config_model()
        result['value'] = SiteConfig.get(key, fallback)

    thread = threading.Thread(target=_read)
    thread.start()
    thread.join(timeout=5)
    return result['value'] or fallback


def site_text(key: str, default: str = '') -> str:
    from core.cache import _cached_config

    fallback = text_default(key, default)
    cached = _cached_config.get(key, '')
    if cached and key not in BOT_TEXTS:
        return cached
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        SiteConfig = _site_config_model()
        value = SiteConfig.get(key, fallback)
    else:
        value = _threaded_site_config_get(key, fallback)
    if value:
        _cached_config[key] = value
    return value if value else fallback


def all_text_keys() -> list[str]:
    return list(BOT_TEXTS.keys())


def init_texts(mode: str = 'missing_only') -> dict[str, int]:
    SiteConfig = _site_config_model()
    created = 0
    updated = 0
    mode = (mode or 'missing_only').strip() or 'missing_only'
    for index, (key, (default_value, _)) in enumerate(BOT_TEXTS.items(), start=1):
        item, was_created = SiteConfig.objects.get_or_create(
            key=key,
            defaults={'value': default_value, 'is_sensitive': False, 'sort_order': index},
        )
        created += int(was_created)
        if was_created:
            continue
        if not item.sort_order:
            item.sort_order = index
            item.save(update_fields=['sort_order'])
        if mode == 'reset_defaults':
            if (item.value or '') != default_value:
                SiteConfig.set(key, default_value, sensitive=item.is_sensitive)
                updated += 1
        elif not (item.value or '').strip():
            SiteConfig.set(key, default_value, sensitive=item.is_sensitive)
            updated += 1
    return {'created': created, 'updated': updated}
