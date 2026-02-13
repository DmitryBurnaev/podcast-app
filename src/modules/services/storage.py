import os
import logging
import mimetypes
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

import aioboto3
import botocore.exceptions

from src.exceptions import StorageConfigurationError
from src.modules.services.redis import RedisClient
from src.settings.app import get_app_settings

logger = logging.getLogger(__name__)


class StorageS3:
    """Async S3 client (session singleton) for access to S3 bucket via aioboto3."""

    CODE_OK = 0
    CODE_CLIENT_ERROR = 1
    CODE_COMMON_ERROR = 2

    def __init__(self) -> None:
        logger.debug("Creating S3 session (aioboto3)...")
        self.settings = get_app_settings()
        if not all(
            [
                self.settings.s3.access_key_id,
                self.settings.s3.secret_access_key,
                self.settings.s3.bucket_name,
            ]
        ):
            raise StorageConfigurationError("Missing S3 access key or secret key")

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
        dst_path: str,
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
                Key=dst_path,
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

    async def delete_files(
        self,
        filenames: list[str],
        remote_path: str,
    ) -> None:
        """Delete multiple objects from S3."""
        for filename in filenames:
            dst_path = os.path.join(remote_path, filename)
            await self.delete_file(dst_path=dst_path)

    async def get_presigned_url(self, remote_path: str) -> str:
        """Get or create cached presigned URL for object."""
        redis = RedisClient()
        url: str | None = None
        if not (url := await redis.async_get(remote_path)):

            async def _presign(s3: Any) -> str:
                # generate_presigned_url is sync (local signing, no I/O)
                return s3.generate_presigned_url(
                    ClientMethod="get_object",
                    Params={
                        "Bucket": self.settings.s3.bucket_name,
                        "Key": remote_path,
                    },
                    ExpiresIn=self.settings.s3.link_expires_in,
                )

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
