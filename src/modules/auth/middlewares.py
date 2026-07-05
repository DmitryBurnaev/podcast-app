import logging
from typing import Any, Optional

from litestar.connection import ASGIConnection
from litestar.middleware import AbstractAuthenticationMiddleware, AuthenticationResult

from src.modules.auth.backends import WebAuthBackend, APIAuthBackend

logger = logging.getLogger(__name__)
type SessionPayloadT = Optional[dict[str, Any]]


class APIAuthMiddleware(AbstractAuthenticationMiddleware):

    async def authenticate_request(self, connection: ASGIConnection) -> AuthenticationResult:
        """
        Given a request, parse the request api key stored in the header and retrieve
        the user correlating to the token from the DB
        """
        auth_backend = APIAuthBackend(connection=connection, header_keyword="Bearer")
        auth_result = await auth_backend.authenticate()
        return AuthenticationResult(user=auth_result.user, auth=auth_result.token_data)


class WebAuthMiddleware(AbstractAuthenticationMiddleware):

    async def authenticate_request(self, connection: ASGIConnection) -> AuthenticationResult:
        """
        Given a request, parse the request api key stored in the header and retrieve
        the user correlating to the token from the DB
        """
        auth_backend = WebAuthBackend(connection=connection)
        auth_result = await auth_backend.authenticate()
        return AuthenticationResult(user=auth_result.user, auth=auth_result.token_data)
