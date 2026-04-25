import asyncio
import os


CONFIG_HELP = {
    'bot_token': 'Telegram 机器人 Token',
    'receive_address': '收款地址（USDT/TRX 共用）',
    'trongrid_api_key': 'TRON API Key',
    'bot_admin_chat_id': '机器人管理员 Telegram Chat ID（支持逗号分隔多个转发目标）',
    'redis_host': 'Redis 主机',
    'redis_port': 'Redis 端口',
    'redis_password': 'Redis 密码',
    'redis_db': 'Redis 数据库编号',
    'mysql_database': 'MySQL 数据库名',
    'mysql_user': 'MySQL 用户名',
    'mysql_password': 'MySQL 密码',
    'mysql_host': 'MySQL 主机',
    'mysql_port': 'MySQL 端口',
    'text_init_enabled': '是否允许后台初始化文案（1=允许，0=禁用）',
    'text_init_mode': '文案初始化模式：missing_only 或 reset_defaults',
}

SENSITIVE_CONFIG_KEYS = {
    'bot_token',
    'trongrid_api_key',
    'mysql_password',
    'redis_password',
}


CONFIG_ENV_MAP = {
    'bot_token': 'BOT_TOKEN',
    'redis_host': 'REDIS_HOST',
    'redis_port': 'REDIS_PORT',
    'redis_password': 'REDIS_PASSWORD',
    'redis_db': 'REDIS_DB',
    'mysql_database': 'MYSQL_DATABASE',
    'mysql_user': 'MYSQL_USER',
    'mysql_password': 'MYSQL_PASSWORD',
    'mysql_host': 'MYSQL_HOST',
    'mysql_port': 'MYSQL_PORT',
    'receive_address': 'RECEIVE_ADDRESS',
    'trongrid_api_key': 'TRONGRID_API_KEY',
    'bot_admin_chat_id': 'BOT_ADMIN_CHAT_ID',
    'text_init_enabled': 'TEXT_INIT_ENABLED',
    'text_init_mode': 'TEXT_INIT_MODE',
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
