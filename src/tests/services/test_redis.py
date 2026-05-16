import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from redis.exceptions import ConnectionError

from src.modules.services import redis as redis_module
from src.modules.services.redis import (
    RedisClient,
    _async_redis_for_loop,
    _sync_redis_connection_dict,
    check_redis_connection,
    close_async_redis_connection,
)


TEST_DATA = {"test": "my-value"}


@pytest.fixture(autouse=True)
def reset_redis_client(monkeypatch: pytest.MonkeyPatch) -> None:
    RedisClient._RedisClient__instance = None
    RedisClient._sync_redis = None
    _async_redis_for_loop.clear()
    redis_settings = SimpleNamespace(
        host="redis",
        port=6379,
        db=1,
        max_connections=3,
        decode_responses=True,
        connection_dict={
            "host": "redis",
            "port": 6379,
            "db": 1,
            "max_connections": 3,
            "decode_responses": True,
        },
    )
    monkeypatch.setattr("src.modules.services.redis.get_redis_settings", lambda: redis_settings)


class TestRedisClientSync:
    def test_connection_dict__forces_binary_decoding_for_rq(self) -> None:
        assert _sync_redis_connection_dict() == {
            "host": "redis",
            "port": 6379,
            "db": 1,
            "max_connections": 3,
            "decode_responses": False,
        }

    def test_sync_redis__is_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis_constructor = Mock(return_value=SimpleNamespace())
        monkeypatch.setattr("src.modules.services.redis.redis.Redis", redis_constructor)

        client = RedisClient()

        assert client.sync_redis is client.sync_redis
        redis_constructor.assert_called_once_with(
            host="redis",
            port=6379,
            db=1,
            max_connections=3,
            decode_responses=False,
        )

    def test_get__bytes_ok(self) -> None:
        sync_redis = SimpleNamespace(get=Mock(return_value=json.dumps(TEST_DATA).encode()))
        RedisClient._sync_redis = sync_redis

        assert RedisClient().get("my-key") == TEST_DATA
        sync_redis.get.assert_called_once_with("my-key")

    def test_get__missing_key__returns_none(self) -> None:
        RedisClient._sync_redis = SimpleNamespace(get=Mock(return_value=None))

        assert RedisClient().get("missing-key") is None

    def test_get__unexpected_type__fail(self) -> None:
        RedisClient._sync_redis = SimpleNamespace(get=Mock(return_value=123))

        with pytest.raises(TypeError, match="Unexpected response from redis client"):
            RedisClient().get("my-key")

    def test_set__json_encodes_value(self) -> None:
        sync_redis = SimpleNamespace(set=Mock(return_value=None))
        RedisClient._sync_redis = sync_redis

        RedisClient().set("my-key", TEST_DATA, ttl=180)

        sync_redis.set.assert_called_once_with("my-key", json.dumps(TEST_DATA), ex=180)

    def test_publish__ok(self) -> None:
        sync_redis = SimpleNamespace(publish=Mock(return_value=None))
        RedisClient._sync_redis = sync_redis

        RedisClient().publish("test-channel", "test-message")

        sync_redis.publish.assert_called_once_with("test-channel", "test-message")

    def test_get_key_by_filename__ok(self) -> None:
        assert RedisClient().get_key_by_filename("test-file.mp3") == "test-file"


class TestRedisClientAsync:
    async def test_async_redis__is_cached_by_loop(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis_constructor = Mock(return_value=SimpleNamespace())
        monkeypatch.setattr("src.modules.services.redis.aioredis.Redis", redis_constructor)

        client = RedisClient()

        assert client.async_redis is client.async_redis
        redis_constructor.assert_called_once_with(
            host="redis",
            port=6379,
            db=1,
            max_connections=3,
            decode_responses=True,
        )

    async def test_async_get__ok(self) -> None:
        _async_redis_for_loop[id(redis_module.asyncio.get_running_loop())] = SimpleNamespace(
            get=AsyncMock(return_value=json.dumps(TEST_DATA))
        )

        assert await RedisClient().async_get("my-key") == TEST_DATA

    async def test_async_set__ok(self) -> None:
        async_redis = SimpleNamespace(set=AsyncMock(return_value=None))
        _async_redis_for_loop[id(redis_module.asyncio.get_running_loop())] = async_redis

        await RedisClient().async_set("my-key", TEST_DATA, ttl=180)

        async_redis.set.assert_awaited_once_with("my-key", json.dumps(TEST_DATA), 180)

    async def test_async_publish__ok(self) -> None:
        async_redis = SimpleNamespace(publish=AsyncMock(return_value=None))
        _async_redis_for_loop[id(redis_module.asyncio.get_running_loop())] = async_redis

        await RedisClient().async_publish("test-channel", "test-message")

        async_redis.publish.assert_awaited_once_with("test-channel", "test-message")

    async def test_async_pubsub__passes_kwargs(self) -> None:
        pubsub = object()
        async_redis = SimpleNamespace(pubsub=Mock(return_value=pubsub))
        _async_redis_for_loop[id(redis_module.asyncio.get_running_loop())] = async_redis

        assert RedisClient().async_pubsub(ignore_subscribe_messages=True) is pubsub
        async_redis.pubsub.assert_called_once_with(ignore_subscribe_messages=True)

    async def test_async_get_many__groups_by_primary_key(self) -> None:
        async_redis = SimpleNamespace(
            mget=AsyncMock(
                return_value=[
                    json.dumps({"event_key": "episode-1", "data": TEST_DATA}),
                    json.dumps({"event_key": "episode-2", "data": TEST_DATA}),
                ]
            )
        )
        _async_redis_for_loop[id(redis_module.asyncio.get_running_loop())] = async_redis

        result = await RedisClient().async_get_many(["episode-1", "episode-2"], pkey="event_key")

        assert result == {
            "episode-1": {"event_key": "episode-1", "data": TEST_DATA},
            "episode-2": {"event_key": "episode-2", "data": TEST_DATA},
        }
        async_redis.mget.assert_awaited_once_with(["episode-1", "episode-2"])

    async def test_async_get_many__bad_key__logs_and_returns_empty(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async_redis = SimpleNamespace(
            mget=AsyncMock(return_value=[json.dumps({"event_key": ["episode-1"]})])
        )
        logger_exception = Mock(return_value=None)
        _async_redis_for_loop[id(redis_module.asyncio.get_running_loop())] = async_redis
        monkeypatch.setattr("src.modules.services.redis.logger.exception", logger_exception)

        result = await RedisClient().async_get_many(["episode-1"], pkey="event_key")

        assert result == {}
        logger_exception.assert_called_once()


class TestRedisConnectionLifecycle:
    async def test_check_redis_connection__ok(self) -> None:
        async_redis = SimpleNamespace(ping=AsyncMock(return_value=True))
        _async_redis_for_loop[id(redis_module.asyncio.get_running_loop())] = async_redis

        await check_redis_connection()

        async_redis.ping.assert_awaited_once_with()

    async def test_check_redis_connection__fail(self) -> None:
        async_redis = SimpleNamespace(ping=AsyncMock(side_effect=ConnectionError("down")))
        _async_redis_for_loop[id(redis_module.asyncio.get_running_loop())] = async_redis

        with pytest.raises(RuntimeError, match="Failed to check Redis connection"):
            await check_redis_connection()

    async def test_close_async_redis_connection__closes_current_loop_client(self) -> None:
        current_client = SimpleNamespace(aclose=AsyncMock(return_value=None))
        other_client = SimpleNamespace(aclose=AsyncMock(return_value=None))
        current_key = id(redis_module.asyncio.get_running_loop())
        _async_redis_for_loop[current_key] = current_client
        _async_redis_for_loop[123] = other_client

        await close_async_redis_connection()

        current_client.aclose.assert_awaited_once_with()
        other_client.aclose.assert_not_awaited()
        assert current_key not in _async_redis_for_loop
        assert _async_redis_for_loop[123] is other_client
