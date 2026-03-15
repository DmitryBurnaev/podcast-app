import logging
from pathlib import Path

from jinja2 import Template

from src.modules.db.models import Podcast, File
from src.constants import FileType, EpisodeStatus
from src.modules.services.storage import StorageS3
from src.modules.utils.processing import get_file_size
from src.modules.tasks.base import RQTask, TaskResultCode
from src.modules.db.repositories import PodcastRepository, FileRepository, EpisodeRepository

__all__ = ["GenerateRSSTask"]
logger = logging.getLogger(__name__)


class GenerateRSSTask(RQTask):
    """Allows recreating and upload RSS for specific podcast or for all of exists"""

    storage: StorageS3
    podcast_repository: PodcastRepository
    file_repository: FileRepository

    async def run(self, *podcast_ids: int, **_) -> TaskResultCode:
        """Run process for generation and upload RSS to the cloud (S3)"""

        self.storage = StorageS3()
        if not self.db_session:
            raise RuntimeError("No database session available")

        self.podcast_repository = PodcastRepository(self.db_session)
        self.file_repository = FileRepository(self.db_session)

        filter_kwargs = {"id__in": [int(pk) for pk in podcast_ids]} if podcast_ids else {}
        podcasts = await self.podcast_repository.all(**filter_kwargs)
        results = {}

        for podcast in podcasts:
            results.update(await self._generate(podcast))

        if TaskResultCode.ERROR in results.values():
            return TaskResultCode.ERROR

        return TaskResultCode.SUCCESS

    async def _generate(self, podcast: Podcast) -> dict:
        """Render RSS and upload it"""

        logger.info("START rss generation for %s", podcast)
        local_path = await self._render_rss_to_file(podcast)
        remote_path = self.storage.upload_file(
            local_path,
            dst_path=self.settings.s3.bucket_rss_path,
        )
        if not remote_path:
            logger.error("Couldn't upload RSS file to storage. SKIP")
            return {podcast.id: TaskResultCode.ERROR}

        rss_data = {
            "path": remote_path,
            "size": get_file_size(local_path),
            "available": True,
        }
        if podcast.rss_id:
            await self.file_repository.update_by_ids([podcast.rss_id], value=rss_data)
        else:
            rss_file_data = rss_data | {
                "file_type": FileType.RSS,
                "owner_id": podcast.owner_id,
            }
            rss_file: File = await self.file_repository.create(value=rss_file_data)
            await self.podcast_repository.update_by_filters(filters={"rss_id": rss_file.id})

        logger.info("Podcast #%i: RSS file uploaded, podcast record updated", podcast.id)
        logger.info("FINISH generation for %s | PATH: %s", podcast, remote_path)
        return {podcast.id: TaskResultCode.SUCCESS}

    async def _render_rss_to_file(self, podcast: Podcast) -> Path:
        """Generate rss for Podcast and Episodes marked as "published" """

        logger.info("Podcast #%i: RSS generation has been started", podcast.id)
        episode_repository: EpisodeRepository = EpisodeRepository(self.db_session)
        episodes = await episode_repository.all(
            podcast_id=podcast.id,
            status=EpisodeStatus.PUBLISHED,
            published_at__ne=None,
        )
        context = {"episodes": episodes, "settings": self.settings}
        logger.info("Podcast #%i: Adding chapters", podcast.id)
        rss_path = self.settings.template_path / "rss" / "feed_template.xml"

        with open(rss_path, encoding="utf-8") as f:
            template = Template(f.read(), trim_blocks=True)

        rss_filename = self.settings.tmp_rss_path / f"{podcast.publish_id}.xml"
        logger.info("Podcast #%i: Generation new file rss [%s]", podcast.id, rss_filename)
        with open(rss_filename, "wt", encoding="utf-8") as f:
            result_rss = template.render(podcast=podcast, **context)
            f.write(result_rss)

        logger.info("Podcast #%i: RSS file %s generated.", podcast.id, rss_filename)
        return rss_filename
