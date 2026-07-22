import datetime
import logging
from typing import cast, TypedDict

import jwt
from sqladmin.authentication import AuthenticationBackend
from starlette.requests import Request

from modules.auth.backends import WebAuthBackend, AdminAuthBackend
from src.modules.db.repositories import UserRepository
from src.modules.db.services import SASessionUOW
from src.modules.db.models import User
from src.modules.admin.utils import register_error_alert
from src.modules.auth.tokens import encode_jwt, TokenPayload, decode_jwt
from src.settings.app import AppSettings
from src.utils import utcnow

logger = logging.getLogger(__name__)
type USER_ID = int


class UserPayload(TypedDict):
    id: int
    email: str


class AdminAuth(AuthenticationBackend):
    """
    Customized admin authentication (based on encoding JWT token based on current user)
    """

    def __init__(self, secret_key: str, settings: AppSettings) -> None:
        super().__init__(secret_key=secret_key)
        self.settings: AppSettings = settings

    async def login(self, request: Request) -> bool:
        form = await request.form()
        username: str = cast(str, form["username"])
        password: str = cast(str, form["password"])
        backend = AdminAuthBackend(request=request)
        auth_result = await backend.login(email=username, password=password)
        admin = auth_result.user

        payload: UserPayload = {"id": admin.id, "email": admin.email}
        request.session.update({"token": self._encode_token(payload)})
        return True

    async def logout(self, request: Request) -> bool:
        request.session.clear()
        return True

    async def authenticate(self, request: Request) -> bool:
        token = request.session.get("token")

        if not token:
            return False

        user_id = self._decode_token(token)
        if not user_id:
            logger.warning("[admin-auth] Invalid or outdated session's token")
            register_error_alert(
                title="Authentication failed", details="Invalid or outdated session's token"
            )
            return False

        async with SASessionUOW() as uow:
            user = await UserRepository(session=uow.session).first(instance_id=user_id)
            ok, message = self._check_user(user, identety=user_id)
            if not ok:
                register_error_alert(title="Authentication failed", details=message)
                return False

        return True

    def _encode_token(self, user_payload: UserPayload) -> str:
        exp_time = self.settings.admin.session_expiration_time
        admin_login_token = encode_jwt(
            payload=TokenPayload(sub=str(user_payload["id"])),
            expires_at=(utcnow() + datetime.timedelta(seconds=exp_time)),
            settings=self.settings,
        )
        return admin_login_token

    def _decode_token(self, token: str) -> USER_ID | None:
        try:
            user_payload = decode_jwt(token, settings=self.settings)
        except jwt.PyJWTError:
            return None

        return int(user_payload.sub)

    @staticmethod
    def _check_user(
        user: User | None, identety: str | int, password: str | None = None
    ) -> tuple[bool, str]:
        if not user:
            logger.error("[admin-auth] User '%s' not found", identety)
            return False, "User not found"

        if password is not None:
            password_verified = user.verify_password(password)
            if not password_verified:
                logger.error("[admin-auth] User '%s' | invalid password", user)
                return False, "Invalid password"

        if not user.is_active:
            logger.error("[admin-auth] User '%s' | inactive", user)
            return False, "User inactive"

        if not user.is_admin:
            logger.error("[admin-auth] User '%s' | not an admin", user)
            return False, "User is not an admin"

        return True, "User is active"
