from datetime import UTC, datetime
from unittest.mock import MagicMock, create_autospec

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.common.types import OwnerScope
from src.modules.db.repositories import (
    EpisodeRepository,
    EpisodesStatData,
    PodcastRepository,
)
from src.modules.db.services import SASessionUOW
from src.modules.services.statistic import StatisticService
from src.tests.factories import make_episode, make_podcast


@pytest.fixture
def statistic_service(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[StatisticService, MagicMock, MagicMock]:
    session = create_autospec(AsyncSession, instance=True, spec_set=True)
    podcasts = create_autospec(PodcastRepository, instance=True, spec_set=True)
    episodes = create_autospec(EpisodeRepository, instance=True, spec_set=True)
    podcasts.all.return_value = []
    episodes.get_aggregated.return_value = EpisodesStatData()
    episodes.all_in_progress.return_value = []
    monkeypatch.setattr(
        "src.modules.services.statistic.PodcastRepository", lambda **_: podcasts
    )
    monkeypatch.setattr(
        "src.modules.services.statistic.EpisodeRepository", lambda **_: episodes
    )
    return (
        StatisticService(SASessionUOW(session=session), OwnerScope(user_id=7)),
        podcasts,
        episodes,
    )


class TestStatisticService:
    async def test_get_app_statistics_with_last_episode_and_downloads(
        self, statistic_service: tuple[StatisticService, MagicMock, MagicMock]
    ) -> None:
        service, podcasts, episodes = statistic_service
        last_published_at = datetime(2026, 5, 16, 12, 30, tzinfo=UTC)
        podcasts.all.return_value = [make_podcast(), make_podcast(id=2)]
        episodes.get_aggregated.return_value = EpisodesStatData(
            total_count=3,
            total_duration=120,
            total_file_size=2048,
            last_created_at=datetime(2026, 5, 15, tzinfo=UTC),
            last_published_at=last_published_at,
        )
        episodes.all_in_progress.return_value = [make_episode()]
        result = await service.get_app_statistics()
        assert result.total_episodes == 3
        assert result.total_podcasts == 2
        assert result.total_duration == 120
        assert result.total_size == 2048
        assert result.downloading_count == 1
        assert result.recent_activity.text == "Last episode: 16 May, 2026 12:30"
        podcasts.all.assert_awaited_once_with()
        episodes.get_aggregated.assert_awaited_once_with()

    async def test_get_app_statistics_without_last_episode(
        self, statistic_service: tuple[StatisticService, MagicMock, MagicMock]
    ) -> None:
        service, _, _ = statistic_service
        result = await service.get_app_statistics()
        assert result.recent_activity.text == "No episodes yet"
        assert result.recent_activity.time is None
        assert result.downloading_count == 0

    async def test_get_podcast_statistics(
        self, statistic_service: tuple[StatisticService, MagicMock, MagicMock]
    ) -> None:
        service, _, episodes = statistic_service
        episodes.get_aggregated.return_value = EpisodesStatData(
            total_count=4, total_duration=99
        )
        result = await service.get_podcast_statistics(10)
        assert result.episodes_count == 4
        assert result.total_duration == 99
        assert result.total_size == 0
        episodes.get_aggregated.assert_awaited_once_with(podcast_id=10)
