from urllib.parse import urlsplit

from litestar import Controller
from litestar.connection import ASGIConnection
from litestar.handlers import BaseRouteHandler

from src.modules.common.constants import AuthSkip
from src.modules.common.exceptions import ForbiddenAPIError


def guard_browser_mutation(connection: ASGIConnection, _: BaseRouteHandler) -> None:
    """Cookie mutations require an explicit same-origin AJAX request."""
    if connection.scope.get("method") in {"GET", "HEAD", "OPTIONS"}:
        return
    if connection.headers.get("X-Requested-With") != "XMLHttpRequest":
        raise ForbiddenAPIError(details="An AJAX request is required.")
    origin = connection.headers.get("Origin")
    if origin is None or origin == "null":
        raise ForbiddenAPIError(details="A same-origin request is required.")
    try:
        parsed = urlsplit(origin)
    except ValueError as exc:
        raise ForbiddenAPIError(details="A same-origin request is required.") from exc
    expected = urlsplit(str(connection.base_url))
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or (parsed.scheme, parsed.netloc) != (expected.scheme, expected.netloc)
    ):
        raise ForbiddenAPIError(details="A same-origin request is required.")


class WebJSONController(Controller):
    include_in_schema = False
    opt = {AuthSkip.SKIP_AUTH_API: True}
    guards = [guard_browser_mutation]
