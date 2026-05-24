import asyncio
import contextlib
import logging
import os
import time
from pathlib import Path

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'shop.settings')

import django
django.setup()

from django.core.management import call_command
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aiogram.exceptions import TelegramNetworkError
from bot.config import BOT_TOKEN
from bot.fsm import close_fsm_storage
from bot.handlers import create_dispatcher_and_register
from bot.telegram_listener import run_telegram_account_listeners
from bot.telegram_sender import send_with_notification_account_attempts
from cloud.services import refresh_custom_plan_cache
from cloud.lifecycle import auto_renew_patrol_tick, daily_expiry_summary_tick, lifecycle_tick, sync_server_status_tick, sync_cloud_accounts_tick
from core.cache import refresh_config, close as cache_close
from core.models import SiteConfig
from core.runtime_config import get_cloud_asset_sync_interval_seconds, get_runtime_config
from cloud.cache import init_monitor_cache
from orders.services import get_trx_price
from orders.runtime import check_resources, scan_forever, set_bot, set_resource_bot

logger = logging.getLogger(__name__)

BOT_ALIVE_LOG_INTERVAL_SECONDS = int(os.getenv('BOT_ALIVE_LOG_INTERVAL_SECONDS', '60') or '60')


def _parse_notify_chat_ids(raw_value: str) -> list[int | str]:
    values: list[int | str] = []
    for item in str(raw_value or '').replace('\n', ',').replace(';', ',').split(','):
        text = item.strip()
        if not text:
            continue
        if text.startswith('@'):
            values.append(text)
            continue
        try:
            values.append(int(text))
        except ValueError:
            logger.warning('通知抄送 Chat ID 格式不正确: %s', text)
    return values


def _admin_notice_copy_text(user, text: str) -> str:
    tg_user_id = getattr(user, 'tg_user_id', None) or getattr(user, 'id', None) or '-'
    username = getattr(user, 'primary_username', '') or getattr(user, 'username', '') or ''
    first_name = getattr(user, 'first_name', '') or ''
    user_label = f'{first_name} @{username}'.strip() if username else (first_name or '-')
    return f'📣 通知抄送\n用户: {user_label}\nTG ID: {tg_user_id}\n\n{text}'


def _log_task_done(name: str):
    def _callback(task: asyncio.Task):
        if task.cancelled():
            logger.warning('BOT_TASK_CANCELLED name=%s', name)
            return
        try:
            exc = task.exception()
        except Exception as err:
            logger.exception('BOT_TASK_EXCEPTION_READ_FAILED name=%s error=%s', name, err)
            return
        if exc:
            logger.exception('BOT_TASK_CRASHED name=%s error=%s', name, exc, exc_info=(type(exc), exc, exc.__traceback__))
        else:
            logger.warning('BOT_TASK_EXITED name=%s result=completed_without_exception', name)
    return _callback


async def _bot_alive_logger(started_at: float, scheduler: AsyncIOScheduler, scanner_task: asyncio.Task, telegram_listener_task: asyncio.Task):
    while True:
        await asyncio.sleep(max(BOT_ALIVE_LOG_INTERVAL_SECONDS, 10))
        uptime_seconds = int(time.time() - started_at)
        try:
            jobs = scheduler.get_jobs() if scheduler.running else []
            job_summary = ','.join(
                f'{job.id}:{job.next_run_time.isoformat() if job.next_run_time else "none"}'
                for job in jobs
            )
            logger.info(
                '机器人心跳：进程=%s 运行时长=%s秒 调度器=%s 下次任务=%s 扫块器已结束=%s 个人号监听已结束=%s 待处理协程=%s',
                os.getpid(),
                uptime_seconds,
                '运行中' if scheduler.running else '已停止',
                job_summary or '-',
                scanner_task.done(),
                telegram_listener_task.done(),
                len([task for task in asyncio.all_tasks() if not task.done()]),
            )
        except Exception as exc:
            logger.exception('机器人心跳日志输出失败：%s', exc)


async def delete_webhook_with_retry(bot, retries: int = 5, base_delay: float = 2.0):
    for attempt in range(1, retries + 1):
        try:
            await bot.delete_webhook(drop_pending_updates=True)
            return
        except TelegramNetworkError as exc:
            if attempt >= retries:
                logger.exception('Telegram 删除 webhook 失败，已重试 %s 次: %s', retries, exc)
                raise
            delay = base_delay * attempt
            logger.warning('Telegram 删除 webhook 网络失败，将在 %.1f 秒后重试 (%s/%s): %s', delay, attempt, retries, exc)
            await asyncio.sleep(delay)


async def warm_trx_rate_cache():
    try:
        await get_trx_price(force_refresh=True)
    except Exception as exc:
        logger.warning('TRX 汇率缓存预热失败: %s', exc)


async def run_bot():
    started_at = time.time()
    loop = asyncio.get_running_loop()

    def _handle_loop_exception(_loop, context):
        message = context.get('message') or 'event loop exception'
        exception = context.get('exception')
        if exception:
            logger.exception('BOT_LOOP_EXCEPTION message=%s error=%s context=%s', message, exception, context, exc_info=(type(exception), exception, exception.__traceback__))
        else:
            logger.error('BOT_LOOP_EXCEPTION message=%s context=%s', message, context)

    loop.set_exception_handler(_handle_loop_exception)
    logger.info('机器人进程启动：进程=%s 心跳间隔=%s秒', os.getpid(), BOT_ALIVE_LOG_INTERVAL_SECONDS)
    if not BOT_TOKEN:
        logger.warning('未配置 BOT_TOKEN，跳过机器人启动')
        return

    # 初始化 Redis 缓存
    try:
        await refresh_config(['receive_address', 'trongrid_api_key'])
        await init_monitor_cache(force_log=True)
        await refresh_custom_plan_cache()
        await warm_trx_rate_cache()
    except Exception as e:
        logger.error('Redis 缓存初始化失败: %s', e)

    bot, dp = await create_dispatcher_and_register()
    set_bot(bot)
    set_resource_bot(bot)
    bot_notice_label = 'Bot'
    try:
        me = await bot.get_me()
        bot_notice_label = f'@{me.username}' if getattr(me, 'username', None) else (getattr(me, 'full_name', None) or f'Bot {me.id}')
        await asyncio.to_thread(SiteConfig.set, 'bot_notice_sender_label', bot_notice_label)
    except Exception as exc:
        logger.warning('获取机器人身份失败，通知计划将使用默认 Bot 标签: %s', exc)

    async def _copy_notice_to_admins(user, text: str):
        raw_copy_value = await asyncio.to_thread(get_runtime_config, 'bot_notice_copy_chat_ids', '')
        copy_chat_ids = _parse_notify_chat_ids(raw_copy_value)
        if not copy_chat_ids:
            return
        copy_text = _admin_notice_copy_text(user, text)
        for copy_chat_id in copy_chat_ids:
            if str(copy_chat_id) == str(getattr(user, 'tg_user_id', '')):
                continue
            try:
                await bot.send_message(chat_id=copy_chat_id, text=copy_text, parse_mode='HTML')
            except Exception as exc:
                logger.warning('用户通知抄送失败 copy_chat_id=%s user=%s err=%s', copy_chat_id, getattr(user, 'id', None), exc)

    async def _notify(user_id: int, text: str, reply_markup=None):
        from bot.models import TelegramUser

        user = await asyncio.to_thread(lambda: TelegramUser.objects.filter(id=user_id).first())
        if not user:
            return {'ok': False, 'attempts': [{'channel': 'bot', 'ok': False, 'error': '用户不存在'}]}
        attempts = []
        delivered = False
        try:
            await bot.send_message(user.tg_user_id, text, reply_markup=reply_markup, parse_mode='HTML')
            attempts.append({'channel': 'bot', 'channel_label': bot_notice_label, 'ok': True, 'error': ''})
            delivered = True
        except Exception as exc:
            attempts.append({'channel': 'bot', 'channel_label': bot_notice_label, 'ok': False, 'error': str(exc)})
            logger.warning('机器人生命周期通知发送失败 user=%s err=%s，尝试个人号通知', user_id, exc)
            try:
                fallback_result = await send_with_notification_account_attempts(user.tg_user_id, text)
                attempts.extend(fallback_result.get('attempts') or [])
                if fallback_result.get('ok'):
                    delivered = True
                else:
                    logger.warning('个人号生命周期通知无可用账号 user=%s attempts=%s', user_id, fallback_result.get('attempts'))
            except Exception as fallback_exc:
                attempts.append({'channel': 'account', 'ok': False, 'error': str(fallback_exc)})
                logger.warning('个人号生命周期通知发送失败 user=%s err=%s', user_id, fallback_exc)
        await _copy_notice_to_admins(user, text)
        return {'ok': delivered, 'attempts': attempts}

    async def _notify_target(chat_id, text: str, reply_markup=None):
        try:
            await bot.send_message(chat_id=chat_id, text=text, reply_markup=reply_markup, parse_mode='HTML')
            return True
        except Exception as exc:
            logger.warning('机器人自动续费执行目标通知发送失败 chat_id=%s err=%s', chat_id, exc)
            return False

    cloud_sync_interval_seconds = await asyncio.to_thread(get_cloud_asset_sync_interval_seconds)

    async def _run_management_command(command_name: str):
        await asyncio.to_thread(call_command, command_name)

    # TRON 扫块器 / 资源巡检 / 生命周期调度
    scheduler = AsyncIOScheduler()
    scheduler.add_job(check_resources, 'interval', minutes=3, id='tron_resource_checker', max_instances=1)
    scheduler.add_job(warm_trx_rate_cache, 'interval', hours=24, id='trx_rate_cache_refresh', max_instances=1, coalesce=True)
    scheduler.add_job(refresh_custom_plan_cache, 'interval', minutes=10, id='custom_plan_cache_refresh', max_instances=1, coalesce=True)
    scheduler.add_job(lifecycle_tick, 'interval', minutes=10, id='cloud_lifecycle', max_instances=1, kwargs={'notify': _notify, 'notify_target': _notify_target})
    scheduler.add_job(_run_management_command, 'interval', minutes=10, id='cloud_lifecycle_plan_refresh', max_instances=1, coalesce=True, args=['refresh_lifecycle_plans'])
    scheduler.add_job(_run_management_command, 'interval', minutes=10, id='cloud_notice_plan_refresh', max_instances=1, coalesce=True, args=['refresh_notice_plans'])
    scheduler.add_job(auto_renew_patrol_tick, 'interval', minutes=30, id='cloud_auto_renew_patrol', max_instances=1, coalesce=True, kwargs={'notify': _notify, 'notify_target': _notify_target})
    scheduler.add_job(daily_expiry_summary_tick, 'cron', hour=12, minute=0, id='cloud_daily_expiry_summary', max_instances=1, coalesce=True, kwargs={'notify_target': _notify_target})
    scheduler.add_job(sync_server_status_tick, 'interval', seconds=cloud_sync_interval_seconds, id='cloud_server_sync', max_instances=1, coalesce=True)
    scheduler.add_job(sync_cloud_accounts_tick, 'interval', minutes=15, id='cloud_account_check', max_instances=1, coalesce=True)
    scheduler.add_job(_run_management_command, 'interval', minutes=20, id='server_dedupe', max_instances=1, coalesce=True, args=['dedupe_servers'])
    scheduler.add_job(_run_management_command, 'cron', hour=18, minute=0, id='old_records_cleanup', max_instances=1, coalesce=True, args=['cleanup_old_records'])
    scheduler.start()
    scanner_stop = asyncio.Event()
    scanner_task = asyncio.create_task(scan_forever(scanner_stop), name='tron_scanner')
    scanner_task.add_done_callback(_log_task_done('tron_scanner'))
    telegram_listener_stop = asyncio.Event()
    telegram_listener_task = asyncio.create_task(run_telegram_account_listeners(telegram_listener_stop), name='telegram_account_listener')
    telegram_listener_task.add_done_callback(_log_task_done('telegram_account_listener'))
    alive_task = asyncio.create_task(_bot_alive_logger(started_at, scheduler, scanner_task, telegram_listener_task), name='bot_alive_logger')
    alive_task.add_done_callback(_log_task_done('bot_alive_logger'))
    logger.info('TRON 顺序扫块器已启动（移除6秒调度限制）')
    logger.info('资源巡检已启动 (每3分钟)')
    logger.info('TRX 汇率缓存刷新已启动 (每24小时)')
    logger.info('定制套餐缓存刷新已启动 (每10分钟)')
    logger.info('云服务器生命周期调度已启动 (每10分钟)')
    logger.info('删机计划表刷新已启动 (每10分钟)')
    logger.info('通知计划表刷新已启动 (每10分钟)')
    logger.info('云服务器每日到期汇总通知已启动 (每天12:00)')
    logger.info('自动续费巡检已启动 (每30分钟，到期前1天至关机前持续兜底，失败通知冷却1小时)')
    logger.info('云服务器状态同步已启动 (每%s秒，每次轮询1个云账号)', cloud_sync_interval_seconds)
    logger.info('云账号状态巡检已启动 (每15分钟)')
    logger.info('服务器去重任务已启动 (每20分钟)')
    logger.info('旧订单/聊天记录自动清理任务已启动 (每天18:00)')

    logger.info('Telegram个人号消息监听调度已启动')
    try:
        logger.info('启动时执行云服务器生命周期检查')
        try:
            defer_seconds = int(str(get_runtime_config('cloud_startup_lifecycle_defer_seconds', '0')).strip() or 0)
            await lifecycle_tick(notify=_notify, notify_target=_notify_target, defer_destructive_seconds=max(defer_seconds, 0))
            logger.info('启动时云服务器生命周期检查完成')
        except Exception as exc:
            logger.exception('启动时云服务器生命周期检查失败: %s', exc)
        logger.info('Telegram Bot 已启动 (aiogram)')
        await delete_webhook_with_retry(bot)
        logger.info('机器人轮询启动：进程=%s', os.getpid())
        try:
            await dp.start_polling(bot)
            logger.warning('机器人轮询已停止：原因=正常返回但不应退出')
        except Exception as exc:
            logger.exception('机器人轮询异常退出：%s', exc)
            raise
    finally:
        logger.warning('机器人开始关闭：进程=%s 运行时长=%s秒', os.getpid(), int(time.time() - started_at))
        scanner_stop.set()
        scanner_task.cancel()
        telegram_listener_stop.set()
        telegram_listener_task.cancel()
        alive_task.cancel()
        await asyncio.gather(scanner_task, telegram_listener_task, alive_task, return_exceptions=True)
        with contextlib.suppress(Exception):
            scheduler.shutdown(wait=False)
        await cache_close()
        await close_fsm_storage()
        await bot.session.close()
        logger.warning('BOT_SHUTDOWN_DONE pid=%s', os.getpid())


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
    logging.getLogger('telethon.client.updates').setLevel(logging.WARNING)
    logging.getLogger('apscheduler.executors.default').setLevel(logging.ERROR)
    logging.getLogger('apscheduler.scheduler').setLevel(logging.ERROR)
    asyncio.run(run_bot())


if __name__ == '__main__':
    main()
