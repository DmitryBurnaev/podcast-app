from dataclasses import dataclass
from typing import Any
import urllib.parse

from sqlalchemy import Select
from sqladmin import action
from starlette.requests import Request
from starlette.responses import RedirectResponse, Response

from src.modules.admin.forms import ReadOnlyIntegerField, ReadOnlyTextField
from src.modules.admin.utils import (
    format_bool,
    format_file_size,
    format_instance_details_link,
    register_error_alert,
    register_success_alert,
)
from src.modules.db import SASessionUOW
from src.modules.db.models import File
from src.modules.db.repositories import FileRepository
from src.modules.admin.views.base import BaseModelView
from src.modules.services.storage import (
    FileCleanupBatchResult,
    FileCleanupStatus,
    FileStorageCleanupService,
    get_file_presigned_url,
)
from src.settings.app import get_app_settings

__all__ = ("MediaFileAdminView",)


@dataclass(frozen=True, slots=True)
class S3AdminContext:
    """Safe S3 configuration details rendered in file admin headers."""

    host: str
    bucket: str
    badge_label: str
    badge_class: str


class MediaFileAdminView(BaseModelView, model=File):
    """Configure list and edit screens for stored media files."""

    name = "File"
    name_plural = "Files"
    icon = "fa-solid fa-file-audio"
    list_template = "media_list.html"
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

    async def get_object_for_edit(self, request: Request) -> Any:
        """Load the edit context through the File repository."""
        try:
            file_id = int(request.path_params["pk"])
        except KeyError, ValueError:
            return None

        async with SASessionUOW() as uow:
            repository = FileRepository(session=uow.session)
            media_file = await repository.first_with_episodes(file_id)
            if media_file is None:
                return None
            same_path_files = await repository.all_by_path(
                media_file.path,
                excluded_ids=(media_file.id,),
            )

        setattr(media_file, "same_path_files", same_path_files)
        return media_file

    async def update_model(self, request: Request, pk: str, data: dict[str, Any]) -> Any:
        """Keep media metadata immutable when saving other file settings."""
        data.pop("type", None)
        data.pop("size", None)
        return await super().update_model(request, pk, data)

    @property
    def s3_admin_context(self) -> S3AdminContext:
        """Return safe storage metadata for the file list and edit headers."""
        s3_settings = get_app_settings().s3
        if not s3_settings.storage_url:
            return S3AdminContext(
                host="No Config",
                bucket=s3_settings.bucket_name,
                badge_label="NO CONFIG",
                badge_class="bg-yellow-lt",
            )

        parsed = urllib.parse.urlsplit(s3_settings.storage_url)
        hostname = parsed.hostname or "No Config"
        try:
            port = parsed.port
        except ValueError:
            port = None
            hostname = "No Config"

        if hostname != "No Config" and ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        host = f"{hostname}:{port}" if port else hostname

        badge_class = "bg-green-lt" if s3_settings.env == "dev" else "bg-red-lt"
        if hostname == "No Config":
            badge_label = "NO CONFIG"
            badge_class = "bg-yellow-lt"
        else:
            badge_label = s3_settings.env.upper()

        return S3AdminContext(
            host=host,
            bucket=s3_settings.bucket_name,
            badge_label=badge_label,
            badge_class=badge_class,
        )

    @staticmethod
    def s3_content_label(file: File) -> str | None:
        """Return the bucket-relative object name shown on the edit page."""
        if not file.path:
            return None

        s3_settings = get_app_settings().s3
        return f"{s3_settings.bucket_name}/{file.path.lstrip('/')}"

    @action(
        name="open-external-file",
        label="Open external file",
        add_in_detail=False,
        add_in_list=False,
    )
    async def open_external_file(self, request: Request) -> Response:
        """Redirect an administrator to a freshly generated presigned S3 URL."""
        raw_id = request.query_params.get("pks", "")
        try:
            file_id = int(raw_id)
        except ValueError:
            register_error_alert(
                "External file preview failed",
                f"Invalid File ID: {raw_id!r}",
                request=request,
            )
            url = request.url_for("admin:list", identity=self.identity)
            return RedirectResponse(url=url, status_code=302)

        async with SASessionUOW() as uow:
            repository = FileRepository(session=uow.session)
            media_file = await repository.first(file_id)

        if media_file is None:
            register_error_alert(
                "External file preview failed",
                f"File #{file_id} not found",
                request=request,
            )
            url = request.url_for("admin:list", identity=self.identity)
            return RedirectResponse(url=url, status_code=302)

        try:
            presigned_url = await get_file_presigned_url(media_file)
        except Exception as exc:
            register_error_alert("External file preview failed", str(exc), request=request)
            url = request.url_for("admin:edit", identity=self.identity, pk=file_id)
            return RedirectResponse(url=url, status_code=302)

        return RedirectResponse(url=presigned_url, status_code=307)

    @action(
        name="clear-external-files",
        label="Clear external files",
        confirmation_message="Delete selected files from external storage?",
        add_in_detail=False,
    )
    async def clear_external_files(self, request: Request) -> Response:
        """Remove selected S3 objects in parallel while retaining File rows."""
        raw_ids = request.query_params.get("pks", "")
        file_ids: list[int] = []
        try:
            file_ids = [int(value) for value in raw_ids.split(",") if value]
        except ValueError:
            register_error_alert(
                "External file cleanup failed",
                f"Invalid File IDs: {raw_ids!r}",
                request=request,
            )
        else:
            if not file_ids:
                register_error_alert(
                    "External file cleanup failed",
                    "Select at least one file to clean up.",
                    request=request,
                )
            else:
                try:
                    result = await FileStorageCleanupService().clear_files(file_ids)
                except Exception as exc:
                    register_error_alert(
                        "External file cleanup failed",
                        str(exc),
                        request=request,
                    )
                else:
                    if result.failures:
                        register_error_alert(
                            "External file cleanup completed with errors",
                            self._format_cleanup_failures(result),
                            request=request,
                        )
                    else:
                        register_success_alert(
                            "External file cleanup completed",
                            self._format_cleanup_success(result),
                            request=request,
                        )

        if request.query_params.get("return_to") == "edit" and len(file_ids) == 1:
            url = request.url_for("admin:edit", identity=self.identity, pk=file_ids[0])
        else:
            url = request.url_for("admin:list", identity=self.identity)
        return RedirectResponse(url=url, status_code=302)

    async def on_model_delete(self, model: File, request: Request) -> None:
        """Clear S3 first; SQLAdmin deletes the database row only after this hook."""
        result = await FileStorageCleanupService().clear_files([model.id])
        item = result.items[0]
        if item.status == FileCleanupStatus.FAILED:
            details = self._format_cleanup_failures(result)
            register_error_alert("File deletion failed", details, request=request)
            raise RuntimeError(details)

    async def after_model_delete(self, model: File, request: Request) -> None:
        """Show a success notification only after the database commit completes."""
        register_success_alert(
            "File deleted",
            f"File #{model.id} was deleted after external storage cleanup.",
            request=request,
        )

    @staticmethod
    def _format_cleanup_failures(result: FileCleanupBatchResult) -> str:
        """Build alert details suitable for the global admin error overlay."""
        return "; ".join(
            f"File #{item.file_id} ({item.path or 'no S3 path'}): {item.error or 'unknown error'}"
            for item in result.failures
        )

    @staticmethod
    def _format_cleanup_success(result: FileCleanupBatchResult) -> str:
        """Build concise details for a successful cleanup notification."""
        parts: list[str] = []
        if result.cleared_ids:
            parts.append(f"Cleared File IDs: {list(result.cleared_ids)}")
        if result.already_clear_ids:
            parts.append(f"Already clear File IDs: {list(result.already_clear_ids)}")
        return "; ".join(parts) or "No external files required cleanup."
