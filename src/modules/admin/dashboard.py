from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.constants import format_file_size
from src.modules.db.models.podcasts import EpisodeStatus
from src.modules.db.repositories import (
    EpisodeRepository,
    FileRepository,
    PodcastRepository,
    UserRepository,
)


async def collect_dashboard_stats(
    session: AsyncSession,
) -> dict[str, Any]:
    """Collect aggregate data for the admin dashboard."""
    user_repository = UserRepository(session)
    episode_repository = EpisodeRepository(session)
    total_users = await user_repository.get_total_count()
    active_users = await user_repository.get_total_count(is_active=True)
    podcasts = await PodcastRepository(session).get_total_count()
    episodes = await episode_repository.get_total_count()
    media_storage_usage = await FileRepository(session).get_total_size()
    status_counts = await episode_repository.count_by_status()

    grouped_by_status = {status.value: 0 for status in EpisodeStatus}
    for status, count in status_counts.items():
        grouped_by_status[status.value] = count

    return {
        "total_users": total_users,
        "active_users": active_users,
        "podcasts": podcasts,
        "episodes": episodes,
        "episodes_by_status": grouped_by_status,
        "media_storage_usage": media_storage_usage,
        "media_storage_usage_label": format_file_size(media_storage_usage),
    }
