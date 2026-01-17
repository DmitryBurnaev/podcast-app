from litestar import get, Request
from litestar.response import Template
from litestar.exceptions import NotFoundException

from src import constants as const
from src.modules.db import SASessionUOW
from src.modules.db.repositories import PodcastRepository, EpisodeRepository
from src.modules.views.base import BaseController


class PodcastsController(BaseController):
    @get("/podcasts/")
    async def get(self, request: Request) -> Template:
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcasts = await podcast_repository.all(owner_id=1)

        return self.get_response_template(
            template_name="podcasts.html",
            context={
                "podcasts": podcasts,
                "current": "podcasts",
                "format_duration": const.format_duration,
                "format_file_size": const.format_file_size,
            },
        )


class PodcastsDetailsController(BaseController):
    @get("/podcasts/{podcast_id:int}/")
    async def get_detail(self, podcast_id: int, request: Request) -> Template:
        """Get podcast detail page with episodes list"""
        async with SASessionUOW() as uow:
            podcast_repository = PodcastRepository(session=uow.session)
            podcast = await podcast_repository.get_by_id(podcast_id)
            if not podcast:
                raise NotFoundException(f"Podcast with id {podcast_id} not found")

            # Get episodes through backref relationship
            # Episodes are loaded via lazy="subquery" in the relationship

            episodes = list(podcast.episodes)
            # Sort episodes by published_at or created_at (newest first)
            episodes.sort(
                key=lambda e: e.published_at if e.published_at else e.created_at, reverse=True
            )

            # Calculate podcast statistics
            # TODO: just for demo! replace with DB functions!
            episodes_count = len(episodes)
            total_duration = sum(episode.length for episode in episodes if episode.length) or 0
            total_size = (
                sum(
                    episode.audio.size
                    for episode in episodes
                    if episode.audio and hasattr(episode.audio, "size") and episode.audio.size
                )
                or 0
            )
            last_published_at = (
                max((e.published_at for e in episodes if e.published_at), default=None)
                if episodes
                else None
            )
            last_created_at = (
                max((e.created_at for e in episodes if e.created_at), default=None)
                if episodes
                else None
            )

            # Generate RSS URL
            rss_url = None
            if podcast.rss:
                rss_url = podcast.rss.url

        return self.get_response_template(
            template_name="podcasts_detail.html",
            context={
                "podcast": podcast,
                "episodes": episodes,
                "episodes_count": episodes_count,
                "total_duration": total_duration,
                "total_size": total_size,
                "last_published_at": last_published_at,
                "last_created_at": last_created_at,
                "rss_url": rss_url,
                "current": "podcasts",
            },
        )


class EpisodesController(BaseController):
    @get("/episodes")
    async def get(self, request: Request) -> Template:
        # Get filter parameters from query string
        query_params = request.query_params
        filters = {
            "status": query_params.get("status"),
            "size_min": query_params.get("size_min"),
            "size_max": query_params.get("size_max"),
            "podcast": query_params.get("podcast"),
            "search": query_params.get("search"),
        }

        # # Remove None values
        # filters = {k: v for k, v in filters.items() if v is not None}
        #
        # # Apply filters
        # filtered_episodes = (
        #     const.filter_episodes(const.EPISODES, filters) if filters else const.EPISODES
        # )
        async with SASessionUOW() as uow:
            episodes_repository = EpisodeRepository(session=uow.session)
            episodes = await episodes_repository.all()

        return self.get_response_template(
            template_name="episodes.html",
            context={
                "episodes": episodes,
                "podcasts": const.PODCASTS,
                "filters": filters,
                "current": "episodes",
            },
        )


class EpisodeDetailsController(BaseController):
    @get("/episodes/{episode_id:int}/")
    async def get_detail(self, episode_id: int, request: Request) -> Template:
        """Get episode detail page with edit form"""
        async with SASessionUOW() as uow:
            episode_repository = EpisodeRepository(session=uow.session)
            episode = await episode_repository.get_by_id(episode_id)
            if not episode:
                raise NotFoundException(f"Episode with id {episode_id} not found")

            # Get related podcast
            podcast = episode.podcast

            # Prepare audio URL
            audio_url = None
            if episode.audio:
                audio_url = episode.audio.url if hasattr(episode.audio, "url") else None

            # Prepare source URL (watch_url)
            source_url = episode.watch_url

            # Calculate episode size
            episode_size = 0
            if episode.audio and hasattr(episode.audio, "size") and episode.audio.size:
                episode_size = episode.audio.size

        return self.get_response_template(
            template_name="episode_detail.html",
            context={
                "episode": episode,
                "podcast": podcast,
                "audio_url": audio_url,
                "source_url": source_url,
                "episode_size": episode_size,
                "current": "episodes",
            },
        )
