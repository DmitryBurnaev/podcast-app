from litestar.dto import DTOConfig
from litestar.plugins.pydantic import PydanticDTO

from src.modules.schemas.podcasts import EpisodeDetails, EpisodeInList, Podcast

__all__ = (
    "EpisodeDetails",
    "EpisodeInList",
    "Podcast",
    "PodcastCreateDTO",
    "PodcastListDTO",
    "PodcastUpdateDTO",
)


class PodcastListDTO(PydanticDTO[Podcast]): ...


class PodcastCreateDTO(PydanticDTO[Podcast]):
    config = DTOConfig(include={"name", "description"})


class PodcastUpdateDTO(PydanticDTO[Podcast]):
    config = DTOConfig(include={"name", "description", "download_automatically"})
