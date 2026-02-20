"""Episode creation service: create episode from source URL (stub with random data)."""

import hashlib
import logging
import random

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.db.models.podcasts import Episode, EpisodeStatus, SourceType
from src.modules.db.repositories import EpisodeRepository, PodcastRepository

__all__ = ("EpisodeCreator",)

logger = logging.getLogger(__name__)

DEFAULT_OWNER_ID = 1
SOURCE_ID_MAX_LENGTH = 32
WATCH_URL_MAX_LENGTH = 128


class EpisodeCreator:
    """Creates episodes from a source URL; stub implementation with random metadata."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_from_source_url(
        self, source_url: str, podcast_id: int | None = None
    ) -> Episode:
        """
        Create an episode from the given source URL.
        Uses stub data (random title, length, etc.) and default cover (image_id=None).
        """
        url = (source_url or "").strip()
        if not url:
            raise ValueError("source_url is required")

        logger.debug("create_from_source_url: source_url=%s, podcast_id=%s", url[:80], podcast_id)
        podcast_repo = PodcastRepository(session=self._session)
        if podcast_id is None:
            podcasts = await podcast_repo.all()
            if not podcasts:
                logger.warning("create_from_source_url: no podcasts in DB")
                raise ValueError("No podcasts found")
            podcast_id = podcasts[0].id
            logger.debug("create_from_source_url: using first podcast_id=%s", podcast_id)

        source_id = hashlib.md5(url.encode()).hexdigest()[:SOURCE_ID_MAX_LENGTH]
        watch_url = url[:WATCH_URL_MAX_LENGTH] if len(url) > WATCH_URL_MAX_LENGTH else url

        payload = {
            "title": f"Episode stub {random.randint(1000, 9999)}",
            "source_id": source_id,
            "source_type": SourceType.YOUTUBE,
            "podcast_id": podcast_id,
            "owner_id": DEFAULT_OWNER_ID,
            "watch_url": watch_url,
            "audio_id": None,
            "image_id": None,
            "length": random.randint(60, 3600),
            "description": f"Stub episode from {url[:50]}{'...' if len(url) > 50 else ''}",
            "author": None,
            "status": EpisodeStatus.NEW,
        }

        episode_repo = EpisodeRepository(session=self._session)
        episode = await episode_repo.add_episode(payload)
        logger.info(
            "Episode created: id=%s, podcast_id=%s, source_id=%s",
            episode.id,
            podcast_id,
            source_id,
        )
        return episode
