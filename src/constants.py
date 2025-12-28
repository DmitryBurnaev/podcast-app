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


def format_storage_size(size_mb: float) -> str:
    """Format storage size in human-readable format."""
    if size_mb >= 1024:
        return f"{size_mb / 1024:.2f} GB"
    return f"{size_mb:.2f} MB"


def get_stats() -> dict:
    """Calculate statistics for the dashboard."""
    total_podcasts = len(PODCASTS)
    total_episodes = sum(podcast.get("episodes_count", 0) for podcast in PODCASTS)

    # Calculate total storage size (average ~75 MB per episode)
    # This is a placeholder calculation, can be replaced with actual data
    average_episode_size_mb = 75.0
    total_storage_mb = total_episodes * average_episode_size_mb
    total_storage = format_storage_size(total_storage_mb)

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
        "total_storage": total_storage,
        "recent_activity": recent_activity,
    }


def get_recent_episodes(limit: int = 10) -> list:
    """Get recent episodes for timeline widget."""
    return EPISODES[:limit] if len(EPISODES) >= limit else EPISODES
