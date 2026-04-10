import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.types import Message

from .config import BOT_TOKEN

logger = logging.getLogger(__name__)


async def cmd_start(message: Message):
    await message.answer('Django Bot 已启动。')


async def run_bot():
    if not BOT_TOKEN:
        logger.warning('未配置 BOT_TOKEN，跳过机器人启动')
        return

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.message.register(cmd_start, CommandStart())

    logger.info('Telegram Bot 已启动')
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


def main():
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    asyncio.run(run_bot())


if __name__ == '__main__':
    main()
