import logging
import redis.asyncio as aioredis
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage

from bot.config import REDIS_URL, FSM_STATE_TTL, FSM_DATA_TTL

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None
_storage: MemoryStorage | RedisStorage | None = None


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(REDIS_URL, decode_responses=True)
    return _redis


async def create_fsm_storage() -> MemoryStorage | RedisStorage:
    global _storage
    try:
        redis_client = await get_redis()
        await redis_client.ping()
        _storage = RedisStorage(
            redis=redis_client,
            state_ttl=FSM_STATE_TTL,
            data_ttl=FSM_DATA_TTL,
        )
        logger.info('FSM 已切换为 RedisStorage')
        return _storage
    except Exception as exc:
        logger.warning('RedisStorage 初始化失败，回退 MemoryStorage: %s', exc)
        _storage = MemoryStorage()
        return _storage


async def close_fsm_storage():
    global _redis, _storage
    if _storage and hasattr(_storage, 'close'):
        try:
            await _storage.close()
        except Exception:
            pass
        _storage = None
    if _redis is not None:
        try:
            await _redis.close()
        except Exception:
            pass
        _redis = None
