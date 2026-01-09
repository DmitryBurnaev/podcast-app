from litestar import get
from litestar.response import Template

from src import constants as const
from src.modules.db import SASessionUOW
from src.modules.db.repositories import PodcastRepository
from src.modules.views.base import BaseController


class IndexController(BaseController):

    @get("/")
    async def get(self) -> Template:
        stats = const.get_stats()
        recent_episodes = const.get_recent_episodes(limit=5)
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcasts = await podcast_repository.all(owner_id=1)

        return self.get_response_template(
            template_name="index.html",
            context={
                "podcasts": podcasts,
                "stats": stats,
                "recent_episodes": recent_episodes,
                "current": "home",
            },
        )
