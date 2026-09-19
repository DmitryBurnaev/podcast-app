import asyncio
import logging
from typing import Any, cast

from litestar import Request, delete, get, patch, post, put
from litestar.datastructures import UploadFile
from litestar.di import NamedDependency
from litestar.exceptions import HTTPException, NotFoundException
from litestar.params import FromPath, FromQuery, JSONBody, MultipartBody
from litestar.status_codes import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from src.modules.common.constants import FileType
from src.modules.db import User
from src.modules.db.models import File
from src.modules.db.models.podcasts import Podcast
from src.modules.db.repositories import (
    EpisodeRepository,
    FileRepository,
    PodcastOrderT,
    PodcastRepository,
)
from src.modules.common.types import OwnerScope
from src.modules.db.services import SASessionUOW
from src.modules.services.storage import StorageS3
from src.modules.tasks import GenerateRSSTask
from src.modules.tasks.base import RQTask
from src.modules.utils.processing import get_file_size, save_uploaded_file
from src.modules.schemas.common import Pagination
from src.modules.schemas.podcasts import (
    PodcastCreateRequest,
    PodcastResponse,
    PodcastTaskResponse,
    PodcastUpdateRequest,
)
from src.modules.api.base import BaseApiController
from src.modules.schemas.statistics import PodcastStatistics
from src.settings.app import get_app_settings

logger = logging.getLogger(__name__)


class PodcastAPIController(BaseApiController):
    path = "/api/podcasts"
    tags = ["Podcasts"]

    @post("/", status_code=HTTP_201_CREATED)
    async def create(
        self,
        data: JSONBody[PodcastCreateRequest],
        current_user: NamedDependency[User],
        user_scope: NamedDependency[OwnerScope],
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

    @get("/")
    async def get_list(
        self,
        user_scope: NamedDependency[OwnerScope],
        limit: FromQuery[int] = 10,
        offset: FromQuery[int] = 0,
        order_by: FromQuery[PodcastOrderT] = "-created_at",
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

    @get("/{podcast_id:int}/")
    async def get_details(
        self,
        podcast_id: FromPath[int],
        user_scope: NamedDependency[OwnerScope],
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
            raise NotFoundException(f"Podcast with id {podcast_id} not found")

        logger.info("[API] Requested podcast details for %s | podcast #%i", user_scope, podcast_id)
        return PodcastResponse.model_validate(podcast)

    @patch("/{podcast_id:int}/")
    async def update(
        self,
        podcast_id: FromPath[int],
        data: JSONBody[PodcastUpdateRequest],
        current_user: NamedDependency[User],
        user_scope: NamedDependency[OwnerScope],
    ) -> PodcastResponse:
        """Update editable fields for a podcast owned by the current user."""
        update_data = data.model_dump(exclude_unset=True)
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            podcast = await podcast_repository.get(podcast_id)
            if update_data:
                await podcast_repository.update(podcast, **update_data)
                await uow.flush()
                uow.mark_for_commit()

            updated_podcast = await podcast_repository.get_first_with_aggregations(ids=[podcast_id])

        if not updated_podcast:
            raise NotFoundException(f"Podcast with id {podcast_id} not found")

        logger.info("[API] Updated podcast #%i | user #%i", podcast_id, current_user.id)
        return PodcastResponse.model_validate(updated_podcast)

    @delete("/{podcast_id:int}/", status_code=HTTP_204_NO_CONTENT)
    async def delete(
        self,
        podcast_id: FromPath[int],
        user_scope: NamedDependency[OwnerScope],
    ) -> None:
        """Delete a podcast owned by the current user."""
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            episode_repository = EpisodeRepository(session=uow.session, scope=user_scope)
            podcast = await podcast_repository.get(podcast_id)
            episodes = await episode_repository.all(podcast_id=podcast_id)
            for episode in episodes:
                await episode_repository.safe_delete(episode)

            await podcast_repository.delete(podcast)
            uow.mark_for_commit()

        logger.info("[API] Deleted podcast #%i | user #%i", podcast_id, user_scope.user_id)

    @post("/{podcast_id:int}/upload-image/")
    async def upload_image(
        self,
        podcast_id: FromPath[int],
        data: MultipartBody[dict[str, UploadFile]],
        user_scope: NamedDependency[OwnerScope],
    ) -> PodcastResponse:
        """Upload and attach a cover image to a podcast."""
        uploaded_file = data.get("file") or next(iter(data.values()), None)
        if not isinstance(uploaded_file, UploadFile):
            raise HTTPException(status_code=400, detail="Image file is required")

        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            file_repository = FileRepository(session=uow.session, scope=user_scope)
            podcast = await podcast_repository.get(podcast_id)
            try:
                remote_path, file_size = await self._upload_podcast_image(uploaded_file)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

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
            raise NotFoundException(f"Podcast with id {podcast_id} not found")

        logger.info("[API] Uploaded image for podcast #%i | %s", podcast_id, user_scope)
        return PodcastResponse.model_validate(updated_podcast)

    @put("/{podcast_id:int}/generate-rss/")
    async def generate_rss(
        self,
        podcast_id: FromPath[int],
        request: Request,
        current_user: NamedDependency[User],
        user_scope: NamedDependency[OwnerScope],
    ) -> PodcastTaskResponse:
        """Enqueue RSS generation for a podcast."""
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            podcast = await podcast_repository.get(podcast_id)

        job_id = await self._enqueue_task(request, GenerateRSSTask, podcast.id)
        logger.info(
            "[API] Enqueued RSS generation for podcast #%i | user #%i",
            podcast_id,
            current_user.id,
        )
        return PodcastTaskResponse(job_id=job_id)

    @classmethod
    async def _enqueue_task(
        cls,
        request: Request,
        task_class: type[RQTask],
        *args: Any,
        **kwargs: Any,
    ) -> str:
        task = task_class()
        job_id = task_class.get_job_id(*args, **kwargs)
        kwargs["job_id"] = job_id
        app = cast(Any, request.app)
        await asyncio.to_thread(app.rq_queue.enqueue, task, *args, **kwargs)
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
            raise HTTPException(status_code=500, detail="Unable to upload podcast image")

        return remote_path, get_file_size(local_path)
