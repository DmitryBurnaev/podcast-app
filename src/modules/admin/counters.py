import dataclasses

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.db.repositories import PodcastRepository


@dataclasses.dataclass(frozen=True)
class DashboardCounts:
    total_podcasts: int
    total_episodes: int


class AdminCounter:
    """Admin's dashboard aggregations"""

    @classmethod
    async def get_stat(cls, session: AsyncSession) -> DashboardCounts:
        """Get vendors counts"""
        podcast_repository = PodcastRepository(session)
        # active_vendors = await podcast_repository.group_by_active()
        return DashboardCounts(
            total_podcasts=123,
            total_episodes=455,
        )
