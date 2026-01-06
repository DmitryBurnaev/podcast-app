from litestar import get, Request
from litestar.response import Template

from src import constants as const
from src.modules.views.base import BaseController


class PodcastsController(BaseController):

    @get("/podcasts")
    async def get(self) -> Template:
        return self.get_response_template(
            template_name="podcasts.html",
            context={
                "podcasts": const.PODCASTS,
                "current": "podcasts",
                "format_duration": const.format_duration,
                "format_file_size": const.format_file_size,
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
