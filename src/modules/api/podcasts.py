from datetime import datetime
from typing import Generic, TypeVar

from litestar import Request, get
from litestar.exceptions import NotFoundException
from pydantic import BaseModel, Field

from src.modules.api.base import BaseApiController
from src.modules.auth.load_user import get_current_user
from src.modules.db.models.podcasts import Podcast
from src.modules.db.repositories import PodcastRepository
from src.modules.db.services import SASessionUOW


class PodcastResponse(BaseModel):
    id: int
    name: str
    description: str | None
    created_at: datetime
    image_url: str | None = None
    rss_url: str | None = None
    download_automatically: bool
    episodes_count: int = 0


def podcast_to_response(podcast: Podcast) -> PodcastResponse:
    stat = podcast.stat
    return PodcastResponse(
        id=podcast.id,
        name=podcast.name,
        description=podcast.description,
        created_at=podcast.created_at,
        image_url=podcast.image.url if podcast.image else None,
        rss_url=podcast.rss_url,
        download_automatically=podcast.download_automatically,
        episodes_count=stat.episodes_count if stat else 0,
    )


ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class LimitOffsetPagination(Generic[ResponseModelT]):
    """
    Limit and offset pagination for API responses.
    """

    offset: int = Field(default=0, description="Offset of the first item to return")
    items: list[ResponseModelT] = Field(
        default_factory=list, description="List of items for requested limit and offset"
    )
    total: int = Field(default=0, description="Total number of items in the database")


class ListItemsPaginationRequest(BaseModel):
    offset: int = Field(default=0, description="Offset of the first item to return")
    limit: int = Field(default=10, description="Number of items to return")
    sort_by: str = Field(default="id", description="Field to sort by")


class PodcastApiController(BaseApiController):
    path = "/api/podcasts"
    tags = ["Podcasts"]

    @get("/", return_dto=LimitOffsetPagination[PodcastResponse], dto=ListItemsPaginationRequest)
    async def get_list(
        self,
        request: Request,
        request_data: ListItemsPaginationRequest,
    ) -> LimitOffsetPagination[PodcastResponse]:
        current_user = get_current_user(request)
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcasts, total = await podcast_repository.all_with_aggregations(
                limit=request_data.limit,
                offset=request_data.offset,
                sort_by=request_data.sort_by,
                owner_id=current_user.id,
            )

        return LimitOffsetPagination(
            items=[
                podcast_to_response(podcast) for podcast in sorted(podcasts, key=lambda p: p.id)
            ],
            total=total,
        )

    @get("/{podcast_id:int}/")
    async def get_podcast(self, podcast_id: int, request: Request) -> PodcastResponse:
        current_user = get_current_user(request)
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcasts, _ = await podcast_repository.all_with_aggregations(
                ids=[podcast_id],
                owner_id=current_user.id,
            )

        if not podcasts:
            raise NotFoundException(f"Podcast with id {podcast_id} not found")

        return podcast_to_response(podcasts[0])
