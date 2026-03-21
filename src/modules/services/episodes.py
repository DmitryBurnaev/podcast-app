"""Episode creation service: create episode from source URL (stub with random data)."""

import hashlib
import logging
import random
import re

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import FileType
from src.exceptions import SourceFetchError
from src.modules.db.models import File
from src.modules.db.models.podcasts import Episode, EpisodeStatus, SourceType
from src.modules.db.repositories import EpisodeRepository, PodcastRepository, FileRepository
from src.modules.db.utils import cookie_file_ctx
from src.modules.utils import common as common_utils

__all__ = ("EpisodeCreator",)

from src.modules.utils.common import SourceInfo, SourceConfig, SOURCE_CFG_MAP, SourceMediaInfo
from src.settings.old__settings import RENDER_LINKS

logger = logging.getLogger(__name__)

DEFAULT_OWNER_ID = 1
SOURCE_ID_MAX_LENGTH = 32
WATCH_URL_MAX_LENGTH = 128


class EpisodeCreator:
    """Allows extracting info from Source end create episode (if necessary)"""

    symbols_regex = re.compile(r"[&^<>*#]")
    http_link_regex = re.compile(r"https?://(?:[a-zA-Z]|[0-9]|[?._\-@*()%=/])+")
    podcast_id: int
    source_info: SourceInfo
    source_id: str

    def __init__(self, db_session: AsyncSession, user_id: int):
        self.db_session: AsyncSession = db_session
        self.user_id: int = user_id

        self.episode_repository: EpisodeRepository = EpisodeRepository(db_session)
        self.podcast_repository: PodcastRepository = PodcastRepository(db_session)

    async def create(self, podcast_id: int, source_url: str) -> Episode:
        """
        Allows to create new or return exists episode for current podcast

        :raise: `modules.providers.exceptions.SourceFetchError`
        :return: New <Episode> object
        """
        self.podcast_id: int = podcast_id
        self.source_info = common_utils.extract_source_info(source_url)

        same_episodes: list[Episode] = await self.episode_repository.all(
            source_id=self.source_info.id
        )
        episode_in_podcast, last_same_episode = None, None
        for episode in same_episodes:
            last_same_episode = last_same_episode or episode
            if episode.podcast_id == self.podcast_id:
                episode_in_podcast = episode
                break

        if episode_in_podcast:
            logger.info(
                "Episode for video [%s] already exists for current podcast %s. Retrieving %s...",
                self.source_info.id,
                self.podcast_id,
                episode_in_podcast,
            )
            return episode_in_podcast

        episode_data = await self._get_episode_data(same_episode=last_same_episode)
        audio, image = episode_data.pop("audio"), episode_data.pop("image")
        episode = await self.episode_repository.create(**episode_data)
        episode.audio, episode.image = audio, image
        return episode

    def _replace_special_symbols(self, value):
        if RENDER_LINKS:
            # skip links masking for showing links in description
            return value

        res = self.http_link_regex.sub("[LINK]", value)
        return self.symbols_regex.sub("", res)

    async def _get_episode_data(self, same_episode: Episode | None) -> dict:
        """
        Allows getting information for new episode.
        This info can be given from same episode (episode which has same source_id)
        and part information - from ExternalSource (ex.: YouTube)

        :return: dict with information for the new episode
        """

        if same_episode:
            logger.info(
                "Episode for video %s already exists: %s.", self.source_info.id, same_episode
            )
            same_episode_data = same_episode.to_dict()
        else:
            logger.info("New episode for source %s will be created.", self.source_info.id)
            same_episode_data = {}

        async with cookie_file_ctx(self.db_session, self.user_id, self.source_info.type) as cookie:
            source_config: SourceConfig = SOURCE_CFG_MAP[self.source_info.type]
            self.source_info.cookie_path = cookie.file_path if cookie else None
            self.source_info.proxy_url = source_config.proxy_url
            extract_error, source_info = await common_utils.get_source_media_info(self.source_info)

        if source_info:
            chapters = None
            if source_info.chapters:
                chapters = [chapter.as_dict for chapter in source_info.chapters]

            new_episode_data = {
                "source_id": self.source_info.id,
                "source_type": self.source_info.type,
                "watch_url": source_info.watch_url,
                "title": self._replace_special_symbols(source_info.title),
                "description": self._replace_special_symbols(source_info.description),
                "author": source_info.author,
                "length": source_info.length,
                "chapters": chapters,
            }

        elif same_episode:
            same_episode_data.pop("id", None)
            new_episode_data = same_episode_data

        else:
            raise SourceFetchError(f"Extracting data for new Episode failed: {extract_error}")

        audio_file, image_file = await self._create_files(same_episode, source_info)
        new_episode_data.update(
            {
                "podcast_id": self.podcast_id,
                "owner_id": self.user_id,
                "cookie_id": cookie.id if cookie else None,
                "image_id": image_file.id,
                "audio_id": audio_file.id,
                "image": image_file,
                "audio": audio_file,
            }
        )
        return new_episode_data

    async def _create_files(
        self, same_episode: Episode, source_info: SourceMediaInfo
    ) -> tuple[File, File]:
        file_repository = FileRepository(self.db_session)
        if same_episode:
            image_file = await file_repository.copy(
                owner_id=self.user_id, file_id=same_episode.image_id
            )
            audio_file = await file_repository.copy(
                file_id=same_episode.audio_id,
                owner_id=self.user_id,
                available=False,
            )
        elif source_info:
            image_file = await file_repository.create(
                type=FileType.IMAGE,
                public=True,
                available=False,
                owner_id=self.user_id,
                source_url=source_info.thumbnail_url,
            )
            audio_file = await file_repository.create(
                type=FileType.AUDIO,
                available=False,
                owner_id=self.user_id,
                source_url=source_info.watch_url,
            )
        else:
            raise SourceFetchError(
                f"Creating new files failed: same_episode or source_info required | source_id: "
                f"{self.source_info.id}"
            )

        return audio_file, image_file


class EpisodeCreatorOld:
    """Creates episodes from a source URL; stub implementation with random metadata."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create_from_source_url(
        self,
        source_url: str,
        podcast_id: int | None = None,
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
                logger.warning("EpisodeCreator: no podcasts in DB")
                raise ValueError("No podcasts found")
            podcast_id = podcasts[0].id
            logger.debug("EpisodeCreator: using first podcast_id=%s", podcast_id)

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
            "cookie_id": None,
            "length": random.randint(60, 3600),
            "description": f"Stub episode from {url[:50]}{'...' if len(url) > 50 else ''}",
            "author": None,
            "status": EpisodeStatus.NEW,
        }

        episode_repo = EpisodeRepository(session=self._session)
        episode = await episode_repo.create(**payload)
        logger.info("EpisodeCreator: %s", episode)
        return episode
