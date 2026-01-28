from litestar import get, Request
from litestar.response import Template
from litestar.exceptions import NotFoundException

from src.modules.db import SASessionUOW
from src.modules.db.repositories import PodcastRepository, EpisodeRepository
from src.modules.views.base import BaseController
from src.utils import cut_string


class PodcastsController(BaseController):
    @get("/podcasts/")
    async def get(self, request: Request) -> Template:
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcasts = await podcast_repository.all(owner_id=1)

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
            aggregations = await episode_repository.get_aggregated(podcast.id)

        return self.get_response_template(
            template_name="podcasts_detail.html",
            context={
                "podcast": podcast,
                "episodes": episodes,
                "episodes_count": aggregations.total_count,
                "total_duration": aggregations.total_duration,
                "total_size": aggregations.total_file_size,
                "last_published_at": aggregations.last_published_at,
                "last_created_at": aggregations.last_created_at,
                "current": "podcasts",
                "title": cut_string(podcast.name, max_length=32),
            },
        )


class EpisodesController(BaseController):
    @get("/episodes")
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
