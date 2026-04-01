"""Route guards for authentication."""

from litestar.connection import ASGIConnection
from litestar.exceptions import HTTPException
from litestar.handlers.base import BaseRouteHandler
from litestar.status_codes import HTTP_302_FOUND

__all__ = ("require_authenticated_user",)


async def require_authenticated_user(
    connection: ASGIConnection,
    _: BaseRouteHandler,
) -> None:
    """Redirect to /login when no user session (HTML routes)."""
    if getattr(connection.state, "current_user", None):
        return
    raise HTTPException(
        status_code=HTTP_302_FOUND,
        detail="",
        headers={"Location": "/login"},
    )
