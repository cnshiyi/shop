import asyncio
import logging
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')

import django
django.setup()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bot.config import BOT_TOKEN
from bot.handlers import create_dispatcher_and_register
from tron.cache import init_all, close as cache_close
from tron.scanner import scan_block, set_bot

logger = logging.getLogger(__name__)


async def run_bot():
    if not BOT_TOKEN:
        logger.warning('未配置 BOT_TOKEN，跳过机器人启动')
        return

    # 初始化 Redis 缓存
    try:
        await init_all()
    except Exception as e:
        logger.error('Redis 缓存初始化失败: %s', e)

    bot, dp = create_dispatcher_and_register()
    set_bot(bot)

    # TRON 扫块器
    scheduler = AsyncIOScheduler()
    scheduler.add_job(scan_block, 'interval', seconds=2, id='tron_scanner', max_instances=1)
    scheduler.start()
    logger.info('TRON 扫块器已启动 (每2秒)')

    logger.info('Telegram Bot 已启动 (aiogram)')
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    asyncio.run(run_bot())


if __name__ == '__main__':
    main()
