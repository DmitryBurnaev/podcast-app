from datetime import datetime
from typing import Optional, Generic, TypeVar

from pydantic import BaseModel, Field, computed_field, EmailStr, SecretStr, ConfigDict

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

    model_config = ConfigDict(from_attributes=True)

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


ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class LimitOffsetPagination(BaseModel, Generic[ResponseModelT]):
    """
    Limit and offset pagination for API responses.
    """

    offset: int = Field(default=0, description="Offset of the first item to return")
    items: list[ResponseModelT] = Field(
        default_factory=list, description="List of items for requested limit and offset"
    )
    total: int = Field(default=0, description="Total number of items in the database")


class PodcastResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str | None
    created_at: datetime
    image_url: str | None = None
    rss_url: str | None = None
    download_automatically: bool
    stat: PodcastStatistics


class PodcastCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=256)
    description: str = ""
    download_automatically: bool = False


class PodcastUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    download_automatically: bool | None = None


class PodcastTaskResponse(BaseModel):
    job_id: str


class UploadedImageData(BaseModel):
    name: str | None = None
    path: str
    hash: str
    size: int
    preview_url: str | None = None


class UploadedAudioData(BaseModel):
    name: str
    path: str
    size: int
    meta: dict
    hash: str
    cover: UploadedImageData | None = None


class CookieResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    source_type: str
    created_at: datetime


class PlaylistEntryResponse(BaseModel):
    id: str
    title: str
    description: str
    thumbnail_url: str
    url: str


class PlaylistResponse(BaseModel):
    id: str
    title: str
    entries: list[PlaylistEntryResponse]


class ProgressEpisodeResponse(BaseModel):
    id: int
    title: str
    image_url: str | None = None
    status: str


class ProgressPodcastResponse(BaseModel):
    id: int
    name: str
    image_url: str | None = None


class ProgressItemResponse(BaseModel):
    status: str
    completed: float
    current_file_size: int
    total_file_size: int
    episode: ProgressEpisodeResponse
    podcast: ProgressPodcastResponse | None = None


class EpisodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    podcast_id: int
    title: str
    description: str | None = None
    author: str | None = None
    length: int = 0
    chapters: list[dict] | None = None
    status: str
    source_id: str
    source_type: str
    watch_url: str | None = None
    image_url: str | None = None
    audio_url: str | None = None
    created_at: datetime
    published_at: datetime | None = None


class UploadedEpisodeResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    hash: str
    path: str
    size: int
    available: bool
    source_url: str
    created_at: datetime
