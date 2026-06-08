from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from src.schemas.statistics import PodcastStatistics


class PodcastResponse(BaseModel):
    """API response with podcast details and statistics."""

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
    """Request payload for creating a podcast."""

    name: str = Field(min_length=1, max_length=256)
    description: str = ""
    download_automatically: bool = False


class PodcastUpdateRequest(BaseModel):
    """Request payload for updating podcast fields."""

    name: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    download_automatically: bool | None = None


class PodcastTaskResponse(BaseModel):
    """API response for a podcast background task."""

    job_id: str


class Podcast(BaseModel):
    """DTO schema with podcast fields used by Litestar DTOs."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    download_automatically: bool
    rss_url: str
    image_url: str


class EpisodeInList(BaseModel):
    """DTO schema with episode list fields used by Litestar DTOs."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    source_id: str
    source_type: str
    podcast_id: int
    audio_url: int
    image_url: int
    owner_id: int
    watch_url: str
    length: int
    description: str


class EpisodeDetails(EpisodeInList):
    """DTO schema with detailed episode fields used by Litestar DTOs."""

    podcast: Podcast
