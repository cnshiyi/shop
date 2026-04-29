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
    'cloud_renew_notice_debug_repeat': 'IP到期提醒调试重复开关（1=忽略已提醒记录，启动/定时检查都会重复提醒；0=只提醒一次）',
    'dashboard_totp_secret': '后台 Google Authenticator TOTP 密钥（Base32，更换后旧绑定失效）',
    'cleanup_retention_days': '自动清理保留天数，默认100天；订单和聊天记录超过该天数会被定时清理',
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
    'cloud_renew_notice_debug_repeat': 'CLOUD_RENEW_NOTICE_DEBUG_REPEAT',
    'dashboard_totp_secret': 'DASHBOARD_TOTP_SECRET',
    'cleanup_retention_days': 'CLEANUP_RETENTION_DAYS',
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
    return os.getenv(env_key, default)
