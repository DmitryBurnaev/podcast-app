import dataclasses

from sqlalchemy.ext.asyncio import AsyncSession


@dataclasses.dataclass(frozen=True)
class DashboardCounts:
    total_podcasts: int
    total_episodes: int


class AdminCounter:
    """Admin's dashboard aggregations"""

    @classmethod
    async def get_stat(cls, session: AsyncSession) -> DashboardCounts:
        """Get vendors counts"""
        del session
        return DashboardCounts(
            total_podcasts=123,
            total_episodes=455,
        )
