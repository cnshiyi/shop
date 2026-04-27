import asyncio
import os


CONFIG_HELP = {
    'bot_token': 'Telegram 机器人 Token',
    'receive_address': '收款地址（USDT/TRX 共用）',
    'trongrid_api_key': 'TRON API Key',
    'bot_admin_chat_id': '机器人管理员 Telegram Chat ID（支持逗号分隔多个转发目标）',
    'telegram_api_id': 'Telegram API ID（用于登录 Telegram 账号）',
    'telegram_api_hash': 'Telegram API Hash（用于登录 Telegram 账号）',
    'fsm_state_ttl': '机器人 FSM 状态缓存 TTL（秒）',
    'fsm_data_ttl': '机器人 FSM 数据缓存 TTL（秒）',
    'usdt_contract': 'TRON USDT 合约地址',
    'trongrid_base_url': 'TRON 节点基础地址',
    'scanner_verbose': 'TRON 扫描器详细日志开关（1=开启，0=关闭）',
    'text_init_enabled': '是否允许后台初始化文案（1=允许，0=禁用）',
    'text_init_mode': '文案初始化模式：missing_only 或 reset_defaults',
    'cloud_renew_notice_days': 'IP到期提醒提前天数，默认5天',
    'cloud_renew_notice_debug_repeat': 'IP到期提醒调试重复开关（1=忽略已提醒记录，启动/定时检查都会重复提醒；0=只提醒一次）',
    'github_oauth_client_id': 'GitHub OAuth Client ID（后台登录）',
    'github_oauth_client_secret': 'GitHub OAuth Client Secret（后台登录）',
    'github_oauth_allowed_users': '允许登录后台的 GitHub 用户名或邮箱，逗号分隔',
    'dashboard_password_login_enabled': '是否允许后台账号密码登录（1=允许，0=禁用）',
}

SENSITIVE_CONFIG_KEYS = {
    'bot_token',
    'trongrid_api_key',
    'telegram_api_hash',
    'github_oauth_client_secret',
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
    'github_oauth_client_id': 'GITHUB_OAUTH_CLIENT_ID',
    'github_oauth_client_secret': 'GITHUB_OAUTH_CLIENT_SECRET',
    'github_oauth_allowed_users': 'GITHUB_OAUTH_ALLOWED_USERS',
    'dashboard_password_login_enabled': 'DASHBOARD_PASSWORD_LOGIN_ENABLED',
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
