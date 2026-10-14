import asyncio
import logging
from functools import partial
from typing import Any, Iterable, cast

import yt_dlp
from litestar import get
from litestar.di import NamedDependency
from litestar.params import FromQuery

from src.modules.api.base import BaseApiController
from src.modules.common.constants import AuthSkip
from src.modules.common.exceptions import InvalidParametersAPIError
from src.modules.common.types import OwnerScope
from src.modules.db import User
from src.modules.db.services import SASessionUOW
from src.modules.db.utils import cookie_file_ctx
from src.modules.schemas.playlist import PlaylistEntryResponse, PlaylistResponse
from src.modules.schemas.progress import (
    ProgressItemResponse,
)
from src.modules.schemas.system import HealthCheck, SystemInfo
from src.modules.services.progress import ProgressOperations
from src.modules.services.redis import check_redis_connection
from src.modules.utils import common as common_utils
from src.modules.utils.common import cut_string, utcnow
from src.settings.app import AppSettings

logger = logging.getLogger(__name__)


class SystemAPIController(BaseApiController):
    tags = ["System"]
    opt = BaseApiController.opt | {AuthSkip.SKIP_AUTH_API: True}

    @get("/api/system/info/")
    async def system_info(self, settings: NamedDependency[AppSettings]) -> SystemInfo:
        """Return runtime system information."""
        return SystemInfo(status="ok", vendors=[settings.app_version])

    @get("/api/system/health/")
    async def system_health(self) -> HealthCheck:
        """Run lightweight dependency checks and return health status."""
        await check_redis_connection()
        return HealthCheck(status="ok", timestamp=utcnow())


class PlaylistAPIController(BaseApiController):
    path = "/api/playlist"
    tags = ["Playlist"]

    @get("/")
    async def get_playlist(
        self, current_user: NamedDependency[User], url: FromQuery[str]
    ) -> PlaylistResponse:
        """Extract playlist metadata for the current user."""
        try:
            source_info = common_utils.extract_source_info(url, playlist=True)
        except Exception as exc:
            raise InvalidParametersAPIError(details=str(exc)) from exc

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
                        raise InvalidParametersAPIError(
                            details=f"Couldn't extract playlist: {exc}"
                        ) from exc

        if source_data.get("_type") != "playlist":
            raise InvalidParametersAPIError(details="It seems like incorrect playlist URL.")

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
        user_scope: NamedDependency[OwnerScope],
        episode_id: FromQuery[int | None] = None,
    ) -> dict[str, list[ProgressItemResponse]]:
        return await ProgressOperations().get_progress(user_scope, episode_id)


def _prepare_description(data: dict) -> str:
    if data.get("description"):
        return cut_string(str(data["description"]), 200)
    if data.get("playlist"):
        return (
            f'Playlist "{data["playlist"]}" '
            f'| Track #{data.get("playlist_index")} of {data.get("n_entries")}'
        )
    return ""
