from litestar import get
from litestar.response import Template

from src import constants as const
from src.modules.views.base import BaseController


class IndexController(BaseController):

    @get("/")
    async def get(self) -> Template:
        stats = const.get_stats()
        recent_episodes = const.get_recent_episodes(limit=5)

        return self.get_response_template(
            template_name="index.html",
            context={
                "podcasts": const.PODCASTS,
                "stats": stats,
                "recent_episodes": recent_episodes,
                "current": "home",
            },
        )
