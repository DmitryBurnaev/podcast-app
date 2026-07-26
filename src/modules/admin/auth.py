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
        form = await request.form()
        email = str(form.get("email") or "")
        password = str(form.get("password") or "")
        try:
            result = await AdminAuthBackend(
                request=cast(ASGIConnection, request), settings=self.settings
            ).login(email=email, password=password)
        except AuthenticationError:
            return False

        if result.token is None:
            logger.error("[admin-auth] Login succeeded without a session token")
            return False
        request.session["token"] = result.token
        return True

    async def logout(self, request: Request) -> bool:
        await AdminAuthBackend(
            request=cast(ASGIConnection, request), settings=self.settings
        ).logout()
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        try:
            await AdminAuthBackend(
                request=cast(ASGIConnection, request), settings=self.settings
            ).authenticate()
        except AuthenticationError:
            return False
        return True
