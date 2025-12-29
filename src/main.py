import logging
import logging.config

from litestar import Litestar
from litestar.template import TemplateConfig
from litestar.static_files import StaticFilesConfig
from litestar.contrib.jinja import JinjaTemplateEngine
from pathlib import Path

from src import settings
from src.modules.views import *


src_root = Path(__file__).parent
template_config = TemplateConfig(directory=src_root / "templates", engine=JinjaTemplateEngine)
static_files_config = StaticFilesConfig(path="/static", directories=[str(src_root / "static")])


async def startup():
    logging.config.dictConfig(settings.LOGGING)
    logger = logging.getLogger(__name__)
    logger.info("Starting up ...")


# Create Litestar application
app = Litestar(
    route_handlers=[index, episodes, podcasts, progress, about, profile],
    template_config=template_config,
    static_files_config=[static_files_config],
    on_startup=[startup],
    debug=True,
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
