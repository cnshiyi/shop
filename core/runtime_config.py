import os


CONFIG_HELP = {
    'bot_token': 'Telegram 机器人访问令牌',
    'receive_address': '收款地址（USDT/TRX 共用）',
    'trongrid_api_key': 'TRONGrid 接口密钥',
    'redis_url': 'Redis 连接地址',
    'database_url': '数据库连接串（优先）',
    'mysql_database': 'MySQL 数据库名',
    'mysql_user': 'MySQL 用户名',
    'mysql_password': 'MySQL 密码',
    'mysql_host': 'MySQL 主机',
    'mysql_port': 'MySQL 端口',
    'telegram_api_hash': 'Telegram 登录应用密钥',
    'telegram_api_id': 'Telegram 登录应用 ID',
    'telegram_drop_pending_updates_on_start': '机器人启动时是否丢弃 Telegram 待处理更新',
}

SENSITIVE_CONFIG_KEYS = {
    'bot_token',
    'trongrid_api_key',
    'mysql_password',
    'telegram_api_hash',
    'database_url',
}


CONFIG_ENV_MAP = {
    'bot_token': 'BOT_TOKEN',
    'redis_url': 'REDIS_URL',
    'database_url': 'DATABASE_URL',
    'mysql_database': 'MYSQL_DATABASE',
    'mysql_user': 'MYSQL_USER',
    'mysql_password': 'MYSQL_PASSWORD',
    'mysql_host': 'MYSQL_HOST',
    'mysql_port': 'MYSQL_PORT',
    'receive_address': 'RECEIVE_ADDRESS',
    'telegram_api_hash': 'TELEGRAM_API_HASH',
    'telegram_api_id': 'TELEGRAM_API_ID',
    'telegram_drop_pending_updates_on_start': 'TELEGRAM_DROP_PENDING_UPDATES_ON_START',
    'trongrid_api_key': 'TRONGRID_API_KEY',
}


def _read_site_config(key: str, default: str = '') -> str:
    try:
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
