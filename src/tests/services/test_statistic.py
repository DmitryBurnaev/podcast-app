from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.modules.db.repositories import EpisodesStatData
from src.modules.services.statistic import StatisticService
from src.tests.factories import make_podcast
from src.tests.mocks import MockSession


class TestStatisticService:
    async def test_get_app_statistics__with_last_episode(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        last_published_at = datetime(2026, 5, 16, 12, 30, tzinfo=timezone.utc)
        podcast_repository = SimpleNamespace(
            all=AsyncMock(return_value=[make_podcast(), make_podcast(id=2)])
        )
        episode_repository = SimpleNamespace(
            get_aggregated=AsyncMock(
                return_value=EpisodesStatData(
                    total_count=3,
                    total_duration=120,
                    total_file_size=2048,
                    last_created_at=datetime(2026, 5, 15, tzinfo=timezone.utc),
                    last_published_at=last_published_at,
                )
            )
        )
        monkeypatch.setattr(
            "src.modules.services.statistic.PodcastRepository",
            Mock(return_value=podcast_repository),
        )
        monkeypatch.setattr(
            "src.modules.services.statistic.EpisodeRepository",
            Mock(return_value=episode_repository),
        )

        result = await StatisticService(SimpleNamespace(session=MockSession())).get_app_statistics(
            owner_id=7
        )

        assert result.total_episodes == 3
        assert result.total_podcasts == 2
        assert result.total_duration == 120
        assert result.total_size == 2048
        assert result.recent_activity.text == "Last episode: 16 May, 2026 12:30"
        podcast_repository.all.assert_awaited_once_with(owner_id=7)
        episode_repository.get_aggregated.assert_awaited_once_with()

    async def test_get_app_statistics__without_last_episode(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "src.modules.services.statistic.PodcastRepository",
            Mock(return_value=SimpleNamespace(all=AsyncMock(return_value=[]))),
        )
        monkeypatch.setattr(
            "src.modules.services.statistic.EpisodeRepository",
            Mock(
                return_value=SimpleNamespace(
                    get_aggregated=AsyncMock(return_value=EpisodesStatData())
                )
            ),
        )

        result = await StatisticService(SimpleNamespace(session=MockSession())).get_app_statistics()

        assert result.recent_activity.text == "No episodes yet"
        assert result.recent_activity.time is None

    async def test_get_podcast_statistics__ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        episode_repository = SimpleNamespace(
            get_aggregated=AsyncMock(
                return_value=EpisodesStatData(
                    total_count=4, total_duration=99, total_file_size=None
                )
            )
        )
        monkeypatch.setattr(
            "src.modules.services.statistic.EpisodeRepository",
            Mock(return_value=episode_repository),
        )

        result = await StatisticService(
            SimpleNamespace(session=MockSession())
        ).get_podcast_statistics(10)

        assert result.episodes_count == 4
        assert result.total_duration == 99
        assert result.total_size == 0
        episode_repository.get_aggregated.assert_awaited_once_with(podcast_id=10)
