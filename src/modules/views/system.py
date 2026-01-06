from litestar import get
from litestar.response import Template

from src.modules.views.base import BaseController
from src.settings.app import AppSettings


class AboutController(BaseController):

    @get("/about")
    async def get(self, settings: AppSettings) -> Template:
        return self.get_response_template(
            template_name="about.html",
            context={
                "title": "About",
                "current": "about",
                "version": settings.app_version,
            },
        )

