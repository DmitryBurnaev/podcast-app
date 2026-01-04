import logging
import logging.config
import sys

from litestar import Litestar
from litestar.template import TemplateConfig
from litestar.static_files import StaticFilesConfig
from litestar.contrib.jinja import JinjaTemplateEngine

from src import settings
from src.exceptions import AppSettingsError
from src.modules.views import *
from src.settings import AppSettings, get_app_settings
from src.settings.app import APP_DIR

logger = logging.getLogger(__name__)


async def startup():
    logging.config.dictConfig(settings.LOGGING)
    logger = logging.getLogger(__name__)
    logger.info("Starting up ...")


#
#
# def create_app() -> Litestar:
#     """Simple app creation"""
#     src_root: Path = Path(__file__).parent
#     app = Litestar(
#         route_handlers=[index, episodes, podcasts, progress, about, profile],
#         template_config=TemplateConfig(
#             directory=src_root / "templates", engine=JinjaTemplateEngine
#         ),
#         static_files_config=[
#             StaticFilesConfig(path="/static", directories=[str(src_root / "static")])
#         ],
#         on_startup=[startup],
#         debug=True,
#     )
#     return app
#

# class PodcastApp(Litestar): ...


def make_app(settings: AppSettings | None = None) -> Litestar:
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
    app = Litestar(
        route_handlers=[index, episodes, podcasts, progress, about, profile],
        template_config=TemplateConfig(directory=APP_DIR / "templates", engine=JinjaTemplateEngine),
        static_files_config=[
            StaticFilesConfig(path="/static", directories=[str(APP_DIR / "static")])
        ],
        on_startup=[startup],
        debug=True,
    )

    logger.info("Setting up routes...")
    # app.include_router(system_router, prefix="/api", dependencies=[Depends(verify_api_token)])
    # app.include_router(proxy_router, prefix="/api", dependencies=[Depends(verify_api_token)])

    logger.info("Application configured!")
    return app


app: Litestar = make_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
