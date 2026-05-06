from litestar import Request, get
from litestar.exceptions import NotFoundException

from src.schemas import PodcastResponse, LimitOffsetPagination
from src.modules.api.base import BaseApiController
from src.modules.auth.load_user import get_current_user
from src.modules.db.repositories import PodcastRepository
from src.modules.db.services import SASessionUOW


class PodcastApiController(BaseApiController):
    path = "/api/podcasts"
    tags = ["Podcasts"]

    @get("/")
    async def get_list(
        self,
        request: Request,
        limit: int = 10,
        offset: int = 0,
        sort_by: str = "id",
    ) -> LimitOffsetPagination[PodcastResponse]:
        current_user = get_current_user(request)
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcasts, total = await podcast_repository.all_with_aggregations(
                limit=limit,
                offset=offset,
                sort_by=sort_by,
                owner_id=current_user.id,
            )

        return LimitOffsetPagination[PodcastResponse](
            items=[PodcastResponse.model_validate(podcast) for podcast in podcasts],
            offset=offset,
            total=total,
        )

    @get("/{podcast_id:int}/")
    async def get_podcast(self, podcast_id: int, request: Request) -> PodcastResponse:
        current_user = get_current_user(request)
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            # TODO: implement stats calc for `get_first` method
            podcasts, _ = await podcast_repository.all_with_aggregations(
                ids=[podcast_id],
                owner_id=current_user.id,
            )

        if not podcasts:
            raise NotFoundException(f"Podcast with id {podcast_id} not found")

        return PodcastResponse.model_validate(podcasts[0])
