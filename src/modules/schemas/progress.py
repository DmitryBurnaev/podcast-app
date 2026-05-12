from pydantic import BaseModel, ConfigDict


class ProgressEpisodeResponse(BaseModel):
    """API response with episode details for a progress item."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    image_url: str | None = None
    status: str


class ProgressPodcastResponse(BaseModel):
    """API response with podcast details for a progress item."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    image_url: str | None = None


class ProgressItemResponse(BaseModel):
    """API response with current episode processing progress."""

    status: str
    completed: float
    current_file_size: int
    total_file_size: int
    episode: ProgressEpisodeResponse
    podcast: ProgressPodcastResponse | None = None
