import logging
from typing import NamedTuple, Any, Optional

from jwt import ExpiredSignatureError, InvalidTokenError
from litestar.connection import ASGIConnection
from litestar.middleware import AbstractAuthenticationMiddleware, AuthenticationResult
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.auth.constants import AuthTokenType, LENGTH_USER_ACCESS_TOKEN
from src.modules.auth.tokens import decode_jwt
from src.modules.db import SASessionUOW, User
from src.modules.db.repositories import (
    UserAccessTokenRepository,
    UserRepository,
    AuthUserSessionRepository,
)
from src.exceptions import (
    AuthenticationRequiredError,
    AuthenticationFailedError,
    SignatureExpiredError,
)
from src.utils import hash_string

logger = logging.getLogger(__name__)
type SessionPayloadT = Optional[dict[str, Any]]


class _AuthUserResult(NamedTuple):
    user: User
    payload: SessionPayloadT
    session_id: str | None


class ByTokenData(NamedTuple):
    user_id: int
    session_id: str = ""
    payload: SessionPayloadT = None


class APIAuthenticationMiddleware(AbstractAuthenticationMiddleware):
    keyword = "Bearer"

    async def authenticate_request(self, connection: ASGIConnection) -> AuthenticationResult:
        """
        Given a request, parse the request api key stored in the header and retrieve
        the user correlating to the token from the DB
        """
        header = connection.headers.get("Authorization") or connection.headers.get("authorization")
        if not header:
            raise AuthenticationRequiredError("Invalid token header. No credentials provided.")

        auth = header.split()
        if len(auth) != 2:
            logger.warning("Trying to authenticate with header %s", header)
            raise AuthenticationFailedError("Invalid token header. Token should be format as JWT.")

        if auth[0] != self.keyword:
            raise AuthenticationFailedError("Invalid token header. Keyword mismatch.")

        async with SASessionUOW() as uow:
            auth_result = await self._authenticate_user(
                jwt_token=auth[1],
                db_session=uow.session,
                token_type=AuthTokenType.USER_ACCESS,
            )

        return AuthenticationResult(user=auth_result.user, auth=auth_result.session_id)

    async def _authenticate_user(
        self,
        jwt_token: str,
        db_session: AsyncSession,
        token_type: AuthTokenType = AuthTokenType.ACCESS,
    ) -> _AuthUserResult:
        """Allows to find active user by jwt_token"""

        if self._seems_like_user_access_token(jwt_token):
            by_token_data = await self._encode_user_access_token(db_session, jwt_token)
            token_type = AuthTokenType.USER_ACCESS
        else:
            by_token_data = self._encode_jwt(jwt_token, token_type)

        user_id = by_token_data.user_id
        user_repo = UserRepository(session=db_session)
        user = await user_repo.first(id=user_id, is_active=True)
        if not user:
            msg = "Couldn't found active user with id=%s."
            logger.warning(msg, user_id)
            raise AuthenticationFailedError(details=(msg % (user_id,)))

        if token_type in (AuthTokenType.RESET_PASSWORD, AuthTokenType.USER_ACCESS):
            return _AuthUserResult(user=user, payload=by_token_data.payload, session_id=None)

        session_id = by_token_data.session_id
        if not session_id:
            raise AuthenticationFailedError("Incorrect data in JWT: session_id is missed")

        user_session_repo = AuthUserSessionRepository(session=db_session)
        user_session = await user_session_repo.get_active_by_public_id(session_id)
        if not user_session:
            raise AuthenticationFailedError(
                f"Couldn't found active session: {user_id=} | {session_id=}."
            )

        return _AuthUserResult(user=user, payload=by_token_data.payload, session_id=session_id)

    @staticmethod
    def _encode_jwt(token: str, token_type: AuthTokenType) -> ByTokenData:
        """
        Encodes given JWT token and extract stored data in a JWT payload

        :param token: JWT token
        :return: ByTokenData instance (stores token-specific info)
        """
        logger.debug("Logging via JWT auth. Got token: %s", token)
        try:
            jwt_payload = decode_jwt(token)
        except ExpiredSignatureError as exc:
            logger.debug("JWT signature has been expired for %s token", token_type)
            exception_class = (
                SignatureExpiredError
                if token_type == AuthTokenType.ACCESS
                else AuthenticationFailedError
            )
            raise exception_class("JWT signature has been expired for token") from exc

        except InvalidTokenError as exc:
            msg = "Token could not be decoded: %s"
            logger.exception(msg, exc)
            raise AuthenticationFailedError(msg % (exc,)) from exc

        expected_token_type = str(token_type).lower()
        if jwt_payload["token_type"].lower() != expected_token_type:
            raise AuthenticationFailedError(
                f"Token type '{expected_token_type}' expected, "
                f"got '{jwt_payload['token_type'].lower()}' instead."
            )

        session_id: str | None = jwt_payload.get("session_id")
        if not session_id:
            raise AuthenticationFailedError("Incorrect data in JWT: session_id is missed")

        return ByTokenData(
            user_id=jwt_payload["user_id"],
            payload=jwt_payload,
            session_id=session_id,
        )

    @staticmethod
    async def _encode_user_access_token(db_session: AsyncSession, token: str) -> ByTokenData:
        """
        Finds active UserAccessToken instance by provided token

        :param token: access token (will be hashed for finding stored in DB)
        :return: ByTokenData instance (stores token-specific info)
        """
        logger.debug("Logging via UserAccess token. Got token: %s", token)
        user_token_repo = UserAccessTokenRepository(session=db_session)
        user_access_token = await user_token_repo.get_active_by_token(hash_string(token))
        if not user_access_token:
            raise AuthenticationFailedError("Provided access token is unknown.")

        return ByTokenData(user_id=user_access_token.user_id)

    @staticmethod
    def _seems_like_user_access_token(token: str) -> bool:
        return len(token) == LENGTH_USER_ACCESS_TOKEN and len(token.split(".")) == 1


class RegularAPIAuthMiddleware(APIAuthenticationMiddleware): ...


class AdminAPIAuthMiddleware(APIAuthenticationMiddleware):
    """Regular auth middleware but requires superuser privileges"""

    async def authenticate_request(self, connection: ASGIConnection) -> AuthenticationResult:
        result = await super().authenticate_request(connection)
        if not result.user.is_superuser:
            raise AuthenticationFailedError(details="User is not a superuser")

        return result
