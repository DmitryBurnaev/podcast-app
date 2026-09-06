"""Typed application-boundary providers.

The application factory owns one :class:`AppProviders` instance. Production
uses the adapters in ``from_production()``; tests may pass stateful fakes
without patching module globals or starting external services.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, cast

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.settings.app import AppSettings
from src.settings.db import S3Settings


class TaskQueue(Protocol):
    """Narrow RQ boundary used by controllers and views."""

    def enqueue(self, task: str | Callable[..., Any], *args: Any, **kwargs: Any) -> Any: ...


class Storage(Protocol):
    """External object-storage operations used by application code."""

    async def upload_file(
        self,
        src_path: str | Path,
        dst_path: str | Path,
        filename: str | None = None,
        callback: Callable[..., Any] | None = None,
    ) -> str | None: ...

    async def download_file(self, src_path: str | Path, dst_path: str | Path) -> str | None: ...

    async def copy_file(self, src_path: str, dst_path: str) -> str | None: ...

    async def delete_file(self, *args: Any, **kwargs: Any) -> dict[str, Any] | None: ...

    async def get_presigned_url(self, remote_path: str) -> str: ...


class RedisStore(Protocol):
    """Small Redis API required by progress and storage code."""

    def get(self, key: str) -> Any: ...

    def set(self, key: str, value: Any, ttl: int = 120) -> None: ...

    def publish(self, channel: str, message: str) -> None: ...

    async def async_get(self, key: str) -> Any: ...

    async def async_set(self, key: str, value: Any, ttl: int = 120) -> None: ...

    async def async_publish(self, channel: str, message: str) -> None: ...


class Mailer(Protocol):
    """Outbound email boundary."""

    async def send(self, recipient_email: str, subject: str, html_content: str) -> None: ...


class HTTPClient(Protocol):
    """Minimal asynchronous HTTP boundary for source adapters."""

    async def get(self, url: str) -> bytes: ...


class MediaSource(Protocol):
    """Source-media metadata extraction boundary."""

    async def extract(self, url: str, *, playlist: bool = False) -> dict[str, Any]: ...


class MediaProcessor(Protocol):
    """Media-processing boundary (ffmpeg and local metadata tooling)."""

    def audio_metadata(self, path: str | Path) -> Any: ...

    def audio_cover(self, path: str | Path) -> Any: ...


UOWFactory = Callable[[], Any]
SessionFactory = Callable[[], AsyncSession]


@dataclass(frozen=True, slots=True)
class AppProviders:
    """All replaceable application boundaries owned by one app instance."""

    initialize_database: Callable[[], Awaitable[None]]
    verify_database: Callable[[], Awaitable[None]]
    close_database: Callable[[], Awaitable[None]]
    session_factory: Callable[[], async_sessionmaker[AsyncSession]]
    uow_factory: UOWFactory
    validate_storage_settings: Callable[[S3Settings], None]
    check_redis: Callable[[], Awaitable[None]]
    close_redis: Callable[[], Awaitable[None]]
    make_task_queue: Callable[[AppSettings], TaskQueue]
    make_storage: Callable[[], Storage]
    make_redis: Callable[[], RedisStore]
    mailer: Mailer
    http_client: HTTPClient
    media_source: MediaSource
    media_processor: MediaProcessor

    @classmethod
    def from_production(cls) -> AppProviders:
        """Build lazy production adapters without creating network clients yet."""
        import rq
        from redis import Redis

        from src.modules.db import close_database, get_session_factory, verify_database_reachable
        from src.modules.db.services import SASessionUOW
        from src.modules.db.session import initialize_database
        from src.modules.services.redis import RedisClient, check_redis_connection, close_async_redis_connection
        from src.modules.services.storage import StorageS3, validate_s3_settings
        from src.modules.utils import ffmpeg

        return cls(
            initialize_database=initialize_database,
            verify_database=verify_database_reachable,
            close_database=close_database,
            session_factory=get_session_factory,
            uow_factory=SASessionUOW,
            validate_storage_settings=validate_s3_settings,
            check_redis=check_redis_connection,
            close_redis=close_async_redis_connection,
            make_task_queue=lambda settings: cast(
                TaskQueue,
                rq.Queue(
                    name=settings.rq_queue_name,
                    connection=Redis(*settings.redis.connection_tuple),
                    default_timeout=settings.rq_default_timeout,
                ),
            ),
            make_storage=StorageS3,
            make_redis=RedisClient,
            mailer=_ProductionMailer(),
            http_client=_UnsupportedHTTPClient(),
            media_source=_UnsupportedMediaSource(),
            media_processor=_FFmpegProcessor(ffmpeg),
        )


class _ProductionMailer:
    async def send(self, recipient_email: str, subject: str, html_content: str) -> None:
        from src.modules.services.email import send_email

        await send_email(recipient_email, subject, html_content)


class _UnsupportedHTTPClient:
    async def get(self, url: str) -> bytes:
        raise NotImplementedError("No generic HTTP client is configured for this application.")


class _UnsupportedMediaSource:
    async def extract(self, url: str, *, playlist: bool = False) -> dict[str, Any]:
        raise NotImplementedError("Source-media extraction is provided by a dedicated adapter.")


class _FFmpegProcessor:
    def __init__(self, ffmpeg: Any) -> None:
        self._ffmpeg = ffmpeg

    def audio_metadata(self, path: str | Path) -> Any:
        return self._ffmpeg.audio_metadata(path)

    def audio_cover(self, path: str | Path) -> Any:
        return self._ffmpeg.audio_cover(path)
