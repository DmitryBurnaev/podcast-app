import logging
import logging.config
import sys
from contextlib import asynccontextmanager
from typing import Any, AsyncGenerator

# from litestar.middleware.session.server_side import (
#     ServerSideSessionBackend,
#     ServerSideSessionConfig,
# )
from litestar import Litestar
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.di import Provide
from litestar.static_files import StaticFilesConfig
from litestar.template import TemplateConfig

from src.exceptions import AppSettingsError, StartupError
from src.modules.db import close_database, initialize_database
from src.modules.views.base import BaseController
from src.settings.app import APP_DIR, AppSettings, get_app_settings

logger = logging.getLogger("app")


class PodcastApp(Litestar):
    """Podcast application instance"""

    def __init__(self, *args, settings: AppSettings, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.settings = settings

    def __str__(self) -> str:
        return f"PodcastApp #{id(self)}"


@asynccontextmanager
async def lifespan(podcast_app: PodcastApp) -> AsyncGenerator[None, Any]:
    """Application lifespan context manager for startup and shutdown events."""
    logger.info("Starting up %s ...", podcast_app)
    try:
        await initialize_database()
        logger.info("Application startup completed successfully")
    except Exception as exc:
        raise StartupError("Failed to initialize DB connection") from exc

    yield

    logger.info("===== shutdown ====")
    logger.info("Shutting down this application...")
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
        template_config=TemplateConfig(
            directory=APP_DIR / "templates", engine=JinjaTemplateEngine
        ),
        static_files_config=[
            StaticFilesConfig(path="/static", directories=[str(APP_DIR / "static")])
        ],
        lifespan=[lifespan],
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


app: PodcastApp = make_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "src.main:app",
        host=app.settings.app_host,
        port=app.settings.app_port,
        reload=app.settings.app_hot_reload,
        log_config=app.settings.log.dict_config_any,
        proxy_headers=True,
    )
