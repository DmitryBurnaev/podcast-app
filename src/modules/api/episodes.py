import logging
from datetime import datetime

from litestar import Request, get
from litestar.exceptions import NotFoundException
from pydantic import BaseModel

from modules.db import User
from schemas import LimitOffsetPagination
from src.modules.api.base import BaseApiController
from src.modules.auth.load_user import get_current_user
from src.modules.db.models.podcasts import Episode
from src.modules.db.repositories import EpisodeRepository, EpisodeOrderT
from src.modules.db.services import SASessionUOW

logger = logging.getLogger(__name__)


class EpisodeResponse(BaseModel):
    id: int
    title: str
    created_at: datetime


def episode_to_response(episode: Episode) -> EpisodeResponse:
    return EpisodeResponse(
        id=episode.id,
        title=episode.title,
        created_at=episode.created_at,
    )


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
            episode_repository = EpisodeRepository(session=uow.session)
            episodes, total = await episode_repository.all_paginated(
                owner_id=current_user.id,
                limit=limit,
                offset=offset,
                order_by=order_by,
            )

        logger.info(
            "[API] Returned episodes list | user #%i | found %i podcasts, total: %i",
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
    async def get_episode(self, episode_id: int, request: Request) -> EpisodeResponse:
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episode = await episode_repository.get(episode_id)

        if not episode:
            raise NotFoundException(f"Episode with id {episode_id} not found")

        return episode_to_response(episode)
