from datetime import UTC, datetime

from sqlalchemy import select
from starlette.requests import Request
from starlette.responses import Response
from sqladmin.authentication import AuthenticationBackend

from src.modules.db.models import User
from src.modules.db.repositories import UserRepository
from src.modules.db.services import SASessionUOW
from src.settings.app import AppSettings

ADMIN_USER_ID_KEY = "admin_user_id"
ADMIN_EMAIL_KEY = "admin_email"
ADMIN_EXPIRES_AT_KEY = "admin_expires_at"


class PodcastAdminAuth(AuthenticationBackend):
    """SQLAdmin authentication backed by existing Podcast App users."""

    def __init__(self, settings: AppSettings) -> None:
        self.settings = settings
        super().__init__(
            secret_key=settings.app_secret_key.get_secret_value(),
            session_cookie="podcast_admin_session",
            max_age=settings.admin_session_expiration_time,
            path=settings.admin_base_url,
            same_site="lax",
            https_only=settings.auth_cookie_secure_effective(),
        )

    async def login(self, request: Request) -> bool:
        form = await request.form()
        email = str(form.get("email") or form.get("username") or "").strip().lower()
        password = str(form.get("password") or "")
        if not email or not password:
            return False

        async with SASessionUOW() as uow:
            repository = UserRepository(session=uow.session)
            user = await repository.get_by_email(email)

        if user is None or not user.is_active or not user.is_superuser:
            return False

        if not user.verify_password(password):
            return False

        request.session.update(
            {
                ADMIN_USER_ID_KEY: user.id,
                ADMIN_EMAIL_KEY: user.email,
                ADMIN_EXPIRES_AT_KEY: self._expires_at(),
            }
        )
        return True

    async def logout(self, request: Request) -> Response | bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> Response | bool:
        user_id = request.session.get(ADMIN_USER_ID_KEY)
        expires_at = request.session.get(ADMIN_EXPIRES_AT_KEY)
        if not user_id or not expires_at:
            return False

        if float(expires_at) <= self._now():
            request.session.clear()
            return False

        async with SASessionUOW() as uow:
            statement = select(User).where(
                User.id == int(user_id),
                User.is_active.is_(True),
                User.is_superuser.is_(True),
            )
            user = await uow.session.scalar(statement)

        if user is None:
            request.session.clear()
            return False

        request.session[ADMIN_EXPIRES_AT_KEY] = self._expires_at()
        return True

    def _expires_at(self) -> float:
        return self._now() + self.settings.admin_session_expiration_time

    @staticmethod
    def _now() -> float:
        return datetime.now(UTC).timestamp()
