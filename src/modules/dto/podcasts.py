from dataclasses import dataclass, asdict
from datetime import timedelta

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


@dataclass
class EpisodeChapter:
    """Base info about episode's chapter"""

    title: str
    start: int
    end: int

    @property
    def as_dict(self) -> dict:
        """Return chapter fields as a dictionary."""
        return asdict(self)  # noqa

    @property
    def start_str(self) -> str:  # ex.: 0:45:05
        """Return the chapter start time as HH:MM:SS."""
        return self._ftime(self.start)

    @property
    def end_str(self) -> str:  # ex.: 0:45:05
        """Return the chapter end time as HH:MM:SS."""
        return self._ftime(self.end)

    @staticmethod
    def _ftime(sec: int) -> str:
        result_delta: timedelta = timedelta(seconds=sec)
        mm, ss = divmod(result_delta.total_seconds(), 60)
        hh, mm = divmod(mm, 60)
        return f"{int(hh):02d}:{int(mm):02d}:{int(ss):02d}"  # 123sec -> '00:02:03'
