import logging
import logging.config
import sys
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any, AsyncGenerator

import rq

# from litestar.middleware.session.server_side import (
#     ServerSideSessionBackend,
#     ServerSideSessionConfig,
# )
import uvicorn
from litestar import Litestar
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.di import Provide
from litestar.static_files import StaticFilesConfig
from litestar.template import TemplateConfig

# from litestar.middleware.session.memory_backend import MemoryBackendConfig
from redis import Redis

from src.exceptions import AppSettingsError, StartupError, StorageConfigurationError
from src.modules.db import close_database, initialize_database, verify_database_reachable
from src.modules.services.redis import check_redis_connection
from src.modules.services.storage import validate_s3_settings
from src.modules.auth.before_request import browser_auth_gate
from src.modules.views.base import BaseController
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

    logger.info("=====")


def make_app(settings: AppSettings | None = None) -> PodcastApp:
    """Forming Application instance with required settings and dependencies"""

    if settings is None:
        try:
            settings = get_app_settings()
        except AppSettingsError as exc:
            logger.error("Unable to get settings from environment: %r", exc)
            sys.exit(1)

    logging.config.dictConfig(settings.log.dict_config_any)
    logging.captureWarnings(capture=True)

    logger.info("Setting up application...")
    podcast_app = PodcastApp(
        route_handlers=[
            *BaseController.get_controllers(),
        ],
        before_request=browser_auth_gate,
        template_config=TemplateConfig(directory=APP_DIR / "templates", engine=JinjaTemplateEngine),
        static_files_config=[
            StaticFilesConfig(path="/static", directories=[str(APP_DIR / "static")])
        ],
        lifespan=[lambda _: lifespan(settings)],
        debug=settings.flags.debug_mode,
        dependencies={
            "settings": Provide(get_app_settings, sync_to_thread=False),
        },
        settings=settings,
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
