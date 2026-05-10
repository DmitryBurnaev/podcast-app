import logging
from litestar import get
from litestar.exceptions import NotFoundException

from src.modules.db import User
from src.modules.db.repositories import PodcastOrderT
from src.schemas import PodcastResponse, LimitOffsetPagination
from src.modules.api.base import BaseApiController
from src.modules.db.repositories import PodcastRepository
from src.modules.db.services import SASessionUOW

logger = logging.getLogger(__name__)


class PodcastAPIController(BaseApiController):
    path = "/api/podcasts"
    tags = ["Podcasts"]

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
