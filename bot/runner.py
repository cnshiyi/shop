import asyncio
import logging
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')

import django
django.setup()

from bot.config import BOT_TOKEN
from bot.handlers import create_dispatcher_and_register

logger = logging.getLogger(__name__)


async def run_bot():
    if not BOT_TOKEN:
        logger.warning('未配置 BOT_TOKEN，跳过机器人启动')
        return
    bot, dp = create_dispatcher_and_register()
    logger.info('Telegram Bot 已启动 (aiogram)')
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    asyncio.run(run_bot())


if __name__ == '__main__':
    main()
