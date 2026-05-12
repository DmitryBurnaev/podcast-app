from pydantic import BaseModel


class PlaylistEntryResponse(BaseModel):
    """API response item for a playlist entry."""

    id: str
    title: str
    description: str
    thumbnail_url: str
    url: str


class PlaylistResponse(BaseModel):
    """API response with playlist metadata and entries."""

    id: str
    title: str
    entries: list[PlaylistEntryResponse]
