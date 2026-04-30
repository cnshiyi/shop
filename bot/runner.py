import asyncio
import logging
import os
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')

import django
django.setup()

from django.core.management import call_command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from bot.config import BOT_TOKEN
from bot.fsm import close_fsm_storage
from bot.handlers import create_dispatcher_and_register
from bot.telegram_listener import run_telegram_account_listeners
from bot.telegram_sender import send_with_notification_account
from cloud.services import refresh_custom_plan_cache
from cloud.lifecycle import lifecycle_tick, sync_server_status_tick, sync_cloud_accounts_tick
from core.cache import refresh_config, close as cache_close
from core.runtime_config import get_cloud_asset_sync_interval_seconds
from cloud.cache import init_monitor_cache
from orders.runtime import check_resources, scan_forever, set_bot, set_resource_bot

logger = logging.getLogger(__name__)


async def run_bot():
    if not BOT_TOKEN:
        logger.warning('未配置 BOT_TOKEN，跳过机器人启动')
        return

    # 初始化 Redis 缓存
    try:
        await refresh_config(['receive_address', 'trongrid_api_key'])
        await init_monitor_cache(force_log=True)
        await refresh_custom_plan_cache()
    except Exception as e:
        logger.error('Redis 缓存初始化失败: %s', e)

    bot, dp = await create_dispatcher_and_register()
    set_bot(bot)
    set_resource_bot(bot)

    async def _notify(user_id: int, text: str, reply_markup=None):
        from bot.models import TelegramUser

        user = await asyncio.to_thread(lambda: TelegramUser.objects.filter(id=user_id).first())
        if not user:
            return False
        try:
            await bot.send_message(user.tg_user_id, text, reply_markup=reply_markup, parse_mode='HTML')
            return True
        except Exception as exc:
            logger.warning('机器人生命周期通知发送失败 user=%s err=%s，尝试个人号通知', user_id, exc)
            try:
                sent = await send_with_notification_account(user.tg_user_id, text)
                if sent:
                    return True
                logger.warning('个人号生命周期通知无可用账号 user=%s', user_id)
            except Exception as fallback_exc:
                logger.warning('个人号生命周期通知发送失败 user=%s err=%s', user_id, fallback_exc)
        return False

    cloud_sync_interval_seconds = await asyncio.to_thread(get_cloud_asset_sync_interval_seconds)

    # TRON 扫块器 / 资源巡检 / 生命周期调度
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_resources, 'interval', minutes=3, id='tron_resource_checker', max_instances=1)
    scheduler.add_job(refresh_custom_plan_cache, 'interval', minutes=10, id='custom_plan_cache_refresh', max_instances=1, coalesce=True)
    scheduler.add_job(lifecycle_tick, 'interval', minutes=10, id='cloud_lifecycle', max_instances=1, kwargs={'notify': _notify})
    scheduler.add_job(sync_server_status_tick, 'interval', seconds=cloud_sync_interval_seconds, id='cloud_server_sync', max_instances=1, coalesce=True)
    scheduler.add_job(sync_cloud_accounts_tick, 'interval', minutes=15, id='cloud_account_check', max_instances=1, coalesce=True)
    scheduler.add_job(lambda: asyncio.to_thread(call_command, 'dedupe_servers'), 'interval', minutes=20, id='server_dedupe', max_instances=1, coalesce=True)
    scheduler.add_job(lambda: asyncio.to_thread(call_command, 'cleanup_old_records'), 'cron', hour=18, minute=0, id='old_records_cleanup', max_instances=1, coalesce=True)
    scheduler.start()
    scanner_stop = asyncio.Event()
    scanner_task = asyncio.create_task(scan_forever(scanner_stop))
    telegram_listener_stop = asyncio.Event()
    telegram_listener_task = asyncio.create_task(run_telegram_account_listeners(telegram_listener_stop))
    logger.info('TRON 顺序扫块器已启动（移除6秒调度限制）')
    logger.info('资源巡检已启动 (每3分钟)')
    logger.info('定制套餐缓存刷新已启动 (每10分钟)')
    logger.info('云服务器生命周期调度已启动 (每10分钟)')
    logger.info('云服务器状态同步已启动 (每%s秒)', cloud_sync_interval_seconds)
    logger.info('云账号状态巡检已启动 (每15分钟)')
    logger.info('服务器去重任务已启动 (每20分钟)')
    logger.info('旧订单/聊天记录自动清理任务已启动 (每天18:00)')

    logger.info('Telegram个人号消息监听调度已启动')
    logger.info('启动时执行云服务器生命周期检查')
    try:
        await lifecycle_tick(notify=_notify, defer_destructive_seconds=3600)
        logger.info('启动时云服务器生命周期检查完成')
    except Exception as exc:
        logger.exception('启动时云服务器生命周期检查失败: %s', exc)
    logger.info('Telegram Bot 已启动 (aiogram)')
    await bot.delete_webhook(drop_pending_updates=True)
    try:
        await dp.start_polling(bot)
    finally:
        scanner_stop.set()
        scanner_task.cancel()
        telegram_listener_stop.set()
        telegram_listener_task.cancel()
        await asyncio.gather(scanner_task, telegram_listener_task, return_exceptions=True)
        scheduler.shutdown(wait=False)
        await cache_close()
        await close_fsm_storage()


def main():
    log_dir = Path(__file__).resolve().parent.parent / 'tmp'
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / 'bot.log'
    formatter = logging.Formatter('%(asctime)s [%(levelname)s] %(name)s: %(message)s')

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.setLevel(logging.INFO)
    root_logger.handlers.clear()
    root_logger.addHandler(stream_handler)
    root_logger.addHandler(file_handler)

    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
    logging.getLogger('apscheduler').setLevel(logging.ERROR)
    logging.getLogger('aiogram.event').setLevel(logging.WARNING)
    logging.getLogger('apscheduler.executors.default').setLevel(logging.ERROR)
    logging.getLogger('apscheduler.scheduler').setLevel(logging.ERROR)
    asyncio.run(run_bot())


if __name__ == '__main__':
    main()
