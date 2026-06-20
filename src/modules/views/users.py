from litestar import get
from litestar.response import Template

from src.modules.views.base import BaseViewController, AppRequest


class ProfileController(BaseViewController):

    @get("/profile")
    async def get(self, request: AppRequest) -> Template:
        """Render the current user's profile page."""
        return self.get_response_template(
            template_name="profile.html",
            context={
                "title": "Profile",
                "current": "profile",
                "current_user": request.user.display_name,
            },
            request=request,
        )
