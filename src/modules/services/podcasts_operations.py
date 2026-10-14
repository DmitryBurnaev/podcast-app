import asyncio
import logging
from typing import Any

from litestar.datastructures import UploadFile
from rq.exceptions import NoSuchJobError
from rq.job import Job

from src.modules.common.constants import FileType
from src.modules.common.exceptions import (
    APIError,
    InvalidParametersAPIError,
    NotFoundAPIError,
    StateConflictAPIError,
)
from src.modules.common.types import OwnerScope
from src.modules.db import User
from src.modules.db.models import File
from src.modules.db.models.podcasts import Podcast
from src.modules.db.repositories import (
    EpisodeRepository,
    FileRepository,
    PodcastOrderT,
    PodcastRepository,
)
from src.modules.db.services import SASessionUOW
from src.modules.schemas.common import Pagination
from src.modules.schemas.podcasts import (
    PodcastCreateRequest,
    PodcastResponse,
    PodcastTaskResponse,
    PodcastTaskStatusResponse,
    PodcastUpdateRequest,
)
from src.modules.schemas.statistics import PodcastStatistics
from src.modules.services.redis import RedisClient
from src.modules.services.storage import StorageS3
from src.modules.services.task_queue import TaskQueue
from src.modules.tasks import GenerateRSSTask
from src.modules.tasks.base import RQTask, TaskResultCode
from src.modules.utils.processing import get_file_size, save_uploaded_file
from src.settings.app import get_app_settings

logger = logging.getLogger(__name__)


class PodcastOperations:
    async def rss_task_status(
        self, podcast_id: int, user_scope: OwnerScope
    ) -> PodcastTaskStatusResponse:
        async with SASessionUOW() as uow:
            await self._get_owned_podcast(
                PodcastRepository(uow.session, scope=user_scope), podcast_id
            )
        job_id = GenerateRSSTask.get_job_id(podcast_id)
        try:
            return await asyncio.to_thread(self._read_rss_job, job_id, podcast_id)
        except NoSuchJobError:
            return PodcastTaskStatusResponse(job_id=job_id, status="unknown")
        except Exception as exc:
            raise APIError(
                message="Unable to read RSS generation status.", status_code=503
            ) from exc

    @staticmethod
    def _read_rss_job(job_id: str, podcast_id: int) -> PodcastTaskStatusResponse:
        job = Job.fetch(job_id, connection=RedisClient().sync_redis)
        if job.id != job_id or job.args != (podcast_id,):
            raise NoSuchJobError(job_id)
        state = job.get_status(refresh=True)
        result = PodcastTaskStatusResponse(job_id=job_id, status="unknown")
        if state in {"queued", "deferred", "scheduled"}:
            result.status = "queued"
        elif state == "started":
            result.status = "running"
        elif state == "finished":
            outcome = job.return_value(refresh=True)
            if outcome == TaskResultCode.SUCCESS:
                result.status = "success"
            elif outcome in {TaskResultCode.ERROR, TaskResultCode.CANCEL}:
                result.status = "error"
        elif state in {"failed", "stopped", "canceled"}:
            result.status = "error"
        if result.status == "error":
            result.error = "RSS generation failed. Please try again."
        return result

    @staticmethod
    async def _get_owned_podcast(
        repository: PodcastRepository,
        podcast_id: int,
    ) -> Podcast:
        podcast = await repository.first(id=podcast_id)
        if podcast is None:
            raise NotFoundAPIError(details=f"Podcast with id {podcast_id} not found")
        return podcast

    async def create(
        self,
        data: PodcastCreateRequest,
        current_user: User,
        user_scope: OwnerScope,
    ) -> PodcastResponse:
        """Create a podcast for the current user."""
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            podcast = await podcast_repository.create(
                publish_id=Podcast.generate_publish_id(),
                name=data.name,
                description=data.description,
                download_automatically=data.download_automatically,
                owner_id=current_user.id,
            )
            await uow.flush()
            uow.mark_for_commit()

        logger.info("[API] Created podcast #%i | user #%i", podcast.id, current_user.id)
        return PodcastResponse(
            id=podcast.id,
            name=podcast.name,
            description=podcast.description,
            created_at=podcast.created_at,
            image_url=None,
            rss_url=None,
            download_automatically=podcast.download_automatically,
            stat=PodcastStatistics(),
        )

    async def get_list(
        self,
        user_scope: OwnerScope,
        limit: int = 10,
        offset: int = 0,
        order_by: PodcastOrderT = "-created_at",
    ) -> Pagination[PodcastResponse]:
        """
        Get paginated list of podcasts (for current user) with pagination

        Args:
            user_scope: Current user's scope
            limit: Limit of podcasts to return
            offset: Offset of podcasts to return
            order_by: Order by field

        Returns:
            Paginated list of podcasts
        """
        logger.info("[API] Getting paginated list of podcasts | %s", user_scope)
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            podcasts, total = await podcast_repository.all_with_aggregations(
                limit=limit,
                offset=offset,
                order_by=order_by,
            )

        logger.info(
            "[API] Returned podcasts list | %s | found %i podcasts, total: %i",
            user_scope,
            len(podcasts),
            total,
        )
        return Pagination[PodcastResponse](
            items=[PodcastResponse.model_validate(podcast) for podcast in podcasts],
            offset=offset,
            total=total,
        )

    async def get_details(
        self,
        podcast_id: int,
        user_scope: OwnerScope,
    ) -> PodcastResponse:
        """
        Get details of a podcast

        Args:
            podcast_id: ID of the podcast
            user_scope: Current user's scope

        Returns:
            Details of the podcast
        """
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            podcast = await podcast_repository.get_first_with_aggregations(ids=[podcast_id])

        if not podcast:
            raise NotFoundAPIError(details=f"Podcast with id {podcast_id} not found")

        logger.info("[API] Requested podcast details for %s | podcast #%i", user_scope, podcast_id)
        return PodcastResponse.model_validate(podcast)

    async def update(
        self,
        podcast_id: int,
        data: PodcastUpdateRequest,
        current_user: User,
        user_scope: OwnerScope,
    ) -> PodcastResponse:
        """Update editable fields for a podcast owned by the current user."""
        update_data = data.model_dump(exclude_unset=True)
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            podcast = await self._get_owned_podcast(podcast_repository, podcast_id)
            if update_data:
                await podcast_repository.update(podcast, **update_data)
                await uow.flush()
                uow.mark_for_commit()

            updated_podcast = await podcast_repository.get_first_with_aggregations(ids=[podcast_id])

        if not updated_podcast:
            raise NotFoundAPIError(details=f"Podcast with id {podcast_id} not found")

        logger.info("[API] Updated podcast #%i | user #%i", podcast_id, current_user.id)
        return PodcastResponse.model_validate(updated_podcast)

    async def delete(
        self,
        podcast_id: int,
        user_scope: OwnerScope,
    ) -> None:
        """Delete a podcast owned by the current user."""
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            episode_repository = EpisodeRepository(session=uow.session, scope=user_scope)
            podcast = await podcast_repository.first_for_update(id=podcast_id)
            if podcast is None:
                raise NotFoundAPIError(details=f"Podcast with id {podcast_id} not found")
            episodes = await episode_repository.all(podcast_id=podcast_id)
            locked_episodes = []
            for episode in sorted(episodes, key=lambda item: item.id):
                locked = await episode_repository.first_for_update(id=episode.id)
                if locked is not None:
                    locked_episodes.append(locked)
            episodes = locked_episodes
            if any(episode.status in episode.PROGRESS_STATUSES for episode in episodes):
                raise StateConflictAPIError(
                    details="A podcast with episodes in progress cannot be deleted."
                )
            for episode in episodes:
                try:
                    await episode_repository.safe_delete(episode)
                except ValueError as exc:
                    raise StateConflictAPIError(details=str(exc)) from exc

            await podcast_repository.delete(podcast)
            uow.mark_for_commit()

        logger.info("[API] Deleted podcast #%i | user #%i", podcast_id, user_scope.user_id)

    async def upload_image(
        self,
        podcast_id: int,
        data: dict[str, UploadFile],
        user_scope: OwnerScope,
    ) -> PodcastResponse:
        """Upload and attach a cover image to a podcast."""
        uploaded_file = data.get("file") or next(iter(data.values()), None)
        if not isinstance(uploaded_file, UploadFile):
            raise InvalidParametersAPIError(details="Image file is required")

        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            file_repository = FileRepository(session=uow.session, scope=user_scope)
            podcast = await self._get_owned_podcast(podcast_repository, podcast_id)
            try:
                remote_path, file_size = await self._upload_podcast_image(uploaded_file)
            except ValueError as exc:
                raise InvalidParametersAPIError(details=str(exc)) from exc

            image_file = await file_repository.create(
                type=FileType.IMAGE,
                path=remote_path,
                size=file_size,
                available=True,
                access_token=File.generate_token(),
            )
            await uow.flush()
            await podcast_repository.update(podcast, image_id=image_file.id)
            await uow.flush()
            uow.mark_for_commit()
            updated_podcast = await podcast_repository.get_first_with_aggregations(ids=[podcast_id])

        if not updated_podcast:
            raise NotFoundAPIError(details=f"Podcast with id {podcast_id} not found")

        logger.info("[API] Uploaded image for podcast #%i | %s", podcast_id, user_scope)
        return PodcastResponse.model_validate(updated_podcast)

    async def generate_rss(
        self,
        podcast_id: int,
        queue: TaskQueue,
        current_user: User,
        user_scope: OwnerScope,
    ) -> PodcastTaskResponse:
        """Enqueue RSS generation for a podcast."""
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            podcast = await self._get_owned_podcast(podcast_repository, podcast_id)

        job_id = await self._enqueue_task(queue, GenerateRSSTask, podcast.id)
        logger.info(
            "[API] Enqueued RSS generation for podcast #%i | user #%i",
            podcast_id,
            current_user.id,
        )
        return PodcastTaskResponse(job_id=job_id)

    @classmethod
    async def _enqueue_task(
        cls,
        queue: TaskQueue,
        task_class: type[RQTask],
        *args: Any,
        **kwargs: Any,
    ) -> str:
        task = task_class()
        job_id = task_class.get_job_id(*args, **kwargs)
        kwargs["job_id"] = job_id
        try:
            await asyncio.to_thread(queue.enqueue, task, *args, **kwargs)
        except Exception as exc:
            raise APIError(message="Unable to start RSS generation.", status_code=503) from exc
        return job_id

    @classmethod
    async def _upload_podcast_image(cls, uploaded_file: UploadFile) -> tuple[str, int]:
        settings = get_app_settings()
        local_path = await save_uploaded_file(
            uploaded_file=uploaded_file,
            prefix="podcast_image_",
            max_file_size=settings.max_upload_image_filesize,
            tmp_path=settings.tmp_image_path,
        )
        remote_path = await StorageS3().upload_file(
            local_path,
            dst_path=settings.s3.bucket_podcast_images_path,
        )
        if not remote_path:
            raise APIError(details="Unable to upload podcast image")

        return remote_path, get_file_size(local_path)
