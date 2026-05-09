import logging

from litestar import get
from litestar.exceptions import NotFoundException

from src.modules.db import User
from src.schemas import EpisodeResponse, LimitOffsetPagination
from src.modules.api.base import BaseApiController
from src.modules.db.repositories import EpisodeOrderT, EpisodeRepository
from src.modules.db.services import SASessionUOW

logger = logging.getLogger(__name__)


class EpisodeApiController(BaseApiController):
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
        """
        Get paginated list of episodes (for current user) with pagination

        Args:
            current_user: Current user
            limit: Limit of episodes to return
            offset: Offset of episodes to return
            order_by: Order by field

        Returns:
            Paginated list of episodes
        """
        logger.info("[API] Getting paginated list of episodes | user #%i", current_user.id)
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episodes, total = await episode_repository.all_paginated(
                owner_id=current_user.id,
                limit=limit,
                offset=offset,
                order_by=order_by,
            )

        logger.info(
            "[API] Returned episodes list | user #%i | found %i episodes, total: %i",
            current_user.id,
            len(episodes),
            total,
        )
        return LimitOffsetPagination[EpisodeResponse](
            items=[EpisodeResponse.model_validate(episode) for episode in episodes],
            offset=offset,
            total=total,
        )

    @get("/{episode_id:int}/")
    async def get_details(self, episode_id: int, current_user: User) -> EpisodeResponse:
        """
        Get details of an episode

        Args:
            episode_id: ID of the episode
            current_user: Current user

        Returns:
            Details of the episode
        """
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episode = await episode_repository.first(id=episode_id, owner_id=current_user.id)

        if not episode:
            raise NotFoundException(f"Episode with id {episode_id} not found")

        logger.info(
            "[API] Requested episode details for user #%i | episode #%i",
            current_user.id,
            episode_id,
        )
        return EpisodeResponse.model_validate(episode)
