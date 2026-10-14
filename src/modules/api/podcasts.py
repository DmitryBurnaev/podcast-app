from typing import cast

from litestar import Request, delete, get, patch, post, put
from litestar.datastructures import UploadFile
from litestar.di import NamedDependency
from litestar.params import FromPath, FromQuery, JSONBody, MultipartBody
from litestar.status_codes import HTTP_201_CREATED, HTTP_204_NO_CONTENT

from src.modules.api.base import BaseApiController
from src.modules.common.types import OwnerScope
from src.modules.db import User
from src.modules.db.repositories import (
    PodcastOrderT,
)
from src.modules.schemas.common import Pagination
from src.modules.schemas.podcasts import (
    PodcastCreateRequest,
    PodcastResponse,
    PodcastTaskResponse,
    PodcastUpdateRequest,
)
from src.modules.services.podcasts_operations import PodcastOperations
from src.modules.services.task_queue import TaskQueueApp


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
        return await PodcastOperations().create(
            data=data, current_user=current_user, user_scope=user_scope
        )

    @get("/")
    async def get_list(
        self,
        user_scope: NamedDependency[OwnerScope],
        limit: FromQuery[int] = 10,
        offset: FromQuery[int] = 0,
        order_by: FromQuery[PodcastOrderT] = "-created_at",
    ) -> Pagination[PodcastResponse]:
        return await PodcastOperations().get_list(
            user_scope=user_scope, limit=limit, offset=offset, order_by=order_by
        )

    @get("/{podcast_id:int}/")
    async def get_details(
        self,
        podcast_id: FromPath[int],
        user_scope: NamedDependency[OwnerScope],
    ) -> PodcastResponse:
        return await PodcastOperations().get_details(podcast_id=podcast_id, user_scope=user_scope)

    @patch("/{podcast_id:int}/")
    async def update(
        self,
        podcast_id: FromPath[int],
        data: JSONBody[PodcastUpdateRequest],
        current_user: NamedDependency[User],
        user_scope: NamedDependency[OwnerScope],
    ) -> PodcastResponse:
        return await PodcastOperations().update(
            podcast_id=podcast_id, data=data, current_user=current_user, user_scope=user_scope
        )

    @delete("/{podcast_id:int}/", status_code=HTTP_204_NO_CONTENT)
    async def delete(
        self,
        podcast_id: FromPath[int],
        user_scope: NamedDependency[OwnerScope],
    ) -> None:
        return await PodcastOperations().delete(podcast_id=podcast_id, user_scope=user_scope)

    @post("/{podcast_id:int}/upload-image/")
    async def upload_image(
        self,
        podcast_id: FromPath[int],
        data: MultipartBody[dict[str, UploadFile]],
        user_scope: NamedDependency[OwnerScope],
    ) -> PodcastResponse:
        return await PodcastOperations().upload_image(
            podcast_id=podcast_id, data=data, user_scope=user_scope
        )

    @put("/{podcast_id:int}/generate-rss/")
    async def generate_rss(
        self,
        podcast_id: FromPath[int],
        request: Request,
        current_user: NamedDependency[User],
        user_scope: NamedDependency[OwnerScope],
    ) -> PodcastTaskResponse:
        return await PodcastOperations().generate_rss(
            podcast_id=podcast_id,
            queue=cast(TaskQueueApp, request.app).rq_queue,
            current_user=current_user,
            user_scope=user_scope,
        )
