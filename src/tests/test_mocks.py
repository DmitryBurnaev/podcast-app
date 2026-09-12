"""Contracts for monkeypatched application boundaries and their stateful fakes."""

from collections.abc import Callable
from inspect import signature
from typing import Any, ClassVar
from unittest.mock import AsyncMock

import pytest

from src import main as app_main
from src.modules.common.exceptions import StartupError, StorageConfigurationError
from src.main import DbStartMode, lifespan, make_app
from src.modules.services.storage import StorageS3
from src.tests.conftest import _make_settings
from src.tests.fakes import FakeLifecycle, FakeMailer, FakeRedis, FakeStorage, FakeTaskQueue
from src.tests.mocks import BaseMock, BaseMockWithContextManager, mock_target_class


class _Target:
    def __init__(self, value: str) -> None:
        self.value = value

    def operation(self, value: str) -> str:
        return value


class _TargetMock(BaseMock):
    target_class: ClassVar[type[Any]] = _Target
    mocked_methods = ("operation",)

    def __init__(self) -> None:
        super().__init__()
        self.values: list[str] = []

    def operation(self, value: str) -> str:
        self.values.append(value)
        return f"mocked:{value}"


class _ContextTarget:
    async def __aenter__(self) -> "_ContextTarget":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _ContextTargetMock(BaseMockWithContextManager):
    target_class: ClassVar[type[Any]] = _ContextTarget


class TestBaseMock:
    def test_patches_constructor_and_methods_only_inside_fixture_scope(self) -> None:
        original_init = _Target.__init__
        original_operation = _Target.operation
        fake = _TargetMock()

        with pytest.MonkeyPatch.context() as monkeypatch:
            replacement = mock_target_class(fake, monkeypatch)
            next(replacement)
            target = _Target("created")

            assert target.operation("value") == "mocked:value"
            assert fake.target_obj is target
            assert fake.values == ["value"]
            assert fake.init_mock is not None
            fake.init_mock.assert_called_once_with(target, "created")
            replacement.close()

        assert _Target.__init__ is original_init
        assert _Target.operation is original_operation

    async def test_storage_class_uses_stateful_fake(
        self,
        mocked_storage: FakeStorage,
    ) -> None:
        storage = StorageS3()
        uploaded = await storage.upload_file(
            "/tmp/source.mp3",
            "audio",
            filename="episode.mp3",
        )

        assert uploaded == "audio/episode.mp3"
        assert mocked_storage.target_obj is storage
        assert mocked_storage.uploads[0].destination == "audio/episode.mp3"

    async def test_patches_async_context_manager_and_restores_it(self) -> None:
        original_enter = _ContextTarget.__aenter__
        original_exit = _ContextTarget.__aexit__
        fake = _ContextTargetMock()

        with pytest.MonkeyPatch.context() as monkeypatch:
            replacement = mock_target_class(fake, monkeypatch)
            next(replacement)
            target = _ContextTarget()

            async with target as entered:
                assert entered is fake

            fake.enter_mock.assert_awaited_once_with()
            fake.exit_mock.assert_awaited_once_with(None, None, None)
            replacement.close()

        assert _ContextTarget.__aenter__ is original_enter
        assert _ContextTarget.__aexit__ is original_exit


class TestApplicationComposition:
    async def test_lifespan_calls_direct_boundaries(
        self,
        mocked_app_lifecycle: FakeLifecycle,
        mocked_rq_queue: FakeTaskQueue,
    ) -> None:
        settings = _make_settings(api_debug_mode=True)
        app = make_app(settings=settings)

        assert app.rq_queue is mocked_rq_queue.target_obj
        assert not hasattr(app, "providers")
        assert "providers" not in signature(make_app).parameters
        assert "providers" not in signature(lifespan).parameters

        async with lifespan(settings, app):
            assert mocked_app_lifecycle.calls == ["initialize_database", "check_redis"]

        assert mocked_app_lifecycle.calls == [
            "initialize_database",
            "check_redis",
            "close_database",
            "close_redis",
        ]

    async def test_worker_lifespan_uses_database_verification(
        self,
        mocked_app_lifecycle: FakeLifecycle,
    ) -> None:
        settings = _make_settings(api_debug_mode=True)

        async with lifespan(settings, db_start_mode=DbStartMode.VERIFY):
            pass

        assert mocked_app_lifecycle.calls == ["verify_database", "check_redis", "close_redis"]

    @pytest.mark.parametrize("db_start_mode", list(DbStartMode))
    async def test_database_startup_errors_are_wrapped(
        self,
        db_start_mode: DbStartMode,
        mocked_app_lifecycle: FakeLifecycle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        database_check = AsyncMock(side_effect=RuntimeError("database unavailable"))
        monkeypatch.setitem(app_main._DB_STARTUP_CHECKS, db_start_mode, database_check)

        with pytest.raises(StartupError, match="Failed to initialize DB connection"):
            async with lifespan(_make_settings(api_debug_mode=True), db_start_mode=db_start_mode):
                pass

        database_check.assert_awaited_once_with()
        assert mocked_app_lifecycle.calls == []

    async def test_storage_and_redis_startup_errors_are_wrapped(
        self,
        mocked_app_lifecycle: FakeLifecycle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fail_storage(_: object) -> None:
            raise StorageConfigurationError(details="bucket is missing")

        monkeypatch.setattr(app_main, "validate_s3_settings", fail_storage)
        settings = _make_settings(api_debug_mode=True)

        with pytest.raises(StartupError, match="bucket is missing"):
            async with lifespan(settings):
                pass

        assert mocked_app_lifecycle.calls == ["initialize_database"]

        monkeypatch.setattr(app_main, "validate_s3_settings", lambda _: None)
        redis_check = AsyncMock(side_effect=RuntimeError("redis unavailable"))
        monkeypatch.setattr(app_main, "check_redis_connection", redis_check)

        with pytest.raises(StartupError, match="Failed to initialize Redis connection"):
            async with lifespan(settings):
                pass

        redis_check.assert_awaited_once_with()
        assert mocked_app_lifecycle.calls == ["initialize_database", "initialize_database"]

    async def test_shutdown_closes_redis_after_database_close_error(
        self,
        mocked_app_lifecycle: FakeLifecycle,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        async def fail_database_close() -> None:
            mocked_app_lifecycle.calls.append("close_database")
            raise RuntimeError("database close failed")

        monkeypatch.setattr(app_main, "close_database", fail_database_close)

        async with lifespan(_make_settings(api_debug_mode=True)):
            pass

        assert mocked_app_lifecycle.calls == [
            "initialize_database",
            "check_redis",
            "close_database",
            "close_redis",
        ]


class TestExternalFakes:
    async def test_storage_records_upload_copy_and_delete(self) -> None:
        storage = FakeStorage()

        uploaded = await storage.upload_file("/tmp/source.mp3", "audio", filename="episode.mp3")
        assert uploaded is not None
        storage.files[uploaded] = b"content"
        copied = await storage.copy_file(uploaded, "rss/episode.mp3")
        deleted = await storage.delete_file(dst_path=copied)

        assert storage.uploads[0].destination == "audio/episode.mp3"
        assert copied == "rss/episode.mp3"
        assert deleted == {}
        assert storage.deleted_paths == ["rss/episode.mp3"]

    async def test_redis_queue_and_mail_fakes_expose_call_history(self) -> None:
        redis = FakeRedis()
        queue = FakeTaskQueue()
        mailer = FakeMailer()

        await redis.async_set("episode", {"status": "queued"}, ttl=30)
        await redis.async_publish("progress", "updated")
        queued = queue.enqueue("download", 1, priority="high")
        await mailer.send("user@example.test", "Ready", "<p>done</p>")

        assert await redis.async_get("episode") == {"status": "queued"}
        assert redis.set_calls == [("episode", {"status": "queued"}, 30)]
        assert redis.published == [("progress", "updated")]
        assert queued.kwargs == {"priority": "high"}
        assert mailer.sent[0].recipient_email == "user@example.test"

    @pytest.mark.parametrize(
        ("factory", "invoke"),
        [
            (
                lambda: FakeTaskQueue(error=RuntimeError("queue unavailable")),
                lambda fake: fake.enqueue("task"),
            ),
            (
                lambda: FakeStorage(error=RuntimeError("storage unavailable")),
                lambda fake: fake.get_presigned_url("file"),
            ),
            (
                lambda: FakeMailer(error=RuntimeError("mail unavailable")),
                lambda fake: fake.send("a@test", "s", "b"),
            ),
        ],
    )
    async def test_fake_can_simulate_external_failure(
        self,
        factory: Callable[[], Any],
        invoke: Callable[[Any], Any],
    ) -> None:
        with pytest.raises(RuntimeError):
            result = invoke(factory())
            if hasattr(result, "__await__"):
                await result
