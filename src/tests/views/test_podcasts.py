from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from litestar.exceptions import NotFoundException
from litestar.response import File

from src.modules.db.models.media import MediaType
from src.modules.views.podcasts import (
    PodcastCoverController,
    PodcastsController,
    PodcastsDetailsController,
)
from src.tests.factories import make_file, make_podcast
from src.tests.mocks import MockUOW


def _controller(controller_type: type) -> object:
    return controller_type.__new__(controller_type)


async def _get_podcasts(controller: PodcastsController, request: SimpleNamespace) -> object:
    return await PodcastsController.get.fn(controller, request)


async def _get_podcast_detail(
    controller: PodcastsDetailsController,
    podcast_id: int,
    request: SimpleNamespace,
) -> object:
    return await PodcastsDetailsController.get_detail.fn(controller, podcast_id, request)


async def _get_podcast_cover(controller: PodcastCoverController, podcast_id: int) -> File:
    return await PodcastCoverController.get_cover.fn(controller, podcast_id)


class TestPodcastsController:
    async def test_get__passes_context_to_template(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        request = SimpleNamespace()
        user = SimpleNamespace(id=7)
        podcasts = [make_podcast()]
        template = object()
        repository = SimpleNamespace(all_with_aggregations=AsyncMock(return_value=(podcasts, 1)))
        controller = _controller(PodcastsController)
        controller.get_response_template = Mock(return_value=template)
        monkeypatch.setattr("src.modules.views.podcasts.get_current_user", Mock(return_value=user))
        monkeypatch.setattr("src.modules.views.podcasts.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.views.podcasts.PodcastRepository",
            Mock(return_value=repository),
        )

        result = await _get_podcasts(controller, request)

        assert result is template
        repository.all_with_aggregations.assert_awaited_once_with(owner_id=7)
        controller.get_response_template.assert_called_once_with(
            template_name="podcasts.html",
            context={
                "podcasts": podcasts,
                "current": "podcasts",
                "title": "Podcasts",
            },
            request=request,
        )


class TestPodcastsDetailsController:
    async def test_get_detail__passes_context_to_template(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        request = SimpleNamespace()
        podcast = make_podcast(name="A" * 40)
        episodes = [object()]
        podcast_stats = object()
        template = object()
        podcast_repository = SimpleNamespace(first=AsyncMock(return_value=podcast))
        episode_repository = SimpleNamespace(all_paginated=AsyncMock(return_value=(episodes, 1)))
        statistic_service = SimpleNamespace(
            get_podcast_statistics=AsyncMock(return_value=podcast_stats)
        )
        controller = _controller(PodcastsDetailsController)
        controller.get_response_template = Mock(return_value=template)
        monkeypatch.setattr("src.modules.views.podcasts.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.views.podcasts.PodcastRepository",
            Mock(return_value=podcast_repository),
        )
        monkeypatch.setattr(
            "src.modules.views.podcasts.EpisodeRepository",
            Mock(return_value=episode_repository),
        )
        monkeypatch.setattr(
            "src.modules.views.podcasts.StatisticService",
            Mock(return_value=statistic_service),
        )

        result = await _get_podcast_detail(controller, 1, request)

        assert result is template
        podcast_repository.first.assert_awaited_once_with(1)
        episode_repository.all_paginated.assert_awaited_once_with(podcast_id=1, limit=10)
        statistic_service.get_podcast_statistics.assert_awaited_once_with(1)
        controller.get_response_template.assert_called_once_with(
            template_name="podcasts_detail.html",
            context={
                "podcast": podcast,
                "episodes": episodes,
                "podcast_stats": podcast_stats,
                "current": "podcasts",
                "title": "A" * 32 + "...",
            },
            request=request,
        )

    async def test_get_detail__podcast_missing__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        podcast_repository = SimpleNamespace(first=AsyncMock(return_value=None))
        monkeypatch.setattr("src.modules.views.podcasts.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.views.podcasts.PodcastRepository",
            Mock(return_value=podcast_repository),
        )

        with pytest.raises(NotFoundException, match="Podcast with id 1 not found"):
            await _get_podcast_detail(
                _controller(PodcastsDetailsController),
                1,
                SimpleNamespace(),
            )


class TestPodcastCoverController:
    async def test_get_cover__returns_cached_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        image = make_file(type=MediaType.IMAGE, path="images/cover.jpg")
        podcast = make_podcast()
        podcast.image_id = image.id
        podcast.image = image
        cached_path = tmp_path / "cover.jpg"
        repository = SimpleNamespace(first=AsyncMock(return_value=podcast))
        cover_service = SimpleNamespace(get_or_download=AsyncMock(return_value=cached_path))
        monkeypatch.setattr("src.modules.views.podcasts.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.views.podcasts.PodcastRepository",
            Mock(return_value=repository),
        )
        monkeypatch.setattr(
            "src.modules.views.podcasts.CoverService",
            Mock(return_value=cover_service),
        )

        result = await _get_podcast_cover(_controller(PodcastCoverController), 1)

        assert isinstance(result, File)
        assert result.file_path == cached_path
        assert result.filename == "cover.jpg"
        assert result.media_type == "image/jpeg"
        repository.first.assert_awaited_once_with(1)
        cover_service.get_or_download.assert_awaited_once_with(
            image,
            "podcasts",
            "podcast_cover",
        )

    @pytest.mark.parametrize(
        "podcast",
        [
            None,
            make_podcast(),
        ],
    )
    async def test_get_cover__podcast_or_image_missing__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        podcast: object | None,
    ) -> None:
        repository = SimpleNamespace(first=AsyncMock(return_value=podcast))
        monkeypatch.setattr("src.modules.views.podcasts.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.views.podcasts.PodcastRepository",
            Mock(return_value=repository),
        )

        with pytest.raises(NotFoundException):
            await _get_podcast_cover(_controller(PodcastCoverController), 1)

    def test_build_cover_file_response__unknown_extension__uses_octet_stream(
        self,
    ) -> None:
        file_obj = make_file(type=MediaType.IMAGE, path="images/cover.unknown")

        result = PodcastCoverController._build_cover_file_response(
            Path("/tmp/cover.unknown"),
            file_obj,
        )

        assert result.filename == "cover.unknown"
        assert result.media_type == "application/octet-stream"
