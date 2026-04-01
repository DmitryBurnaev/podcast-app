from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, computed_field, EmailStr, SecretStr

from src.constants import format_file_size

__all__ = (
    "SystemInfo",
    "HealthCheck",
)


class SystemInfo(BaseModel):
    """System information response model."""

    status: str = "ok"
    vendors: list[str] = Field(default_factory=list)


class HealthCheck(BaseModel):
    """Health check response model."""

    status: str
    timestamp: datetime


class ErrorResponse(BaseModel):
    """Base error response model"""

    error: str
    detail: Optional[str] = None


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


class User(BaseModel):
    """DTO aligned with ORM `auth_users` (see `src.modules.db.models.users.User`)."""

    id: int
    email: EmailStr


class UserCreatePayload(BaseModel):
    name: str
    email: EmailStr
    password: SecretStr


class UserLoginPayload(BaseModel):
    email: EmailStr
    password: SecretStr
