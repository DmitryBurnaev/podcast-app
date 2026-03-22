import logging
import mimetypes
from pathlib import Path
from typing import ClassVar

from litestar import get, post, Request
from litestar.response import File, Template
from litestar.exceptions import HTTPException, NotFoundException
from litestar.status_codes import HTTP_201_CREATED
from pydantic import ValidationError

from src.constants import EpisodeStatus
from src.modules import tasks
from src.modules.db import SASessionUOW
from src.modules.db.models import File as MediaFile
from src.modules.db.repositories import EpisodeRepository, PodcastRepository
from src.modules.schemas import EpisodeCreateSchema
from src.modules.services.cover import CoverService
from src.modules.services.episodes import EpisodeCreator
from src.modules.views.base import BaseController
from src.settings.app import get_app_settings
from src.utils import cut_string

logger = logging.getLogger(__name__)


class EpisodesController(BaseController):
    """
    Allows to create episode from source URL (JSON body: sourceURL) and retrieve all episodes.
    """

    @post("/episodes/", status_code=HTTP_201_CREATED)
    async def post(self, request: Request) -> dict:
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
        episode_create_schema = await self._validate_create_episode_request(request)
        podcast_id = episode_create_schema.podcast_id
        source_url = episode_create_schema.normalized_source_url
        # TODO: replace with real user
        user_id = 1

        logger.info(
            "Creating episode from source_url (podcast_id=%s, source_url=%s)",
            podcast_id,
            source_url,
        )

        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcast = await podcast_repository.first(podcast_id)
            if not podcast:
                raise NotFoundException(f"Podcast with id {podcast_id} not found")

            creator = EpisodeCreator(db_session=uow.session, user_id=user_id)
            try:
                episode = await creator.create(
                    podcast_id=podcast_id,
                    source_url=source_url,
                )
            except ValueError as e:
                logger.warning("Episode creation failed: %s", e)
                raise HTTPException(status_code=400, detail=str(e)) from e

            episode_repository = EpisodeRepository(uow.session)
            if podcast.download_automatically:
                await episode_repository.update(episode, status=EpisodeStatus.DOWNLOADING)

        if podcast.download_automatically:
            await self._run_task(request.app, tasks.DownloadEpisodeTask, episode_id=episode.id)

        await self._run_task(request.app, tasks.DownloadEpisodeImageTask, episode_id=episode.id)

        if logger.isEnabledFor(logging.DEBUG):
            logger.debug("Episode created: #%s | url: %r", episode.id, source_url)
        else:
            logger.info("Episode created: #%s", episode.id)

        return {"id": episode.id}

    @get("/episodes/")
    async def get(self, request: Request) -> Template:
        query_params = request.query_params
        filters: dict = {}
        if query_params.get("status"):
            filters["statuses"] = query_params.get("status")

        if query_params.get("search"):
            filters["search"] = query_params.get("search")

        if query_params.get("podcast"):
            filters["podcast__name"] = query_params.get("podcast")

        if size_min := query_params.get("size_min"):
            filters["audio__size__gte"] = int(size_min)

        if size_max := query_params.get("size_max"):
            filters["audio__size__lte"] = int(size_max)

        async with SASessionUOW() as uow:
            episodes_repository = EpisodeRepository(session=uow.session)
            episodes = await episodes_repository.all(**filters)
            podcast_repository = PodcastRepository(session=uow.session)
            podcasts = await podcast_repository.all(**filters)

        return self.get_response_template(
            template_name="episodes.html",
            context={
                "episodes": episodes,
                "podcasts": podcasts,
                "filters": filters,
                "current": "episodes",
                "title": "episodes",
            },
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


class EpisodeDetailsController(BaseController):
    @get("/episodes/{episode_id:int}/")
    async def get_detail(self, episode_id: int, _: Request) -> Template:
        """Get episode detail page with edit form"""
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episode = await episode_repository.first(episode_id)
            if not episode:
                raise NotFoundException(f"Episode with id {episode_id} not found")

            # Get related podcast
            podcast = episode.podcast

            # Prepare audio URL
            audio_url = None
            if episode.audio:
                audio_url = episode.audio.url if hasattr(episode.audio, "url") else None

            # Prepare source URL (watch_url)
            source_url = episode.watch_url

            # Calculate episode size
            episode_size = 0
            if episode.audio and hasattr(episode.audio, "size") and episode.audio.size:
                episode_size = episode.audio.size

        return self.get_response_template(
            template_name="episodes_detail.html",
            context={
                "episode": episode,
                "podcast": podcast,
                "audio_url": audio_url,
                "source_url": source_url,
                "episode_size": episode_size,
                "current": "episodes",
                "title": cut_string(episode.title, max_length=32),
            },
        )


class EpisodeCoverController(BaseController):
    cache_dir_prefix: ClassVar[str] = "episodes"
    cache_file_prefix: ClassVar[str] = "episode_cover"

    @get("/episodes/{episode_id:int}/cover/")
    async def get_cover(self, episode_id: int) -> File:
        """Return episode cover image; download from S3 or source_url and cache."""
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episode = await episode_repository.first(episode_id)
            if not episode:
                raise NotFoundException(f"Episode with id {episode_id} not found")

            if not episode.image_id or not episode.image:
                raise NotFoundException(f"Episode {episode_id} has no cover image")

            image = episode.image

        cover_service = CoverService()
        try:
            cached_path = await cover_service.get_or_download(
                image, self.cache_dir_prefix, self.cache_file_prefix
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
