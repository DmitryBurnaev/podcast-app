import logging
import uuid
from datetime import timedelta
from typing import NamedTuple

from jwt import InvalidTokenError, ExpiredSignatureError
from litestar.connection import ASGIConnection
from litestar.datastructures import Cookie, Address
from litestar.exceptions import PermissionDeniedException
from litestar.handlers import BaseRouteHandler
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.tokens import issue_token_pair
from src.modules.auth.types import AuthenticatedUserResult, ByTokenData, TokenData
from src.modules.db import SASessionUOW, User
from src.settings.app import AppSettings, get_app_settings
from src.exceptions import (
    AuthCredentialsInvalidError,
    AuthMissingCredentialsError,
    SignatureExpiredError,
)
from src.modules.db.repositories import (
    AuthUserSessionRepository,
    UserAccessTokenRepository,
    UserRepository,
    UserSessionRepository,
    UserIPRepository,
)
from src.utils import hash_string, utcnow
from src.modules.auth.tokens import TokenCollection
from src.modules.auth.utils import decode_jwt
from src.modules.auth.constants import LENGTH_USER_ACCESS_TOKEN, AuthTokenType

logger = logging.getLogger(__name__)


def admin_user_guard(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    if not connection.user.is_superuser:
        raise PermissionDeniedException()


class SuccessLoginData(NamedTuple):
    user: User
    cookie: Cookie | None = None
    tokens: TokenCollection | None = None


class AuthBackend:
    """Core of authenticate system, based on JWT auth approach"""

    keyword = "Bearer"

    def __init__(self, connection: ASGIConnection, header_keyword: str | None = None) -> None:
        self.connection = connection
        self.settings: AppSettings = get_app_settings()
        self.header_keyword: str = header_keyword if header_keyword else self.keyword

    async def authenticate(self) -> AuthenticatedUserResult:
        headers = self.connection.headers
        auth_header = headers.get("Authorization") or headers.get("authorization")
        if not auth_header:
            raise AuthMissingCredentialsError("Invalid token header. No credentials provided.")

        auth = auth_header.split()
        if len(auth) != 2:
            logger.warning("Trying to authenticate with header %s", auth_header)
            raise AuthCredentialsInvalidError(
                "Invalid token header. Token should be format as JWT."
            )

        if auth[0] != self.header_keyword:
            raise AuthCredentialsInvalidError("Invalid token header. Keyword mismatch.")

        async with SASessionUOW() as uow:
            auth_result = await self._authenticate_user(
                jwt_token=auth[1],
                db_session=uow.session,
            )

        return auth_result

    async def login(self, email: str, password: str) -> SuccessLoginData:
        raise NotImplementedError

    async def _authenticate_user(
        self,
        jwt_token: str,
        db_session: AsyncSession,
        token_type: AuthTokenType = AuthTokenType.ACCESS,
    ) -> AuthenticatedUserResult:
        """Allows to find active user by provided jwt_token"""

        if self._seems_like_user_access_token(jwt_token):
            by_token_data = await self._encode_user_access_token(jwt_token, db_session=db_session)
            token_type = AuthTokenType.USER_ACCESS
        else:
            by_token_data = self._decode_jwt(jwt_token, token_type)

        user_id = by_token_data.user_id
        user_repo = UserRepository(session=db_session)
        user = await user_repo.first(id=user_id, is_active=True)
        if not user:
            msg = "Couldn't found active user with id=%s."
            logger.warning(msg, user_id)
            raise AuthCredentialsInvalidError(details=(msg % (user_id,)))

        if token_type in (AuthTokenType.RESET_PASSWORD, AuthTokenType.USER_ACCESS):
            return AuthenticatedUserResult(user, by_token_data.payload, None)

        session_id = by_token_data.session_id
        if not session_id:
            raise AuthCredentialsInvalidError("Incorrect data in JWT: session_id is missed")

        user_session_repo = AuthUserSessionRepository(session=db_session)
        user_session = await user_session_repo.get_active_by_public_id(session_id)
        if not user_session:
            raise AuthCredentialsInvalidError(
                f"Couldn't found active session: {user_id=} | {session_id=}."
            )

        return AuthenticatedUserResult(user, by_token_data.payload, session_id)

    def _decode_jwt(self, token: str, token_type: AuthTokenType) -> ByTokenData:
        """
        Encodes given JWT token and extract stored data in a JWT payload

        :param token: JWT token
        :return: ByTokenData instance (stores token-specific info)
        """
        logger.debug("Logging via JWT auth. Got token: %s", token)
        try:
            jwt_payload: TokenData = decode_jwt(token, settings=self.settings)
        except ExpiredSignatureError as exc:
            logger.debug("JWT signature has been expired for %s token", token_type)
            exception_class = (
                SignatureExpiredError
                if token_type == AuthTokenType.ACCESS
                else AuthCredentialsInvalidError
            )
            raise exception_class("JWT signature has been expired for token") from exc

        except InvalidTokenError as exc:
            msg = "Token could not be decoded: %s"
            logger.exception(msg, exc)
            raise AuthCredentialsInvalidError(msg % (exc,)) from exc

        expected_token_type = str(token_type).lower()
        if jwt_payload["token_type"].lower() != expected_token_type:
            raise AuthCredentialsInvalidError(
                f"Token type '{expected_token_type}' expected, "
                f"got '{jwt_payload['token_type'].lower()}' instead."
            )

        session_id: str | None = jwt_payload.get("session_id")
        if not session_id:
            raise AuthCredentialsInvalidError("Incorrect data in JWT: session_id is missed")

        return ByTokenData(
            user_id=jwt_payload["user_id"],
            payload=jwt_payload,
            session_id=session_id,
        )

    @staticmethod
    async def _encode_user_access_token(token: str, db_session: AsyncSession) -> ByTokenData:
        """
        Finds active UserAccessToken instance by provided token

        :param token: access token (will be hashed for finding stored in DB)
        :param db_session: current db's session instance
        :return: ByTokenData instance (stores token-specific info)
        """
        logger.debug("Logging via UserAccess token. Got token: %s", token)
        user_token_repo = UserAccessTokenRepository(session=db_session)
        user_access_token = await user_token_repo.get_active_by_token(hash_string(token))
        if not user_access_token:
            raise AuthCredentialsInvalidError("Provided access token is unknown.")

        return ByTokenData(user_id=user_access_token.user_id)

    @staticmethod
    def _seems_like_user_access_token(token: str) -> bool:
        return len(token) == LENGTH_USER_ACCESS_TOKEN and len(token.split(".")) == 1

    async def _register_user_ip(self) -> None:
        """Best-effort IP history registration used by sign-in and profile requests."""
        # TODO: move to middleware?
        request, settings = self.connection, self.settings
        address = request.headers.get(settings.request_ip_header)
        if not address:
            client: Address = request.client or Address(settings.default_request_user_ip, port=0)
            address = client.host

        user = request.user
        hashed_address = hash_string(address or settings.default_request_user_ip)
        try:
            async with SASessionUOW() as uow:
                repository = UserIPRepository(uow.session)
                if await repository.first(user_id=user.id, hashed_address=hashed_address) is None:
                    await repository.create(
                        user_id=user.id,
                        hashed_address=hashed_address,
                        registered_by="",
                    )
                    uow.mark_for_commit()

        except Exception as exc:
            logger.exception("[API] Failed to register IP for user #%s (%r)", user.id, exc)


class APIAuthBackend(AuthBackend):
    """Header based authentication backend"""

    async def login(self, email: str, password: str) -> SuccessLoginData:
        async with SASessionUOW() as uow:
            user_repo = UserRepository(uow.session)
            user = await user_repo.get_by_email(email)

        if user is None or not user.is_active:
            raise AuthCredentialsInvalidError(details="Active user not found")

        if not user.verify_password(password):
            raise AuthCredentialsInvalidError(details="Unable to authenticate user")

        tokens = await self._create_user_session(user)
        await self._register_user_ip()
        return SuccessLoginData(user=user, tokens=tokens, cookie=None)

    async def _create_user_session(self, user: User) -> TokenCollection:
        session_id = str(uuid.uuid4())
        tokens = issue_token_pair(user_id=user.id, session_id=session_id, settings=self.settings)
        async with SASessionUOW() as uow:
            session_repo = UserSessionRepository(uow.session)
            await session_repo.create(
                public_id=session_id,
                user_id=user.id,
                refresh_token=tokens.refresh_token,
                is_active=True,
                expired_at=tokens.refresh_token_expired_at,
                created_at=utcnow(),
                refreshed_at=utcnow(),
            )
            uow.mark_for_commit()

        return tokens


class WebAuthBackend(AuthBackend):
    """Cookies + JWT based authentication backend"""

    async def authenticate(self) -> AuthenticatedUserResult:
        cookie_jwt = self.connection.cookies.get(self.settings.auth.session_cookie_name)
        if not cookie_jwt:
            raise AuthMissingCredentialsError("Auth: unable to resolve token from session cookie")

        async with SASessionUOW() as uow:
            auth_result = await self._authenticate_user(
                jwt_token=cookie_jwt,
                db_session=uow.session,
                token_type=AuthTokenType.COOKIE_ACCESS,
            )

        return auth_result

    async def login(self, email: str, password: str) -> SuccessLoginData:
        if not all([email, password]):
            raise AuthCredentialsInvalidError("Email or password is required.")

        async with SASessionUOW() as uow:
            user_repo = UserRepository(session=uow.session)
            user = await user_repo.get_by_email(email)
            if not user:
                raise AuthCredentialsInvalidError("Unable to find user with provided email")

            if not user.verify_password(password):
                raise AuthCredentialsInvalidError("Incorrect password")

            public_id = str(uuid.uuid4())
            now = utcnow()
            expired_at = now + timedelta(seconds=self.settings.auth.session_ttl_seconds)
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
            key=self.settings.auth.session_cookie_name,
            value=public_id,
            max_age=self.settings.auth.session_ttl_seconds,
            httponly=True,
            secure=self.settings.auth_cookie_secure_effective(),
            samesite="lax",
            path="/",
        )
        await self._register_user_ip()
        return SuccessLoginData(user=user, cookie=session_cookie)

    async def logout(self) -> Cookie:
        public_id = self.connection.cookies.get(self.settings.auth.session_cookie_name)
        if public_id:
            async with SASessionUOW() as uow:
                repo = UserSessionRepository(session=uow.session)
                await repo.deactivate_by_public_id(public_id)

        clear_cookie = Cookie(
            key=self.settings.auth.session_cookie_name,
            value="",
            max_age=0,
            httponly=True,
            secure=self.settings.auth_cookie_secure_effective(),
            samesite="lax",
            path="/",
        )
        return clear_cookie


#
# class LoginRequiredAuthBackend(BaseAuthBackend):
#     """Each request must have filled `user` attribute"""
#
#
# class AdminRequiredAuthBackend(BaseAuthBackend):
#     """Login-ed used must have `is_superuser` attribute"""
#
#     async def authenticate_user(
#         self,
#         jwt_token: str,
#         token_type: AuthTokenType = AuthTokenType.ACCESS,
#     ) -> tuple[User, dict | None, str | None]:
#         """
#         Authenticate user by jwt_token and check that current user is superuser
#
#         :param jwt_token: Currently detected JWT token
#         :param token_type: expected token's type (access or refresh)
#         """
#         user, jwt_payload, session_id = await super().authenticate_user(jwt_token)
#         if not user.is_superuser:
#             raise PermissionDeniedError("You don't have an admin privileges.")
#
#         return user, jwt_payload, session_id
