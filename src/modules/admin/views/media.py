from typing import Any

from sqlalchemy import Select
from sqlalchemy.orm import selectinload
from sqladmin import action
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from src.modules.admin.forms import ReadOnlyIntegerField, ReadOnlyTextField
from src.modules.admin.utils import (
    format_bool,
    format_file_size,
    format_instance_details_link,
    register_error_alert,
)
from src.modules.db.models import File
from src.modules.admin.views.base import BaseModelView
from src.modules.services.storage import (
    FileCleanupBatchResult,
    FileCleanupStatus,
    FileStorageCleanupService,
)

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

        order_by = (
            File.size.desc() if request.query_params.get("sort") == "desc" else File.size.asc()
        )
        return stmt.order_by(order_by.nulls_last())

    def form_edit_query(self, request: Request) -> Select:
        """Load episodes that reference the file for its read-only admin form."""
        return (
            super()
            .form_edit_query(request)
            .options(
                selectinload(File.audio_episodes),
                selectinload(File.image_episodes),
            )
        )

    async def update_model(self, request: Request, pk: str, data: dict[str, Any]) -> Any:
        """Keep media metadata immutable when saving other file settings."""
        data.pop("type", None)
        data.pop("size", None)
        return await super().update_model(request, pk, data)

    @action(
        name="clear-external-files",
        label="Clear external files",
        confirmation_message="Delete selected files from external storage?",
        add_in_detail=False,
    )
    async def clear_external_files(self, request: Request) -> Response:
        """Remove selected S3 objects in parallel while retaining File rows."""
        raw_ids = request.query_params.get("pks", "")
        try:
            file_ids = [int(value) for value in raw_ids.split(",") if value]
        except ValueError:
            register_error_alert(
                "External file cleanup failed",
                f"Invalid File IDs: {raw_ids!r}",
            )
        else:
            if not file_ids:
                register_error_alert(
                    "External file cleanup failed",
                    "Select at least one file to clean up.",
                )
            else:
                try:
                    result = await FileStorageCleanupService().clear_files(file_ids)
                except Exception as exc:
                    register_error_alert(
                        "External file cleanup failed",
                        str(exc),
                    )
                else:
                    if result.failures:
                        register_error_alert(
                            "External file cleanup completed with errors",
                            self._format_cleanup_failures(result),
                        )

        url = request.url_for("admin:list", identity=self.identity)
        return RedirectResponse(url=url, status_code=302)

    async def on_model_delete(self, model: File, request: Request) -> None:
        """Clear S3 first; SQLAdmin deletes the database row only after this hook."""
        result = await FileStorageCleanupService().clear_files([model.id])
        item = result.items[0]
        if item.status == FileCleanupStatus.FAILED:
            details = self._format_cleanup_failures(result)
            register_error_alert("File deletion failed", details)
            raise RuntimeError(details)

    @staticmethod
    def _format_cleanup_failures(result: FileCleanupBatchResult) -> str:
        """Build alert details suitable for the global admin error overlay."""
        return "; ".join(
            f"File #{item.file_id} ({item.path or 'no S3 path'}): {item.error or 'unknown error'}"
            for item in result.failures
        )
