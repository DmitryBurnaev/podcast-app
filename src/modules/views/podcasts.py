import logging
import mimetypes
from pathlib import Path
from typing import ClassVar

from litestar import get, Request
from litestar.response import File, Template
from litestar.exceptions import NotFoundException

from settings.app import AppSettings
from src.modules.db import SASessionUOW
from src.modules.db.models import File as MediaFile
from src.modules.db.repositories import EpisodeRepository, PodcastRepository
from src.modules.services.statistic import StatisticService
from src.modules.services.storage import StorageS3
from src.modules.views.base import BaseController
from src.settings.app import get_app_settings
from src.utils import cut_string

logger = logging.getLogger(__name__)


class PodcastsController(BaseController):
    @get("/podcasts/")
    async def get(self, request: Request) -> Template:
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcasts = await podcast_repository.all_with_aggregations(owner_id=1)

        return self.get_response_template(
            template_name="podcasts.html",
            context={
                "podcasts": podcasts,
                "current": "podcasts",
                "title": "Podcasts",
            },
        )


class PodcastsDetailsController(BaseController):
    @get("/podcasts/{podcast_id:int}/")
    async def get_detail(self, podcast_id: int, request: Request) -> Template:
        """Get podcast detail page with episodes list"""
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            episode_repository = EpisodeRepository(session=uow.session)
            podcast = await podcast_repository.first(podcast_id)
            if not podcast:
                raise NotFoundException(f"Podcast with id {podcast_id} not found")

            episodes = await episode_repository.all(podcast_id=podcast_id)
            statistic_service = StatisticService(uow)
            podcast_stats = await statistic_service.get_podcast_statistics(podcast_id)

        return self.get_response_template(
            template_name="podcasts_detail.html",
            context={
                "podcast": podcast,
                "episodes": episodes,
                "podcast_stats": podcast_stats,
                "current": "podcasts",
                "title": cut_string(podcast.name, max_length=32),
            },
        )


class EpisodesController(BaseController):
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


class EpisodeDetailsController(BaseController):
    @get("/episodes/{episode_id:int}/")
    async def get_detail(self, episode_id: int, request: Request) -> Template:
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


class CoverControllerMixin:
    """Helper class for cover controllers"""

    cache_dir_prefix: ClassVar[str] = ""

    @staticmethod
    def _build_cover_file_response(cached_path: Path, file_obj: MediaFile) -> File:
        """Build File response for episode cover from local cache path."""
        media_type, _ = mimetypes.guess_type(str(cached_path)) or (
            "application/octet-stream",
            None,
        )
        return File(path=cached_path, filename=file_obj.name, media_type=media_type)

    async def _get_or_download_cover(self, file_obj: MediaFile) -> Path:
        """Get or download cover from S3 and cache if not present locally."""
        media_cache_dir = get_app_settings().media_cache_dir
        suffix = Path(file_obj.path).suffix or ".jpg"
        cached_path = media_cache_dir / self.cache_dir_prefix / f"{file_obj.id}{suffix}"
        if cached_path.exists():
            return cached_path

        cached_path.parent.mkdir(parents=True, exist_ok=True)
        storage = StorageS3()
        downloaded = await storage.download_file(src_path=file_obj.path, dst_path=cached_path)
        if not downloaded:
            raise NotFoundException(f"{file_obj.type.value} cover not found in storage")

        logger.info(
            f"{file_obj.type.value} cover downloaded from S3 and cached: file_id={file_obj.id}, path={cached_path}",
        )
        return cached_path


class EpisodeCoverController(BaseController, CoverControllerMixin):
    cache_dir_prefix = "episodes"

    @get("/episodes/{episode_id:int}/cover/")
    async def get_cover(self, episode_id: int, settings: AppSettings) -> File:
        """Return episode cover image; download from S3 and cache if not present locally."""
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episode = await episode_repository.first(episode_id)
            if not episode:
                raise NotFoundException(f"Episode with id {episode_id} not found")

            if not episode.image_id or not episode.image:
                raise NotFoundException(f"Episode {episode_id} has no cover image")
        try:
            cached_path = await self._get_or_download_cover(episode.image)
        except NotFoundException:
            cached_path = settings.app_dir / "static" / "img" / "podcast-default.jpg"

        return self._build_cover_file_response(cached_path, episode.image)


class PodcastCoverController(BaseController, CoverControllerMixin):
    """Serves podcast cover image from local cache or S3, caching on first request."""

    cache_dir_prefix = "podcasts"

    @get("/podcasts/{podcast_id:int}/cover/")
    async def get_cover(self, podcast_id: int) -> File:
        """Return podcast cover image; download from S3 and cache if not present locally."""

        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcast = await podcast_repository.first(podcast_id)
            if not podcast:
                raise NotFoundException(f"Podcast with id {podcast_id} not found")

            if not podcast.image_id or not podcast.image:
                raise NotFoundException(f"Podcast {podcast_id} has no cover image")

        cached_path = await self._get_or_download_cover(podcast.image)
        return self._build_cover_file_response(cached_path, podcast.image)
