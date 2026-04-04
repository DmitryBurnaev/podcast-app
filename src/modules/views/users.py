from litestar import Request, get
from litestar.response import Template

from src.modules.views.base import BaseController


class ProfileController(BaseController):

    @get("/profile")
    async def get(self, request: Request) -> Template:
        user = request.state.current_user
        return self.get_response_template(
            template_name="profile.html",
            context={
                "title": "Profile",
                "current": "profile",
                "current_user": user.display_name,
            },
            request=request,
        )
