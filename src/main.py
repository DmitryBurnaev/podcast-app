import logging
import logging.config
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any, AsyncGenerator

import rq

import uvicorn
from litestar import Litestar
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.di import Provide
from litestar.exceptions import HTTPException, ValidationException
from litestar.middleware import DefineMiddleware
from litestar.static_files import StaticFilesConfig
from litestar.template import TemplateConfig

from redis import Redis

from src.constants import AuthSkip
from src.exceptions import BaseApplicationError, StartupError, StorageConfigurationError, APIError
from src.modules.auth.middlewares import APIAuthMiddleware, WebAuthMiddleware
from src.modules.auth.utils import provide_current_user
from src.modules.db import close_database, initialize_database, verify_database_reachable
from src.modules.services.redis import check_redis_connection, close_async_redis_connection
from src.modules.services.storage import validate_s3_settings
from src.modules.api import BaseApiController
from src.modules.api.errors import (
    api_error_handler,
    app_error_handler,
    http_error_handler,
    validation_error_handler,
)
from src.modules.views.base import BaseViewController
from src.settings.app import APP_DIR, AppSettings, get_app_settings

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

    def __init__(self, *args, settings: AppSettings, **kwargs) -> None:
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


def make_app(settings: AppSettings | None = None) -> PodcastApp:
    """Forming Application instance with required settings and dependencies"""
    app_settings: AppSettings = settings or get_app_settings()
    logging.config.dictConfig(app_settings.log.dict_config_any)
    logging.captureWarnings(capture=True)

    def provide_settings(_: Any) -> AppSettings:
        return app_settings

    logger.info("Setting up application...")
    podcast_app = PodcastApp(
        route_handlers=[
            *BaseApiController.get_controllers(),
            *BaseViewController.get_controllers(),
        ],
        middleware=[
            DefineMiddleware(APIAuthMiddleware, exclude_from_auth_key=AuthSkip.SKIP_AUTH_API),
            DefineMiddleware(WebAuthMiddleware, exclude_from_auth_key=AuthSkip.SKIP_AUTH_WEB),
        ],
        template_config=TemplateConfig(directory=APP_DIR / "templates", engine=JinjaTemplateEngine),
        static_files_config=[
            StaticFilesConfig(
                path="/static",
                directories=[str(APP_DIR / "static")],
                opt={
                    AuthSkip.SKIP_AUTH_API: True,
                    AuthSkip.SKIP_AUTH_WEB: True,
                },
            ),
        ],
        lifespan=[lambda: lifespan(app_settings)],
        debug=app_settings.flags.debug_mode,
        exception_handlers={
            APIError: api_error_handler,
            BaseApplicationError: app_error_handler,
            ValidationException: validation_error_handler,
            HTTPException: http_error_handler,
        },
        dependencies={
            "settings": Provide(provide_settings, sync_to_thread=False),
            "current_user": Provide(provide_current_user, sync_to_thread=False),
        },
        settings=app_settings,
    )

    # logger.info("Setting up routes...")
    # app.include_router(system_router, prefix="/api", dependencies=[Depends(verify_api_token)])
    # app.include_router(proxy_router, prefix="/api", dependencies=[Depends(verify_api_token)])

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
