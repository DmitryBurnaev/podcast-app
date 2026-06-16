import logging
from typing import Any, Optional, cast

from litestar.connection import ASGIConnection, Request
from litestar.middleware import AbstractAuthenticationMiddleware, AuthenticationResult

from src.modules.auth.backend import WebAuthBackend, APIAuthBackend

# from src.modules.auth.tokens import authenticate_bearer_request
# from src.settings.app import get_app_settings

logger = logging.getLogger(__name__)
type SessionPayloadT = Optional[dict[str, Any]]


# def _set_request_state(connection: ASGIConnection, **values: Any) -> None:
#     state = connection.scope.setdefault("state", {})
#     state.update(values)


class APIAuthenticationMiddleware(AbstractAuthenticationMiddleware):

    async def authenticate_request(self, connection: ASGIConnection) -> AuthenticationResult:
        """
        Given a request, parse the request api key stored in the header and retrieve
        the user correlating to the token from the DB
        """
        auth_backend = APIAuthBackend(connection=connection, header_keyword="Bearer")
        user, session_id = await auth_backend.authenticate()
        return AuthenticationResult(user=user, auth=session_id)


#         authenticated = await authenticate_bearer_request(
#             cast(Request, connection),
#             settings=get_app_settings(),
#         )
#         _set_request_state(
#             connection,
#             current_user=authenticated.user,
#             api_auth=authenticated,
#         )
#         return AuthenticationResult(user=authenticated.user, auth=authenticated.session_id)


class WebAppAuthenticationMiddleware(AbstractAuthenticationMiddleware):

    async def authenticate_request(self, connection: ASGIConnection) -> AuthenticationResult:
        """
        Given a request, parse the request api key stored in the header and retrieve
        the user correlating to the token from the DB
        """
        auth_backend = WebAuthBackend(connection=connection)
        user, session_id = await auth_backend.authenticate()
        # _set_request_state(connection, current_user=user)
        return AuthenticationResult(user=user, auth=session_id)


#
# class RegularAPIAuthMiddleware(APIAuthenticationMiddleware): ...
#
#
# class AdminAPIAuthMiddleware(APIAuthenticationMiddleware):
#     """Regular auth middleware but requires superuser privileges"""
#
#     async def authenticate_request(self, connection: ASGIConnection) -> AuthenticationResult:
#         result = await super().authenticate_request(connection)
#         if not result.user.is_superuser:
#             raise AuthenticationFailedError(details="User is not a superuser")
#
#         return result

#
# async def _authenticate_user(
#     self,
#     jwt_token: str,
#     db_session: AsyncSession,
#     token_type: AuthTokenType = AuthTokenType.ACCESS,
# ) -> _AuthUserResult:
#     """Allows to find active user by jwt_token"""
#
#     if self._seems_like_user_access_token(jwt_token):
#         by_token_data = await self._encode_user_access_token(db_session, jwt_token)
#         token_type = AuthTokenType.USER_ACCESS
#     else:
#         by_token_data = self._encode_jwt(jwt_token, token_type)
#
#     user_id = by_token_data.user_id
#     user_repo = UserRepository(session=db_session)
#     user = await user_repo.first(id=user_id, is_active=True)
#     if not user:
#         msg = "Couldn't found active user with id=%s."
#         logger.warning(msg, user_id)
#         raise AuthenticationFailedError(details=(msg % (user_id,)))
#
#     if token_type in (AuthTokenType.RESET_PASSWORD, AuthTokenType.USER_ACCESS):
#         return _AuthUserResult(user=user, payload=by_token_data.payload, session_id=None)
#
#     session_id = by_token_data.session_id
#     if not session_id:
#         raise AuthenticationFailedError("Incorrect data in JWT: session_id is missed")
#
#     user_session_repo = AuthUserSessionRepository(session=db_session)
#     user_session = await user_session_repo.get_active_by_public_id(session_id)
#     if not user_session:
#         raise AuthenticationFailedError(
#             f"Couldn't found active session: {user_id=} | {session_id=}."
#         )
#
#     return _AuthUserResult(user=user, payload=by_token_data.payload, session_id=session_id)
#
# @staticmethod
# def _encode_jwt(token: str, token_type: AuthTokenType) -> ByTokenData:
#     """
#     Encodes given JWT token and extract stored data in a JWT payload
#
#     :param token: JWT token
#     :return: ByTokenData instance (stores token-specific info)
#     """
#     logger.debug("Logging via JWT auth. Got token: %s", token)
#     try:
#         jwt_payload = decode_jwt(token)
#     except ExpiredSignatureError as exc:
#         logger.debug("JWT signature has been expired for %s token", token_type)
#         exception_class = (
#             SignatureExpiredError
#             if token_type == AuthTokenType.ACCESS
#             else AuthenticationFailedError
#         )
#         raise exception_class("JWT signature has been expired for token") from exc
#
#     except InvalidTokenError as exc:
#         msg = "Token could not be decoded: %s"
#         logger.exception(msg, exc)
#         raise AuthenticationFailedError(msg % (exc,)) from exc
#
#     expected_token_type = str(token_type).lower()
#     if jwt_payload["token_type"].lower() != expected_token_type:
#         raise AuthenticationFailedError(
#             f"Token type '{expected_token_type}' expected, "
#             f"got '{jwt_payload['token_type'].lower()}' instead."
#         )
#
#     session_id: str | None = jwt_payload.get("session_id")
#     if not session_id:
#         raise AuthenticationFailedError("Incorrect data in JWT: session_id is missed")
#
#     return ByTokenData(
#         user_id=jwt_payload["user_id"],
#         payload=jwt_payload,
#         session_id=session_id,
#     )
#
# @staticmethod
# async def _encode_user_access_token(db_session: AsyncSession, token: str) -> ByTokenData:
#     """
#     Finds active UserAccessToken instance by provided token
#
#     :param token: access token (will be hashed for finding stored in DB)
#     :return: ByTokenData instance (stores token-specific info)
#     """
#     logger.debug("Logging via UserAccess token. Got token: %s", token)
#     user_token_repo = UserAccessTokenRepository(session=db_session)
#     user_access_token = await user_token_repo.get_active_by_token(hash_string(token))
#     if not user_access_token:
#         raise AuthenticationFailedError("Provided access token is unknown.")
#
#     return ByTokenData(user_id=user_access_token.user_id)
#
# @staticmethod
# def _seems_like_user_access_token(token: str) -> bool:
#     return len(token) == LENGTH_USER_ACCESS_TOKEN and len(token.split(".")) == 1
