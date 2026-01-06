from litestar import get
from litestar.response import Template

from src.modules.views.base import BaseController


class ProfileController(BaseController):

    @get("/profile")
    async def get(self) -> Template:
        return self.get_response_template(
            template_name="profile.html",
            context={
                "title": "Profile",
                "current": "profile",
                "current_user": "Test User",
            },
        )

