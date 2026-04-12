import asyncio
import logging
import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')

import django
django.setup()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bot.config import BOT_TOKEN
from bot.fsm import close_fsm_storage
from bot.handlers import create_dispatcher_and_register
from biz.services import refresh_custom_plan_cache
from cloud.lifecycle import lifecycle_tick
from core.cache import refresh_config, close as cache_close
from monitoring.cache import init_monitor_cache
from tron.resource_checker import check_resources, set_bot as set_resource_bot
from tron.scanner import scan_block, set_bot

logger = logging.getLogger(__name__)
_scan_lock = asyncio.Lock()


async def _scan_block_job():
    if _scan_lock.locked():
        return
    async with _scan_lock:
        await scan_block()


async def run_bot():
    if not BOT_TOKEN:
        logger.warning('未配置 BOT_TOKEN，跳过机器人启动')
        return

    # 初始化 Redis 缓存
    try:
        await refresh_config(['receive_address', 'trongrid_api_key'])
        await init_monitor_cache()
        await refresh_custom_plan_cache()
    except Exception as e:
        logger.error('Redis 缓存初始化失败: %s', e)

    bot, dp = await create_dispatcher_and_register()
    set_bot(bot)
    set_resource_bot(bot)

    async def _notify(user_id: int, text: str):
        try:
            from accounts.models import TelegramUser
            user = await asyncio.to_thread(lambda: TelegramUser.objects.filter(id=user_id).first())
            if user:
                await bot.send_message(user.tg_user_id, text)
        except Exception as exc:
            logger.warning('生命周期通知发送失败 user=%s err=%s', user_id, exc)

    # TRON 扫块器 / 资源巡检 / 生命周期调度
    scheduler = AsyncIOScheduler()
    scheduler.add_job(_scan_block_job, 'interval', seconds=2, id='tron_scanner', coalesce=True)
    scheduler.add_job(check_resources, 'interval', minutes=3, id='tron_resource_checker', max_instances=1)
    scheduler.add_job(refresh_custom_plan_cache, 'interval', minutes=10, id='custom_plan_cache_refresh', max_instances=1, coalesce=True)
    scheduler.add_job(lifecycle_tick, 'interval', minutes=10, id='cloud_lifecycle', max_instances=1, kwargs={'notify': _notify})
    scheduler.start()
    logger.info('TRON 扫块器已启动 (每2秒)')
    logger.info('资源巡检已启动 (每3分钟)')
    logger.info('定制套餐缓存刷新已启动 (每10分钟)')
    logger.info('云服务器生命周期调度已启动 (每10分钟)')

    logger.info('Telegram Bot 已启动 (aiogram)')
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await cache_close()
        await close_fsm_storage()


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('apscheduler').setLevel(logging.ERROR)
    logging.getLogger('apscheduler.executors.default').setLevel(logging.ERROR)
    logging.getLogger('apscheduler.scheduler').setLevel(logging.ERROR)
    asyncio.run(run_bot())


if __name__ == '__main__':
    main()
