"""Typed application-boundary providers.

The application factory owns one :class:`AppProviders` instance. The
production composition root supplies concrete adapters; tests may pass
stateful fakes without patching module globals or starting external services.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.modules.common.contracts import (
    HTTPClient,
    Mailer,
    MediaProcessor,
    MediaSource,
    RedisStore,
    Storage,
    TaskQueue,
)
from src.settings.app import AppSettings
from src.settings.db import S3Settings

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
    cancel_task: Callable[..., None]
    make_storage: Callable[[], Storage]
    make_redis: Callable[[], RedisStore]
    mailer: Mailer
    http_client: HTTPClient
    media_source: MediaSource
    media_processor: MediaProcessor
