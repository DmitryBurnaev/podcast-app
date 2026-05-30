from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.modules.views.index import IndexController
from src.tests.mocks import MockUOW


def _controller() -> IndexController:
    return IndexController.__new__(IndexController)


async def _get(controller: IndexController, request: SimpleNamespace) -> object:
    return await IndexController.get.fn(controller, request)


class TestIndexController:
    async def test_get__passes_dashboard_context_to_template(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        request = SimpleNamespace()
        template = object()
        podcasts = [object()]
        recent_episodes = [object()]
        stats = object()
        podcast_repository = SimpleNamespace(
            all_with_aggregations=AsyncMock(return_value=(podcasts, 1))
        )
        episode_repository = SimpleNamespace(
            all_paginated=AsyncMock(return_value=(recent_episodes, 1))
        )
        statistic_service = SimpleNamespace(get_app_statistics=AsyncMock(return_value=stats))
        controller = _controller()
        controller.get_response_template = Mock(return_value=template)
        monkeypatch.setattr("src.modules.views.index.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.views.index.PodcastRepository",
            Mock(return_value=podcast_repository),
        )
        monkeypatch.setattr(
            "src.modules.views.index.EpisodeRepository",
            Mock(return_value=episode_repository),
        )
        monkeypatch.setattr(
            "src.modules.views.index.StatisticService",
            Mock(return_value=statistic_service),
        )

        result = await _get(controller, request)

        assert result is template
        podcast_repository.all_with_aggregations.assert_awaited_once_with(owner_id=1)
        episode_repository.all_paginated.assert_awaited_once_with(owner_id=1, limit=7)
        statistic_service.get_app_statistics.assert_awaited_once_with(owner_id=1)
        controller.get_response_template.assert_called_once_with(
            template_name="index.html",
            context={
                "podcasts": podcasts,
                "recent_episodes": recent_episodes,
                "current": "home",
                "stats": stats,
            },
            request=request,
        )
