import asyncio
import logging
from typing import Any, Annotated, cast

from litestar import Request, delete, get, patch, post, put
from litestar.datastructures import UploadFile
from litestar.enums import RequestEncodingType
from litestar.exceptions import HTTPException, NotFoundException
from litestar.params import Body
from litestar.status_codes import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from src.constants import FileType
from src.modules.db import User
from src.modules.db.models import File
from src.modules.db.models.podcasts import Podcast
from src.modules.db.repositories import EpisodeRepository, FileRepository, PodcastOrderT
from src.modules.services.storage import StorageS3
from src.modules.tasks import GenerateRSSTask
from src.modules.tasks.base import RQTask
from src.modules.utils.processing import get_file_size, save_uploaded_file
from src.modules.schemas.common import LimitOffsetPagination
from src.modules.schemas.podcasts import (
    PodcastCreateRequest,
    PodcastResponse,
    PodcastTaskResponse,
    PodcastUpdateRequest,
)
from src.modules.api.base import BaseApiController
from src.modules.db.repositories import PodcastRepository
from src.modules.db.services import SASessionUOW
from src.settings.app import get_app_settings

logger = logging.getLogger(__name__)


async def _enqueue_task(
    request: Request, task_class: type[RQTask], *args: Any, **kwargs: Any
) -> str:
    task = task_class()
    job_id = task_class.get_job_id(*args, **kwargs)
    kwargs["job_id"] = job_id
    app = cast(Any, request.app)
    await asyncio.to_thread(app.rq_queue.enqueue, task, *args, **kwargs)
    return job_id


async def _get_owned_podcast(
    repository: PodcastRepository, podcast_id: int, owner_id: int
) -> Podcast:
    podcast = await repository.first(id=podcast_id, owner_id=owner_id)
    if not podcast:
        raise NotFoundException(f"Podcast with id {podcast_id} not found")

    return podcast


async def _upload_podcast_image(uploaded_file: UploadFile) -> tuple[str, int]:
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


class PodcastAPIController(BaseApiController):
    path = "/api/podcasts"
    tags = ["Podcasts"]

    @post("/", status_code=HTTP_201_CREATED)
    async def create(
        self,
        data: PodcastCreateRequest,
        current_user: User,
    ) -> PodcastResponse:
        """Create a podcast for the current user."""
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcast = await podcast_repository.create(
                publish_id=Podcast.generate_publish_id(),
                name=data.name,
                description=data.description,
                download_automatically=data.download_automatically,
                owner_id=current_user.id,
            )
            await uow.session.flush()
            created_podcast = await podcast_repository.get_first_with_aggregations(
                ids=[podcast.id],
                owner_id=current_user.id,
            )

        if not created_podcast:
            raise NotFoundException("Podcast was not created")

        logger.info("[API] Created podcast #%i | user #%i", created_podcast.id, current_user.id)
        return PodcastResponse.model_validate(created_podcast)

    @get("/")
    async def get_list(
        self,
        current_user: User,
        limit: int = 10,
        offset: int = 0,
        order_by: PodcastOrderT = "-created_at",
    ) -> LimitOffsetPagination[PodcastResponse]:
        """
        Get paginated list of podcasts (for current user) with pagination

        Args:
            current_user: Current user
            limit: Limit of podcasts to return
            offset: Offset of podcasts to return
            order_by: Order by field

        Returns:
            Paginated list of podcasts
        """
        logger.info("[API] Getting paginated list of podcasts | user #%i", current_user.id)
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcasts, total = await podcast_repository.all_with_aggregations(
                limit=limit,
                offset=offset,
                order_by=order_by,
                owner_id=current_user.id,
            )

        logger.info(
            "[API] Returned podcasts list | user #%i | found %i podcasts, total: %i",
            current_user.id,
            len(podcasts),
            total,
        )
        return LimitOffsetPagination[PodcastResponse](
            items=[PodcastResponse.model_validate(podcast) for podcast in podcasts],
            offset=offset,
            total=total,
        )

    @get("/{podcast_id:int}/")
    async def get_details(self, podcast_id: int, current_user: User) -> PodcastResponse:
        """
        Get details of a podcast

        Args:
            podcast_id: ID of the podcast
            current_user: Current user

        Returns:
            Details of the podcast
        """
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcast = await podcast_repository.get_first_with_aggregations(
                ids=[podcast_id],
                owner_id=current_user.id,
            )

        if not podcast:
            raise NotFoundException(f"Podcast with id {podcast_id} not found")

        logger.info(
            "[API] Requested podcast details for user #%i | podcast #%i",
            current_user.id,
            podcast_id,
        )
        return PodcastResponse.model_validate(podcast)

    @patch("/{podcast_id:int}/")
    async def update(
        self,
        podcast_id: int,
        data: PodcastUpdateRequest,
        current_user: User,
    ) -> PodcastResponse:
        """Update editable fields for a podcast owned by the current user."""
        update_data = data.model_dump(exclude_unset=True)
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcast = await _get_owned_podcast(
                repository=podcast_repository,
                podcast_id=podcast_id,
                owner_id=current_user.id,
            )
            if update_data:
                await podcast_repository.update(podcast, **update_data)
                await uow.session.flush()

            updated_podcast = await podcast_repository.get_first_with_aggregations(
                ids=[podcast_id],
                owner_id=current_user.id,
            )

        if not updated_podcast:
            raise NotFoundException(f"Podcast with id {podcast_id} not found")

        logger.info("[API] Updated podcast #%i | user #%i", podcast_id, current_user.id)
        return PodcastResponse.model_validate(updated_podcast)

    @delete("/{podcast_id:int}/", status_code=HTTP_204_NO_CONTENT)
    async def delete(self, podcast_id: int, current_user: User) -> None:
        """Delete a podcast owned by the current user."""
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            episode_repository = EpisodeRepository(session=uow.session)
            podcast = await _get_owned_podcast(
                repository=podcast_repository,
                podcast_id=podcast_id,
                owner_id=current_user.id,
            )
            episodes = await episode_repository.all(
                podcast_id=podcast_id,
                owner_id=current_user.id,
            )
            for episode in episodes:
                await episode_repository.safe_delete(episode)

            await podcast_repository.delete(podcast)

        logger.info("[API] Deleted podcast #%i | user #%i", podcast_id, current_user.id)

    @post("/{podcast_id:int}/upload-image/")
    async def upload_image(
        self,
        podcast_id: int,
        data: Annotated[dict[str, UploadFile], Body(media_type=RequestEncodingType.MULTI_PART)],
        current_user: User,
    ) -> PodcastResponse:
        """Upload and attach a cover image to a podcast."""
        uploaded_file = data.get("file") or next(iter(data.values()), None)
        if not isinstance(uploaded_file, UploadFile):
            raise HTTPException(status_code=400, detail="Image file is required")

        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            file_repository = FileRepository(session=uow.session)
            podcast = await _get_owned_podcast(
                repository=podcast_repository,
                podcast_id=podcast_id,
                owner_id=current_user.id,
            )
            try:
                remote_path, file_size = await _upload_podcast_image(
                    uploaded_file=uploaded_file,
                )
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            image_file = await file_repository.create(
                type=FileType.IMAGE,
                path=remote_path,
                size=file_size,
                available=True,
                access_token=File.generate_token(),
                owner_id=current_user.id,
            )
            await uow.session.flush()
            await podcast_repository.update(podcast, image_id=image_file.id)
            await uow.session.flush()
            updated_podcast = await podcast_repository.get_first_with_aggregations(
                ids=[podcast_id],
                owner_id=current_user.id,
            )

        if not updated_podcast:
            raise NotFoundException(f"Podcast with id {podcast_id} not found")

        logger.info("[API] Uploaded image for podcast #%i | user #%i", podcast_id, current_user.id)
        return PodcastResponse.model_validate(updated_podcast)

    @put("/{podcast_id:int}/generate-rss/")
    async def generate_rss(
        self,
        podcast_id: int,
        request: Request,
        current_user: User,
    ) -> PodcastTaskResponse:
        """Enqueue RSS generation for a podcast."""
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            await _get_owned_podcast(
                repository=podcast_repository,
                podcast_id=podcast_id,
                owner_id=current_user.id,
            )

        job_id = await _enqueue_task(request, GenerateRSSTask, podcast_id)
        logger.info(
            "[API] Enqueued RSS generation for podcast #%i | user #%i",
            podcast_id,
            current_user.id,
        )
        return PodcastTaskResponse(job_id=job_id)
