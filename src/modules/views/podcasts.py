from litestar import get, Request
from litestar.response import Template
from litestar.exceptions import NotFoundException

from src import constants as const
from src.modules.db import SASessionUOW
from src.modules.db.repositories import PodcastRepository
from src.modules.views.base import BaseController


class PodcastsController(BaseController):

    @get("/podcasts")
    async def get(self, request: Request) -> Template:
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcasts = await podcast_repository.all(owner_id=1)

        return self.get_response_template(
            template_name="podcasts.html",
            context={
                "podcasts": podcasts,
                "current": "podcasts",
                "format_duration": const.format_duration,
                "format_file_size": const.format_file_size,
            },
        )


class PodcastsDetailsController(BaseController):

    @get("/podcasts/{podcast_id:int}/")
    async def get_detail(self, podcast_id: int, request: Request) -> Template:
        """Get podcast detail page with episodes list"""
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcast = await podcast_repository.get_by_id(podcast_id)
            print(podcast)
            if not podcast:
                raise NotFoundException(f"Podcast with id {podcast_id} not found")

            # Get episodes through backref relationship
            # Episodes are loaded via lazy="subquery" in the relationship
            # episodes = list(podcast.episodes) if podcast.episodes else []
            # Sort episodes by published_at or created_at (newest first)
            # episodes.sort(
            #     key=lambda e: e.published_at if e.published_at else e.created_at, reverse=True
            # )
            episodes = []
            # Generate RSS URL
            rss_url = None
            if podcast.rss:
                rss_url = podcast.rss.url

        return self.get_response_template(
            template_name="podcasts_detail.html",
            context={
                "podcast": podcast,
                "episodes": episodes,
                "rss_url": rss_url,
                "current": "podcasts",
                "format_duration": const.format_duration,
                "format_file_size": const.format_file_size,
                "get_episode_status_color": const.get_episode_status_color,
                "get_episode_status_label": const.get_episode_status_label,
            },
        )


class EpisodesController(BaseController):

    @get("/episodes")
    async def get(self, request: Request) -> Template:
        # Get filter parameters from query string
        query_params = request.query_params
        filters = {
            "status": query_params.get("status"),
            "size_min": query_params.get("size_min"),
            "size_max": query_params.get("size_max"),
            "podcast": query_params.get("podcast"),
            "search": query_params.get("search"),
        }

        # Remove None values
        filters = {k: v for k, v in filters.items() if v is not None}

        # Apply filters
        filtered_episodes = (
            const.filter_episodes(const.EPISODES, filters) if filters else const.EPISODES
        )

        return self.get_response_template(
            template_name="episodes.html",
            context={
                "episodes": filtered_episodes,
                "podcasts": const.PODCASTS,
                "filters": filters,
                "current": "episodes",
                "format_duration": const.format_duration,
                "format_file_size": const.format_file_size,
            },
        )
