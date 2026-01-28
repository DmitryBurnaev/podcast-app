from litestar import get
from litestar.response import Template

from src.modules.db import SASessionUOW
from src.modules.db.repositories import PodcastRepository, EpisodeRepository
from src.modules.views.base import BaseController


class IndexController(BaseController):
    @get("/")
    async def get(self) -> Template:
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcasts = await podcast_repository.all(owner_id=1)
            episodes_repository = EpisodeRepository(session=uow.session)
            recent_episodes, _ = await episodes_repository.all_paginated(owner_id=1, limit=5)
            episodes_stats = await episodes_repository.get_aggregated()

        return self.get_response_template(
            template_name="index.html",
            context={
                "podcasts": podcasts,
                "episodes_stats": episodes_stats,
                "recent_episodes": recent_episodes,
                "current": "home",
                "stats": {
                    "total_episodes": episodes_stats.total_count,
                    "total_duration": episodes_stats.total_duration,
                    "total_size": episodes_stats.total_file_size,
                    "last_published_at": episodes_stats.last_published_at,
                    "last_created_at": episodes_stats.last_created_at,
                    "total_podcasts": len(podcasts),
                    "downloading_count": 125,
                    "recent_activity": {
                        "text": f"Last episode: {episodes_stats.last_published_at.strftime('%d %b, %Y %H:%M')}",
                        "time": episodes_stats.last_published_at.strftime("%d %b, %Y %H:%M"),
                    },
                },
            },
        )
