"""Contracts for typed application providers and stateful external fakes."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from src.main import DbStartMode, lifespan, make_app
from src.providers import AppProviders
from src.tests.conftest import _make_settings
from src.tests.fakes import (
    FakeHTTPClient,
    FakeLifecycle,
    FakeMailer,
    FakeMediaProcessor,
    FakeMediaSource,
    FakeRedis,
    FakeStorage,
    FakeTaskQueue,
)


@pytest.fixture
def fake_providers() -> tuple[AppProviders, FakeLifecycle, FakeTaskQueue]:
    lifecycle = FakeLifecycle()
    queue = FakeTaskQueue()
    providers = AppProviders(
        initialize_database=lifecycle.initialize_database,
        verify_database=lifecycle.verify_database,
        close_database=lifecycle.close_database,
        session_factory=lambda: None,  # type: ignore[arg-type, return-value]
        uow_factory=lambda: None,
        validate_storage_settings=lambda _: None,
        check_redis=lifecycle.check_redis,
        close_redis=lifecycle.close_redis,
        make_task_queue=lambda _: queue,
        make_storage=FakeStorage,
        make_redis=FakeRedis,
        mailer=FakeMailer(),
        http_client=FakeHTTPClient(),
        media_source=FakeMediaSource(),
        media_processor=FakeMediaProcessor(),
    )
    return providers, lifecycle, queue


class TestAppProviders:
    async def test_lifespan_uses_injected_boundaries(
        self,
        fake_providers: tuple[AppProviders, FakeLifecycle, FakeTaskQueue],
    ) -> None:
        providers, lifecycle, queue = fake_providers
        settings = _make_settings(api_debug_mode=True)
        app = make_app(settings=settings, providers=providers)

        assert app.providers is providers
        assert app.rq_queue is queue

        async with lifespan(settings, app, providers=providers):
            assert lifecycle.calls == ["initialize_database", "check_redis"]

        assert lifecycle.calls == [
            "initialize_database",
            "check_redis",
            "close_database",
            "close_redis",
        ]

    async def test_worker_lifespan_uses_injected_database_verification(
        self,
        fake_providers: tuple[AppProviders, FakeLifecycle, FakeTaskQueue],
    ) -> None:
        providers, lifecycle, _ = fake_providers
        settings = _make_settings(api_debug_mode=True)

        async with lifespan(settings, db_start_mode=DbStartMode.VERIFY, providers=providers):
            pass

        assert lifecycle.calls == ["verify_database", "check_redis", "close_redis"]


class TestExternalFakes:
    async def test_storage_records_upload_copy_and_delete(self) -> None:
        storage = FakeStorage()

        uploaded = await storage.upload_file("/tmp/source.mp3", "audio", filename="episode.mp3")
        copied = await storage.copy_file(uploaded, "rss/episode.mp3")
        deleted = await storage.delete_file(dst_path=copied)

        assert storage.uploads[0].destination == "audio/episode.mp3"
        assert copied == "rss/episode.mp3"
        assert deleted == {}
        assert storage.deleted_paths == ["rss/episode.mp3"]

    async def test_redis_queue_mail_http_and_media_fakes_expose_call_history(self) -> None:
        redis = FakeRedis()
        queue = FakeTaskQueue()
        mailer = FakeMailer()
        http = FakeHTTPClient({"https://source.test/file": b"media"})
        source = FakeMediaSource({"https://source.test": {"id": "source"}})
        processor = FakeMediaProcessor(metadata={"duration": 10}, cover=b"cover")

        await redis.async_set("episode", {"status": "queued"}, ttl=30)
        await redis.async_publish("progress", "updated")
        queued = queue.enqueue("download", 1, priority="high")
        await mailer.send("user@example.test", "Ready", "<p>done</p>")

        assert await redis.async_get("episode") == {"status": "queued"}
        assert redis.set_calls == [("episode", {"status": "queued"}, 30)]
        assert redis.published == [("progress", "updated")]
        assert queued.kwargs == {"priority": "high"}
        assert mailer.sent[0].recipient_email == "user@example.test"
        assert await http.get("https://source.test/file") == b"media"
        assert await source.extract("https://source.test", playlist=True) == {"id": "source"}
        assert processor.audio_metadata(Path("/tmp/audio.mp3")) == {"duration": 10}
        assert processor.audio_cover("/tmp/audio.mp3") == b"cover"

    @pytest.mark.parametrize(
        ("factory", "invoke"),
        [
            (lambda: FakeTaskQueue(error=RuntimeError("queue unavailable")), lambda fake: fake.enqueue("task")),
            (lambda: FakeStorage(error=RuntimeError("storage unavailable")), lambda fake: fake.get_presigned_url("file")),
            (lambda: FakeMailer(error=RuntimeError("mail unavailable")), lambda fake: fake.send("a@test", "s", "b")),
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
