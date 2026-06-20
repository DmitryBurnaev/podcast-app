import mimetypes
from pathlib import Path
from typing import ClassVar

from litestar import get
from litestar.response import File, Template

from src.exceptions import NotFoundError
from src.modules.db import SASessionUOW
from src.modules.db.models import File as MediaFile
from src.modules.db.repositories import EpisodeRepository, PodcastRepository
from src.modules.services.cover import CoverService
from src.modules.services.statistic import StatisticService
from src.modules.views.base import BaseViewController, AppRequest
from src.settings.app import AppSettings
from src.utils import cut_string


class PodcastsController(BaseViewController):
    @get("/podcasts/")
    async def get(self, request: AppRequest) -> Template:
        """Render the podcast list page."""

        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            owner_id = request.user.id
            podcasts, _ = await podcast_repository.all_with_aggregations(owner_id=owner_id)

        return self.get_response_template(
            template_name="podcasts.html",
            context={
                "podcasts": podcasts,
                "current": "podcasts",
                "title": "Podcasts",
            },
            request=request,
        )


class PodcastsDetailsController(BaseViewController):
    @get("/podcasts/{podcast_id:int}/")
    async def get_detail(
        self,
        podcast_id: int,
        request: AppRequest,
        settings: AppSettings,
    ) -> Template:
        """Get podcast detail page with episodes list"""

        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, user_id=request.user.id)
            episode_repository = EpisodeRepository(session=uow.session, user_id=request.user.id)
            podcast = await podcast_repository.get(podcast_id)
            episodes, _ = await episode_repository.all_paginated(
                podcast_id=podcast_id, limit=settings.default_pagination_limit
            )
            podcast_stats = await StatisticService(uow).get_podcast_statistics(
                podcast_id, user_id=request.user.id
            )

        return self.get_response_template(
            template_name="podcasts_detail.html",
            context={
                "podcast": podcast,
                "episodes": episodes,
                "podcast_stats": podcast_stats,
                "current": "podcasts",
                "title": cut_string(podcast.name, max_length=32),
            },
            request=request,
        )


class PodcastCoverController(BaseViewController):
    """Serves podcast cover image from local cache or S3, caching on first request."""

    cache_dir_prefix: ClassVar[str] = "podcasts"
    cache_file_prefix: ClassVar[str] = "podcast_cover"

    @get("/podcasts/{podcast_id:int}/cover/")
    async def get_cover(self, podcast_id: int, request: AppRequest) -> File:
        """Return podcast cover image; download from S3 or source_url and cache."""

        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, user_id=request.user.id)
            podcast = await podcast_repository.get(podcast_id)
            if not podcast.image_id or not podcast.image:
                raise NotFoundError(f"Podcast {podcast_id} has no cover image")

            image = podcast.image

        cover_service = CoverService()
        cached_path = await cover_service.get_or_download(
            file_obj=image,
            cache_dir_prefix=self.cache_dir_prefix,
            cache_file_prefix=self.cache_file_prefix,
        )
        return self._build_cover_file_response(cached_path, image)

    @staticmethod
    def _build_cover_file_response(cached_path: Path, file_obj: MediaFile) -> File:
        """Build File response for cover from local cache path."""
        media_type, _ = mimetypes.guess_type(str(cached_path))
        media_type = media_type or "application/octet-stream"
        return File(path=cached_path, filename=file_obj.name, media_type=media_type)
