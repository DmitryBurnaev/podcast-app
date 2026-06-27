from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any, cast

from litestar import asgi
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqladmin import Admin
from sqladmin.authentication import login_required
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import Response

from src.constants import AuthSkip
from src.modules.admin.auth import PodcastAdminAuth
from src.modules.admin.dashboard import collect_dashboard_stats
from src.modules.admin.views import ADMIN_VIEWS
from src.modules.db.session import get_session_factory
from src.settings.app import APP_DIR, AppSettings

type Message = MutableMapping[str, Any]
type Receive = Callable[[], Awaitable[Message]]
type Send = Callable[[Message], Awaitable[None]]
type Scope = MutableMapping[str, Any]


class PodcastSQLAdmin(Admin):
    """SQLAdmin application with a Podcast App dashboard."""

    @login_required
    async def index(self, request: Request) -> Response:
        session_maker = cast(async_sessionmaker[AsyncSession], self.session_maker)
        stats = await collect_dashboard_stats(session_maker)
        return await self.templates.TemplateResponse(
            request,
            "sqladmin/index.html",
            {
                "title": "Dashboard",
                "subtitle": "Podcast App administration",
                "stats": stats,
            },
        )


def build_admin_app(settings: AppSettings) -> Starlette:
    """Build the Starlette/SQLAdmin app mounted under Litestar."""
    starlette_app = Starlette()
    admin = PodcastSQLAdmin(
        app=starlette_app,
        session_maker=get_session_factory(),
        base_url="/",
        title=settings.admin_title,
        templates_dir=str(APP_DIR / "templates"),
        authentication_backend=PodcastAdminAuth(settings),
    )
    for view in ADMIN_VIEWS:
        admin.add_view(view)

    return starlette_app


def create_admin_route(settings: AppSettings):
    """Create a lazily initialized Litestar ASGI mount for SQLAdmin."""
    admin_app: Starlette | None = None

    async def handle_admin(scope: Scope, receive: Receive, send: Send) -> None:
        nonlocal admin_app
        if admin_app is None:
            admin_app = build_admin_app(settings)

        scope = dict(scope)
        scope["root_path"] = f"{scope.get('root_path', '')}{settings.admin_base_url.rstrip('/')}"
        if scope.get("path") != "/" and str(scope.get("path", "")).endswith("/"):
            scope["path"] = str(scope["path"]).rstrip("/")
        await admin_app(scope, receive, send)

    return asgi(
        settings.admin_base_url,
        is_mount=True,
        copy_scope=True,
        opt={AuthSkip.SKIP_AUTH_API: True, AuthSkip.SKIP_AUTH_WEB: True},
    )(handle_admin)
