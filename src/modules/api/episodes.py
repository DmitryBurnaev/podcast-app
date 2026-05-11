import asyncio
import logging
from typing import Any, cast

from litestar import Request, delete, get, patch, post, put
from litestar.exceptions import HTTPException, NotFoundException
from litestar.status_codes import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from src.constants import EpisodeStatus, FileType, SourceType
from src.modules import tasks
from src.modules.api.base import BaseApiController
from src.modules.db import User
from src.modules.db.models import Episode, File
from src.modules.db.repositories import (
    EpisodeOrderT,
    EpisodeRepository,
    FileRepository,
    PodcastRepository,
)
from src.modules.db.services import SASessionUOW
from src.modules.schemas import (
    EpisodeCreateNestedSchema,
    EpisodePatchSchema,
    UploadedEpisodeCreateSchema,
)
from src.modules.services.episodes import EpisodeCreator
from src.modules.tasks.base import RQTask
from src.modules.utils.processing import publish_redis_stop_downloading
from src.schemas import EpisodeResponse, LimitOffsetPagination, UploadedEpisodeResponse

logger = logging.getLogger(__name__)


class TaskQueueAppProtocol:
    rq_queue: Any


class EpisodeTaskMixin:
    @staticmethod
    async def _run_task(
        app: TaskQueueAppProtocol,
        task_class: type[RQTask],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        task = task_class()
        kwargs["job_id"] = task_class.get_job_id(*args, **kwargs)
        await asyncio.to_thread(app.rq_queue.enqueue, task, *args, **kwargs)

    @staticmethod
    async def _get_owned_episode(
        repository: EpisodeRepository,
        episode_id: int,
        owner_id: int,
    ) -> Episode:
        episode = await repository.first(id=episode_id, owner_id=owner_id)
        if not episode:
            raise NotFoundException(f"Episode with id {episode_id} not found")
        return episode

    @staticmethod
    async def _ensure_owned_podcast(
        repository: PodcastRepository,
        podcast_id: int,
        owner_id: int,
    ) -> None:
        podcast = await repository.first(id=podcast_id, owner_id=owner_id)
        if not podcast:
            raise NotFoundException(f"Podcast with id {podcast_id} not found")


class PodcastEpisodeAPIController(EpisodeTaskMixin, BaseApiController):
    path = "/api/podcasts/{podcast_id:int}/episodes"
    tags = ["Episodes"]

    @get("/")
    async def get_list(
        self,
        podcast_id: int,
        current_user: User,
        limit: int = 10,
        offset: int = 0,
        order_by: EpisodeOrderT = "-created_at",
    ) -> LimitOffsetPagination[EpisodeResponse]:
        logger.info(
            "[API] Getting podcast episodes | user #%i | podcast #%i",
            current_user.id,
            podcast_id,
        )
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            await self._ensure_owned_podcast(podcast_repository, podcast_id, current_user.id)

            episode_repository = EpisodeRepository(session=uow.session)
            episodes, total = await episode_repository.all_paginated(
                owner_id=current_user.id,
                podcast_id=podcast_id,
                limit=limit,
                offset=offset,
                order_by=order_by,
            )

        return LimitOffsetPagination[EpisodeResponse](
            items=[EpisodeResponse.model_validate(episode) for episode in episodes],
            offset=offset,
            total=total,
        )

    @post("/", status_code=HTTP_201_CREATED)
    async def create(
        self,
        request: Request,
        podcast_id: int,
        current_user: User,
        data: EpisodeCreateNestedSchema,
    ) -> EpisodeResponse:
        source_url = data.normalized_source_url
        logger.info(
            "[API] Creating episode | user #%i | podcast #%i | source %s",
            current_user.id,
            podcast_id,
            source_url,
        )

        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcast = await podcast_repository.first(id=podcast_id, owner_id=current_user.id)
            if not podcast:
                raise NotFoundException(f"Podcast with id {podcast_id} not found")

            creator = EpisodeCreator(db_session=uow.session, user_id=current_user.id)
            try:
                episode = await creator.create(podcast_id=podcast_id, source_url=source_url)
            except ValueError as exc:
                raise HTTPException(status_code=400, detail=str(exc)) from exc

            if podcast.download_automatically:
                await EpisodeRepository(uow.session).update(
                    episode,
                    status=EpisodeStatus.DOWNLOADING,
                )

        app = cast(TaskQueueAppProtocol, request.app)
        if podcast.download_automatically:
            await self._run_task(app, tasks.DownloadEpisodeTask, episode_id=episode.id)
        await self._run_task(app, tasks.DownloadEpisodeImageTask, episode_id=episode.id)

        return EpisodeResponse.model_validate(episode)

    @post("/uploaded/", status_code=HTTP_201_CREATED)
    async def create_uploaded(
        self,
        request: Request,
        podcast_id: int,
        current_user: User,
        data: UploadedEpisodeCreateSchema,
    ) -> EpisodeResponse:
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            await self._ensure_owned_podcast(podcast_repository, podcast_id, current_user.id)

            file_repository = FileRepository(session=uow.session)
            audio_file = await file_repository.first(
                hash=data.hash,
                owner_id=current_user.id,
                type=FileType.AUDIO,
            )
            if not audio_file:
                if not data.path or data.size is None:
                    raise NotFoundException(
                        f"Uploaded episode file with hash {data.hash} not found"
                    )

                audio_file = await file_repository.create(
                    type=FileType.AUDIO,
                    available=False,
                    owner_id=current_user.id,
                    path=data.path,
                    size=data.size,
                    hash=data.hash,
                    meta=data.meta,
                    access_token=File.generate_token(),
                )

            image_id = None
            if data.cover:
                cover_hash = data.cover.get("hash")
                image_file = (
                    await file_repository.first(
                        hash=cover_hash,
                        owner_id=current_user.id,
                        type=FileType.IMAGE,
                    )
                    if cover_hash
                    else None
                )
                if image_file is None and data.cover.get("path"):
                    image_file = await file_repository.create(
                        type=FileType.IMAGE,
                        available=True,
                        owner_id=current_user.id,
                        path=data.cover["path"],
                        size=data.cover.get("size") or 0,
                        hash=cover_hash or "",
                        access_token=File.generate_token(),
                    )
                image_id = image_file.id if image_file is not None else None

            episode_repository = EpisodeRepository(session=uow.session)
            episode = await episode_repository.first(
                source_id=data.source_id,
                source_type=SourceType.UPLOAD,
                podcast_id=podcast_id,
                owner_id=current_user.id,
            )
            created = episode is None
            if created:
                episode = await episode_repository.create(
                    title=data.prepared_title[:255],
                    description=data.prepared_description,
                    author=data.prepared_author,
                    length=data.duration,
                    source_id=data.source_id,
                    source_type=SourceType.UPLOAD,
                    watch_url="",
                    podcast_id=podcast_id,
                    owner_id=current_user.id,
                    audio_id=audio_file.id,
                    image_id=image_id,
                    status=EpisodeStatus.DOWNLOADING,
                )
            if episode is None:
                raise NotFoundException(f"Episode with hash {data.hash} not found")
            episode.audio = audio_file

        if created:
            await self._run_task(
                cast(TaskQueueAppProtocol, request.app),
                tasks.UploadedEpisodeTask,
                episode_id=episode.id,
            )
        return EpisodeResponse.model_validate(episode)

    @get("/uploaded/{hash:str}/")
    async def get_uploaded(
        self,
        podcast_id: int,
        hash: str,
        current_user: User,
    ) -> UploadedEpisodeResponse:
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            await self._ensure_owned_podcast(podcast_repository, podcast_id, current_user.id)

            file_repository = FileRepository(session=uow.session)
            uploaded_file = await file_repository.first(
                hash=hash,
                owner_id=current_user.id,
                type=FileType.AUDIO,
            )

        if not uploaded_file:
            raise NotFoundException(f"Uploaded episode file with hash {hash} not found")

        return UploadedEpisodeResponse.model_validate(uploaded_file)


class EpisodeAPIController(EpisodeTaskMixin, BaseApiController):
    path = "/api/episodes"
    tags = ["Episodes"]

    @get("/")
    async def get_list(
        self,
        current_user: User,
        limit: int = 10,
        offset: int = 0,
        order_by: EpisodeOrderT = "-created_at",
    ) -> LimitOffsetPagination[EpisodeResponse]:
        logger.info("[API] Getting paginated list of episodes | user #%i", current_user.id)
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episodes, total = await episode_repository.all_paginated(
                owner_id=current_user.id,
                limit=limit,
                offset=offset,
                order_by=order_by,
            )

        return LimitOffsetPagination[EpisodeResponse](
            items=[EpisodeResponse.model_validate(episode) for episode in episodes],
            offset=offset,
            total=total,
        )

    @get("/{episode_id:int}/")
    async def get_details(self, episode_id: int, current_user: User) -> EpisodeResponse:
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episode = await self._get_owned_episode(
                episode_repository,
                episode_id,
                current_user.id,
            )

        logger.info(
            "[API] Requested episode details for user #%i | episode #%i",
            current_user.id,
            episode_id,
        )
        return EpisodeResponse.model_validate(episode)

    @patch("/{episode_id:int}/")
    async def update(
        self,
        episode_id: int,
        current_user: User,
        data: EpisodePatchSchema,
    ) -> EpisodeResponse:
        update_data = data.update_data
        if not update_data:
            raise HTTPException(status_code=400, detail="No update fields provided")

        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episode = await self._get_owned_episode(
                episode_repository,
                episode_id,
                current_user.id,
            )
            await episode_repository.update(episode, **update_data)

        return EpisodeResponse.model_validate(episode)

    @delete("/{episode_id:int}/", status_code=HTTP_204_NO_CONTENT)
    async def delete(self, episode_id: int, current_user: User) -> None:
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episode = await self._get_owned_episode(
                episode_repository,
                episode_id,
                current_user.id,
            )
            try:
                await episode_repository.safe_delete(episode)
            except ValueError as exc:
                raise HTTPException(status_code=409, detail=str(exc)) from exc

    @put("/{episode_id:int}/download/")
    async def download(
        self,
        request: Request,
        episode_id: int,
        current_user: User,
    ) -> EpisodeResponse:
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episode = await self._get_owned_episode(
                episode_repository,
                episode_id,
                current_user.id,
            )
            if episode.status in Episode.PROGRESS_STATUSES:
                raise HTTPException(status_code=409, detail="Episode is already in progress")

            await episode_repository.update(episode, status=EpisodeStatus.DOWNLOADING)
            task_class = (
                tasks.UploadedEpisodeTask
                if episode.source_type == SourceType.UPLOAD
                else tasks.DownloadEpisodeTask
            )

        await self._run_task(
            cast(TaskQueueAppProtocol, request.app),
            task_class,
            episode_id=episode.id,
        )
        return EpisodeResponse.model_validate(episode)

    @put("/{episode_id:int}/cancel-downloading/")
    async def cancel_downloading(
        self,
        episode_id: int,
        current_user: User,
    ) -> EpisodeResponse:
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episode = await self._get_owned_episode(
                episode_repository,
                episode_id,
                current_user.id,
            )
            if episode.status != EpisodeStatus.DOWNLOADING:
                raise HTTPException(status_code=409, detail="Episode is not downloading")

            await episode_repository.update(episode, status=EpisodeStatus.CANCELING)

        tasks.DownloadEpisodeTask.cancel_task(episode_id=episode.id)
        tasks.DownloadEpisodeImageTask.cancel_task(episode_id=episode.id)
        await publish_redis_stop_downloading(episode.id)
        return EpisodeResponse.model_validate(episode)
