from litestar import Request, get
from litestar.response import Template

from src.modules.auth.load_user import get_current_user
from src.modules.views.base import BaseViewController


class ProfileController(BaseViewController):

    @get("/profile")
    async def get(self, request: Request) -> Template:
        """Render the current user's profile page."""
        user = get_current_user(request)
        return self.get_response_template(
            template_name="profile.html",
            context={
                "title": "Profile",
                "current": "profile",
                "current_user": user.display_name,
            },
            request=request,
        )
