"""Statistics service: app-wide and per-podcast stats via Pydantic models."""

from datetime import datetime

from pydantic import BaseModel, computed_field

from src.constants import format_file_size
from src.modules.db.repositories import EpisodeRepository, PodcastRepository
from src.modules.db.services import SASessionUOW

__all__ = ("AppStatistics", "PodcastStatistics", "StatisticService")


class RecentActivity(BaseModel):
    """Recent activity text and optional time string for display."""

    text: str
    time: str | None = None


class AppStatistics(BaseModel):
    """Application-wide statistics for dashboard (index)."""

    total_episodes: int = 0
    total_podcasts: int = 0
    total_duration: int = 0
    total_size: int = 0
    downloading_count: int = 0
    last_published_at: datetime | None = None
    last_created_at: datetime | None = None
    recent_activity: RecentActivity = RecentActivity(text="No episodes yet", time=None)

    @computed_field
    @property
    def total_storage(self) -> str:
        """Human-readable total storage size derived from total_size (bytes)."""
        return format_file_size(self.total_size)


class PodcastStatistics(BaseModel):
    """Per-podcast statistics for podcast detail page."""

    episodes_count: int = 0
    total_duration: int = 0
    total_size: int = 0
    last_published_at: datetime | None = None
    last_created_at: datetime | None = None


class StatisticService:
    """Service that builds app and podcast statistics using UOW-backed repositories."""

    def __init__(self, uow: SASessionUOW) -> None:
        self._uow = uow

    async def get_app_statistics(self, owner_id: int = 1) -> AppStatistics:
        """Build application-wide statistics (podcasts count, episodes agg, recent activity)."""
        podcast_repo = PodcastRepository(session=self._uow.session)
        episode_repo = EpisodeRepository(session=self._uow.session)
        podcasts = await podcast_repo.all(owner_id=owner_id)
        episodes_agg = await episode_repo.get_aggregated()

        last_pub = episodes_agg.last_published_at
        recent_text = (
            f"Last episode: {last_pub.strftime('%d %b, %Y %H:%M')}" if last_pub else "No episodes yet"
        )
        recent_time = last_pub.strftime("%d %b, %Y %H:%M") if last_pub else None

        return AppStatistics(
            total_episodes=episodes_agg.total_count,
            total_podcasts=len(podcasts),
            total_duration=episodes_agg.total_duration or 0,
            total_size=episodes_agg.total_file_size or 0,
            downloading_count=125,  # placeholder until real downloading count is available
            last_published_at=episodes_agg.last_published_at,
            last_created_at=episodes_agg.last_created_at,
            recent_activity=RecentActivity(text=recent_text, time=recent_time),
        )

    async def get_podcast_statistics(self, podcast_id: int) -> PodcastStatistics:
        """Build statistics for a single podcast from its episodes aggregation."""
        episode_repo = EpisodeRepository(session=self._uow.session)
        agg = await episode_repo.get_aggregated(podcast_id=podcast_id)
        return PodcastStatistics(
            episodes_count=agg.total_count,
            total_duration=agg.total_duration or 0,
            total_size=agg.total_file_size or 0,
            last_published_at=agg.last_published_at,
            last_created_at=agg.last_created_at,
        )
