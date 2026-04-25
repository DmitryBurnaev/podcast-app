"""Login and logout HTTP handlers."""

import uuid
from datetime import UTC, datetime, timedelta

from litestar import Request, get, post
from litestar.datastructures import Cookie
from litestar.response import Redirect, Template

from src.modules.auth.load_user import get_current_user_or_none
from src.settings.app import get_app_settings
from src.modules.db.repositories import UserRepository, UserSessionRepository
from src.modules.db.services import SASessionUOW
from src.modules.views.base import BaseController


class AuthController(BaseController):
    """Session cookie auth (UserSession.public_id)."""

    @get("/login")
    async def login_page(self, request: Request) -> Template | Redirect:
        if get_current_user_or_none(request) is not None:
            return Redirect(path="/")
        err = request.query_params.get("error")
        return self.get_response_template(
            template_name="login.html",
            context={
                "title": "Sign in",
                "current": "home",
                "login_error": err,
            },
            request=request,
        )

    @post("/login")
    async def login_submit(self, request: Request) -> Redirect:
        settings = get_app_settings()
        form = await request.form()
        raw_email = form.get("email")
        raw_password = form.get("password")
        email = (str(raw_email).strip() if raw_email else "") or ""
        password = str(raw_password) if raw_password is not None else ""

        if not email or not password:
            return Redirect(path="/login?error=missing")

        async with SASessionUOW() as uow:
            user_repo = UserRepository(session=uow.session)
            user = await user_repo.get_by_email(email)
            if user is None or not user.verify_password(password):
                return Redirect(path="/login?error=invalid")

            public_id = str(uuid.uuid4())
            now = datetime.now(UTC)
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
        return Redirect(path="/", cookies=[session_cookie])

    @post("/logout")
    async def logout(self, request: Request) -> Redirect:
        settings = get_app_settings()
        public_id = request.cookies.get(settings.auth.session_cookie_name)
        if public_id:
            async with SASessionUOW() as uow:
                repo = UserSessionRepository(session=uow.session)
                await repo.deactivate_by_public_id(public_id)
                uow.mark_for_commit()

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
