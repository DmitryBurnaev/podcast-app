import dataclasses

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.db.repositories import PodcastRepository, EpisodeRepository


@dataclasses.dataclass(frozen=True)
class DashboardCounts:
    total_podcasts: int
    total_episodes: int


class AdminCounter:
    """Admin's dashboard aggregations"""

    @classmethod
    async def get_stat(cls, session: AsyncSession) -> DashboardCounts:
        """Get vendors counts"""
        total_podcasts = await PodcastRepository(session).get_total_count()
        total_episodes = await EpisodeRepository(session).get_total_count()
        return DashboardCounts(
            total_podcasts=total_podcasts,
            total_episodes=total_episodes,
        )
