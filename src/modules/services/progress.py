import logging

from src.modules.common.types import OwnerScope
from src.modules.db.repositories import EpisodeRepository, PodcastRepository
from src.modules.db.services import SASessionUOW
from src.modules.schemas.progress import (
    ProgressEpisodeResponse,
    ProgressItemResponse,
    ProgressPodcastResponse,
)
from src.modules.utils.processing import check_state

logger = logging.getLogger(__name__)


class ProgressOperations:

    async def get_progress(
        self,
        user_scope: OwnerScope,
        episode_id: int | None = None,
    ) -> dict[str, list[ProgressItemResponse]]:
        """Return active processing progress for the current user."""
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(uow.session, scope=user_scope)
            podcast_repository = PodcastRepository(uow.session, scope=user_scope)
            podcasts = {podcast.id: podcast for podcast in await podcast_repository.all()}
            if episode_id:
                episode = await episode_repository.first(id=episode_id)
                episodes = [episode] if episode else []
            else:
                episodes = await episode_repository.all_in_progress()

            states = await check_state(episodes)

        progress_items: list[ProgressItemResponse] = []
        episodes_by_id = {episode.id: episode for episode in episodes}
        for state in states:
            episode = episodes_by_id[state["episode_id"]]
            podcast = podcasts.get(state["podcast_id"])
            progress_items.append(
                ProgressItemResponse(
                    status=str(state["status"]),
                    completed=state["completed"],
                    current_file_size=state["current_file_size"],
                    total_file_size=state["total_file_size"],
                    episode=ProgressEpisodeResponse.model_validate(episode, from_attributes=True),
                    podcast=(
                        ProgressPodcastResponse.model_validate(podcast, from_attributes=True)
                        if podcast
                        else None
                    ),
                )
            )

        return {"progressItems": progress_items}
