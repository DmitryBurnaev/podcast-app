from datetime import datetime

from litestar import Request, get
from litestar.exceptions import NotFoundException
from pydantic import BaseModel

from src.modules.api.base import BaseApiController
from src.modules.auth.load_user import get_current_user
from src.modules.db.models.podcasts import Episode
from src.modules.db.repositories import EpisodeRepository
from src.modules.db.services import SASessionUOW


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
    async def list_episodes(self, request: Request) -> list[EpisodeResponse]:
        current_user = get_current_user(request)
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episodes, _ = await episode_repository.all_paginated(owner_id=current_user.id)

        # TODO: support pagination
        return [episode_to_response(episode) for episode in sorted(episodes, key=lambda p: p.id)]

    @get("/{episode_id:int}/")
    async def get_episode(self, episode_id: int, request: Request) -> EpisodeResponse:
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episode = await episode_repository.get(episode_id)

        if not episode:
            raise NotFoundException(f"Episode with id {episode_id} not found")

        return episode_to_response(episode)
