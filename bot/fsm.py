import logging

import redis.asyncio as aioredis
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.storage.redis import RedisStorage
from redis.exceptions import ConnectionError as RedisConnectionError

from bot.config import REDIS_URL, FSM_STATE_TTL, FSM_DATA_TTL

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None
_storage: MemoryStorage | RedisStorage | None = None
_fallback_memory = MemoryStorage()


class ResilientRedisStorage(RedisStorage):
    async def _with_reconnect(self, action_name: str, func, default=None):
        try:
            return await func()
        except RedisConnectionError as exc:
            logger.warning('Redis FSM %s 失败，回退内存存储: %s', action_name, exc)
            try:
                await self.redis.aclose()
            except Exception:
                pass
            return default
        except Exception as exc:
            logger.warning('Redis FSM %s 异常，回退内存存储: %s', action_name, exc)
            return default

    async def get_state(self, key: StorageKey):
        default = await _fallback_memory.get_state(key)
        return await self._with_reconnect('get_state', lambda: super().get_state(key), default)

    async def set_state(self, key: StorageKey, state=None):
        result = await self._with_reconnect('set_state', lambda: super().set_state(key, state), None)
        await _fallback_memory.set_state(key, state)
        return result

    async def get_data(self, key: StorageKey):
        default = await _fallback_memory.get_data(key)
        return await self._with_reconnect('get_data', lambda: super().get_data(key), default)

    async def set_data(self, key: StorageKey, data):
        result = await self._with_reconnect('set_data', lambda: super().set_data(key, data), None)
        await _fallback_memory.set_data(key, data)
        return result

    async def close(self):
        try:
            await super().close()
        except Exception:
            pass


async def get_redis() -> aioredis.Redis:
    global _redis
    if _redis is None:
        _redis = aioredis.from_url(
            REDIS_URL,
            decode_responses=True,
            socket_keepalive=True,
            health_check_interval=30,
            retry_on_timeout=True,
        )
    return _redis


async def create_fsm_storage() -> MemoryStorage | RedisStorage:
    global _storage
    try:
        redis_client = await get_redis()
        await redis_client.ping()
        _storage = ResilientRedisStorage(
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
            await _redis.aclose()
        except Exception:
            pass
        _redis = None
