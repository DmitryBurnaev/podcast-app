import logging
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any, AsyncGenerator, TYPE_CHECKING

import rq
import uvicorn
from litestar import Litestar, Request
from litestar.di import Provide
from litestar.exceptions import HTTPException, ValidationException
from litestar.logging import LoggingConfig
from litestar.middleware import DefineMiddleware
from litestar.openapi import OpenAPIConfig
from litestar.openapi.plugins import SwaggerRenderPlugin
from litestar.plugins.jinja import JinjaTemplateEngine
from litestar.router import Router
from litestar.static_files import create_static_files_router
from litestar.template import TemplateConfig
from redis import Redis

from src.modules.common.constants import AuthSkip
from src.modules.common.exceptions import (
    BaseApplicationError,
    StartupError,
    StorageConfigurationError,
    APIError,
    AuthMissingCredentialsError,
    exception_logging_handler,
)
from src.modules.admin.app import make_admin
from src.modules.auth.middlewares import APIAuthMiddleware, WebAuthMiddleware
from src.modules.db import close_database, initialize_database, verify_database_reachable
from src.modules.services.redis import check_redis_connection, close_async_redis_connection
from src.modules.services.storage import validate_s3_settings
from src.modules.api import API_CONTROLLERS
from src.modules.api.errors import (
    api_error_handler,
    app_error_handler,
    http_error_handler,
    validation_error_handler,
    http_redirect_handler,
)
from src.modules.views import VIEW_CONTROLLERS
from src.modules.common.types import OwnerScope
from src.settings.app import APP_DIR, AppSettings, get_app_settings

if TYPE_CHECKING:
    pass

logger = logging.getLogger("app")


class DbStartMode(StrEnum):
    """How lifespan sets up SQLAlchemy async DB before serving / before RQ work loop."""

    INIT = "init"
    VERIFY = "verify"


_DB_STARTUP_CHECKS: dict[DbStartMode, Callable[[], Awaitable[None]]] = {
    DbStartMode.INIT: initialize_database,
    DbStartMode.VERIFY: verify_database_reachable,
}


class PodcastApp(Litestar):
    """Podcast application instance"""

    rq_queue: rq.Queue
    settings: AppSettings

    def __init__(self, *args: Any, settings: AppSettings, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.settings = settings
        self.rq_queue = rq.Queue(
            name=settings.rq_queue_name,
            connection=Redis(*settings.redis.connection_tuple),
            default_timeout=settings.rq_default_timeout,
        )

    def __str__(self) -> str:
        return f"PodcastApp #{id(self)}"


@asynccontextmanager
async def lifespan(
    settings: AppSettings,
    app: "PodcastApp | None" = None,
    start_msg_suffix: str = "",
    *,
    db_start_mode: DbStartMode = DbStartMode.INIT,
) -> AsyncGenerator[None, Any]:
    """Application lifespan context manager for startup and shutdown events."""
    logger.info("Starting up %s...", start_msg_suffix or "PodcastApp")
    db_startup_check = _DB_STARTUP_CHECKS[db_start_mode]
    try:
        await db_startup_check()
    except Exception as exc:
        raise StartupError("Failed to initialize DB connection") from exc

    try:
        validate_s3_settings(settings.s3)
    except StorageConfigurationError as exc:
        logger.error("Failed to validate S3 settings: %s", exc)
        raise StartupError(details=str(exc.details or exc)) from exc

    try:
        await check_redis_connection()
    except Exception as exc:
        raise StartupError("Failed to initialize Redis connection") from exc

    if app is not None:
        logger.info("Setting up admin application...")
        make_admin(app)

    logger.info("Application startup completed successfully")

    yield

    logger.info("===== shutdown ====")
    logger.info("Shutting down this application...")
    if db_start_mode is DbStartMode.INIT:
        try:
            await close_database()
        except Exception as exc:
            logger.error("Error during application shutdown: %r", exc)
        else:
            logger.info("Application shutdown completed successfully")

    try:
        await close_async_redis_connection()
    except Exception as exc:
        logger.debug("Async Redis shutdown: %r", exc)

    logger.info("=====")


def provide_current_user(request: Request):
    """
    Simple dependency for getting current user from request object
    Provides by `src.modules.auth.middlewares.APIAuthMiddleware.authenticate_request`
    """
    return request.user


def provide_current_user_scope(request: Request) -> OwnerScope:
    """
    Simple dependency for getting current user's scope for DB's repositories
    """
    return OwnerScope(user_id=request.user.id)


def make_app(settings: AppSettings | None = None) -> PodcastApp:
    """Forming Application instance with required settings and dependencies"""
    app_settings: AppSettings = settings or get_app_settings()

    def provide_settings() -> AppSettings:
        return app_settings

    logger.info("Preparing configs for application...")
    logging_config = LoggingConfig(
        root={"level": "INFO", "handlers": [app_settings.log.default_handler]},
        formatters=app_settings.log.dict_config["formatters"],
        exception_logging_handler=exception_logging_handler,
        log_exceptions="always",
    )
    static_files_router = create_static_files_router(
        path="/static",
        directories=[str(APP_DIR / "static")],
        opt={
            AuthSkip.SKIP_AUTH_API: True,
            AuthSkip.SKIP_AUTH_WEB: True,
        },
    )
    openapi_config = OpenAPIConfig(
        path="/api/schema/",
        title="Podcast API",
        version=app_settings.app_version,
        description="CRUD and functional API for working with Podcast application",
        render_plugins=[
            SwaggerRenderPlugin(
                path="/",
                css_url="/static/css/swagger-ui.css",
                js_url="/static/js/swagger-ui-bundle.js",
                standalone_preset_js_url="/static/js/swagger-ui-standalone-preset.js",
                favicon="<link rel='icon' href='/static/img/favicon.ico'>",
            )
        ],
        openapi_router=Router(
            path="/api/schema/",
            route_handlers=[],
            include_in_schema=False,
            opt={
                AuthSkip.SKIP_AUTH_API: True,
                AuthSkip.SKIP_AUTH_WEB: True,
            },
        ),
    )

    logger.info("Setting up application...")
    podcast_app = PodcastApp(
        middleware=[
            DefineMiddleware(APIAuthMiddleware, exclude_from_auth_key=AuthSkip.SKIP_AUTH_API),
            DefineMiddleware(WebAuthMiddleware, exclude_from_auth_key=AuthSkip.SKIP_AUTH_WEB),
        ],
        template_config=TemplateConfig(directory=APP_DIR / "templates", engine=JinjaTemplateEngine),
        route_handlers=[
            *API_CONTROLLERS,
            *VIEW_CONTROLLERS,
            static_files_router,
        ],
        openapi_config=openapi_config,
        lifespan=[lambda app: lifespan(app_settings, app)],
        debug=app_settings.flags.debug_mode,
        logging_config=logging_config,
        exception_handlers={
            APIError: api_error_handler,
            BaseApplicationError: app_error_handler,
            ValidationException: validation_error_handler,
            HTTPException: http_error_handler,
            AuthMissingCredentialsError: http_redirect_handler,
        },
        dependencies={
            "settings": Provide(provide_settings, sync_to_thread=False),
            "current_user": Provide(provide_current_user, sync_to_thread=False),
            "user_scope": Provide(provide_current_user_scope, sync_to_thread=False),
        },
        settings=app_settings,
    )
    logger.info("Application configured!")
    return podcast_app


if __name__ == "__main__":
    app: PodcastApp = make_app()
    uvicorn.run(
        app=("src.main:make_app" if app.settings.app_hot_reload else app),
        host=app.settings.app_host,
        port=app.settings.app_port,
        reload=app.settings.app_hot_reload,
        log_config=app.settings.log.dict_config_any,
        proxy_headers=True,
    )
