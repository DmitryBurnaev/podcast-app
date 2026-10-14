import logging
import mimetypes
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import ClassVar, TypedDict, cast

from litestar import Request, get, post
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException, NotFoundException
from litestar.params import FromPath
from litestar.response import File, Template
from litestar.status_codes import HTTP_201_CREATED
from pydantic import ValidationError

from src.modules.common.constants import EpisodeStatus
from src.modules.common.types import AppRequest, OwnerScope
from src.modules.db import SASessionUOW
from src.modules.db.models import File as MediaFile
from src.modules.db.repositories import EpisodeRepository, PodcastRepository
from src.modules.schemas.episodes import EpisodeCreateNestedSchema, EpisodeCreateSchema
from src.modules.services.cover import CoverService
from src.modules.services.episodes_operations import PodcastEpisodeOperations
from src.modules.services.task_queue import TaskQueueApp
from src.modules.utils.common import cut_string
from src.modules.views.base import BaseViewController
from src.settings.app import AppSettings, get_app_settings

logger = logging.getLogger(__name__)


class EpisodesController(BaseViewController):
    """
    Allows to create episode from source URL (JSON body: sourceURL) and retrieve all episodes.
    """

    @post("/episodes/", status_code=HTTP_201_CREATED)
    async def post(
        self,
        request: AppRequest,
        user_scope: NamedDependency[OwnerScope],
    ) -> dict:
        """
        Create episode from source URL (JSON body: sourceURL). Returns { id }.

        Example:
        {
            "sourceURL": "https://www.youtube.com/watch?v=testyoutubeid",
            "podcastID": 1
        }

        Returns:
        {
            "id": 1
        }
        """
        data = await self._validate_create_episode_request(request)
        episode = await PodcastEpisodeOperations().create(
            queue=cast(TaskQueueApp, request.app).rq_queue,
            podcast_id=data.podcast_id,
            current_user=request.user,
            user_scope=user_scope,
            data=EpisodeCreateNestedSchema(sourceURL=data.normalized_source_url),
        )
        return {"id": episode.id}

    @get("/episodes/")
    async def get(
        self,
        request: AppRequest,
        settings: NamedDependency[AppSettings],
        user_scope: NamedDependency[OwnerScope],
    ) -> Template:
        """Render the episode list page with optional filters."""
        filters, repository_filters = prepare_episode_filters(dict(request.query_params))

        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            podcasts, _ = await podcast_repository.all_paginated(
                limit=settings.default_pagination_limit,
            )
            episodes_repository = EpisodeRepository(session=uow.session, scope=user_scope)
            episodes, _ = await episodes_repository.all_paginated(
                limit=settings.default_pagination_limit,
                **repository_filters,
            )

        return self.get_response_template(
            template_name="episodes.html",
            context={
                "episodes": episodes,
                "podcasts": podcasts,
                "filters": filters,
                "current": "episodes",
                "title": "episodes",
            },
            request=request,
        )

    @staticmethod
    async def _validate_create_episode_request(request: Request) -> EpisodeCreateSchema:
        """
        Validate create episode request and return EpisodeCreateSchema object.
        """
        try:
            return EpisodeCreateSchema.model_validate(await request.json())
        except ValidationError as e:
            logger.warning("Invalid create episode request: %s", e)
            raise HTTPException(status_code=400, detail=str(e)) from e


class EpisodeDetailsController(BaseViewController):
    @get("/episodes/{episode_id:int}/")
    async def get_detail(
        self,
        episode_id: FromPath[int],
        request: AppRequest,
        user_scope: NamedDependency[OwnerScope],
    ) -> Template:
        """Get episode detail page with edit form"""
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session, scope=user_scope)
            episode = await episode_repository.first(episode_id)
            if not episode:
                raise NotFoundException(f"Episode with id {episode_id} not found")

            # Debug: episode audio player (enable DEBUG on src.modules.views.episodes for url_len)
            audio_url = episode.audio_url
            logger.info(
                "[episode_player] detail episode_id=%s audio_id=%s has_audio_url=%s",
                episode.id,
                episode.audio_id,
                bool(audio_url),
            )
            if logger.isEnabledFor(logging.DEBUG):
                url_len = len(audio_url) if audio_url else 0
                logger.debug(
                    "[episode_player] audio_url length=%s episode_status=%s",
                    url_len,
                    episode.status,
                )

            episode_size = 0
            if episode.audio and hasattr(episode.audio, "size") and episode.audio.size:
                episode_size = episode.audio.size

        return self.get_response_template(
            template_name="episodes_detail.html",
            context={
                "episode": episode,
                "podcast": episode.podcast,
                "audio_url": audio_url,
                "source_url": episode.watch_url,
                "episode_size": episode_size,
                "current": "episodes",
                "title": cut_string(episode.title, max_length=32),
            },
            request=request,
        )


class EpisodeCoverController(BaseViewController):
    cache_dir_prefix: ClassVar[str] = "episodes"
    cache_file_prefix: ClassVar[str] = "episode_cover"

    @get("/episodes/{episode_id:int}/cover/")
    async def get_cover(
        self,
        episode_id: FromPath[int],
        user_scope: NamedDependency[OwnerScope],
    ) -> File:
        """Return episode cover image; download from S3 or source_url and cache."""
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session, scope=user_scope)
            episode = await episode_repository.first(episode_id)
            if not episode:
                raise NotFoundException(f"Episode with id {episode_id} not found")

            if not episode.image_id or not episode.image:
                raise NotFoundException(f"Episode {episode_id} has no cover image")

            image = episode.image

        try:
            cached_path = await CoverService().get_or_download(
                file_obj=image,
                cache_dir_prefix=self.cache_dir_prefix,
                cache_file_prefix=self.cache_file_prefix,
            )
        except NotFoundException:
            settings = get_app_settings()
            cached_path = settings.app_dir / "static" / "img" / "podcast-default.jpg"

        return self._build_cover_file_response(cached_path, image)

    @staticmethod
    def _build_cover_file_response(cached_path: Path, file_obj: MediaFile) -> File:
        """Build File response for cover from local cache path."""
        media_type, _ = mimetypes.guess_type(str(cached_path)) or (
            "application/octet-stream",
            None,
        )
        return File(path=cached_path, filename=file_obj.name, media_type=media_type)


class EpisodeFilters(TypedDict, total=False):
    statuses: str
    search: str
    podcast__name: str
    audio__size__gte: int
    audio__size__lte: int


def file_size_bytes(value: str) -> int:
    try:
        size = Decimal(value)
        if not size.is_finite() or size < 0:
            raise ValueError
        return int(size * 1024 * 1024)
    except (InvalidOperation, ValueError, OverflowError) as exc:
        raise HTTPException(status_code=400, detail="Invalid file size filter.") from exc


def prepare_episode_filters(query: dict[str, str]) -> tuple[dict[str, str], EpisodeFilters]:
    """Separate displayed filters from repository fields and convert MB to bytes."""
    filters = {
        key: value.strip()
        for key, value in query.items()
        if key in {"status", "search", "podcast", "size_min", "size_max"} and value.strip()
    }
    prepared: EpisodeFilters = {}
    if "status" in filters:
        statuses = [value.strip().upper() for value in filters["status"].split(",")]
        statuses = ["NEW" if value == "PENDING" else value for value in statuses]
        if any(value not in EpisodeStatus.members() for value in statuses):
            raise HTTPException(status_code=400, detail="Invalid episode status filter.")
        prepared["statuses"] = ",".join(statuses)
    if "search" in filters:
        prepared["search"] = filters["search"]
    if "podcast" in filters:
        prepared["podcast__name"] = filters["podcast"]
    if "size_min" in filters:
        prepared["audio__size__gte"] = file_size_bytes(filters["size_min"])
    if "size_max" in filters:
        prepared["audio__size__lte"] = file_size_bytes(filters["size_max"])
    if (
        "size_min" in filters
        and "size_max" in filters
        and Decimal(filters["size_min"]) > Decimal(filters["size_max"])
    ):
        raise HTTPException(status_code=400, detail="Minimum file size exceeds maximum.")
    return filters, prepared
