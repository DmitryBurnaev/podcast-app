import logging
import logging.config

import jinja2
from litestar import Litestar
from litestar.template import TemplateConfig
from litestar.static_files import StaticFilesConfig
from litestar.contrib.jinja import JinjaTemplateEngine
from pathlib import Path

from src import settings
from src.modules.views import *


async def startup():
    logging.config.dictConfig(settings.LOGGING)
    logger = logging.getLogger(__name__)
    logger.info("Starting up ...")


def create_app() -> Litestar:
    """Simple app creation"""
    src_root: Path = Path(__file__).parent
    app = Litestar(
        route_handlers=[index, episodes, podcasts, progress, about, profile],
        template_config=TemplateConfig(
            directory=src_root / "templates", engine=JinjaTemplateEngine
        ),
        static_files_config=[
            StaticFilesConfig(path="/static", directories=[str(src_root / "static")])
        ],
        on_startup=[startup],
        debug=True,
    )
    return app


app: Litestar = create_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
