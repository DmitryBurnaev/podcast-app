from litestar import get
from litestar.response import Template

from src.modules.db import SASessionUOW
from src.modules.db.repositories import EpisodeRepository, PodcastRepository
from src.modules.services.statistic import StatisticService
from src.modules.views.base import BaseViewController, AppRequest


class IndexController(BaseViewController):
    @get("/")
    async def get(self, request: AppRequest) -> Template:
        """Render the application dashboard."""
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, user_id=request.user.id)
            podcasts, _ = await podcast_repository.all_with_aggregations()
            episodes_repository = EpisodeRepository(session=uow.session, user_id=request.user.id)
            recent_episodes, _ = await episodes_repository.all_paginated(limit=7)
            stats = await StatisticService(uow).get_app_statistics(owner_id=request.user.id)

        return self.get_response_template(
            template_name="index.html",
            context={
                "podcasts": podcasts,
                "recent_episodes": recent_episodes,
                "current": "home",
                "stats": stats,
            },
            request=request,
        )
