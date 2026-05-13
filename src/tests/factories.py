from datetime import datetime

from src.modules.db.models import User
from src.modules.db.models.podcasts import Podcast
from src.modules.schemas.statistics import PodcastStatistics
from src.utils import utcnow


def make_user(
    *,
    id: int = 1,
    email: str = "user@podcast.dev",
    is_active: bool = True,
    is_superuser: bool = False,
) -> User:
    return User(
        id=id,
        email=email,
        password="hashed-password",
        is_active=is_active,
        is_superuser=is_superuser,
    )


def make_podcast(
    *,
    id: int = 1,
    owner_id: int = 1,
    name: str = "Test podcast",
    description: str = "Test podcast description",
    download_automatically: bool = False,
    created_at: datetime | None = None,
    episodes_count: int = 0,
) -> Podcast:
    podcast = Podcast(
        id=id,
        publish_id=f"publish-{id}",
        name=name,
        description=description,
        download_automatically=download_automatically,
        owner_id=owner_id,
        created_at=created_at or utcnow(),
        rss_id=None,
        image_id=None,
    )
    podcast.stat = PodcastStatistics(episodes_count=episodes_count)
    return podcast
