"""Statistics against the isolated PostgreSQL used by functional API tests."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.common.constants import SourceType
from src.modules.common.types import OwnerScope
from src.modules.db.models import Episode, File, Podcast, User
from src.modules.db.models.media import MediaType
from src.modules.db.models.podcasts import EpisodeStatus
from src.modules.db.repositories import EpisodeRepository
from src.modules.db.services import SASessionUOW
from src.modules.services.statistic import StatisticService


class TestOwnedStatistics:
    @pytest.mark.usefixtures("use_functional_session_factory")
    async def test_empty_statistics_return_zero_totals(self, db_user: User) -> None:
        async with SASessionUOW() as uow:
            statistics = await StatisticService(
                uow, OwnerScope(db_user.id)
            ).get_app_statistics()
        assert statistics.total_episodes == 0
        assert statistics.total_size == 0
        assert statistics.total_duration == 0
        assert statistics.downloading_count == 0

    @pytest.mark.usefixtures("use_functional_session_factory")
    async def test_statistics_include_pending_audio_and_exclude_foreign_episodes(
        self, functional_session: AsyncSession, db_user: User, db_podcast: Podcast
    ) -> None:
        other = User(
            email="statistics-other@podcast.example", password="hash", is_active=True
        )
        functional_session.add(other)
        await functional_session.flush()
        foreign = Podcast(
            name="Foreign",
            description="",
            publish_id=Podcast.generate_publish_id(),
            owner_id=other.id,
        )
        audio = File(
            type=MediaType.AUDIO,
            owner_id=db_user.id,
            path="own.wav",
            size=4096,
            available=True,
            access_token=File.generate_token(),
        )
        functional_session.add_all([foreign, audio])
        await functional_session.flush()
        own = Episode(
            title="Own",
            source_id="own-stat",
            source_type=SourceType.YOUTUBE,
            owner_id=db_user.id,
            podcast_id=db_podcast.id,
            audio_id=audio.id,
            status=EpisodeStatus.PUBLISHED,
            length=60,
        )
        pending = Episode(
            title="Pending",
            source_id="pending-stat",
            source_type=SourceType.YOUTUBE,
            owner_id=db_user.id,
            podcast_id=db_podcast.id,
            status=EpisodeStatus.DOWNLOADING,
            length=30,
        )
        foreign_episode = Episode(
            title="Foreign",
            source_id="foreign-stat",
            source_type=SourceType.YOUTUBE,
            owner_id=other.id,
            podcast_id=foreign.id,
            status=EpisodeStatus.DOWNLOADING,
            length=100,
        )
        functional_session.add_all([own, pending, foreign_episode])
        await functional_session.commit()
        async with SASessionUOW() as uow:
            service = StatisticService(uow, OwnerScope(db_user.id))
            statistics = await service.get_app_statistics()
            podcast_statistics = await service.get_podcast_statistics(db_podcast.id)
        assert statistics.total_episodes == 2
        assert statistics.total_podcasts == 1
        assert statistics.total_duration == 90
        assert statistics.total_size == 4096
        assert statistics.downloading_count == 1
        assert podcast_statistics.episodes_count == 2
        assert podcast_statistics.total_duration == 90
        assert podcast_statistics.total_size == 4096

        await EpisodeRepository(functional_session, OwnerScope(db_user.id)).delete(
            pending
        )
        await functional_session.commit()
        async with SASessionUOW() as uow:
            statistics = await StatisticService(
                uow, OwnerScope(db_user.id)
            ).get_app_statistics()
        assert statistics.total_episodes == 1
        assert statistics.total_size == 4096
        assert statistics.downloading_count == 0
