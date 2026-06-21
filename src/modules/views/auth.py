"""Login and logout HTTP handlers."""

import logging

from litestar import Request, get, post
from litestar.datastructures import State
from litestar.response import Redirect, Template

from src.constants import AuthSkip
from src.exceptions import AuthenticationError
from src.modules.auth.backend import TokenData, WebAuthBackend
from src.modules.db import User
from src.modules.views.base import BaseViewController

type R = Request[User | None, TokenData, State]
logger = logging.getLogger(__file__)


class AuthLoginController(BaseViewController):
    """Session cookie auth (UserSession.public_id)."""

    opt = BaseViewController.base_auth_opt | {AuthSkip.SKIP_AUTH_WEB: True}
    template_name = "login.html"

    @get("/login")
    async def login_page(self, request: R) -> Template | Redirect:
        """Render the login page or redirect authenticated users home."""

        if request.user is not None:
            return Redirect(path="/")

        login_error = request.query_params.get("error")
        return self.get_response_template(
            template_name=self.template_name,
            context={"title": "Sign in", "current": "home", "login_error": login_error},
            request=request,
        )

    @post("/login")
    async def login(self, request: R) -> Redirect:
        """Authenticate form credentials and create a browser session."""
        if request.user is not None:
            logger.info("Auth: user '%s' already logged in. Redirecting -> '/' ...", request.user)
            return Redirect(path="/")

        form = await request.form()
        email = str(form.get("email") or "").strip()
        password = str(form.get("password") or "")

        try:
            auth_backend = WebAuthBackend(request)
            user, session_cookie = await auth_backend.login(email=email, password=password)
        except AuthenticationError as err:
            return Redirect(path=f"/login?error={err.message}")

        logger.info("Auth: user %s successful logged in", user)
        return Redirect(path="/", cookies=[session_cookie])


class AuthLogoutController(BaseViewController):
    """Session cookie auth (UserSession.public_id)."""

    @post("/logout")
    async def logout(self, request: Request) -> Redirect:
        """Deactivate the browser session and clear its cookie."""
        auth_backend = WebAuthBackend(request)
        clear_cookie = await auth_backend.logout()
        return Redirect(path="/login", cookies=[clear_cookie])
