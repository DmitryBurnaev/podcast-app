from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, PositiveInt


class EpisodeCreateSchema(BaseModel):
    """Request payload for creating an episode from a source URL."""

    source_url: str = Field(
        alias="sourceURL",
        title="Source URL",
        description="The URL of the source media",
        min_length=1,
        max_length=2048,
    )
    podcast_id: PositiveInt = Field(
        alias="podcastID",
        title="Podcast ID",
        description="The ID of the podcast",
    )

    @property
    def normalized_source_url(self) -> str:
        """Return the source URL stripped for consistent downstream usage."""
        return str(self.source_url).strip()

    class Config:
        json_schema_extra = {
            "example": {
                "sourceURL": "https://www.youtube.com/watch?v=testyoutubeid",
                "podcastID": 1,
            }
        }


class EpisodeCreateNestedSchema(BaseModel):
    """Nested request payload for creating an episode under a podcast."""

    source_url: str = Field(
        alias="sourceURL",
        title="Source URL",
        description="The URL of the source media",
        min_length=1,
        max_length=2048,
    )

    @property
    def normalized_source_url(self) -> str:
        """Return the source URL stripped for consistent downstream usage."""
        return str(self.source_url).strip()


class EpisodePatchSchema(BaseModel):
    """Request payload for updating episode fields."""

    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    author: str | None = Field(default=None, max_length=256)
    chapters: list[dict] | None = None

    @property
    def update_data(self) -> dict:
        """Return only fields explicitly provided by the client."""
        return self.model_dump(exclude_unset=True)


class UploadedEpisodeCreateSchema(BaseModel):
    """Request payload for turning an uploaded audio file into an episode."""

    path: str | None = Field(default=None, max_length=256)
    name: str | None = Field(default=None, max_length=256)
    size: int | None = Field(default=None, ge=0)
    hash: str = Field(min_length=1, max_length=32)
    meta: dict | None = None
    cover: dict | None = None
    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    author: str | None = Field(default=None, max_length=256)
    length: int = Field(default=0, ge=0)

    @property
    def source_id(self) -> str:
        """Return the generated source id for the uploaded file."""
        return f"upl_{self.hash[:11]}"

    @property
    def duration(self) -> int:
        """Return the metadata duration when present, otherwise the provided length."""
        if self.meta and self.meta.get("duration") is not None:
            return int(self.meta["duration"])
        return self.length

    @property
    def prepared_title(self) -> str:
        """Return the best display title derived from explicit and embedded metadata."""
        if self.title:
            return self.title
        if self.meta and self.meta.get("title"):
            title = str(self.meta["title"])
        elif self.name:
            title = self.name.rpartition(".")[0] if "." in self.name else self.name
        else:
            title = self.hash

        title_prefix = ""
        if self.meta:
            if album := self.meta.get("album"):
                title_prefix += str(album)
            if track := self.meta.get("track"):
                title_prefix += f" #{track}" if title_prefix else f"Track #{track}"

        return f"{title_prefix}. {title}" if title_prefix else title

    @property
    def prepared_author(self) -> str | None:
        """Return the best author value derived from explicit and embedded metadata."""
        if self.author:
            return self.author
        if self.meta and self.meta.get("author"):
            return str(self.meta["author"])
        return None

    @property
    def prepared_description(self) -> str:
        """Return a display description derived from explicit and embedded metadata."""
        if self.description:
            return self.description

        title = self.prepared_title
        description = f"Uploaded Episode '{title}'"
        if not self.meta:
            return description

        album = self.meta.get("album")
        track = self.meta.get("track")
        if album:
            description += f"\nAlbum: {album}"
        if album and track:
            description += f" (track #{track})"
        elif track:
            description += f"\nTrack: #{track}"
        if author := self.meta.get("author"):
            description += f"\nAuthor: {author}"
        return description


class EpisodeResponse(BaseModel):
    """API response with episode details."""

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
    """API response with uploaded episode file metadata."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    hash: str
    path: str
    size: int
    available: bool
    source_url: str
    created_at: datetime
