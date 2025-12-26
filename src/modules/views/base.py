from operator import index

from litestar import get
from litestar.response import Template

from src import constants as const, settings

__all__ = (
    "index",
    "episodes",
    "progress",
    "profile",
    "about",
)


@get("/", include_in_schema=False)
async def index() -> Template:
    return Template(
        template_name="index.html",
        context={
            "podcasts": const.PODCASTS,
            "title": "Home",
            "current": "home",
            "navigation": const.NAVIGATION,
        },
    )


@get("/episodes", include_in_schema=False)
async def episodes() -> Template:
    return Template(
        template_name="episodes.html",
        context={
            "episodes": const.EPISODES,
            "title": "Episodes",
            "current": "episodes",
            "navigation": const.NAVIGATION,
        },
    )


@get("/progress", include_in_schema=False)
async def progress() -> Template:
    return Template(
        template_name="episodes.html",
        context={
            "episodes": const.EPISODES,
            "title": "Episodes in progress",
            "current": "progress",
            "navigation": const.NAVIGATION,
        },
    )


@get("/profile", include_in_schema=False)
async def profile() -> Template:
    return Template(
        template_name="profile.html",
        context={
            "title": "Profile",
            "current": "profile",
            "navigation": const.NAVIGATION,
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
            "navigation": const.NAVIGATION,
            "version": settings.APP_VERSION,
        },
    )
