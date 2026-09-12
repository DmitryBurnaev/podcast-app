import logging
from typing import Any

from starlette.requests import Request

from src.modules.admin.utils import (
    format_instance_details_link,
    format_datetime,
    format_bool,
    format_source_type,
    format_episode_details_link,
)
from src.modules.admin.forms import LongTextAreaField
from src.modules.db.models import Podcast, Episode, Cookie
from src.modules.admin.views.base import BaseModelView
from src.modules.utils.common import utcnow

__all__ = (
    "PodcastAdminView",
    "EpisodeAdminView",
    "CookieAdminView",
)
logger = logging.getLogger(__name__)


class PodcastAdminView(BaseModelView, model=Podcast):
    """Configure list and edit screens for podcasts."""

    name = "Podcast"
    name_plural = "Podcasts"
    icon = "fa-solid fa-podcast"
    column_list = (
        Podcast.id,
        Podcast.publish_id,
        Podcast.download_automatically,
        Podcast.owner_id,
        Podcast.created_at,
    )
    column_searchable_list = (Podcast.name, Podcast.publish_id)
    column_sortable_list = (Podcast.id, Podcast.name, Podcast.owner_id, Podcast.created_at)
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
    form_columns = (
        Podcast.name,
        Podcast.owner,
        Podcast.description,
        Podcast.created_at,
        Podcast.publish_id,
        Podcast.download_automatically,
    )
    column_formatters = {
        Podcast.id: format_instance_details_link,
        Podcast.created_at: format_datetime,
        Podcast.download_automatically: format_bool,
    }
    form_overrides = {"description": LongTextAreaField}
    can_view_details = False
    column_labels = {
        Podcast.id: "ID",
        Podcast.name: "Name",
        Podcast.publish_id: "Publish ID",
        Podcast.download_automatically: "Auto Download",
        Podcast.created_at: "Created At",
        Podcast.owner_id: "Owner",
    }


class EpisodeAdminView(BaseModelView, model=Episode):
    """Configure list and edit screens for podcast episodes."""

    name = "Episode"
    name_plural = "Episodes"
    icon = "fa-solid fa-headphones"
    column_list = (
        Episode.id,
        Episode.source_type,
        Episode.podcast,
        Episode.owner_id,
        Episode.created_at,
        Episode.published_at,
    )
    column_searchable_list = (Episode.title, Episode.source_id, Episode.watch_url)
    column_sortable_list = (Episode.id, Episode.title, Episode.created_at, Episode.published_at)
    column_default_sort = (Episode.id, True)
    column_formatters = {
        Episode.id: format_episode_details_link,
        Episode.created_at: format_datetime,
        Episode.published_at: format_datetime,
        Episode.source_type: format_source_type,
    }
    form_overrides = {"description": LongTextAreaField}
    column_labels = {
        Episode.id: "ID",
        Episode.created_at: "Created At",
        Episode.owner_id: "Owner",
        Episode.podcast: "Podcast",
        Episode.source_type: "S",
        Episode.published_at: "Publish",
    }


class CookieAdminView(BaseModelView, model=Cookie):
    """Configure list and edit screens for encrypted source cookies."""

    name = "Cookie"
    name_plural = "Cookies"
    icon = "fa-solid fa-cookie-bite"
    can_export = False
    column_list = (
        Cookie.id,
        Cookie.source_type,
        Cookie.owner,
        Cookie.created_at,
        Cookie.updated_at,
    )
    column_details_list = (
        Cookie.id,
        Cookie.source_type,
        Cookie.owner,
        Cookie.created_at,
        Cookie.updated_at,
    )
    form_columns = (Cookie.id, Cookie.source_type, Cookie.data)
    column_sortable_list = (Cookie.id, Cookie.source_type, Cookie.created_at, Cookie.updated_at)
    column_default_sort = (Cookie.id, True)
    column_formatters = {
        Cookie.id: format_instance_details_link,
        Cookie.created_at: format_datetime,
        Cookie.updated_at: format_datetime,
    }

    async def insert_model(self, request: Request, data: dict[str, Any]) -> Cookie:
        """Encrypt required cookie data before delegating creation to the base view."""
        raw_data = str(data.pop("raw_data") or "")
        if not raw_data:
            raise ValueError("Cookie data is required.")

        data["data"] = Cookie.get_encrypted_data(raw_data)
        return await super().insert_model(request, data)

    async def update_model(self, request: Request, pk: str, data: dict[str, Any]) -> Cookie:
        """Encrypt replacement data and timestamp the cookie before the base update."""
        raw_data = str(data.pop("raw_data") or "")
        data["updated_at"] = utcnow()
        if raw_data:
            data["data"] = Cookie.get_encrypted_data(raw_data)

        return await super().update_model(request, pk, data)
