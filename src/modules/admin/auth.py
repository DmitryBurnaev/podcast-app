import logging
from typing import cast

from sqladmin.authentication import AuthenticationBackend
from litestar.connection import ASGIConnection
from starlette.requests import Request

from src.exceptions import AuthenticationError
from src.modules.auth.backends import AdminAuthBackend
from src.settings.app import AppSettings

logger = logging.getLogger(__name__)


class AdminAuth(AuthenticationBackend):
    """
    Customized admin authentication (based on encoding JWT token based on current user)
    """

    def __init__(self, secret_key: str, settings: AppSettings) -> None:
        super().__init__(secret_key=secret_key)
        self.settings: AppSettings = settings

    async def login(self, request: Request) -> bool:
        """Authenticate submitted credentials and persist the admin session state."""
        form = await request.form()
        email = str(form.get("email") or "")
        password = str(form.get("password") or "")
        try:
            auth_backend = self._get_auth_backend(request)
            result = await auth_backend.login(email=email, password=password)
        except AuthenticationError:
            return False

        if result.token is None:
            logger.error("[admin-auth] Login proceed without a session token")
            return False

        request.session["token"] = result.token
        request.session["user_id"] = result.user.id
        logger.debug("[admin-auth] Successfully logged in user: %r", email)
        return True

    async def logout(self, request: Request) -> bool:
        """Invalidate the current admin session and clear its browser state."""
        auth_backend = self._get_auth_backend(request)
        await auth_backend.logout()
        request.session.clear()
        logger.debug("[admin-auth] Successfully logged out")
        return True

    async def authenticate(self, request: Request) -> bool:
        """Validate the credentials associated with the current admin request."""
        try:
            auth_backend = self._get_auth_backend(request)
            await auth_backend.authenticate()
        except AuthenticationError:
            logger.error("[admin-auth] Unable to authenticate with provided credentials")
            return False

        logger.debug("[admin-auth] Successfully authenticated with provided credentials")
        return True

    def _get_auth_backend(self, request: Request) -> AdminAuthBackend:
        return AdminAuthBackend(
            request=cast(ASGIConnection, cast(object, request)),
            settings=self.settings,
        )
