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
    NavigationItem(title="Home", icon="🏠", path="/", slug="home"),
    NavigationItem(title="Podcasts", icon="☰", path="/podcasts", slug="podcasts"),
    NavigationItem(title="Episodes", icon="☰", path="/episodes", slug="episodes"),
    NavigationItem(title="Progress", icon="🏃", path="/progress", slug="progress"),
    NavigationItem(title="My Profile", icon="👤", path="/profile", slug="profile"),
    NavigationItem(title="About", icon="ℹ", path="/about", slug="about"),
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
    # recent_activity = None
    # if EPISODES:
    #     last_episode = EPISODES[0]
    #     # recent_activity = {
    #     #     "text": f"Last episode: {last_episode.get('title', 'Unknown')}",
    #     #     "time": "2h ago",  # Placeholder, can be extended with actual timestamps
    #     # }
    # else:
    #     # recent_activity = {
    #     #     "text": "No episodes yet",
    #     #     "time": None,
    #     # }

    return {
        "total_podcasts": total_podcasts,
        "total_episodes": total_episodes,
        "total_storage": total_storage,
        # "recent_activity": recent_activity,
    }


def get_recent_episodes(limit: int = 10) -> list:
    """Get recent episodes for timeline widget."""
    return EPISODES[:limit] if len(EPISODES) >= limit else EPISODES


def format_duration(seconds: int) -> str:
    """Format duration in seconds to human-readable format (HH:MM:SS or MM:SS)."""
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    secs = seconds % 60

    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_file_size(bytes_size: int) -> str:
    """Format file size in bytes to human-readable format (B, KB, MB, GB)."""
    if bytes_size < 1024:
        return f"{bytes_size} B"
    elif bytes_size < 1024 * 1024:
        return f"{bytes_size / 1024:.2f} KB"
    elif bytes_size < 1024 * 1024 * 1024:
        return f"{bytes_size / (1024 * 1024):.2f} MB"
    else:
        return f"{bytes_size / (1024 * 1024 * 1024):.2f} GB"


def get_episode_status_color(status: str) -> dict:
    """Get color scheme for episode status badge."""
    colors = {
        "published": {
            "bg": "bg-green-500/20",
            "text": "text-green-400",
            "border": "border-green-500/30",
        },
        "downloading": {
            "bg": "bg-blue-500/20",
            "text": "text-blue-400",
            "border": "border-blue-500/30",
        },
        "error": {
            "bg": "bg-red-500/20",
            "text": "text-red-400",
            "border": "border-red-500/30",
        },
        "pending": {
            "bg": "bg-slate-500/20",
            "text": "text-slate-400",
            "border": "border-slate-500/30",
        },
    }
    return colors.get(status, colors["pending"])


def get_episode_status_label(status: str) -> str:
    """Get human-readable label for episode status."""
    labels = {
        "published": "Published",
        "downloading": "Downloading",
        "error": "Error",
        "pending": "Pending",
    }
    return labels.get(status, "Unknown")
