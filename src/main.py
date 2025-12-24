import logging

from litestar import Litestar, get
from litestar.template import TemplateConfig
from litestar.static_files import StaticFilesConfig
from litestar.contrib.jinja import JinjaTemplateEngine
from litestar.response import Template
from pathlib import Path

from src import settings
from src.constants import NAVIGATION, PODCASTS, EPISODES


@get("/", include_in_schema=False)
async def index() -> Template:
    return Template(
        template_name="index.html",
        context={
            "podcasts": PODCASTS,
            "title": "Home",
            "current": "home",
            "navigation": NAVIGATION,
        },
    )


@get("/episodes", include_in_schema=False)
async def episodes() -> Template:
    return Template(
        template_name="episodes.html",
        context={
            "episodes": EPISODES,
            "title": "Episodes",
            "current": "episodes",
            "navigation": NAVIGATION,
        },
    )


@get("/progress", include_in_schema=False)
async def progress() -> Template:
    return Template(
        template_name="episodes.html",
        context={
            "episodes": EPISODES,
            "title": "Episodes in progress",
            "current": "progress",
            "navigation": NAVIGATION,
        },
    )


@get("/profile", include_in_schema=False)
async def profile() -> Template:
    return Template(
        template_name="profile.html",
        context={
            "title": "Profile",
            "current": "profile",
            "navigation": NAVIGATION,
            "current_user": "Test User",
        },
    )


@get("/about", include_in_schema=False)
async def about() -> Template:
    return Template(
        template_name="about.html",
        context={
            "title": "About",
            "current": "episodes",
            "navigation": NAVIGATION,
            "version": "1.0",
        },
    )


src_root = Path(__file__).parent

# Configure template engine
template_config = TemplateConfig(
    directory=src_root / "templates",
    engine=JinjaTemplateEngine,
)

# Configure static files
# Get project root directory (parent of app directory)
static_files_config = StaticFilesConfig(
    path="/static",
    directories=[str(src_root / "static")],
)


async def startup():
    logging.config.dictConfig(settings.LOGGING)


# Create Litestar application
app = Litestar(
    route_handlers=[index, episodes, progress, about, profile],
    template_config=template_config,
    static_files_config=[static_files_config],
    on_startup=[startup],
)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
