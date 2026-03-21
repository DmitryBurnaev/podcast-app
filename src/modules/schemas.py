from pydantic import BaseModel, Field


class EpisodeCreateSchema(BaseModel):
    source_url: str = Field(
        ...,
        alias="sourceURL",
        title="Source URL",
        format="uri",
        description="The URL of the source media",
        min_length=1,
        max_length=2048,
    )

    podcast_id: int = Field(
        ...,
        alias="podcastID",
        title="Podcast ID",
        description="The ID of the podcast",
        min_value=1,
    )

    @property
    def normalized_source_url(self) -> str:
        """Returns the source_url as a stripped string for consistent downstream usage."""
        return str(self.source_url).strip()

    class Config:
        schema_extra = {
            "example": {
                "sourceURL": "https://www.youtube.com/watch?v=testyoutubeid",
                "podcastID": 1,
            }
        }
