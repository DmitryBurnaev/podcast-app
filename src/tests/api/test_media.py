from collections import namedtuple
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from litestar.testing import TestClient

from src.main import PodcastApp
from src.modules.api.errors import InvalidParametersError
from src.modules.api.media import _get_upload, _upload_audio_cover
from src.modules.schemas.media import UploadedImageData
from src.tests.helpers import assert_error_response


class TestMediaUploadAPI:
    @pytest.mark.parametrize(
        ("path", "filename", "content_type", "message"),
        [
            (
                "/api/media/upload/audio/",
                "not-audio.txt",
                "text/plain",
                "File must be audio.",
            ),
            (
                "/api/media/upload/image/",
                "not-image.txt",
                "text/plain",
                "File must be image.",
            ),
        ],
    )
    def test_upload__wrong_content_type__fail(
        self,
        client: TestClient[PodcastApp],
        path: str,
        filename: str,
        content_type: str,
        message: str,
    ) -> None:
        response = client.post(
            path,
            files={"file": (filename, b"plain-content", content_type)},
        )

        error = assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )
        assert error["details"] == {"file": message}

    def test_upload__missing_file__fail(self) -> None:
        with pytest.raises(InvalidParametersError) as exc_info:
            _get_upload({})

        assert getattr(exc_info.value, "details", None) == {"file": "File is required."}

    @pytest.mark.parametrize(
        ("path", "content_type", "message"),
        [
            (
                "/api/media/upload/audio/",
                "audio/mpeg",
                "Could not upload audio file.",
            ),
            (
                "/api/media/upload/image/",
                "image/jpeg",
                "Could not upload image file.",
            ),
        ],
    )
    def test_upload__storage_failure__fail(
        self,
        client: TestClient[PodcastApp],
        monkeypatch: pytest.MonkeyPatch,
        path: str,
        content_type: str,
        message: str,
    ) -> None:
        metadata = namedtuple("AudioMetadata", ["duration"])(duration=42)
        storage = SimpleNamespace(upload_file=AsyncMock(return_value=""))
        monkeypatch.setattr("src.modules.api.media.StorageS3", lambda: storage)
        monkeypatch.setattr(
            "src.modules.api.media.save_uploaded_file",
            AsyncMock(return_value=Path("/tmp/uploaded.bin")),
        )
        monkeypatch.setattr("src.modules.api.media.get_file_size", Mock(return_value=512))
        monkeypatch.setattr("src.modules.api.media.ffmpeg_utils.audio_metadata", Mock(return_value=metadata))

        response = client.post(
            path,
            files={"file": ("upload.bin", b"content", content_type)},
        )

        error = assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )
        assert error["details"] == {"file": message}

    def test_upload_image__ok(
        self,
        client: TestClient[PodcastApp],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        storage = SimpleNamespace(
            upload_file=AsyncMock(return_value="tmp/images/uploaded_image.jpg"),
            get_presigned_url=AsyncMock(return_value="https://storage/preview"),
        )
        monkeypatch.setattr("src.modules.api.media.StorageS3", lambda: storage)
        monkeypatch.setattr(
            "src.modules.api.media.save_uploaded_file",
            AsyncMock(return_value=Path("/tmp/uploaded_image.jpg")),
        )
        monkeypatch.setattr("src.modules.api.media.get_file_size", Mock(return_value=256))

        response = client.post(
            "/api/media/upload/image/",
            files={"file": ("cover.jpg", b"image-content", "image/jpeg")},
        )

        assert response.status_code in {200, 201}, response.text
        response_data = response.json()
        assert response_data["name"] == "cover.jpg"
        assert response_data["path"] == "tmp/images/uploaded_image.jpg"
        assert response_data["size"] == 256
        assert response_data["preview_url"] == "https://storage/preview"

    def test_upload_audio__ok_without_cover(
        self,
        client: TestClient[PodcastApp],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        metadata = namedtuple("AudioMetadata", ["duration", "title"])(duration=42, title="Track")
        storage = SimpleNamespace(
            upload_file=AsyncMock(side_effect=["tmp/audio/uploaded.mp3", "images/cover.jpg"]),
            get_presigned_url=AsyncMock(return_value="https://storage/cover"),
        )
        cover = UploadedImageData(
            name="cover.jpg",
            path="images/cover.jpg",
            hash="cover-hash",
            size=64,
            preview_url="https://storage/cover",
        )
        monkeypatch.setattr("src.modules.api.media.StorageS3", lambda: storage)
        monkeypatch.setattr(
            "src.modules.api.media.save_uploaded_file",
            AsyncMock(return_value=Path("/tmp/uploaded.mp3")),
        )
        monkeypatch.setattr("src.modules.api.media.get_file_size", Mock(return_value=512))
        monkeypatch.setattr("src.modules.api.media.ffmpeg_utils.audio_metadata", Mock(return_value=metadata))
        monkeypatch.setattr(
            "src.modules.api.media.ffmpeg_utils.audio_cover",
            Mock(return_value=SimpleNamespace(path=Path("/tmp/cover.jpg"), hash=cover.hash, size=cover.size)),
        )

        response = client.post(
            "/api/media/upload/audio/",
            files={"file": ("episode.mp3", b"audio-content", "audio/mpeg")},
        )

        assert response.status_code in {200, 201}, response.text
        response_data = response.json()
        assert response_data["name"] == "episode.mp3"
        assert response_data["path"] == "tmp/audio/uploaded.mp3"
        assert response_data["size"] == 512
        assert response_data["meta"] == {"duration": 42, "title": "Track"}
        assert response_data["cover"]["hash"] == cover.hash

    def test_upload_audio__cover_upload_failure_returns_without_cover(
        self,
        client: TestClient[PodcastApp],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        metadata = namedtuple("AudioMetadata", ["duration", "title"])(duration=42, title="Track")
        storage = SimpleNamespace(upload_file=AsyncMock(side_effect=["tmp/audio/uploaded.mp3", ""]))
        monkeypatch.setattr("src.modules.api.media.StorageS3", lambda: storage)
        monkeypatch.setattr(
            "src.modules.api.media.save_uploaded_file",
            AsyncMock(return_value=Path("/tmp/uploaded.mp3")),
        )
        monkeypatch.setattr("src.modules.api.media.get_file_size", Mock(return_value=512))
        monkeypatch.setattr("src.modules.api.media.ffmpeg_utils.audio_metadata", Mock(return_value=metadata))
        monkeypatch.setattr(
            "src.modules.api.media.ffmpeg_utils.audio_cover",
            Mock(return_value=SimpleNamespace(path=Path("/tmp/cover.jpg"), hash="cover-hash", size=64)),
        )

        response = client.post(
            "/api/media/upload/audio/",
            files={"file": ("episode.mp3", b"audio-content", "audio/mpeg")},
        )

        assert response.status_code in {200, 201}, response.text
        assert response.json()["cover"] is None

    async def test_upload_audio_cover__without_embedded_cover(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("src.modules.api.media.ffmpeg_utils.audio_cover", Mock(return_value=None))

        assert await _upload_audio_cover(Path("/tmp/uploaded.mp3")) is None
