import logging
from typing import Any

from starlette.requests import Request

from src.modules.admin.utils import format_instance_details_link
from src.modules.admin.forms import CookieAdminForm
from src.modules.db.models import Podcast, Episode, Cookie
from src.modules.admin.views.base import BaseModelView, mask_secret
from src.utils import utcnow

__all__ = (
    "PodcastAdminView",
    "EpisodeAdminView",
    "CookieAdminView",
)
logger = logging.getLogger(__name__)
MASKED_SECRET = "********"


class PodcastAdminView(BaseModelView, model=Podcast):
    name = "Podcast"
    name_plural = "Podcasts"
    icon = "fa-solid fa-podcast"
    column_list = (
        Podcast.id,
        Podcast.name,
        Podcast.publish_id,
        Podcast.owner_id,
        Podcast.download_automatically,
        Podcast.created_at,
    )
    column_searchable_list = (Podcast.name, Podcast.publish_id)
    column_sortable_list = (Podcast.id, Podcast.name, Podcast.created_at)
    column_default_sort = (Podcast.id, True)
    column_details_list = (
        Podcast.id,
        Podcast.name,
        Podcast.owner,
        Podcast.description,
        Podcast.created_at,
        Podcast.publish_id,
        Podcast.download_automatically,
    )
    column_formatters = {Podcast.id: format_instance_details_link}


class EpisodeAdminView(BaseModelView, model=Episode):
    name = "Episode"
    name_plural = "Episodes"
    icon = "fa-solid fa-headphones"
    column_list = [
        Episode.id,
        Episode.title,
        Episode.status,
        Episode.source_type,
        Episode.podcast_id,
        Episode.owner_id,
        Episode.created_at,
        Episode.published_at,
    ]
    column_searchable_list = [Episode.title, Episode.source_id, Episode.watch_url]
    column_sortable_list = [Episode.id, Episode.title, Episode.created_at, Episode.published_at]
    column_default_sort = (Episode.id, True)
    column_formatters = {Episode.id: format_instance_details_link}


class CookieAdminView(BaseModelView, model=Cookie):
    name = "Cookie"
    name_plural = "Cookies"
    icon = "fa-solid fa-cookie-bite"
    can_export = False
    form = CookieAdminForm
    column_list = (
        Cookie.id,
        Cookie.source_type,
        Cookie.owner_id,
        Cookie.created_at,
        Cookie.updated_at,
    )
    column_details_list = (
        Cookie.id,
        Cookie.source_type,
        Cookie.owner_id,
        Cookie.created_at,
        Cookie.updated_at,
    )
    column_sortable_list = (Cookie.id, Cookie.source_type, Cookie.created_at, Cookie.updated_at)
    column_default_sort = (Cookie.id, True)
    column_formatters = {"data": mask_secret, Cookie.id: format_instance_details_link}
    column_formatters_detail = {"data": mask_secret}

    async def insert_model(self, request: Request, data: dict[str, Any]) -> Cookie:
        raw_data = str(data.pop("raw_data") or "")
        if not raw_data:
            raise ValueError("Cookie data is required.")

        cookie = Cookie(
            source_type=data["source_type"],
            data=Cookie.get_encrypted_data(raw_data),
            owner_id=int(data["owner_id"]),
        )
        async with self.session_maker(expire_on_commit=False) as session:
            # TODO: use repository instead!
            session.add(cookie)
            await session.commit()
        return cookie

    async def update_model(self, request: Request, pk: str, data: dict[str, Any]) -> Cookie:
        # TODO: use repository instead!
        raw_data = str(data.pop("raw_data") or "")
        async with self.session_maker(expire_on_commit=False) as session:
            cookie = await session.scalar(self._stmt_by_identifier(pk))
            if cookie is None:
                raise ValueError("Cookie not found.")

            cookie.source_type = data["source_type"]
            cookie.owner_id = int(data["owner_id"])
            cookie.updated_at = utcnow()
            if raw_data:
                cookie.data = Cookie.get_encrypted_data(raw_data)

            await session.commit()
            return cookie
