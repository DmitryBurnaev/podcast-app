from typing import Any

from sqlalchemy import Select
from sqlalchemy.orm import selectinload
from starlette.requests import Request

from src.modules.admin.forms import ReadOnlyIntegerField, ReadOnlyTextField
from src.modules.admin.utils import format_bool, format_instance_details_link, format_file_size
from src.modules.db.models import File
from src.modules.admin.views.base import BaseModelView

__all__ = ("MediaFileAdminView",)


class MediaFileAdminView(BaseModelView, model=File):
    """Configure list and edit screens for stored media files."""

    name = "File"
    name_plural = "Files"
    icon = "fa-solid fa-file-audio"
    edit_template = "media_edit.html"
    column_list = (
        File.id,
        File.type,
        File.size,
        File.available,
        File.public,
        File.owner_id,
    )
    form_columns = (
        File.id,
        File.type,
        File.size,
        File.available,
        File.public,
    )
    can_create = False
    column_searchable_list = (File.path, File.source_url, File.hash)
    column_sortable_list = (File.id, File.path, File.size, File.created_at)
    column_default_sort = (File.id, True)
    column_formatters = {
        File.id: format_instance_details_link,
        File.size: format_file_size,
        File.available: format_bool,
        File.public: format_bool,
    }
    form_overrides = {
        "type": ReadOnlyTextField,
        # "public": ReadOnlyBoolField,
        "size": ReadOnlyIntegerField,
    }
    column_labels = {
        File.id: "ID",
        File.type: "Type",
        File.size: "Size",
        File.available: "Available",
        File.public: "Public",
        File.owner_id: "Owner",
    }

    def sort_query(self, stmt: Select, request: Request) -> Select:
        """Keep media rows with an unknown size at the end of size-sorted lists."""
        if request.query_params.get("sortBy") != File.size.key:
            return super().sort_query(stmt, request)

        order_by = File.size.desc() if request.query_params.get("sort") == "desc" else File.size.asc()
        return stmt.order_by(order_by.nulls_last())

    def form_edit_query(self, request: Request) -> Select:
        """Load episodes that reference the file for its read-only admin form."""
        return super().form_edit_query(request).options(
            selectinload(File.audio_episodes),
            selectinload(File.image_episodes),
        )

    async def update_model(self, request: Request, pk: str, data: dict[str, Any]) -> Any:
        """Keep media metadata immutable when saving other file settings."""
        data.pop("type", None)
        data.pop("size", None)
        return await super().update_model(request, pk, data)
