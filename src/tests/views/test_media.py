from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from litestar.exceptions import NotFoundException
from litestar.response import Redirect
from litestar.status_codes import HTTP_307_TEMPORARY_REDIRECT

from src.exceptions import NotSupportedError
from src.modules.db.models.media import MediaType
from src.modules.views.media import MediaByTokenController
from src.tests.mocks import MockUOW


def _mock_media_file(
    *,
    media_type: MediaType = MediaType.AUDIO,
    available: bool = True,
    presigned_url: str = "https://storage/presigned",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=1,
        type=media_type,
        available=available,
        fetch_presigned_url=AsyncMock(return_value=presigned_url),
    )


def _mock_file_repository(
    monkeypatch: pytest.MonkeyPatch,
    media_file: object | None,
) -> SimpleNamespace:
    repository = SimpleNamespace(first_by_access_token=AsyncMock(return_value=media_file))
    monkeypatch.setattr("src.modules.views.media.SASessionUOW", lambda: MockUOW())
    monkeypatch.setattr("src.modules.views.media.FileRepository", Mock(return_value=repository))
    return repository


def _controller() -> MediaByTokenController:
    return MediaByTokenController.__new__(MediaByTokenController)


async def _get_private_media(controller: MediaByTokenController, access_token: str) -> Redirect:
    return await MediaByTokenController.get_private_media.fn(controller, access_token)


async def _get_rss_media(controller: MediaByTokenController, access_token: str) -> Redirect:
    return await MediaByTokenController.get_rss_media.fn(controller, access_token)


class TestMediaByTokenController:
    @pytest.mark.parametrize("media_type", [MediaType.AUDIO, MediaType.IMAGE])
    async def test_get_private_media__redirects_allowed_media(
        self,
        monkeypatch: pytest.MonkeyPatch,
        media_type: MediaType,
    ) -> None:
        media_file = _mock_media_file(media_type=media_type)
        repository = _mock_file_repository(monkeypatch, media_file)
        controller = _controller()

        result = await _get_private_media(controller, "token")

        assert isinstance(result, Redirect)
        assert result.status_code == HTTP_307_TEMPORARY_REDIRECT
        assert result.url == "https://storage/presigned"
        repository.first_by_access_token.assert_awaited_once_with("token")
        media_file.fetch_presigned_url.assert_awaited_once()

    async def test_get_rss_media__redirects_allowed_media(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        media_file = _mock_media_file(media_type=MediaType.RSS)
        repository = _mock_file_repository(monkeypatch, media_file)
        controller = _controller()

        result = await _get_rss_media(controller, "token")

        assert isinstance(result, Redirect)
        assert result.status_code == HTTP_307_TEMPORARY_REDIRECT
        assert result.url == "https://storage/presigned"
        repository.first_by_access_token.assert_awaited_once_with("token")
        media_file.fetch_presigned_url.assert_awaited_once()

    @pytest.mark.parametrize("access_token", ["", "x" * 129])
    async def test_redirect_presigned__invalid_token__fail(
        self,
        access_token: str,
    ) -> None:
        controller = _controller()

        with pytest.raises(NotFoundException, match="Media not found"):
            await _get_private_media(controller, access_token)

    @pytest.mark.parametrize(
        ("media_file", "allowed_types"),
        [
            (None, (MediaType.AUDIO, MediaType.IMAGE)),
            (_mock_media_file(available=False), (MediaType.AUDIO, MediaType.IMAGE)),
            (_mock_media_file(media_type=MediaType.RSS), (MediaType.AUDIO, MediaType.IMAGE)),
            (_mock_media_file(media_type=MediaType.AUDIO), (MediaType.RSS,)),
        ],
    )
    async def test_redirect_presigned__unavailable_media__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        media_file: object | None,
        allowed_types: tuple[MediaType, ...],
    ) -> None:
        repository = _mock_file_repository(monkeypatch, media_file)
        controller = _controller()

        with pytest.raises(NotFoundException, match="Media not found"):
            await controller._redirect_presigned("token", allowed_types=allowed_types)

        repository.first_by_access_token.assert_awaited_once_with("token")

    async def test_redirect_presigned__presign_error__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        media_file = _mock_media_file()
        media_file.fetch_presigned_url.side_effect = NotSupportedError("no path")
        _mock_file_repository(monkeypatch, media_file)
        controller = _controller()

        with pytest.raises(NotFoundException, match="Media not found"):
            await _get_private_media(controller, "token")

        media_file.fetch_presigned_url.assert_awaited_once()
