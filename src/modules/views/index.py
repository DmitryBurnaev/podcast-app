from litestar import get
from litestar.response import Template

from src.modules.db import SASessionUOW
from src.modules.db.repositories import EpisodeRepository, PodcastRepository
from src.modules.services.statistic import StatisticService
from src.modules.views.base import BaseController


class IndexController(BaseController):
    @get("/")
    async def get(self) -> Template:
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcasts = await podcast_repository.all_with_aggregations(owner_id=1)
            episodes_repository = EpisodeRepository(session=uow.session)
            recent_episodes, _ = await episodes_repository.all_paginated(owner_id=1, limit=7)
            stats = await StatisticService(uow).get_app_statistics(owner_id=1)

        return self.get_response_template(
            template_name="index.html",
            context={
                "podcasts": podcasts,
                "recent_episodes": recent_episodes,
                "current": "home",
                "stats": stats,
            },
        )
