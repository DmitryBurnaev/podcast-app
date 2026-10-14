from typing import cast

from litestar import Request, delete, get, patch, post, put
from litestar.di import NamedDependency
from litestar.params import FromPath, FromQuery, JSONBody
from litestar.status_codes import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from src.modules.api.base import BaseApiController
from src.modules.common.types import OwnerScope
from src.modules.db import User
from src.modules.db.repositories import (
    EpisodeOrderT,
)
from src.modules.schemas.common import Pagination
from src.modules.schemas.episodes import (
    EpisodeCreateNestedSchema,
    EpisodePatchSchema,
    EpisodeResponse,
    UploadedEpisodeCreateSchema,
    UploadedEpisodeResponse,
)
from src.modules.services.episodes_operations import (
    EpisodeOperations,
    PodcastEpisodeOperations,
)
from src.modules.services.task_queue import TaskQueueApp


class PodcastEpisodeAPIController(BaseApiController):
    path = "/api/podcasts/{podcast_id:int}/episodes"
    tags = ["Episodes"]

    @get("/")
    async def get_list(
        self,
        podcast_id: FromPath[int],
        request: Request,
        user_scope: NamedDependency[OwnerScope],
        limit: FromQuery[int] = 10,
        offset: FromQuery[int] = 0,
        order_by: FromQuery[EpisodeOrderT] = "-created_at",
    ) -> Pagination[EpisodeResponse]:
        return await PodcastEpisodeOperations().get_list(
            podcast_id=podcast_id,
            queue=cast(TaskQueueApp, request.app).rq_queue,
            user_scope=user_scope,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    @post("/", status_code=HTTP_201_CREATED)
    async def create(
        self,
        request: Request,
        podcast_id: FromPath[int],
        current_user: NamedDependency[User],
        user_scope: NamedDependency[OwnerScope],
        data: JSONBody[EpisodeCreateNestedSchema],
    ) -> EpisodeResponse:
        return await PodcastEpisodeOperations().create(
            queue=cast(TaskQueueApp, request.app).rq_queue,
            podcast_id=podcast_id,
            current_user=current_user,
            user_scope=user_scope,
            data=data,
        )

    @post("/uploaded/", status_code=HTTP_201_CREATED)
    async def create_uploaded(
        self,
        request: Request,
        podcast_id: FromPath[int],
        data: JSONBody[UploadedEpisodeCreateSchema],
        user_scope: NamedDependency[OwnerScope],
    ) -> EpisodeResponse:
        return await PodcastEpisodeOperations().create_uploaded(
            queue=cast(TaskQueueApp, request.app).rq_queue,
            podcast_id=podcast_id,
            data=data,
            user_scope=user_scope,
        )

    @get("/uploaded/{hash:str}/")
    async def get_uploaded(
        self,
        podcast_id: FromPath[int],
        hash: FromPath[str],
        request: Request,
        user_scope: NamedDependency[OwnerScope],
    ) -> UploadedEpisodeResponse:
        return await PodcastEpisodeOperations().get_uploaded(
            podcast_id=podcast_id,
            hash=hash,
            queue=cast(TaskQueueApp, request.app).rq_queue,
            user_scope=user_scope,
        )


class EpisodeAPIController(BaseApiController):
    path = "/api/episodes"
    tags = ["Episodes"]

    @get("/")
    async def get_list(
        self,
        current_user: NamedDependency[User],
        user_scope: NamedDependency[OwnerScope],
        limit: FromQuery[int] = 10,
        offset: FromQuery[int] = 0,
        order_by: FromQuery[EpisodeOrderT] = "-created_at",
    ) -> Pagination[EpisodeResponse]:
        return await EpisodeOperations().get_list(
            current_user=current_user,
            user_scope=user_scope,
            limit=limit,
            offset=offset,
            order_by=order_by,
        )

    @get("/{episode_id:int}/")
    async def get_details(
        self,
        episode_id: FromPath[int],
        current_user: NamedDependency[User],
        user_scope: NamedDependency[OwnerScope],
    ) -> EpisodeResponse:
        return await EpisodeOperations().get_details(
            episode_id=episode_id, current_user=current_user, user_scope=user_scope
        )

    @patch("/{episode_id:int}/")
    async def update(
        self,
        episode_id: FromPath[int],
        user_scope: NamedDependency[OwnerScope],
        data: JSONBody[EpisodePatchSchema],
    ) -> EpisodeResponse:
        return await EpisodeOperations().update(
            episode_id=episode_id, user_scope=user_scope, data=data
        )

    @delete("/{episode_id:int}/", status_code=HTTP_204_NO_CONTENT)
    async def delete(
        self,
        episode_id: FromPath[int],
        user_scope: NamedDependency[OwnerScope],
    ) -> None:
        return await EpisodeOperations().delete(episode_id=episode_id, user_scope=user_scope)

    @put("/{episode_id:int}/download/")
    async def download(
        self,
        request: Request,
        episode_id: FromPath[int],
        user_scope: NamedDependency[OwnerScope],
    ) -> EpisodeResponse:
        return await EpisodeOperations().download(
            queue=cast(TaskQueueApp, request.app).rq_queue,
            episode_id=episode_id,
            user_scope=user_scope,
        )

    @put("/{episode_id:int}/cancel-downloading/")
    async def cancel_downloading(
        self,
        episode_id: FromPath[int],
        current_user: NamedDependency[User],
        user_scope: NamedDependency[OwnerScope],
    ) -> EpisodeResponse:
        return await EpisodeOperations().cancel_downloading(
            episode_id=episode_id, current_user=current_user, user_scope=user_scope
        )
