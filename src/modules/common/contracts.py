"""Dependency-neutral contracts for application infrastructure boundaries."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol


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

    async def get_source_media_info(self, source_info: Any) -> tuple[str, Any]: ...


class MediaProcessor(Protocol):
    """Media-processing boundary (ffmpeg and local metadata tooling)."""

    def audio_metadata(self, path: str | Path) -> Any: ...

    def audio_cover(self, path: str | Path) -> Any: ...
