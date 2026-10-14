import asyncio
import logging
from typing import Any

from src.modules import tasks
from src.modules.common.constants import SourceType
from src.modules.common.exceptions import (
    APIError,
    InvalidParametersAPIError,
    NotFoundAPIError,
    StateConflictAPIError,
)
from src.modules.common.types import OwnerScope
from src.modules.db import User
from src.modules.db.models import Episode, File
from src.modules.db.models.media import MediaType
from src.modules.db.models.podcasts import EpisodeStatus
from src.modules.db.repositories import (
    EpisodeOrderT,
    EpisodeRepository,
    FileRepository,
    PodcastRepository,
)
from src.modules.db.services import SASessionUOW
from src.modules.schemas.common import Pagination
from src.modules.schemas.episodes import (
    EpisodeCreateNestedSchema,
    EpisodePatchSchema,
    EpisodeResponse,
    UploadedEpisodeCreateSchema,
    UploadedEpisodeResponse,
)
from src.modules.services.episodes import EpisodeCreator
from src.modules.services.task_queue import TaskQueue
from src.modules.tasks.base import RQTask
from src.modules.utils.processing import publish_redis_stop_downloading

logger = logging.getLogger(__name__)


class EpisodeOperationHelpers:
    @staticmethod
    async def _mark_queue_failure(episode_id: int, user_scope: OwnerScope) -> None:
        async with SASessionUOW() as uow:
            repository = EpisodeRepository(uow.session, scope=user_scope)
            episode = await repository.first(id=episode_id)
            if episode is not None and episode.status == EpisodeStatus.DOWNLOADING:
                await repository.update(episode, status=EpisodeStatus.ERROR)
                uow.mark_for_commit()

    @staticmethod
    async def _run_task(
        queue: TaskQueue,
        task_class: type[RQTask],
        *args: Any,
        **kwargs: Any,
    ) -> None:
        task = task_class()
        kwargs["job_id"] = task_class.get_job_id(*args, **kwargs)
        await asyncio.to_thread(queue.enqueue, task, *args, **kwargs)  # noqa

    @staticmethod
    async def _get_owned_episode(
        repository: EpisodeRepository,
        episode_id: int,
        owner_id: int,
    ) -> Episode:
        episode = await repository.first_for_update(id=episode_id, owner_id=owner_id)
        if not episode:
            raise NotFoundAPIError(details=f"Episode with id {episode_id} not found")
        return episode

    @staticmethod
    async def _ensure_owned_podcast(
        repository: PodcastRepository,
        podcast_id: int,
        owner_id: int,
    ) -> None:
        podcast = await repository.first(id=podcast_id, owner_id=owner_id)
        if not podcast:
            raise NotFoundAPIError(details=f"Podcast with id {podcast_id} not found")


class PodcastEpisodeOperations(EpisodeOperationHelpers):

    async def get_list(
        self,
        podcast_id: int,
        queue: TaskQueue,
        user_scope: OwnerScope,
        limit: int = 10,
        offset: int = 0,
        order_by: EpisodeOrderT = "-created_at",
    ) -> Pagination[EpisodeResponse]:
        """Return paginated episodes for a podcast owned by the current user."""
        logger.info(
            "[API] Getting podcast episodes | user #%i | podcast #%i",
            user_scope.user_id,
            podcast_id,
        )
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            await self._ensure_owned_podcast(podcast_repository, podcast_id, user_scope.user_id)

            episode_repository = EpisodeRepository(session=uow.session, scope=user_scope)
            episodes, total = await episode_repository.all_paginated(
                podcast_id=podcast_id,
                limit=limit,
                offset=offset,
                order_by=order_by,
            )

        return Pagination[EpisodeResponse](
            items=[EpisodeResponse.model_validate(episode) for episode in episodes],
            offset=offset,
            total=total,
        )

    async def create(
        self,
        queue: TaskQueue,
        podcast_id: int,
        current_user: User,
        user_scope: OwnerScope,
        data: EpisodeCreateNestedSchema,
    ) -> EpisodeResponse:
        """Create an episode in the requested podcast."""
        source_url = data.normalized_source_url
        logger.info(
            "[API] Creating episode | user #%i | podcast #%i | source %s",
            current_user.id,
            podcast_id,
            source_url,
        )

        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            podcast = await podcast_repository.first_for_update(id=podcast_id)
            if not podcast:
                raise NotFoundAPIError(details=f"Podcast with id {podcast_id} not found")

            creator = EpisodeCreator(db_session=uow.session, user_id=current_user.id)
            try:
                episode = await creator.create(podcast_id=podcast_id, source_url=source_url)
            except ValueError as exc:
                raise InvalidParametersAPIError(details=str(exc)) from exc

            if creator.created and podcast.download_automatically:
                await EpisodeRepository(uow.session, scope=user_scope).update(
                    episode,
                    status=EpisodeStatus.DOWNLOADING,
                )

            await uow.flush()
            uow.mark_for_commit()

        if creator.created:
            download_queued = False
            try:
                if podcast.download_automatically:
                    await self._run_task(queue, tasks.DownloadEpisodeTask, episode_id=episode.id)
                    download_queued = True
                await self._run_task(queue, tasks.DownloadEpisodeImageTask, episode_id=episode.id)
            except Exception as exc:
                if podcast.download_automatically and not download_queued:
                    await self._mark_queue_failure(episode.id, user_scope)
                raise APIError(
                    message="Episode was created, but its background tasks could not be started.",
                    details={"episode_id": episode.id},
                    status_code=503,
                ) from exc

        return EpisodeResponse.model_validate(episode)

    async def create_uploaded(
        self,
        queue: TaskQueue,
        podcast_id: int,
        data: UploadedEpisodeCreateSchema,
        user_scope: OwnerScope,
    ) -> EpisodeResponse:
        """Create an episode from an already uploaded audio file."""
        # TODO: move logic to the service!
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            await self._ensure_owned_podcast(podcast_repository, podcast_id, user_scope.user_id)

            file_repository = FileRepository(session=uow.session, scope=user_scope)
            audio_file = await file_repository.first(hash=data.hash, type=MediaType.AUDIO)
            if not audio_file:
                if not data.path or data.size is None:
                    raise NotFoundAPIError(
                        details=f"Uploaded episode file with hash {data.hash} not found"
                    )

                audio_file = await file_repository.create(
                    type=MediaType.AUDIO,
                    available=False,
                    owner_id=user_scope.user_id,
                    path=data.path,
                    size=data.size,
                    hash=data.hash,
                    meta=data.meta,
                    access_token=File.generate_token(),
                )

            image_id = None
            image_file: File | None = None
            if data.cover:
                cover_hash = data.cover.get("hash")
                image_file = (
                    await file_repository.first(
                        hash=cover_hash,
                        owner_id=user_scope.user_id,
                        type=MediaType.IMAGE,
                    )
                    if cover_hash
                    else None
                )
                if image_file is None and data.cover.get("path"):
                    image_file = await file_repository.create(
                        type=MediaType.IMAGE,
                        available=True,
                        owner_id=user_scope.user_id,
                        path=data.cover["path"],
                        size=data.cover.get("size") or 0,
                        hash=cover_hash or "",
                        access_token=File.generate_token(),
                    )
                image_id = image_file.id if image_file is not None else None

            episode_repository = EpisodeRepository(session=uow.session, scope=user_scope)
            episode = await episode_repository.first(
                source_id=data.source_id,
                source_type=SourceType.UPLOAD,
                podcast_id=podcast_id,
                owner_id=user_scope.user_id,
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
                    owner_id=user_scope.user_id,
                    audio_id=audio_file.id,
                    image_id=image_id,
                    status=EpisodeStatus.DOWNLOADING,
                )
            if episode is None:
                raise NotFoundAPIError(details=f"Episode with hash {data.hash} not found")

            episode.audio = audio_file
            if image_file is not None:
                episode.image = image_file
            await uow.flush()
            if created:
                uow.mark_for_commit()

        if created:
            await self._run_task(
                queue,
                tasks.UploadedEpisodeTask,
                episode_id=episode.id,
            )

        return EpisodeResponse.model_validate(episode)

    async def get_uploaded(
        self,
        podcast_id: int,
        hash: str,
        queue: TaskQueue,
        user_scope: OwnerScope,
    ) -> UploadedEpisodeResponse:
        """Return metadata for an uploaded episode file by hash."""
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session, scope=user_scope)
            await self._ensure_owned_podcast(podcast_repository, podcast_id, user_scope.user_id)

            file_repository = FileRepository(session=uow.session, scope=user_scope)
            uploaded_file = await file_repository.first(
                hash=hash,
                owner_id=user_scope.user_id,
                type=MediaType.AUDIO,
            )

        if not uploaded_file:
            raise NotFoundAPIError(details=f"Uploaded episode file with hash {hash} not found")

        return UploadedEpisodeResponse.model_validate(uploaded_file)


class EpisodeOperations(EpisodeOperationHelpers):

    async def get_list(
        self,
        current_user: User,
        user_scope: OwnerScope,
        limit: int = 10,
        offset: int = 0,
        order_by: EpisodeOrderT = "-created_at",
    ) -> Pagination[EpisodeResponse]:
        """Return paginated episodes owned by the current user."""
        logger.info("[API] Getting paginated list of episodes | user #%i", current_user.id)
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session, scope=user_scope)
            episodes, total = await episode_repository.all_paginated(
                limit=limit,
                offset=offset,
                order_by=order_by,
            )

        return Pagination[EpisodeResponse](
            items=[EpisodeResponse.model_validate(episode) for episode in episodes],
            offset=offset,
            total=total,
        )

    async def get_details(
        self,
        episode_id: int,
        current_user: User,
        user_scope: OwnerScope,
    ) -> EpisodeResponse:
        """Return details for an episode owned by the current user."""
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session, scope=user_scope)
            episode = await episode_repository.get(episode_id)

        logger.info(
            "[API] Requested episode details for user #%i | episode #%i",
            current_user.id,
            episode_id,
        )
        return EpisodeResponse.model_validate(episode)

    async def update(
        self,
        episode_id: int,
        user_scope: OwnerScope,
        data: EpisodePatchSchema,
    ) -> EpisodeResponse:
        """Update editable fields for an episode owned by the current user."""
        update_data = data.update_data
        if not update_data:
            raise InvalidParametersAPIError(details="No update fields provided")

        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session, scope=user_scope)
            episode = await episode_repository.first(id=episode_id)
            if episode is None:
                raise NotFoundAPIError(details=f"Episode with id {episode_id} not found")
            await episode_repository.update(episode, **update_data)
            await uow.flush()
            uow.mark_for_commit()

        return EpisodeResponse.model_validate(episode)

    async def delete(
        self,
        episode_id: int,
        user_scope: OwnerScope,
    ) -> None:
        """Delete an episode owned by the current user."""
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session, scope=user_scope)
            episode = await episode_repository.first_for_update(id=episode_id)
            if episode is None:
                raise NotFoundAPIError(details=f"Episode with id {episode_id} not found")
            try:
                await episode_repository.safe_delete(episode)
            except ValueError as exc:
                raise StateConflictAPIError(details=str(exc)) from exc

            uow.mark_for_commit()

    async def download(
        self,
        queue: TaskQueue,
        episode_id: int,
        user_scope: OwnerScope,
    ) -> EpisodeResponse:
        """Start downloading or processing an episode."""
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session, scope=user_scope)
            episode = await episode_repository.first_for_update(id=episode_id)
            if episode is None:
                raise NotFoundAPIError(details=f"Episode with id {episode_id} not found")
            if episode.status in Episode.PROGRESS_STATUSES:
                raise StateConflictAPIError(details="Episode is already in progress")

            await episode_repository.update(episode, status=EpisodeStatus.DOWNLOADING)
            task_class = (
                tasks.UploadedEpisodeTask
                if episode.source_type == SourceType.UPLOAD
                else tasks.DownloadEpisodeTask
            )
            await uow.flush()
            uow.mark_for_commit()

        try:
            await self._run_task(queue, task_class, episode_id=episode.id)
        except Exception as exc:
            await self._mark_queue_failure(episode.id, user_scope)
            raise APIError(message="Unable to start episode processing.", status_code=503) from exc
        return EpisodeResponse.model_validate(episode)

    async def cancel_downloading(
        self,
        episode_id: int,
        current_user: User,
        user_scope: OwnerScope,
    ) -> EpisodeResponse:
        """Cancel the active download for an episode."""
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session, scope=user_scope)
            episode = await self._get_owned_episode(
                episode_repository,
                episode_id,
                current_user.id,
            )
            if episode.status != EpisodeStatus.DOWNLOADING:
                raise StateConflictAPIError(details="Episode is not downloading")

            await episode_repository.update(episode, status=EpisodeStatus.CANCELING)
            await uow.flush()
            uow.mark_for_commit()

        try:
            tasks.DownloadEpisodeTask.cancel_task(episode_id=episode.id)
            tasks.DownloadEpisodeImageTask.cancel_task(episode_id=episode.id)
            await publish_redis_stop_downloading(episode.id)
        except Exception as exc:
            async with SASessionUOW() as uow:
                repository = EpisodeRepository(uow.session, scope=user_scope)
                current = await repository.first(id=episode.id)
                if current is not None and current.status == EpisodeStatus.CANCELING:
                    await repository.update(current, status=EpisodeStatus.DOWNLOADING)
                    uow.mark_for_commit()
            raise APIError(
                message="Unable to request cancellation. Please try again.", status_code=503
            ) from exc
        return EpisodeResponse.model_validate(episode)
