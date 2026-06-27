from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import format_file_size
from src.modules.db.models import File, User
from src.modules.db.models.podcasts import Episode, EpisodeStatus, Podcast


async def collect_dashboard_stats(
    session_maker: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """Collect aggregate data for the admin dashboard."""
    async with session_maker() as session:
        total_users = await session.scalar(select(func.count(User.id)))
        active_users = await session.scalar(
            select(func.count(User.id)).where(User.is_active.is_(True))
        )
        podcasts = await session.scalar(select(func.count(Podcast.id)))
        episodes = await session.scalar(select(func.count(Episode.id)))
        media_storage_usage = await session.scalar(select(func.coalesce(func.sum(File.size), 0)))

        grouped_rows = await session.execute(
            select(Episode.status, func.count(Episode.id)).group_by(Episode.status)
        )

    grouped_by_status = {status.value: 0 for status in EpisodeStatus}
    for status, count in grouped_rows.all():
        grouped_by_status[str(status)] = count

    return {
        "total_users": total_users or 0,
        "active_users": active_users or 0,
        "podcasts": podcasts or 0,
        "episodes": episodes or 0,
        "episodes_by_status": grouped_by_status,
        "media_storage_usage": media_storage_usage or 0,
        "media_storage_usage_label": format_file_size(media_storage_usage or 0),
    }
