from litestar import get, Request
from litestar.response import Template, Redirect

from src import constants as const

__all__ = (
    "index",
    "episodes",
    "podcasts",
    "progress",
    "profile",
    "about",
)

from src.settings import AppSettings

# Default user data for UI
DEFAULT_USER_DATA = {
    "name": "Test User",
    "avatar": None,  # Can be extended with avatar URL later
}


@get("/", include_in_schema=False)
async def index() -> Template:
    stats = const.get_stats()
    recent_episodes = const.get_recent_episodes(limit=5)

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
            "get_episode_status_color": const.get_episode_status_color,
            "get_episode_status_label": const.get_episode_status_label,
        },
    )


@get("/episodes", include_in_schema=False)
async def episodes(request: Request) -> Template:
    # Get filter parameters from query string
    query_params = request.query_params
    filters = {
        "status": query_params.get("status"),
        "size_min": query_params.get("size_min"),
        "size_max": query_params.get("size_max"),
        "podcast": query_params.get("podcast"),
        "search": query_params.get("search"),
    }

    # Remove None values
    filters = {k: v for k, v in filters.items() if v is not None}

    # Apply filters
    filtered_episodes = (
        const.filter_episodes(const.EPISODES, filters) if filters else const.EPISODES
    )

    return Template(
        template_name="episodes.html",
        context={
            "episodes": filtered_episodes,
            "podcasts": const.PODCASTS,
            "filters": filters,
            "current": "episodes",
            "navigation": const.NAVIGATION,
            "user_data": DEFAULT_USER_DATA,
            "format_duration": const.format_duration,
            "format_file_size": const.format_file_size,
            "get_episode_status_color": const.get_episode_status_color,
            "get_episode_status_label": const.get_episode_status_label,
        },
    )


@get("/podcasts", include_in_schema=False)
async def podcasts() -> Template:
    return Template(
        template_name="podcasts.html",
        context={
            "podcasts": const.PODCASTS,
            "current": "podcasts",
            "navigation": const.NAVIGATION,
            "user_data": DEFAULT_USER_DATA,
            "format_duration": const.format_duration,
            "format_file_size": const.format_file_size,
            "get_episode_status_color": const.get_episode_status_color,
            "get_episode_status_label": const.get_episode_status_label,
        },
    )


@get("/progress", include_in_schema=False)
async def progress() -> Redirect:
    """Redirect to episodes page with downloading filter."""
    return Redirect(path="/episodes?status=downloading")


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
async def about(app_settings: AppSettings) -> Template:
    return Template(
        template_name="about.html",
        context={
            "title": "About",
            "current": "about",
            "navigation": const.NAVIGATION,
            "version": app_settings.app_version,
            "user_data": DEFAULT_USER_DATA,
        },
    )
