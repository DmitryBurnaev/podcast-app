from datetime import datetime

from src.modules.db.models import File, User
from src.modules.db.models.media import MediaType
from src.modules.db.models.podcasts import Cookie, Episode, Podcast
from src.modules.schemas.statistics import PodcastStatistics
from src.constants import EpisodeStatus, SourceType
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
    )
    podcast.stat = PodcastStatistics(episodes_count=episodes_count)
    return podcast


def make_cookie(
    *,
    id: int = 1,
    owner_id: int = 1,
    source_type: SourceType = SourceType.YOUTUBE,
    data: str = "encrypted-cookie-data",
    created_at: datetime | None = None,
) -> Cookie:
    created_at = created_at or utcnow()
    return Cookie(
        id=id,
        owner_id=owner_id,
        source_type=source_type,
        data=data,
        created_at=created_at,
        updated_at=created_at,
    )


def make_file(
    *,
    id: int = 1,
    owner_id: int = 1,
    type: MediaType = MediaType.AUDIO,
    path: str = "audio/test.mp3",
    size: int = 128,
    hash: str = "file-hash",
    available: bool = True,
) -> File:
    return File(
        id=id,
        owner_id=owner_id,
        type=type,
        path=path,
        size=size,
        hash=hash,
        available=available,
        source_url="",
        access_token=f"token{id}",
        created_at=utcnow(),
        public=False,
        meta=None,
    )


def make_episode(
    *,
    id: int = 1,
    owner_id: int = 1,
    podcast_id: int = 1,
    title: str = "Test episode",
    source_id: str = "source-id",
    source_type: SourceType = SourceType.YOUTUBE,
    status: EpisodeStatus = EpisodeStatus.NEW,
) -> Episode:
    episode = Episode(
        id=id,
        owner_id=owner_id,
        podcast_id=podcast_id,
        title=title,
        source_id=source_id,
        source_type=source_type,
        status=status,
        watch_url=f"https://example.com/watch/{source_id}",
        length=60,
        description="Test episode description",
        author="Test author",
        chapters=None,
        audio_id=None,
        image_id=None,
        cookie_id=None,
        created_at=utcnow(),
        published_at=None,
    )
    episode.audio = None
    episode.image = None
    return episode
