import asyncio
import json
import logging
from typing import Any, ClassVar, Iterable

import redis
from redis import asyncio as aioredis

from src.settings.db import get_redis_settings

logger = logging.getLogger(__name__)
JSONT = list[Any] | dict[str, Any] | str | None


def _sync_redis_connection_dict() -> dict[str, str | int | bool]:
    """
    Sync client options for RQ and shared app keys.

    RQ stores binary blobs in job hashes; decode_responses must be False so
    Job.fetch / HGETALL does not force UTF-8. App JSON get/set decodes UTF-8
    explicitly in RedisClient.get.
    """
    cfg = dict(get_redis_settings().connection_dict)
    cfg["decode_responses"] = False
    return cfg


# One asyncio client per event loop; must be closed before the loop stops (see
# close_async_redis_connection) to avoid __del__ touching a dead selector/kqueue.
# TODO: move to class-based var
_async_redis_for_loop: dict[int, aioredis.Redis] = {}


class RedisClient:
    """The class is used to create a redis connection in a single instance."""

    __instance = None
    _sync_redis: ClassVar[redis.Redis | None] = None

    def __new__(cls):
        if cls.__instance is None:
            cls.__instance = super().__new__(cls)
        return cls.__instance

    @property
    def sync_redis(self) -> redis.Redis:
        """Blocking client for RQ jobs, boto3 callbacks, and other sync contexts."""
        cls = type(self)
        if cls._sync_redis is None:
            cls._sync_redis = redis.Redis(**_sync_redis_connection_dict())

        return cls._sync_redis

    def get(self, key: str) -> JSONT:
        """Sync JSON get (same encoding as async_get)."""
        raw = self.sync_redis.get(key)
        if raw is None:
            return json.loads("null")
        if isinstance(raw, (bytes, bytearray)):
            raw = raw.decode("utf-8")
        return json.loads(raw)

    def set(self, key: str, value: JSONT, ttl: int = 120) -> None:
        """Sync JSON set with TTL in seconds (same encoding as async_set)."""
        self.sync_redis.set(key, json.dumps(value), ex=ttl)

    def publish(self, channel: str, message: str) -> None:
        """Sync pub/sub publish."""
        self.sync_redis.publish(channel, message)

    @property
    def async_redis(self) -> aioredis.Redis:
        """Async Redis client bound to the current running event loop (reused per loop)."""
        loop = asyncio.get_running_loop()
        key = id(loop)
        client = _async_redis_for_loop.get(key)
        if client is None:
            settings = get_redis_settings()
            client = aioredis.Redis(**settings.connection_dict)
            _async_redis_for_loop[key] = client
        return client

    async def async_set(self, key: str, value: JSONT, ttl: int = 120) -> None:
        logger.debug("AsyncRedis > Setting value by key %s", key)
        await self.async_redis.set(key, json.dumps(value), ttl)

    async def async_get(self, key: str) -> JSONT:
        logger.debug("AsyncRedis > Getting value by key %s", key)
        return json.loads(await self.async_redis.get(key) or "null")

    async def async_publish(self, channel: str, message: str) -> None:
        logger.debug("AsyncRedis > Publishing message %s to channel %s ", message, channel)
        await self.async_redis.publish(channel, message)

    def async_pubsub(self, **kwargs) -> aioredis.client.PubSub:
        logger.debug("AsyncRedis > PubSub with kwargs %s", kwargs)
        return self.async_redis.pubsub(**kwargs)

    async def async_get_many(self, keys: Iterable[str], pkey: str) -> dict:
        """
        Allows to get several values from redis for 1 request
        :param keys: any iterable object with needed keys
        :param pkey: key in each record for grouping by it

        :return: dict with keys (given from stored records by `pkey`)

        input from redis: ['{"event_key": "episode-1", "data": {"key": 1}}', ...]
        >>> async def get_items_from_redis():
        ...    return await RedisClient().async_get_many(["episode-1"], pkey="event_key")
        {"episode-1": {"event_key": "episode-1", "data": {"key": 1}}, ...}

        """
        stored_items = [json.loads(item) for item in await self.async_redis.mget(keys) if item]
        # stored_items = (json.loads(item) for item in await self.async_redis.mget(keys) if item)
        try:
            logger.debug("Try to extract redis data: %s", list(stored_items))
            result = {
                stored_item[pkey]: stored_item
                for stored_item in stored_items
                if pkey in stored_item
            }
        except TypeError as exc:
            logger.exception("Couldn't extract event data from redis: %r", exc)
            result = {}

        return result

    @staticmethod
    def get_key_by_filename(filename) -> str:
        return filename.partition(".")[0]


async def check_redis_connection() -> None:
    """Check if Redis connection is alive"""
    try:
        await RedisClient().async_redis.ping()
    except redis.exceptions.ConnectionError as exc:
        logger.error("Failed to check Redis connection: %r", exc)
        raise RuntimeError("Failed to check Redis connection") from exc

    logger.info("Redis connection is alive")


async def close_async_redis_connection() -> None:
    """
    Close the asyncio Redis client for the current event loop.

    Call from the same loop during shutdown (e.g. app lifespan, end of RQ job asyncio.run)
    so connections are not garbage-collected after the loop is already closed.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return

    client = _async_redis_for_loop.pop(id(loop), None)
    if client is None:
        return

    try:
        await client.aclose()
    except Exception as exc:
        logger.debug("Async Redis wasn't closed: %r", exc)
