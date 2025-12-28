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

# Default user data for UI
DEFAULT_USER_DATA = {
    "name": "Test User",
    "avatar": None,  # Can be extended with avatar URL later
}


@get("/", include_in_schema=False)
async def index() -> Template:
    stats = const.get_stats()
    recent_episodes = const.get_recent_episodes(limit=10)

    return Template(
        template_name="index.html",
        context={
            "podcasts": const.PODCASTS,
            "stats": stats,
            "recent_episodes": recent_episodes,
            "title": "Home",
            "current": "home",
            "navigation": const.NAVIGATION,
            "user_data": DEFAULT_USER_DATA,
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
            "user_data": DEFAULT_USER_DATA,
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
            "user_data": DEFAULT_USER_DATA,
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
            "user_data": DEFAULT_USER_DATA,
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
            "user_data": DEFAULT_USER_DATA,
        },
    )
