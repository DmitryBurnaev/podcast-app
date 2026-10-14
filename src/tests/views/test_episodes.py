from pathlib import Path
from unittest.mock import create_autospec
from urllib.parse import parse_qsl

import pytest
from jinja2 import Environment, FileSystemLoader
from litestar import Request
from litestar.exceptions import HTTPException, NotFoundException
from litestar.testing import RequestFactory
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.common.types import OwnerScope
from src.modules.db.repositories import EpisodeRepository, PodcastRepository
from src.modules.db.services import SASessionUOW
from src.modules.views import episodes as episode_views
from src.modules.views.base import BaseViewController
from src.modules.views.episodes import (
    EpisodeDetailsController,
    EpisodesController,
    prepare_episode_filters,
)
from src.settings.app import AppSettings
from src.tests.factories import make_episode, make_podcast, make_user


def request_for(query: str = "") -> Request:
    parameters: dict[str, str | list[str]] = dict(parse_qsl(query))
    return RequestFactory().get("/episodes/", query_params=parameters, user=make_user())


class TestEpisodeFilters:
    def test_displayed_filters_are_preserved_and_mb_are_converted(self) -> None:
        displayed, prepared = prepare_episode_filters(
            {
                "status": "pending,error",
                "search": " text ",
                "podcast": "Podcast",
                "size_min": "0.5",
                "size_max": "2",
                "fragment": "1",
            }
        )
        assert displayed == {
            "status": "pending,error",
            "search": "text",
            "podcast": "Podcast",
            "size_min": "0.5",
            "size_max": "2",
        }
        assert prepared == {
            "statuses": "NEW,ERROR",
            "search": "text",
            "podcast__name": "Podcast",
            "audio__size__gte": 524288,
            "audio__size__lte": 2097152,
        }

    @pytest.mark.parametrize(
        "query",
        [
            {"status": "unknown"},
            {"size_min": "-1"},
            {"size_max": "NaN"},
            {"size_min": "Infinity"},
            {"size_max": "abc"},
            {"size_min": "2", "size_max": "1"},
        ],
    )
    def test_invalid_filters_return_bad_request(self, query: dict[str, str]) -> None:
        with pytest.raises(HTTPException) as error:
            prepare_episode_filters(query)
        assert error.value.status_code == 400


class TestEpisodesController:
    async def test_list_passes_display_filters_and_only_filters_episodes(
        self, app_settings: AppSettings, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        uow = create_autospec(SASessionUOW, instance=True, spec_set=True)
        uow.session = create_autospec(AsyncSession, instance=True, spec_set=True)
        uow.__aenter__.return_value = uow
        uow.__aexit__.return_value = None
        episodes = [make_episode()]
        podcasts = [make_podcast()]
        episode_repository = create_autospec(EpisodeRepository, instance=True, spec_set=True)
        podcast_repository = create_autospec(PodcastRepository, instance=True, spec_set=True)
        episode_repository.all_paginated.return_value = (episodes, 1)
        podcast_repository.all_paginated.return_value = (podcasts, 1)
        monkeypatch.setattr(
            episode_views, "SASessionUOW", create_autospec(SASessionUOW, return_value=uow)
        )
        monkeypatch.setattr(
            episode_views,
            "EpisodeRepository",
            create_autospec(EpisodeRepository, return_value=episode_repository),
        )
        monkeypatch.setattr(
            episode_views,
            "PodcastRepository",
            create_autospec(PodcastRepository, return_value=podcast_repository),
        )
        request = request_for("status=pending&size_min=0.5&fragment=1")
        controller = EpisodesController.__new__(EpisodesController)
        result = await EpisodesController.get.fn(controller, request, app_settings, OwnerScope(1))
        assert result.template_name == "fragments/episodes.html"
        assert result.context == BaseViewController.get_base_context(request) | {
            "episodes": episodes,
            "podcasts": podcasts,
            "filters": {"status": "pending", "size_min": "0.5"},
            "current": "episodes",
            "title": "episodes",
            "page_shell": "fragments/base.html",
        }
        podcast_repository.all_paginated.assert_awaited_once_with(
            limit=app_settings.default_pagination_limit
        )
        episode_repository.all_paginated.assert_awaited_once_with(
            limit=app_settings.default_pagination_limit, statuses="NEW", audio__size__gte=524288
        )


class TestEpisodeDetailsController:
    @pytest.mark.parametrize("missing", [False, True])
    async def test_details_pass_owned_episode_context_or_not_found(
        self, monkeypatch: pytest.MonkeyPatch, missing: bool
    ) -> None:
        episode = make_episode()
        episode.podcast = make_podcast()
        uow = create_autospec(SASessionUOW, instance=True, spec_set=True)
        uow.session = create_autospec(AsyncSession, instance=True, spec_set=True)
        uow.__aenter__.return_value = uow
        uow.__aexit__.return_value = None
        repository = create_autospec(EpisodeRepository, instance=True, spec_set=True)
        repository.first.return_value = None if missing else episode
        monkeypatch.setattr(
            episode_views, "SASessionUOW", create_autospec(SASessionUOW, return_value=uow)
        )
        monkeypatch.setattr(
            episode_views,
            "EpisodeRepository",
            create_autospec(EpisodeRepository, return_value=repository),
        )
        request = request_for("fragment=1")
        controller = EpisodeDetailsController.__new__(EpisodeDetailsController)
        if missing:
            with pytest.raises(NotFoundException):
                await EpisodeDetailsController.get_detail.fn(
                    controller, episode.id, request, OwnerScope(1)
                )
            return
        result = await EpisodeDetailsController.get_detail.fn(
            controller, episode.id, request, OwnerScope(1)
        )
        assert result.template_name == "fragments/episodes_detail.html"
        assert result.context == BaseViewController.get_base_context(request) | {
            "episode": episode,
            "podcast": episode.podcast,
            "audio_url": None,
            "source_url": episode.watch_url,
            "episode_size": 0,
            "current": "episodes",
            "title": episode.title,
            "page_shell": "fragments/base.html",
        }


class TestFragmentContext:
    @pytest.mark.parametrize(
        "template",
        [
            "index.html",
            "podcasts.html",
            "podcasts_detail.html",
            "episodes.html",
            "episodes_detail.html",
        ],
    )
    def test_fragment_selects_template_and_preserves_context(self, template: str) -> None:
        request = request_for("fragment=1")
        controller = BaseViewController.__new__(BaseViewController)
        response = controller.get_response_template(template, {"title": "Original"}, request)
        assert response.template_name == f"fragments/{template}"
        assert response.context == BaseViewController.get_base_context(request) | {
            "title": "Original",
            "page_shell": "fragments/base.html",
        }


class TestFragmentTemplateCompilation:
    def test_fragment_inheritance_and_region_blocks_compile(self) -> None:
        """Compile only; view assertions do not render or snapshot HTML."""
        directory = Path(__file__).resolve().parents[2] / "templates"
        engine = Environment(loader=FileSystemLoader(directory))
        regions = {
            "index.html": {"refresh_overview"},
            "podcasts.html": {"refresh_podcasts"},
            "podcasts_detail.html": {"refresh_podcast_summary", "refresh_episodes"},
            "episodes.html": {"refresh_episodes"},
            "episodes_detail.html": {
                "refresh_episode_title",
                "refresh_episode_state",
                "refresh_episode_metadata",
            },
        }
        for name, required_blocks in regions.items():
            assert required_blocks <= engine.get_template(name).blocks.keys()
            assert "fragment_content" in engine.get_template(f"fragments/{name}").blocks
        engine.get_template("fragments/base.html")
