"""App-level before_request: resolve session user and redirect anonymous users to login."""

import re

from litestar.connection import Request
from litestar.response import Redirect

from src.modules.auth.load_user import attach_current_user

__all__ = ("browser_auth_gate",)

# Token URLs: capability links; must not return HTML login for <audio src="..."> or RSS clients.
_MEDIA_TOKEN_PATH = re.compile(r"^/(m|r)/[A-Za-z0-9_-]+/?$")


def _is_auth_exempt(request: Request) -> bool:
    """Routes that must work without a session (and CORS preflight)."""
    if request.method == "OPTIONS":
        return True
    path = request.url.path.rstrip("/") or "/"
    if path == "/login" and request.method in ("GET", "HEAD", "POST"):
        return True
    if path == "/logout" and request.method == "POST":
        return True
    if request.method in ("GET", "HEAD") and _MEDIA_TOKEN_PATH.match(path):
        return True
    return False


async def browser_auth_gate(request: Request) -> Redirect | None:
    """
    Load ``request.state.current_user`` then require a session for all other browser routes.

    Implemented as before_request (not a guard) so redirects do not raise HTTPException outside
    the route try/except and are not logged as uncaught errors.
    """
    await attach_current_user(request)
    if _is_auth_exempt(request):
        return None
    if getattr(request.state, "current_user", None):
        return None
    return Redirect(path="/login")
