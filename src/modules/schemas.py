from pydantic import BaseModel, Field, PositiveInt


class EpisodeCreateSchema(BaseModel):
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
        """Returns the source_url as a stripped string for consistent downstream usage."""
        return str(self.source_url).strip()

    class Config:
        json_schema_extra = {
            "example": {
                "sourceURL": "https://www.youtube.com/watch?v=testyoutubeid",
                "podcastID": 1,
            }
        }


class EpisodeCreateNestedSchema(BaseModel):
    source_url: str = Field(
        alias="sourceURL",
        title="Source URL",
        description="The URL of the source media",
        min_length=1,
        max_length=2048,
    )

    @property
    def normalized_source_url(self) -> str:
        """Returns the source_url as a stripped string for consistent downstream usage."""
        return str(self.source_url).strip()


class EpisodePatchSchema(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=256)
    description: str | None = None
    author: str | None = Field(default=None, max_length=256)
    chapters: list[dict] | None = None

    @property
    def update_data(self) -> dict:
        return self.model_dump(exclude_unset=True)


class UploadedEpisodeCreateSchema(BaseModel):
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
        return f"upl_{self.hash[:11]}"

    @property
    def duration(self) -> int:
        if self.meta and self.meta.get("duration") is not None:
            return int(self.meta["duration"])
        return self.length

    @property
    def prepared_title(self) -> str:
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
        if self.author:
            return self.author
        if self.meta and self.meta.get("author"):
            return str(self.meta["author"])
        return None

    @property
    def prepared_description(self) -> str:
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
