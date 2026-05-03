from datetime import datetime

from litestar import Request, get
from litestar.exceptions import NotFoundException
from pydantic import BaseModel

from modules.dto.podcasts import PodcastListDTO
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


class PodcastApiController(BaseApiController):
    path = "/api/podcasts"
    tags = ["Podcasts"]

    @get("/", return_dto=PodcastListDTO)
    async def get_list(self, request: Request) -> list[PodcastResponse]:
        current_user = get_current_user(request)
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcasts = await podcast_repository.all_with_aggregations(owner_id=current_user.id)

        return [podcast_to_response(podcast) for podcast in sorted(podcasts, key=lambda p: p.id)]

    @get("/{podcast_id:int}/")
    async def get_podcast(self, podcast_id: int, request: Request) -> PodcastResponse:
        current_user = get_current_user(request)
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcasts = await podcast_repository.all_with_aggregations(
                ids=[podcast_id],
                owner_id=current_user.id,
            )

        if not podcasts:
            raise NotFoundException(f"Podcast with id {podcast_id} not found")

        return podcast_to_response(podcasts[0])
