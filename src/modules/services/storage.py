import asyncio
import enum
import inspect
import logging
import mimetypes
import os
import urllib.parse
from collections.abc import Callable, Collection
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Awaitable, cast, Optional, Protocol, Self

import aioboto3
import botocore.exceptions
from sqlalchemy.ext.asyncio import AsyncSession

from src.exceptions import NotSupportedError, StorageConfigurationError
from src.modules.db.models import File
from src.modules.db.repositories import FileRepository, SystemScope
from src.modules.db.services import SASessionUOW
from src.modules.services.redis import RedisClient
from src.settings.app import get_app_settings
from src.settings.db import S3Settings

logger = logging.getLogger(__name__)

DEFAULT_CLEANUP_CONCURRENCY = 10


class StorageDeleteStatus(enum.StrEnum):
    """Outcome of deleting one object from external storage."""

    DELETED = "deleted"
    NOT_FOUND = "not_found"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class StorageDeleteResult:
    """Typed result for an S3 delete request, including idempotent 404 handling."""

    status: StorageDeleteStatus
    response: dict[str, Any] | None = None
    error: str | None = None


class FileCleanupStatus(enum.StrEnum):
    """Public per-file cleanup status returned by the storage service."""

    CLEARED = "cleared"
    ALREADY_CLEAR = "already_clear"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class FileCleanupItemResult:
    """Result of clearing external storage metadata for one File row."""

    file_id: int
    status: FileCleanupStatus
    path: str = ""
    size: int = 0
    audio_episode_ids: tuple[int, ...] = ()
    image_episode_ids: tuple[int, ...] = ()
    error: str | None = None

    @property
    def episode_ids(self) -> tuple[int, ...]:
        """Return all related episode IDs without duplicates."""
        return tuple(sorted(set(self.audio_episode_ids) | set(self.image_episode_ids)))


@dataclass(frozen=True, slots=True)
class FileCleanupBatchResult:
    """Aggregate result returned to admin, API, or UI callers."""

    items: tuple[FileCleanupItemResult, ...] = ()

    @property
    def cleared_ids(self) -> tuple[int, ...]:
        return tuple(
            item.file_id for item in self.items if item.status == FileCleanupStatus.CLEARED
        )

    @property
    def already_clear_ids(self) -> tuple[int, ...]:
        return tuple(
            item.file_id for item in self.items if item.status == FileCleanupStatus.ALREADY_CLEAR
        )

    @property
    def failed_ids(self) -> tuple[int, ...]:
        return tuple(item.file_id for item in self.items if item.status == FileCleanupStatus.FAILED)

    @property
    def failures(self) -> tuple[FileCleanupItemResult, ...]:
        return tuple(item for item in self.items if item.status == FileCleanupStatus.FAILED)

    @property
    def successful(self) -> bool:
        return not self.failures


class StorageCleanupBackend(Protocol):
    """Narrow storage boundary used by the reusable cleanup service."""

    settings: Any

    async def delete_file_result(self, *, dst_path: str) -> StorageDeleteResult: ...


class PresignedURLStorage(Protocol):
    """Storage boundary required to generate a temporary read URL."""

    async def get_presigned_url(self, remote_path: str) -> str: ...


class CleanupUnitOfWork(Protocol):
    """Transaction boundary required by the cleanup service."""

    session: AsyncSession

    async def __aenter__(self) -> Self: ...

    async def __aexit__(self, *args: object) -> None: ...

    def mark_for_commit(self) -> None: ...


def validate_s3_settings(s3_settings: S3Settings) -> None:
    """
    Validate that required S3 storage settings are present (credentials and bucket).
    Raises StorageConfigurationError if any required setting is missing.
    Called at application startup via lifespan, similar to DB connectivity check.
    """
    required = [
        s3_settings.access_key_id,
        s3_settings.secret_access_key,
        s3_settings.bucket_name,
    ]
    if not all(required):
        missing = []
        if not s3_settings.access_key_id:
            missing.append("S3_ACCESS_KEY_ID")
        if not s3_settings.secret_access_key:
            missing.append("S3_SECRET_ACCESS_KEY")
        if not s3_settings.bucket_name:
            missing.append("S3_BUCKET_NAME")
        raise StorageConfigurationError(details=f"Missing S3 settings: {', '.join(missing)}")


async def get_file_presigned_url(
    file: File,
    storage: PresignedURLStorage | None = None,
) -> str:
    """Generate a temporary read URL without coupling the ORM model to S3."""
    if not file.path:
        raise NotSupportedError(f"File {file} has no S3 key; cannot presign.")

    url = await (storage or StorageS3()).get_presigned_url(file.path)
    if not url:
        raise NotSupportedError(f"Presign failed for path {file.path!r}.")
    return url


class StorageS3:
    """Async S3 client (session singleton) for access to S3 bucket via aioboto3."""

    CODE_OK = 0
    CODE_CLIENT_ERROR = 1
    CODE_COMMON_ERROR = 2

    def __init__(self) -> None:
        logger.debug("Creating S3 session (aioboto3)...")
        self.settings = get_app_settings()
        validate_s3_settings(self.settings.s3)

        secret_access_key = (
            self.settings.s3.secret_access_key.get_secret_value()
            if self.settings.s3.secret_access_key
            else None
        )
        self._session = aioboto3.Session(
            aws_access_key_id=self.settings.s3.access_key_id,
            aws_secret_access_key=secret_access_key,
            region_name=self.settings.s3.region_name,
        )
        logger.debug("aioboto3 S3 Session created")

    async def upload_file(
        self,
        src_path: str | Path,
        dst_path: str | Path,
        filename: str | None = None,
        callback: Optional[Callable] = None,
    ) -> str | None:
        """Upload file to S3 storage."""
        mimetype, _ = mimetypes.guess_type(str(src_path))
        filename = filename or os.path.basename(str(src_path))
        dst_path = os.path.join(dst_path, filename)

        async def _upload(s3: Any) -> None:
            await s3.upload_file(
                Filename=str(src_path),
                Bucket=self.settings.s3.bucket_name,
                Key=str(dst_path),
                Callback=callback,
                ExtraArgs={"ContentType": mimetype},
            )

        code, _ = await self._run_with_client(_upload)
        if code != self.CODE_OK:
            return None

        logger.info("File %s successful uploaded. Remote path: %s", filename, dst_path)
        return dst_path

    async def download_file(self, src_path: str | Path, dst_path: str | Path) -> str | None:
        """Download file from S3 storage."""

        async def _download(s3: Any) -> None:
            await s3.download_file(
                Filename=str(dst_path),
                Bucket=self.settings.s3.bucket_name,
                Key=str(src_path),
            )

        code, _ = await self._run_with_client(_download)
        if code != self.CODE_OK:
            return None

        logger.info("File successful downloaded. Local path: %s", dst_path)
        return str(dst_path)

    async def copy_file(self, src_path: str, dst_path: str) -> str | None:
        """Copy object inside S3 bucket."""

        async def _copy(s3: Any) -> dict:
            return await s3.copy_object(
                Bucket=self.settings.s3.bucket_name,
                Key=dst_path,
                CopySource={
                    "Bucket": self.settings.s3.bucket_name,
                    "Key": src_path,
                },
            )

        code, _ = await self._run_with_client(_copy)
        if code != self.CODE_OK:
            return None

        logger.info("File successful copied: %s -> %s", src_path, dst_path)
        return dst_path

    async def get_file_info(
        self,
        filename: str,
        remote_path: str | None = None,
        error_log_level: int = logging.ERROR,
        dst_path: str | None = None,
    ) -> dict | None:
        """Get file metadata (headers) from S3."""
        remote_path = remote_path or self.settings.s3.bucket_audio_path
        dst_path = dst_path or os.path.join(remote_path, filename)

        async def _head(s3: Any) -> dict:
            return await s3.head_object(
                Key=dst_path,
                Bucket=self.settings.s3.bucket_name,
            )

        _, result = await self._run_with_client(_head, error_log_level=error_log_level)
        return result

    async def get_file_size(
        self,
        filename: str | None = None,
        remote_path: str | None = None,
        dst_path: str | None = None,
    ) -> int:
        """Get file size (content-length) from S3."""
        remote_path = remote_path or self.settings.s3.bucket_audio_path
        if filename or dst_path:
            file_info = await self.get_file_info(
                filename or "",
                remote_path,
                dst_path=dst_path,
                error_log_level=logging.WARNING,
            )
            if file_info:
                return int(file_info["ResponseMetadata"]["HTTPHeaders"]["content-length"])

        logger.info("File %s was not found on s3 storage", filename)
        return 0

    async def delete_file(
        self,
        filename: str | None = None,
        remote_path: str | None = None,
        dst_path: str | None = None,
    ) -> dict | None:
        """Delete object from S3."""
        remote_path = remote_path or self.settings.s3.bucket_audio_path
        if not dst_path and not filename:
            raise ValueError("At least one argument must be set: dst_path | filename")

        dst_path = dst_path or os.path.join(remote_path, filename or "")

        async def _delete(s3: Any) -> dict:
            return await s3.delete_object(
                Key=dst_path,
                Bucket=self.settings.s3.bucket_name,
            )

        _, result = await self._run_with_client(_delete)
        return result

    async def delete_file_result(
        self,
        filename: str | None = None,
        remote_path: str | None = None,
        dst_path: str | None = None,
    ) -> StorageDeleteResult:
        """Delete one object and preserve enough error information for batch cleanup."""
        remote_path = remote_path or self.settings.s3.bucket_audio_path
        if not dst_path and not filename:
            raise ValueError("At least one argument must be set: dst_path | filename")

        object_path = dst_path or os.path.join(remote_path, filename or "")
        try:
            async with self._session.client(
                service_name="s3",
                endpoint_url=self.settings.s3.storage_url,
            ) as s3:
                response = await s3.delete_object(
                    Key=object_path,
                    Bucket=self.settings.s3.bucket_name,
                )
        except botocore.exceptions.ClientError as exc:
            error_code = str(exc.response.get("Error", {}).get("Code", ""))
            http_status = exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
            if http_status == 404 or error_code in {"404", "NoSuchKey", "NotFound"}:
                logger.warning(
                    "S3 object already absent during delete: bucket=%s path=%s code=%s "
                    "http_status=%s",
                    self.settings.s3.bucket_name,
                    object_path,
                    error_code,
                    http_status,
                )
                return StorageDeleteResult(
                    status=StorageDeleteStatus.NOT_FOUND,
                    error=f"S3 object was already absent ({error_code or http_status})",
                )

            logger.exception(
                "S3 delete failed: bucket=%s path=%s code=%s",
                self.settings.s3.bucket_name,
                object_path,
                error_code,
            )
            return StorageDeleteResult(
                status=StorageDeleteStatus.FAILED,
                error=f"S3 delete failed ({error_code or 'ClientError'})",
            )
        except Exception as exc:
            logger.exception(
                "S3 delete failed: bucket=%s path=%s",
                self.settings.s3.bucket_name,
                object_path,
            )
            return StorageDeleteResult(
                status=StorageDeleteStatus.FAILED,
                error=f"S3 delete failed: {exc}",
            )

        return StorageDeleteResult(status=StorageDeleteStatus.DELETED, response=response)

    async def delete_files(self, filenames: list[str], remote_path: str) -> None:
        """Delete multiple objects from S3."""
        for filename in filenames:
            dst_path = os.path.join(remote_path, filename)
            await self.delete_file(dst_path=dst_path)

    async def get_presigned_url(self, remote_path: str) -> str:
        """Get or create cached presigned URL for object."""
        redis = RedisClient()
        cached_url = await redis.async_get(remote_path)
        if isinstance(cached_url, str) and cached_url:
            return cached_url

        async def _presign(s3: Any) -> str:
            # aioboto3 client may return a coroutine; botocore sync client returns str.
            raw = s3.generate_presigned_url(
                ClientMethod="get_object",
                Params={
                    "Bucket": self.settings.s3.bucket_name,
                    "Key": remote_path,
                },
                ExpiresIn=self.settings.s3.link_expires_in,
            )
            if inspect.isawaitable(raw):
                raw = await raw
            return raw

        _, url = await self._run_with_client(_presign)
        if url:
            await redis.async_set(
                remote_path,
                value=url,
                ttl=self.settings.s3.link_cache_expires_in,
            )

        return url or ""

    async def _run_with_client(
        self,
        handler: Callable[[Any], Awaitable[Any]],
        error_log_level: int = logging.ERROR,
    ) -> tuple[int, Any]:
        """Run async handler with S3 client; return (code, result)."""
        try:
            async with self._session.client(
                service_name="s3",
                endpoint_url=self.settings.s3.storage_url,
            ) as s3:
                logger.debug("Executing S3 request: %s", handler.__name__)
                response = await handler(s3)
                return self.CODE_OK, response

        except botocore.exceptions.ClientError as exc:
            logger.log(
                error_log_level,
                "Couldn't execute request (%s) to S3: ClientError %r",
                handler.__name__,
                exc,
            )
            return self.CODE_CLIENT_ERROR, None

        except Exception as exc:
            logger.exception("S3 request failed %s: %r", handler.__name__, exc)
            return self.CODE_COMMON_ERROR, None


class FileStorageCleanupService:
    """Remove S3 objects for File IDs and clear metadata for successful rows."""

    def __init__(
        self,
        storage: StorageCleanupBackend | None = None,
        uow_factory: Callable[[], CleanupUnitOfWork] | None = None,
    ) -> None:
        self.storage = storage or StorageS3()
        self.uow_factory = uow_factory or cast(
            Callable[[], CleanupUnitOfWork],
            SASessionUOW,
        )

    async def clear_files(
        self,
        file_ids: Collection[int],
        *,
        concurrency: int = DEFAULT_CLEANUP_CONCURRENCY,
    ) -> FileCleanupBatchResult:
        """Clear external objects for IDs with bounded parallelism and partial success."""
        if concurrency < 1:
            raise ValueError("concurrency must be greater than zero")

        requested_ids = list(file_ids)
        normalized_ids = list(dict.fromkeys(requested_ids))
        if not normalized_ids:
            logger.info("File storage cleanup skipped: no file IDs requested")
            return FileCleanupBatchResult()

        started_at = perf_counter()
        deleted_paths: list[str] = []
        items_by_id: dict[int, FileCleanupItemResult] = {}
        self._log_storage_target(
            requested_count=len(requested_ids),
            unique_count=len(normalized_ids),
            concurrency=concurrency,
        )

        try:
            async with self.uow_factory() as uow:
                file_repository = FileRepository(uow.session, scope=SystemScope.ALL)
                files = await file_repository.all_by_ids_with_episode_references(normalized_ids)
                files_by_id = {file.id: file for file in files}

                for file_id in normalized_ids:
                    if file_id not in files_by_id:
                        items_by_id[file_id] = FileCleanupItemResult(
                            file_id=file_id,
                            status=FileCleanupStatus.FAILED,
                            error=f"File #{file_id} was not found",
                        )

                path_groups: dict[str, list[File]] = {}
                for file in files:
                    if file.path:
                        path_groups.setdefault(file.path, []).append(file)

                logger.info(
                    "File storage cleanup paths resolved: unique_paths=%s paths=%s",
                    len(path_groups),
                    sorted(path_groups),
                )
                self._log_file_context(files)

                conflicts = await self._find_shared_path_conflicts(
                    repository=file_repository,
                    paths=tuple(path_groups),
                    selected_ids=set(normalized_ids),
                )
                if conflicts:
                    items_by_id.update(
                        self._build_blocked_results(
                            files=files,
                            conflicts=conflicts,
                        )
                    )
                    result = self._ordered_result(normalized_ids, items_by_id)
                    self._log_summary(result, started_at, blocked=True)
                    return result

                for file in files:
                    if not file.path:
                        items_by_id[file.id] = self._item_result(
                            file,
                            status=FileCleanupStatus.ALREADY_CLEAR,
                        )

                semaphore = asyncio.Semaphore(concurrency)
                delete_results = await asyncio.gather(
                    *(
                        self._delete_path_group(semaphore, path, group)
                        for path, group in path_groups.items()
                    )
                )

                for path, group, delete_result in delete_results:
                    if delete_result.status == StorageDeleteStatus.FAILED:
                        error = delete_result.error or "S3 delete returned no result"
                        for file in group:
                            items_by_id[file.id] = self._item_result(
                                file,
                                status=FileCleanupStatus.FAILED,
                                error=error,
                            )
                        continue

                    deleted_paths.append(path)
                    if delete_result.status == StorageDeleteStatus.NOT_FOUND:
                        logger.warning(
                            "Treating already absent S3 object as successfully cleared: path=%s "
                            "file_ids=%s",
                            path,
                            [file.id for file in group],
                        )

                    for file in group:
                        items_by_id[file.id] = self._item_result(
                            file,
                            status=FileCleanupStatus.CLEARED,
                        )
                        file.path = ""
                        file.size = 0
                        file.available = False

                if deleted_paths:
                    uow.mark_for_commit()

            result = self._ordered_result(normalized_ids, items_by_id)
        except Exception:
            logger.exception(
                "File storage cleanup database transaction failed after S3 phase: "
                "deleted_paths=%s requested_ids=%s",
                deleted_paths,
                normalized_ids,
            )
            raise

        self._log_summary(result, started_at)
        return result

    async def _find_shared_path_conflicts(
        self,
        *,
        repository: FileRepository,
        paths: tuple[str, ...],
        selected_ids: set[int],
    ) -> dict[str, tuple[int, ...]]:
        if not paths:
            return {}

        normalized = await repository.find_path_references(paths, excluded_ids=selected_ids)
        if normalized:
            logger.warning(
                "File storage cleanup batch blocked by shared S3 paths: conflicts=%s "
                "selected_ids=%s; independent paths will not be deleted",
                normalized,
                sorted(selected_ids),
            )
        return normalized

    async def _delete_path_group(
        self,
        semaphore: asyncio.Semaphore,
        path: str,
        files: list[File],
    ) -> tuple[str, list[File], StorageDeleteResult]:
        file_ids = [file.id for file in files]
        sizes = [file.size for file in files]
        episode_ids = sorted(
            {
                episode.id
                for file in files
                for episode in (*file.audio_episodes, *file.image_episodes)
            }
        )
        logger.info(
            "Starting S3 object deletion: path=%s file_ids=%s sizes=%s episode_ids=%s",
            path,
            file_ids,
            sizes,
            episode_ids,
        )

        async with semaphore:
            try:
                result = await self.storage.delete_file_result(dst_path=path)
            except Exception as exc:
                logger.exception(
                    "Unhandled S3 deletion error: path=%s file_ids=%s",
                    path,
                    file_ids,
                )
                result = StorageDeleteResult(
                    status=StorageDeleteStatus.FAILED,
                    error=f"S3 delete failed: {exc}",
                )

        if result.status == StorageDeleteStatus.FAILED:
            logger.error(
                "S3 object deletion failed: path=%s file_ids=%s error=%s",
                path,
                file_ids,
                result.error,
            )
        else:
            logger.info(
                "S3 object deletion completed: path=%s file_ids=%s status=%s",
                path,
                file_ids,
                result.status,
            )
        return path, files, result

    def _build_blocked_results(
        self,
        *,
        files: list[File],
        conflicts: dict[str, tuple[int, ...]],
    ) -> dict[int, FileCleanupItemResult]:
        results: dict[int, FileCleanupItemResult] = {}
        conflict_paths = sorted(conflicts)
        for file in files:
            if not file.path:
                results[file.id] = self._item_result(
                    file,
                    status=FileCleanupStatus.ALREADY_CLEAR,
                )
                continue

            if file.path in conflicts:
                error = (
                    f"S3 path {file.path!r} is also used by File IDs "
                    f"{list(conflicts[file.path])}"
                )
            else:
                error = (
                    "Batch was blocked by shared S3 paths; no objects were deleted. "
                    f"Conflicting paths: {conflict_paths}"
                )
            results[file.id] = self._item_result(
                file,
                status=FileCleanupStatus.FAILED,
                error=error,
            )
        return results

    @staticmethod
    def _item_result(
        file: File,
        *,
        status: FileCleanupStatus,
        error: str | None = None,
    ) -> FileCleanupItemResult:
        return FileCleanupItemResult(
            file_id=file.id,
            status=status,
            path=file.path,
            size=file.size,
            audio_episode_ids=tuple(sorted(episode.id for episode in file.audio_episodes)),
            image_episode_ids=tuple(sorted(episode.id for episode in file.image_episodes)),
            error=error,
        )

    @staticmethod
    def _ordered_result(
        normalized_ids: list[int],
        items_by_id: dict[int, FileCleanupItemResult],
    ) -> FileCleanupBatchResult:
        return FileCleanupBatchResult(
            items=tuple(items_by_id[file_id] for file_id in normalized_ids)
        )

    def _log_storage_target(
        self,
        *,
        requested_count: int,
        unique_count: int,
        concurrency: int,
    ) -> None:
        s3_settings = self.storage.settings.s3
        logger.info(
            "Starting file storage cleanup: endpoint=%s bucket=%s region=%s "
            "requested_ids=%s unique_ids=%s concurrency=%s",
            self._safe_storage_endpoint(s3_settings.storage_url),
            s3_settings.bucket_name,
            s3_settings.region_name,
            requested_count,
            unique_count,
            concurrency,
        )

    @staticmethod
    def _safe_storage_endpoint(storage_url: str | None) -> str:
        """Strip credentials and query parameters before logging an S3 endpoint."""
        if not storage_url:
            return "AWS default endpoint"

        parsed = urllib.parse.urlsplit(storage_url)
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        netloc = f"{hostname}:{parsed.port}" if parsed.port else hostname
        return urllib.parse.urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))

    @staticmethod
    def _log_file_context(files: list[File]) -> None:
        for file in files:
            audio_context = [
                {"id": episode.id, "title": episode.title, "source_id": episode.source_id}
                for episode in file.audio_episodes
            ]
            image_context = [
                {"id": episode.id, "title": episode.title, "source_id": episode.source_id}
                for episode in file.image_episodes
            ]
            logger.info(
                "File selected for storage cleanup: file_id=%s path=%s size=%s type=%s "
                "episode_ids=%s",
                file.id,
                file.path,
                file.size,
                file.type,
                sorted({episode.id for episode in (*file.audio_episodes, *file.image_episodes)}),
            )
            logger.debug(
                "File cleanup context: file_id=%s owner_id=%s audio_episodes=%s "
                "image_episodes=%s",
                file.id,
                file.owner_id,
                audio_context,
                image_context,
            )

    @staticmethod
    def _log_summary(
        result: FileCleanupBatchResult,
        started_at: float,
        *,
        blocked: bool = False,
    ) -> None:
        duration = perf_counter() - started_at
        cleared_items = [
            {"id": item.file_id, "path": item.path, "size": item.size}
            for item in result.items
            if item.status == FileCleanupStatus.CLEARED
        ]
        already_clear_items = [
            item.file_id for item in result.items if item.status == FileCleanupStatus.ALREADY_CLEAR
        ]
        failed_items = [
            {"id": item.file_id, "path": item.path, "error": item.error} for item in result.failures
        ]
        cleared_bytes = sum(
            item.size for item in result.items if item.status == FileCleanupStatus.CLEARED
        )
        log_level = logging.WARNING if failed_items else logging.INFO
        logger.log(
            log_level,
            "File storage cleanup finished: duration=%.3fs blocked=%s cleared=%s "
            "already_clear=%s failed=%s cleared_bytes=%s",
            duration,
            blocked,
            cleared_items,
            already_clear_items,
            failed_items,
            cleared_bytes,
        )
