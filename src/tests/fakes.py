"""Stateful test doubles for external application boundaries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.modules.common.contracts import (
    HTTPClient,
    Mailer,
    MediaProcessor,
    MediaSource,
    RedisStore,
    Storage,
    TaskQueue,
)


@dataclass(frozen=True, slots=True)
class EnqueuedTask:
    task: Any
    args: tuple[Any, ...]
    kwargs: dict[str, Any]


@dataclass(frozen=True, slots=True)
class StoredFile:
    source: str
    destination: str


@dataclass(frozen=True, slots=True)
class SentEmail:
    recipient_email: str
    subject: str
    html_content: str


class FakeTaskQueue(TaskQueue):
    def __init__(self, error: Exception | None = None) -> None:
        self.enqueued: list[EnqueuedTask] = []
        self.cancelled: list[tuple[type[Any], tuple[Any, ...], dict[str, Any]]] = []
        self.error = error

    def enqueue(self, task: Any, *args: Any, **kwargs: Any) -> EnqueuedTask:
        if self.error is not None:
            raise self.error
        entry = EnqueuedTask(task=task, args=args, kwargs=kwargs)
        self.enqueued.append(entry)
        return entry

    def cancel_task(self, task_class: type[Any], *args: Any, **kwargs: Any) -> None:
        if self.error is not None:
            raise self.error
        self.cancelled.append((task_class, args, kwargs))


class FakeStorage(Storage):
    def __init__(self, error: Exception | None = None, *, return_none: bool = False) -> None:
        self.files: dict[str, bytes] = {}
        self.uploads: list[StoredFile] = []
        self.downloads: list[StoredFile] = []
        self.copies: list[StoredFile] = []
        self.deleted_paths: list[str] = []
        self.presigned_paths: list[str] = []
        self.error = error
        self.return_none = return_none

    def _raise_if_configured(self) -> None:
        if self.error is not None:
            raise self.error

    async def upload_file(
        self,
        src_path: str | Path,
        dst_path: str | Path,
        filename: str | None = None,
        callback: Callable[..., Any] | None = None,
    ) -> str | None:
        self._raise_if_configured()
        source = str(src_path)
        name = filename or Path(source).name
        destination = str(Path(dst_path) / name)
        self.files[destination] = b""
        self.uploads.append(StoredFile(source=source, destination=destination))
        if callback is not None:
            callback(0)
        if self.return_none:
            return None
        return destination

    async def download_file(self, src_path: str | Path, dst_path: str | Path) -> str | None:
        self._raise_if_configured()
        source, destination = str(src_path), str(dst_path)
        self.downloads.append(StoredFile(source=source, destination=destination))
        return destination if source in self.files else None

    async def copy_file(self, src_path: str, dst_path: str) -> str | None:
        self._raise_if_configured()
        self.copies.append(StoredFile(source=src_path, destination=dst_path))
        if src_path not in self.files:
            return None
        self.files[dst_path] = self.files[src_path]
        return dst_path

    async def delete_file(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        self._raise_if_configured()
        destination = str(kwargs.get("dst_path") or kwargs.get("filename") or args[0])
        self.deleted_paths.append(destination)
        self.files.pop(destination, None)
        return {}

    async def get_presigned_url(self, remote_path: str) -> str:
        self._raise_if_configured()
        self.presigned_paths.append(remote_path)
        return f"fake-storage://{remote_path}"


class FakeRedis(RedisStore):
    def __init__(
        self, content: dict[str, Any] | None = None, error: Exception | None = None
    ) -> None:
        self.content = dict(content or {})
        self.set_calls: list[tuple[str, Any, int]] = []
        self.published: list[tuple[str, str]] = []
        self.error = error

    def _raise_if_configured(self) -> None:
        if self.error is not None:
            raise self.error

    def get(self, key: str) -> Any:
        self._raise_if_configured()
        return self.content.get(key)

    def set(self, key: str, value: Any, ttl: int = 120) -> None:
        self._raise_if_configured()
        self.content[key] = value
        self.set_calls.append((key, value, ttl))

    def publish(self, channel: str, message: str) -> None:
        self._raise_if_configured()
        self.published.append((channel, message))

    async def async_get(self, key: str) -> Any:
        return self.get(key)

    async def async_set(self, key: str, value: Any, ttl: int = 120) -> None:
        self.set(key, value, ttl)

    async def async_publish(self, channel: str, message: str) -> None:
        self.publish(channel, message)


class FakeMailer(Mailer):
    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list[SentEmail] = []
        self.error = error

    async def send(self, recipient_email: str, subject: str, html_content: str) -> None:
        if self.error is not None:
            raise self.error
        self.sent.append(SentEmail(recipient_email, subject, html_content))


class FakeHTTPClient(HTTPClient):
    def __init__(
        self, responses: dict[str, bytes] | None = None, error: Exception | None = None
    ) -> None:
        self.responses = dict(responses or {})
        self.requested_urls: list[str] = []
        self.error = error

    async def get(self, url: str) -> bytes:
        if self.error is not None:
            raise self.error
        self.requested_urls.append(url)
        return self.responses[url]


class FakeMediaSource(MediaSource):
    def __init__(
        self,
        results: dict[str, dict[str, Any]] | None = None,
        error: Exception | None = None,
        media_info: dict[str, tuple[str, Any]] | None = None,
    ) -> None:
        self.results = dict(results or {})
        self.media_info = dict(media_info or {})
        self.extractions: list[tuple[str, bool]] = []
        self.error = error

    async def extract(self, url: str, *, playlist: bool = False) -> dict[str, Any]:
        if self.error is not None:
            raise self.error
        self.extractions.append((url, playlist))
        return self.results[url]

    async def get_source_media_info(self, source_info: Any) -> tuple[str, Any]:
        if self.error is not None:
            raise self.error
        url = str(source_info.url)
        self.extractions.append((url, False))
        return self.media_info.get(url, ("No configured source media", None))


class FakeMediaProcessor(MediaProcessor):
    def __init__(
        self, metadata: Any = None, cover: Any = None, error: Exception | None = None
    ) -> None:
        self.metadata = metadata
        self.cover = cover
        self.metadata_paths: list[str] = []
        self.cover_paths: list[str] = []
        self.error = error

    def audio_metadata(self, path: str | Path) -> Any:
        if self.error is not None:
            raise self.error
        self.metadata_paths.append(str(path))
        return self.metadata

    def audio_cover(self, path: str | Path) -> Any:
        if self.error is not None:
            raise self.error
        self.cover_paths.append(str(path))
        return self.cover


class FakeLifecycle:
    """Records app startup/shutdown boundaries without external connections."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def initialize_database(self) -> None:
        self.calls.append("initialize_database")

    async def verify_database(self) -> None:
        self.calls.append("verify_database")

    async def close_database(self) -> None:
        self.calls.append("close_database")

    async def check_redis(self) -> None:
        self.calls.append("check_redis")

    async def close_redis(self) -> None:
        self.calls.append("close_redis")
