"""App-level before_request: resolve session user and redirect anonymous users to login."""

import re
from typing import cast

from litestar.connection import Request
from litestar.response import Redirect, Response

from src.settings.app import get_app_settings
from src.modules.api.errors import APIError, api_error_response
from src.modules.auth.load_user import attach_current_user, get_current_user_or_none
from src.modules.auth.tokens import authenticate_bearer_request

__all__ = ("browser_auth_gate",)

# Token URLs: capability links; must not return HTML login for <audio src="..."> or RSS clients.
_MEDIA_TOKEN_PATH = re.compile(r"^/(m|r)/[A-Za-z0-9_-]+/?$")
_PUBLIC_API_PATHS = {
    "/api/auth/sign-in",
    "/api/auth/sign-up",
    "/api/auth/refresh-token",
    "/api/auth/reset-password",
    "/api/auth/change-password",
    "/api/health",
    "/api/system/health",
}


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

    if path in _PUBLIC_API_PATHS:
        return True

    return False


async def browser_auth_gate(request: Request) -> Redirect | Response | None:
    """
    Load ``request.state.current_user`` then require a session for all other browser routes.

    Implemented as before_request (not a guard) so redirects do not raise HTTPException outside
    the route try/except and are not logged as uncaught errors.
    """
    await attach_current_user(request)
    if _is_auth_exempt(request):
        return None

    if get_current_user_or_none(request) is not None:
        return None

    if request.url.path.startswith("/api/"):
        settings = get_app_settings()
        if settings.flags.api_debug_mode:
            return None

        try:
            authenticated = await authenticate_bearer_request(request)
        except APIError as exc:
            return api_error_response(
                code=exc.code,
                message=exc.message,
                details=exc.details,
                status_code=cast(int, exc.status_code),
            )

        request.state.current_user = authenticated.user
        request.state.api_auth = authenticated
        return None

    return Redirect(path="/login")
