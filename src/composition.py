"""Production composition root for concrete application adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

import rq
from redis import Redis

from src.modules.db import close_database, get_session_factory, verify_database_reachable
from src.modules.db.services import SASessionUOW
from src.modules.db.session import initialize_database
from src.modules.services.email import send_email
from src.modules.services.redis import (
    RedisClient,
    check_redis_connection,
    close_async_redis_connection,
)
from src.modules.services.storage import StorageS3, validate_s3_settings
from src.modules.utils import ffmpeg
from src.modules.utils.common import get_source_media_info
from src.providers import (
    AppProviders,
    HTTPClient,
    Mailer,
    MediaProcessor,
    MediaSource,
    TaskQueue,
)


class _ProductionMailer(Mailer):
    async def send(self, recipient_email: str, subject: str, html_content: str) -> None:
        await send_email(recipient_email, subject, html_content)


class _ProductionMediaSource(MediaSource):
    async def extract(self, url: str, *, playlist: bool = False) -> dict[str, Any]:
        raise NotImplementedError("Playlist extraction is provided by a dedicated adapter.")

    async def get_source_media_info(self, source_info: Any) -> tuple[str, Any]:
        return await get_source_media_info(source_info)


class _UnsupportedHTTPClient(HTTPClient):
    async def get(self, url: str) -> bytes:
        raise NotImplementedError("No generic HTTP client is configured for this application.")


class _FFmpegProcessor(MediaProcessor):
    def __init__(self, implementation: Any) -> None:
        self._implementation = implementation

    def audio_metadata(self, path: str | Path) -> Any:
        return self._implementation.audio_metadata(path)

    def audio_cover(self, path: str | Path) -> Any:
        return self._implementation.audio_cover(path)


def create_production_providers() -> AppProviders:
    """Construct production adapters without creating network clients yet."""
    return AppProviders(
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
        cancel_task=lambda task_class, *args, **kwargs: task_class.cancel_task(*args, **kwargs),
        make_storage=StorageS3,
        make_redis=RedisClient,
        mailer=_ProductionMailer(),
        http_client=_UnsupportedHTTPClient(),
        media_source=_ProductionMediaSource(),
        media_processor=_FFmpegProcessor(ffmpeg),
    )
