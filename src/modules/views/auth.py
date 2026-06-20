"""Login and logout HTTP handlers."""

import uuid
import logging
from datetime import timedelta

from litestar import Request, get, post
from litestar.datastructures import Cookie, State
from litestar.response import Redirect, Template

from src.constants import AuthSkip
from src.exceptions import AuthCredentialsInvalidError, AuthenticationError
from src.modules.auth.backend import TokenData
from src.modules.db import User
from src.settings.app import get_app_settings, AppSettings
from src.modules.db.repositories import UserRepository, UserSessionRepository
from src.modules.db.services import SASessionUOW
from src.modules.views.base import BaseViewController
from src.utils import utcnow

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
    async def login_submit(self, request: R, settings: AppSettings) -> Redirect:
        """Authenticate form credentials and create a browser session."""
        if request.user is not None:
            logger.info("Auth: user '%s' already logged in. Redirecting -> '/' ...", request.user)
            return Redirect(path="/")

        try:
            user, session_cookie = await self._do_login(request, settings)
        except AuthenticationError as err:
            return Redirect(path=f"/login?error={err.message}")

        logger.info("Auth: user %s successful logged in", user)
        return Redirect(path="/", cookies=[session_cookie])

    @staticmethod
    async def _do_login(request: R, settings: AppSettings) -> tuple[User, Cookie]:
        form = await request.form()
        email = str(form.get("email") or "").strip()
        password = str(form.get("password") or "")
        if not all([email, password]):
            raise AuthCredentialsInvalidError("Email or password is required.")

        async with SASessionUOW() as uow:
            # TODO: move to auth backend ?
            user_repo = UserRepository(session=uow.session)
            user = await user_repo.get_by_email(email)
            if not user:
                raise AuthCredentialsInvalidError("Unable to find user with provided email")

            if not user.verify_password(password):
                raise AuthCredentialsInvalidError("Incorrect password")

            public_id = str(uuid.uuid4())
            now = utcnow()
            expired_at = now + timedelta(seconds=settings.auth.session_ttl_seconds)
            session_repo = UserSessionRepository(session=uow.session)
            await session_repo.create(
                public_id=public_id,
                user_id=user.id,
                refresh_token=None,
                is_active=True,
                expired_at=expired_at,
                created_at=now,
                refreshed_at=now,
            )
            uow.mark_for_commit()

        session_cookie = Cookie(
            key=settings.auth.session_cookie_name,
            value=public_id,
            max_age=settings.auth.session_ttl_seconds,
            httponly=True,
            secure=settings.auth_cookie_secure_effective(),
            samesite="lax",
            path="/",
        )
        return user, session_cookie


class AuthLogoutController(BaseViewController):
    """Session cookie auth (UserSession.public_id)."""

    @post("/logout")
    async def logout(self, request: Request) -> Redirect:
        """Deactivate the browser session and clear its cookie."""
        settings = get_app_settings()
        public_id = request.cookies.get(settings.auth.session_cookie_name)
        if public_id:
            async with SASessionUOW() as uow:
                repo = UserSessionRepository(session=uow.session)
                await repo.deactivate_by_public_id(public_id)

        clear_cookie = Cookie(
            key=settings.auth.session_cookie_name,
            value="",
            max_age=0,
            httponly=True,
            secure=settings.auth_cookie_secure_effective(),
            samesite="lax",
            path="/",
        )
        return Redirect(path="/login", cookies=[clear_cookie])
