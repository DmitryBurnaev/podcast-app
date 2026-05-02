from datetime import datetime

from pydantic import BaseModel


class Podcast(BaseModel):
    id: int
    name: str
    description: str
    created_at: datetime
    updated_at: datetime
    download_automatically: bool
    rss_url: str
    image_url: str


class EpisodeInList(BaseModel):
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
    podcast: Podcast
