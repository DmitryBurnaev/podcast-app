from src.modules.admin.forms import ReadOnlyTextField
from src.modules.admin.utils import format_bool, format_instance_details_link, format_file_size
from src.modules.db.models import File
from src.modules.admin.views.base import BaseModelView

__all__ = ("MediaFileAdminView",)


class MediaFileAdminView(BaseModelView, model=File):
    name = "File"
    name_plural = "Files"
    icon = "fa-solid fa-file-audio"
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
        "size": ReadOnlyTextField,
    }
    column_labels = {
        File.id: "ID",
        File.type: "Type",
        File.size: "Size",
        File.available: "Available",
        File.public: "Public",
        File.owner_id: "Owner",
    }
