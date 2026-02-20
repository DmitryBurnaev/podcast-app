import mimetypes
from pathlib import Path
from typing import ClassVar

from litestar import get, Request
from litestar.response import File, Template
from litestar.exceptions import NotFoundException

from src.modules.db import SASessionUOW
from src.modules.db.models import File as MediaFile
from src.modules.db.repositories import EpisodeRepository, PodcastRepository
from src.modules.services.cover import CoverService
from src.modules.services.statistic import StatisticService
from src.modules.views.base import BaseController
from src.utils import cut_string


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

            episodes, _ = await episode_repository.all_paginated(podcast_id=podcast_id, limit=10)
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


class PodcastCoverController(BaseController):
    """Serves podcast cover image from local cache or S3, caching on first request."""

    cache_dir_prefix: ClassVar[str] = "podcasts"
    cache_file_prefix: ClassVar[str] = "podcast_cover"

    @get("/podcasts/{podcast_id:int}/cover/")
    async def get_cover(self, podcast_id: int) -> File:
        """Return podcast cover image; download from S3 or source_url and cache."""

        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcast = await podcast_repository.first(podcast_id)
            if not podcast:
                raise NotFoundException(f"Podcast with id {podcast_id} not found")

            if not podcast.image_id or not podcast.image:
                raise NotFoundException(f"Podcast {podcast_id} has no cover image")

            image = podcast.image

        cover_service = CoverService()
        cached_path = await cover_service.get_or_download(
            image, self.cache_dir_prefix, self.cache_file_prefix
        )
        return self._build_cover_file_response(cached_path, image)

    @staticmethod
    def _build_cover_file_response(cached_path: Path, file_obj: MediaFile) -> File:
        """Build File response for cover from local cache path."""
        media_type, _ = mimetypes.guess_type(str(cached_path)) or (
            "application/octet-stream",
            None,
        )
        return File(path=cached_path, filename=file_obj.name, media_type=media_type)
