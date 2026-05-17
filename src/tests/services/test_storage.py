from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import botocore.exceptions
import pytest
from pydantic import SecretStr

from src.exceptions import StorageConfigurationError
from src.modules.services.storage import StorageS3, validate_s3_settings
from src.settings.db import S3Settings


class TestStorageSettings:
    def test_validate_s3_settings__ok(self) -> None:
        validate_s3_settings(
            S3Settings(
                access_key_id="key",
                secret_access_key=SecretStr("secret"),
                bucket_name="bucket",
            )
        )

    def test_validate_s3_settings__missing__fail(self) -> None:
        with pytest.raises(StorageConfigurationError, match="S3_ACCESS_KEY_ID"):
            validate_s3_settings(S3Settings(access_key_id=None, secret_access_key=None, bucket_name=""))


class TestStorageS3Operations:
    async def test_upload_file__ok(self, tmp_path: Path) -> None:
        storage, s3 = _make_storage()
        src_path = tmp_path / "audio.mp3"
        src_path.write_bytes(b"audio")

        result = await storage.upload_file(src_path, "audio")

        assert result == "audio/audio.mp3"
        s3.upload_file.assert_awaited_once_with(
            Filename=str(src_path),
            Bucket="bucket",
            Key="audio/audio.mp3",
            Callback=None,
            ExtraArgs={"ContentType": "audio/mpeg"},
        )

    async def test_download_file__ok(self, tmp_path: Path) -> None:
        storage, s3 = _make_storage()
        dst_path = tmp_path / "audio.mp3"

        result = await storage.download_file("audio/source.mp3", dst_path)

        assert result == str(dst_path)
        s3.download_file.assert_awaited_once_with(
            Filename=str(dst_path),
            Bucket="bucket",
            Key="audio/source.mp3",
        )

    async def test_copy_file__ok(self) -> None:
        storage, s3 = _make_storage()

        result = await storage.copy_file("tmp/source.mp3", "audio/result.mp3")

        assert result == "audio/result.mp3"
        s3.copy_object.assert_awaited_once_with(
            Bucket="bucket",
            Key="audio/result.mp3",
            CopySource={"Bucket": "bucket", "Key": "tmp/source.mp3"},
        )

    async def test_get_file_size__uses_content_length(self) -> None:
        storage, _ = _make_storage(
            head_result={"ResponseMetadata": {"HTTPHeaders": {"content-length": "42"}}}
        )

        result = await storage.get_file_size(filename="episode.mp3")

        assert result == 42

    async def test_get_file_size__missing__returns_zero(self) -> None:
        storage, _ = _make_storage(head_result=None)

        result = await storage.get_file_size(filename="missing.mp3")

        assert result == 0

    async def test_delete_file__requires_target(self) -> None:
        storage, _ = _make_storage()

        with pytest.raises(ValueError, match="At least one argument"):
            await storage.delete_file()

    async def test_get_presigned_url__uses_cached_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        storage, s3 = _make_storage()
        redis = SimpleNamespace(async_get=AsyncMock(return_value="cached"), async_set=AsyncMock())
        monkeypatch.setattr("src.modules.services.storage.RedisClient", lambda: redis)

        result = await storage.get_presigned_url("audio/source.mp3")

        assert result == "cached"
        s3.generate_presigned_url.assert_not_called()
        redis.async_set.assert_not_awaited()

    async def test_get_presigned_url__stores_generated_url(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        storage, s3 = _make_storage()
        s3.generate_presigned_url.return_value = "generated"
        redis = SimpleNamespace(async_get=AsyncMock(return_value=None), async_set=AsyncMock())
        monkeypatch.setattr("src.modules.services.storage.RedisClient", lambda: redis)

        result = await storage.get_presigned_url("audio/source.mp3")

        assert result == "generated"
        redis.async_set.assert_awaited_once_with(
            "audio/source.mp3",
            value="generated",
            ttl=120,
        )

    async def test_run_with_client__client_error__returns_error_code(self) -> None:
        storage, _ = _make_storage(
            client_error=botocore.exceptions.ClientError(
                {"Error": {"Code": "404", "Message": "missing"}},
                "HeadObject",
            )
        )

        code, result = await storage._run_with_client(AsyncMock())

        assert code == StorageS3.CODE_CLIENT_ERROR
        assert result is None


def _make_storage(
    *,
    head_result: dict | None = None,
    client_error: Exception | None = None,
) -> tuple[StorageS3, "_FakeS3Client"]:
    storage = StorageS3.__new__(StorageS3)
    storage.settings = SimpleNamespace(
        s3=SimpleNamespace(
            bucket_name="bucket",
            bucket_audio_path="audio",
            storage_url="https://storage.local",
            link_expires_in=600,
            link_cache_expires_in=120,
        )
    )
    s3 = _FakeS3Client(head_result=head_result, client_error=client_error)
    storage._session = SimpleNamespace(client=lambda **kwargs: _FakeS3Context(s3, client_error))
    return storage, s3


class _FakeS3Client:
    def __init__(
        self,
        *,
        head_result: dict | None,
        client_error: Exception | None,
    ) -> None:
        self.upload_file = AsyncMock(return_value=None)
        self.download_file = AsyncMock(return_value=None)
        self.copy_object = AsyncMock(return_value={})
        self.delete_object = AsyncMock(return_value={})
        self.head_object = AsyncMock(return_value=head_result)
        self.generate_presigned_url = AsyncMock(return_value="presigned")
        self.client_error = client_error


class _FakeS3Context:
    def __init__(self, client: _FakeS3Client, client_error: Exception | None) -> None:
        self.client = client
        self.client_error = client_error

    async def __aenter__(self) -> _FakeS3Client:
        if self.client_error:
            raise self.client_error
        return self.client

    async def __aexit__(self, *args: object) -> None:
        return None
