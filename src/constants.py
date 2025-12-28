import json
import os
from pathlib import Path
from typing import NamedTuple


class NavigationItem(NamedTuple):
    title: str
    icon: str
    path: str
    slug: str


NAVIGATION: tuple[NavigationItem, ...] = (
    NavigationItem(
        title="Home",
        icon="🏠",
        path="/",
        slug="home",
    ),
    NavigationItem(
        title="Episodes",
        icon="☰",
        path="/episodes",
        slug="episodes",
    ),
    NavigationItem(
        title="Progress",
        icon="🏃",
        path="/progress",
        slug="progress",
    ),
    NavigationItem(
        title="My Profile",
        icon="👤",
        path="/profile",
        slug="profile",
    ),
    NavigationItem(
        title="About",
        icon="ℹ",
        path="/about",
        slug="about",
    ),
)


def read_from_fixture(filename: str) -> list[dict[str, str]]:
    filepath = Path(os.path.dirname(__file__)).parent / ".local" / "fixtures" / filename
    return json.loads(filepath.read_text())


# Sample podcast data for UI demonstration
PODCASTS = read_from_fixture("podcasts.json")

# Sample episodes data for episodes list page
EPISODES = read_from_fixture("episodes.json")


def get_stats() -> dict:
    """Calculate statistics for the dashboard."""
    total_podcasts = len(PODCASTS)
    total_episodes = sum(podcast.get("episodes_count", 0) for podcast in PODCASTS)
    
    # Get recent activity (last episode if available)
    recent_activity = None
    if EPISODES:
        last_episode = EPISODES[0]
        recent_activity = {
            "text": f"Last episode: {last_episode.get('title', 'Unknown')}",
            "time": "2h ago",  # Placeholder, can be extended with actual timestamps
        }
    else:
        recent_activity = {
            "text": "No episodes yet",
            "time": None,
        }
    
    return {
        "total_podcasts": total_podcasts,
        "total_episodes": total_episodes,
        "recent_activity": recent_activity,
    }


def get_recent_episodes(limit: int = 10) -> list:
    """Get recent episodes for timeline widget."""
    return EPISODES[:limit] if len(EPISODES) >= limit else EPISODES
