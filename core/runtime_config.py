import asyncio
import os


CONFIG_HELP = {
    'bot_token': 'Telegram 机器人 Token',
    'receive_address': '收款地址（USDT/TRX 共用）',
    'trongrid_api_key': 'TRON API Key（支持多个：每行一个，或用逗号/分号分隔；请求会自动轮换）',
    'bot_admin_chat_id': '机器人管理员 Telegram Chat ID（支持逗号分隔多个转发目标）',
    'telegram_api_id': 'Telegram API ID（用于登录 Telegram 账号）',
    'telegram_api_hash': 'Telegram API Hash（用于登录 Telegram 账号）',
    'fsm_state_ttl': '机器人 FSM 状态缓存 TTL（秒）',
    'fsm_data_ttl': '机器人 FSM 数据缓存 TTL（秒）',
    'usdt_contract': 'TRON USDT 合约地址',
    'trongrid_base_url': 'TRON 节点基础地址',
    'scanner_verbose': 'TRON 扫描器详细日志开关（1=开启解析细节，0=关闭）',
    'scanner_block_log_enabled': 'TRON 扫块逐块日志开关（1=打印每个块，0=关闭）',
    'text_init_enabled': '是否允许后台初始化文案（1=允许，0=禁用）',
    'text_init_mode': '文案初始化模式：missing_only 或 reset_defaults',
    'cloud_renew_notice_days': 'IP到期提醒提前天数，默认5天',
    'cloud_suspend_after_days': 'IP到期后多少天关机，默认3天',
    'cloud_suspend_time': '到期后关机执行时间，格式HH:mm，默认15:00',
    'cloud_delete_after_days': '关机后多少天删机，默认0天',
    'cloud_delete_time': '到期后删机执行时间，格式HH:mm，默认15:00',
    'cloud_unattached_ip_delete_after_days': '未附加IP发现/到期后多少天删除，默认15天',
    'cloud_unattached_ip_delete_time': '未附加IP删除执行时间，格式HH:mm，默认15:00',
    'cloud_renew_notice_debug_repeat': 'IP到期提醒调试重复开关（1=忽略已提醒记录，启动/定时检查都会重复提醒；0=只提醒一次）',
    'dashboard_totp_secret': '后台 Google Authenticator TOTP 密钥（Base32，更换后旧绑定失效）',
    'cleanup_retention_days': '自动清理保留天数，默认100天；订单和聊天记录超过该天数会被定时清理',
    'cloud_asset_sync_interval_seconds': '代理云资产自动同步/列表自动刷新间隔（秒），默认18000秒=5小时',
    'cloud_auto_renew_execution_notify_enabled': '自动续费执行结果通知开关（1=开启，0=关闭）',
    'cloud_auto_renew_execution_notify_chat_ids': '自动续费执行结果通知目标 Chat ID（支持私聊/群/频道；多个用逗号、分号或换行分隔，频道也可填 @channelusername）',
    'cloud_auto_renew_execution_notify_events': '自动续费执行结果通知类型：all=成功和失败，success=仅成功，failure=仅失败',
}

CLOUD_ASSET_SYNC_INTERVAL_DEFAULT_SECONDS = 5 * 60 * 60
CLOUD_ASSET_SYNC_INTERVAL_MIN_SECONDS = 60

CONFIG_DEFAULTS = {
    'scanner_verbose': '0',
    'scanner_block_log_enabled': '0',
    'cloud_renew_notice_days': '5',
    'cloud_suspend_after_days': '3',
    'cloud_suspend_time': '15:00',
    'cloud_delete_after_days': '0',
    'cloud_delete_time': '15:00',
    'cloud_unattached_ip_delete_after_days': '15',
    'cloud_unattached_ip_delete_time': '15:00',
    'cloud_renew_notice_debug_repeat': '0',
    'cleanup_retention_days': '100',
    'cloud_asset_sync_interval_seconds': str(CLOUD_ASSET_SYNC_INTERVAL_DEFAULT_SECONDS),
    'cloud_auto_renew_execution_notify_enabled': '0',
    'cloud_auto_renew_execution_notify_chat_ids': '',
    'cloud_auto_renew_execution_notify_events': 'all',
}


SENSITIVE_CONFIG_KEYS = {
    'bot_token',
    'trongrid_api_key',
    'telegram_api_hash',
    'dashboard_totp_secret',
    'mysql_password',
    'redis_password',
}


CONFIG_ENV_MAP = {
    'bot_token': 'BOT_TOKEN',
    'receive_address': 'RECEIVE_ADDRESS',
    'trongrid_api_key': 'TRONGRID_API_KEY',
    'bot_admin_chat_id': 'BOT_ADMIN_CHAT_ID',
    'telegram_api_id': 'TELEGRAM_API_ID',
    'telegram_api_hash': 'TELEGRAM_API_HASH',
    'fsm_state_ttl': 'FSM_STATE_TTL',
    'fsm_data_ttl': 'FSM_DATA_TTL',
    'usdt_contract': 'USDT_CONTRACT',
    'trongrid_base_url': 'TRONGRID_BASE_URL',
    'scanner_verbose': 'SCANNER_VERBOSE',
    'scanner_block_log_enabled': 'SCANNER_BLOCK_LOG_ENABLED',
    'redis_host': 'REDIS_HOST',
    'redis_port': 'REDIS_PORT',
    'redis_password': 'REDIS_PASSWORD',
    'redis_db': 'REDIS_DB',
    'mysql_database': 'MYSQL_DATABASE',
    'mysql_user': 'MYSQL_USER',
    'mysql_password': 'MYSQL_PASSWORD',
    'mysql_host': 'MYSQL_HOST',
    'mysql_port': 'MYSQL_PORT',
    'text_init_enabled': 'TEXT_INIT_ENABLED',
    'text_init_mode': 'TEXT_INIT_MODE',
    'cloud_renew_notice_days': 'CLOUD_RENEW_NOTICE_DAYS',
    'cloud_suspend_after_days': 'CLOUD_SUSPEND_AFTER_DAYS',
    'cloud_suspend_time': 'CLOUD_SUSPEND_TIME',
    'cloud_delete_after_days': 'CLOUD_DELETE_AFTER_DAYS',
    'cloud_delete_time': 'CLOUD_DELETE_TIME',
    'cloud_unattached_ip_delete_after_days': 'CLOUD_UNATTACHED_IP_DELETE_AFTER_DAYS',
    'cloud_unattached_ip_delete_time': 'CLOUD_UNATTACHED_IP_DELETE_TIME',
    'cloud_renew_notice_debug_repeat': 'CLOUD_RENEW_NOTICE_DEBUG_REPEAT',
    'dashboard_totp_secret': 'DASHBOARD_TOTP_SECRET',
    'cleanup_retention_days': 'CLEANUP_RETENTION_DAYS',
    'cloud_asset_sync_interval_seconds': 'CLOUD_ASSET_SYNC_INTERVAL_SECONDS',
    'cloud_auto_renew_execution_notify_enabled': 'CLOUD_AUTO_RENEW_EXECUTION_NOTIFY_ENABLED',
    'cloud_auto_renew_execution_notify_chat_ids': 'CLOUD_AUTO_RENEW_EXECUTION_NOTIFY_CHAT_IDS',
    'cloud_auto_renew_execution_notify_events': 'CLOUD_AUTO_RENEW_EXECUTION_NOTIFY_EVENTS',
}


def _read_site_config(key: str, default: str = '') -> str:
    try:
        try:
            asyncio.get_running_loop()
            return default
        except RuntimeError:
            pass
        from django.apps import apps

        if not apps.ready:
            return default
        SiteConfig = apps.get_model('core', 'SiteConfig')
        if not SiteConfig:
            return default
        return SiteConfig.get(key, default)
    except Exception:
        return default


def get_runtime_config(key: str, default: str = '') -> str:
    value = _read_site_config(key, '')
    if value:
        return value
    env_key = CONFIG_ENV_MAP.get(key, key.upper())
    fallback = default if default != '' else CONFIG_DEFAULTS.get(key, '')
    return os.getenv(env_key, fallback)


def get_cloud_asset_sync_interval_seconds() -> int:
    raw = get_runtime_config(
        'cloud_asset_sync_interval_seconds',
        str(CLOUD_ASSET_SYNC_INTERVAL_DEFAULT_SECONDS),
    )
    try:
        seconds = int(str(raw or '').strip())
    except (TypeError, ValueError):
        seconds = CLOUD_ASSET_SYNC_INTERVAL_DEFAULT_SECONDS
    return max(seconds, CLOUD_ASSET_SYNC_INTERVAL_MIN_SECONDS)
