from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from litestar.exceptions import NotFoundException

from src.modules.db.models.media import MediaType
from src.modules.services.cover import CoverService
from src.tests.factories import make_file


class TestCoverServiceCache:
    def test_cache_key_and_ext__uses_path_first(self) -> None:
        file_obj = make_file(type=MediaType.IMAGE, path="images/cover.PNG")
        file_obj.source_url = "https://example.com/fallback.jpg"

        cache_key, ext = CoverService._cache_key_and_ext(file_obj)

        assert cache_key == "images/cover.PNG"
        assert ext == "png"

    def test_cache_key_and_ext__uses_source_url(self) -> None:
        file_obj = make_file(type=MediaType.IMAGE, path="")
        file_obj.source_url = "https://example.com/assets/cover.webp?size=1"

        cache_key, ext = CoverService._cache_key_and_ext(file_obj)

        assert cache_key == file_obj.source_url
        assert ext == "webp"

    def test_cache_key_and_ext__missing_location__fail(self) -> None:
        file_obj = make_file(type=MediaType.IMAGE, path="")
        file_obj.source_url = ""

        with pytest.raises(NotFoundException):
            CoverService._cache_key_and_ext(file_obj)

    def test_cache_filename__normalizes_extension(self) -> None:
        filename = CoverService._cache_filename("images/cover.png", "episode", ".PNG")

        assert filename.startswith("episode_")
        assert filename.endswith(".png")

    async def test_get_or_download__cached__returns_existing_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        settings = SimpleNamespace(media_cache_dir=tmp_path)
        monkeypatch.setattr("src.modules.services.cover.get_app_settings", lambda: settings)
        file_obj = make_file(type=MediaType.IMAGE, path="images/cover.jpg")
        cache_key, ext = CoverService._cache_key_and_ext(file_obj)
        cached_path = tmp_path / "episodes" / CoverService._cache_filename(cache_key, "cover", ext)
        cached_path.parent.mkdir()
        cached_path.write_bytes(b"cached")
        download_from_s3 = AsyncMock()
        monkeypatch.setattr(CoverService, "_download_from_s3", download_from_s3)

        result = await CoverService().get_or_download(file_obj, "episodes", "cover")

        assert result == cached_path
        download_from_s3.assert_not_awaited()

    async def test_get_or_download__public_url__downloads_url(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        settings = SimpleNamespace(media_cache_dir=tmp_path)
        monkeypatch.setattr("src.modules.services.cover.get_app_settings", lambda: settings)
        file_obj = make_file(type=MediaType.IMAGE, path="")
        file_obj.public = True
        file_obj.source_url = "https://example.com/cover.jpg"
        download_from_url = AsyncMock()
        monkeypatch.setattr(CoverService, "_download_from_url", download_from_url)

        result = await CoverService().get_or_download(file_obj, "episodes", "cover")

        assert result.parent == tmp_path / "episodes"
        download_from_url.assert_awaited_once_with(
            file_obj.source_url,
            result,
            file_obj.type.value,
        )

    async def test_download_from_s3__missing__fail(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        storage = SimpleNamespace(download_file=AsyncMock(return_value=None))
        monkeypatch.setattr("src.modules.services.cover.StorageS3", Mock(return_value=storage))

        with pytest.raises(NotFoundException):
            await CoverService._download_from_s3("images/missing.jpg", tmp_path / "cover.jpg", "image")

    async def test_download_from_url__ok(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        response = SimpleNamespace(status_code=200, content=b"image")
        client = _FakeAsyncClient(response=response)
        monkeypatch.setattr("src.modules.services.cover.httpx.AsyncClient", Mock(return_value=client))
        dst_path = tmp_path / "cover.jpg"

        await CoverService._download_from_url("https://example.com/cover.jpg", dst_path, "image")

        assert dst_path.read_bytes() == b"image"
        client.get.assert_awaited_once_with("https://example.com/cover.jpg", timeout=30.0)

    async def test_download_from_url__not_found__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        client = _FakeAsyncClient(response=SimpleNamespace(status_code=404, content=b""))
        monkeypatch.setattr("src.modules.services.cover.httpx.AsyncClient", Mock(return_value=client))

        with pytest.raises(NotFoundException):
            await CoverService._download_from_url("https://example.com/cover.jpg", tmp_path / "cover.jpg", "image")


class _FakeAsyncClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.get = AsyncMock(return_value=response)

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None
