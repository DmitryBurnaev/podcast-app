from datetime import datetime

from pydantic import BaseModel, ConfigDict, computed_field

from src.constants import format_file_size


class RecentActivity(BaseModel):
    """Recent activity text and optional time string for display."""

    text: str
    time: str | None = None


class AppStatistics(BaseModel):
    """Application-wide statistics for dashboard."""

    total_episodes: int = 0
    total_podcasts: int = 0
    total_duration: int = 0
    total_size: int = 0
    downloading_count: int = 0
    last_published_at: datetime | None = None
    last_created_at: datetime | None = None
    recent_activity: RecentActivity = RecentActivity(text="No episodes yet", time=None)

    @computed_field
    def total_storage(self) -> str:
        """Return the human-readable total storage size derived from bytes."""
        return format_file_size(self.total_size)


class PodcastStatistics(BaseModel):
    """Per-podcast statistics for podcast detail page."""

    model_config = ConfigDict(from_attributes=True)

    episodes_count: int = 0
    total_duration: int = 0
    total_size: int = 0
    last_published_at: datetime | None = None
    last_created_at: datetime | None = None
