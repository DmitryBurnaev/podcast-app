import logging

from litestar.connection import ASGIConnection
from litestar.middleware import AbstractAuthenticationMiddleware, AuthenticationResult

from src.exceptions import AuthenticationRequiredError

logger = logging.getLogger(__name__)


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
            logger.warning("Trying to authenticate with header %s", auth_header)
            raise AuthenticationFailedError("Invalid token header. Token should be format as JWT.")

        if auth[0] != self.keyword:
            raise AuthenticationFailedError("Invalid token header. Keyword mismatch.")

        user, _, session_id = await self.authenticate_user(jwt_token=auth[1])

        # retrieve the auth header
        auth_header = connection.headers.get(API_KEY_HEADER)
        if not auth_header:
            raise NotAuthorizedException()

        # this would be a database call
        token = MyToken(api_key=auth_header)
        user = MyUser(name=TOKEN_USER_DATABASE.get(token.api_key))
        if not user.name:
            raise NotAuthorizedException()

        return AuthenticationResult(user=user, auth=token)

    async def authenticate(self) -> tuple[User, str | None]:
        request = self.request
        auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
        if not auth_header:
            raise AuthenticationRequiredError("Invalid token header. No credentials provided.")

        auth = auth_header.split()
        if len(auth) != 2:
            logger.warning("Trying to authenticate with header %s", auth_header)
            raise AuthenticationFailedError("Invalid token header. Token should be format as JWT.")

        if auth[0] != self.keyword:
            raise AuthenticationFailedError("Invalid token header. Keyword mismatch.")

        user, _, session_id = await self.authenticate_user(jwt_token=auth[1])
        return user, session_id
