from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from starlette import types as st_types

logger = logging.getLogger(__name__)


class PathFixMiddleware:
    """Middleware for fixing the path in scope for transition b/w Litestar and Starlette.

    See: https://github.com/encode/starlette/issues/869

    If a route is registered with `Mount` on a Starlette app, it needs a trailing slash. However,
    paths registered with `Route` are not found if they have a trailing slash.

    SQLAdmin uses `Mount` to register the admin app, and the admin app contains `Route`s.

    Litestar forwards all paths without a leading forward slash, and with a trailing one.

    This middleware fixes the path in the scope to ensure that the path is set correctly for the
    admin app, depending on whether the request forwarded to the admin app is the base url of the
    admin app or not.
    """

    def __init__(self, app: st_types.ASGIApp, *, base_url: str) -> None:
        self.app = app
        self.base_url = base_url.rstrip("/")

    async def __call__(
        self, scope: st_types.Scope, receive: st_types.Receive, send: st_types.Send
    ) -> None:
        orig_path = scope["path"]
        orig_raw = scope["raw_path"]

        path = f"/{scope['path'].lstrip('/').rstrip('/')}"
        if path == self.base_url:
            path = f"{path}/"

        scope["path"] = path
        scope["raw_path"] = scope["path"].encode("utf-8")

        def reset_paths() -> None:
            """Restore the path values that the upstream application supplied."""
            scope["path"] = orig_path
            scope["raw_path"] = orig_raw

        async def send_wrapper(message: Any) -> None:
            """Restore the original path before passing an ASGI message downstream."""
            reset_paths()
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            reset_paths()
