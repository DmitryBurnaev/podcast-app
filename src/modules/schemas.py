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
