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
