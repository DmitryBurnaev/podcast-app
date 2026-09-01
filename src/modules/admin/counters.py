import dataclasses

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import format_file_size
from src.modules.db.models import File
from src.modules.db.repositories import PodcastRepository, EpisodeRepository


@dataclasses.dataclass(frozen=True)
class DashboardCounts:
    """Aggregate record counts displayed on the admin dashboard."""

    total_podcasts: int
    total_episodes: int
    total_file_size: int
    total_file_size_label: str


class AdminCounter:
    """Admin's dashboard aggregations"""

    @classmethod
    async def get_stat(cls, session: AsyncSession) -> DashboardCounts:
        """Get vendors counts"""
        total_podcasts = await PodcastRepository(session).get_total_count()
        total_episodes = await EpisodeRepository(session).get_total_count()
        total_file_size = await session.scalar(select(func.coalesce(func.sum(File.size), 0))) or 0
        return DashboardCounts(
            total_podcasts=total_podcasts,
            total_episodes=total_episodes,
            total_file_size=total_file_size,
            total_file_size_label=format_file_size(total_file_size),
        )
