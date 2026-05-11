import asyncio
import logging
from functools import partial
from typing import Any, Iterable, cast

import yt_dlp
from litestar import get

from src.modules.api.base import BaseApiController
from src.modules.api.errors import InvalidParametersError
from src.modules.db import User
from src.modules.db.models import Episode
from src.modules.db.repositories import EpisodeRepository, PodcastRepository
from src.modules.db.services import SASessionUOW
from src.modules.db.utils import cookie_file_ctx
from src.modules.services.redis import check_redis_connection
from src.modules.utils import common as common_utils
from src.modules.utils.processing import check_state
from src.schemas import (
    HealthCheck,
    PlaylistEntryResponse,
    PlaylistResponse,
    ProgressEpisodeResponse,
    ProgressItemResponse,
    ProgressPodcastResponse,
    SystemInfo,
)
from src.settings.app import AppSettings
from src.utils import cut_string, utcnow

logger = logging.getLogger(__name__)


class SystemAPIController(BaseApiController):
    tags = ["System"]

    @get("/api/system/info/")
    async def system_info(self, settings: AppSettings) -> SystemInfo:
        return SystemInfo(status="ok", vendors=[settings.app_version])

    @get("/api/system/health/")
    async def system_health(self) -> HealthCheck:
        await check_redis_connection()
        return HealthCheck(status="ok", timestamp=utcnow())


class PlaylistAPIController(BaseApiController):
    path = "/api/playlist"
    tags = ["Playlist"]

    @get("/")
    async def get_playlist(self, current_user: User, url: str) -> PlaylistResponse:
        try:
            source_info = common_utils.extract_source_info(url, playlist=True)
        except Exception as exc:
            raise InvalidParametersError(details=str(exc)) from exc

        async with SASessionUOW() as uow:
            async with cookie_file_ctx(uow.session, current_user.id, source_info.type) as cookie:
                params: dict[str, Any] = {
                    "logger": logger,
                    "noplaylist": False,
                    "cookiefile": (cookie.file_path if cookie else None),
                    "proxy": common_utils.SOURCE_CFG_MAP[source_info.type].proxy_url,
                }
                with yt_dlp.YoutubeDL(cast(Any, params)) as ydl:
                    extract_info = partial(ydl.extract_info, url, download=False)
                    try:
                        source_data = await asyncio.to_thread(extract_info)
                    except yt_dlp.utils.DownloadError as exc:
                        raise InvalidParametersError(
                            details=f"Couldn't extract playlist: {exc}"
                        ) from exc

        if source_data.get("_type") != "playlist":
            raise InvalidParametersError(details="It seems like incorrect playlist URL.")

        videos = cast(Iterable[dict[str, Any]], source_data.get("entries") or [])
        entries = [
            PlaylistEntryResponse(
                id=str(video.get("id") or ""),
                title=str(video.get("title") or ""),
                description=_prepare_description(video),
                thumbnail_url=(video.get("thumbnails") or [{}])[0].get("url") or "",
                url=video.get("url") or video.get("webpage_url") or "",
            )
            for video in videos
        ]
        return PlaylistResponse(
            id=str(source_data.get("id") or source_info.id),
            title=str(source_data.get("title") or ""),
            entries=entries,
        )


class ProgressAPIController(BaseApiController):
    path = "/api/progress"
    tags = ["Progress"]

    @get("/")
    async def get_progress(
        self,
        current_user: User,
        episode_id: int | None = None,
    ) -> dict[str, list[ProgressItemResponse]]:
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(uow.session)
            podcast_repository = PodcastRepository(uow.session)
            podcasts = {
                podcast.id: podcast
                for podcast in await podcast_repository.all(owner_id=current_user.id)
            }
            if episode_id:
                episode = await episode_repository.first(id=episode_id, owner_id=current_user.id)
                episodes = [episode] if episode else []
            else:
                episodes = await Episode.get_in_progress(uow.session, current_user.id)

            states = await check_state(episodes)

        progress_items: list[ProgressItemResponse] = []
        episodes_by_id = {episode.id: episode for episode in episodes}
        for state in states:
            episode = episodes_by_id[state["episode_id"]]
            podcast = podcasts.get(state["podcast_id"])
            progress_items.append(
                ProgressItemResponse(
                    status=str(state["status"]),
                    completed=state["completed"],
                    current_file_size=state["current_file_size"],
                    total_file_size=state["total_file_size"],
                    episode=ProgressEpisodeResponse.model_validate(episode, from_attributes=True),
                    podcast=(
                        ProgressPodcastResponse.model_validate(podcast, from_attributes=True)
                        if podcast
                        else None
                    ),
                )
            )

        return {"progressItems": progress_items}


def _prepare_description(data: dict) -> str:
    if data.get("description"):
        return cut_string(str(data["description"]), 200)
    if data.get("playlist"):
        return (
            f'Playlist "{data["playlist"]}" '
            f'| Track #{data.get("playlist_index")} of {data.get("n_entries")}'
        )
    return ""
