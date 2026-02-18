"""Cover image cache service: download from S3 or URL and store under path/URL hash."""

import hashlib
import logging
import urllib.parse
from pathlib import Path

import httpx
from litestar.exceptions import NotFoundException

from src.modules.db.models import File as MediaFile
from src.modules.services.storage import StorageS3
from src.settings.app import get_app_settings

logger = logging.getLogger(__name__)
__all__ = ("CoverService",)


class CoverService:
    """Resolves cover image to a local cache path, downloading from S3 or source_url if needed."""

    async def get_or_download(
        self,
        file_obj: MediaFile,
        cache_dir_prefix: str,
        cache_file_prefix: str,
    ) -> Path:
        """
        Return path to cached cover file; download from S3 or source_url if not yet cached.
        Cache key is hash of file path or source_url so the same image is stored once.
        """
        media_cache_dir = get_app_settings().media_cache_dir
        cache_key, ext = self._cache_key_and_ext(file_obj)
        cache_filename = self._cache_filename(cache_key, cache_file_prefix, ext)
        cached_path = media_cache_dir / cache_dir_prefix / cache_filename

        if cached_path.exists():
            return cached_path

        cached_path.parent.mkdir(parents=True, exist_ok=True)

        if file_obj.public and file_obj.source_url:
            await self._download_from_url(file_obj.source_url, cached_path, file_obj.type.value)
        elif file_obj.path:
            await self._download_from_s3(file_obj.path, cached_path, file_obj.type.value)
        else:
            raise NotFoundException(f"{file_obj.type.value} cover has no path or source_url")

        return cached_path

    @staticmethod
    def _cache_key_and_ext(file_obj: MediaFile) -> tuple[str, str]:
        """Derive cache key (path or URL) and file extension from file record."""
        if file_obj.path:
            key = file_obj.path
            ext = (Path(file_obj.path).suffix or ".jpg").lstrip(".").lower() or "jpg"
        elif file_obj.source_url:
            key = file_obj.source_url
            parsed = urllib.parse.urlparse(file_obj.source_url)
            ext = (Path(parsed.path).suffix or ".jpg").lstrip(".").lower() or "jpg"
        else:
            raise NotFoundException(f"{file_obj.type.value} cover has no path or source_url")
        return key, ext

    @staticmethod
    def _cache_filename(cache_key: str, file_prefix: str, ext: str) -> str:
        """Build cache filename: {prefix}_{key_hash}.{ext}."""
        key_hash = hashlib.sha256(cache_key.encode()).hexdigest()[:16]
        ext = ext.lstrip(".").lower() or "jpg"
        return f"{file_prefix}_{key_hash}.{ext}"

    @staticmethod
    async def _download_from_s3(src_path: str, dst_path: Path, file_type_label: str) -> None:
        """Download file from S3 into dst_path. Raises NotFoundException if not found."""
        storage = StorageS3()
        downloaded = await storage.download_file(src_path=src_path, dst_path=dst_path)
        if not downloaded:
            raise NotFoundException(f"{file_type_label} cover not found in storage")

        logger.info(
            "%s cover downloaded from S3 and cached: path=%r, cache=%s",
            file_type_label,
            src_path,
            dst_path,
        )

    @staticmethod
    async def _download_from_url(url: str, dst_path: Path, file_type_label: str) -> None:
        """Download file from URL into dst_path. Raises NotFoundException on non-2xx."""
        async with httpx.AsyncClient() as client:
            response = await client.get(url, timeout=30.0)
            if response.status_code != 200:
                raise NotFoundException(
                    f"{file_type_label} cover not found at URL: {response.status_code}"
                )

            content = response.content

        dst_path.write_bytes(content)
        logger.info(
            "%s cover downloaded from URL and cached: url=%r, cache=%s",
            file_type_label,
            url,
            dst_path,
        )
