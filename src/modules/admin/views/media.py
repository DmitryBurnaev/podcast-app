from src.modules.admin.utils import format_instance_details_link
from src.modules.db.models import File
from src.modules.admin.views.base import BaseModelView, mask_secret

__all__ = ("MediaFileAdminView",)


class MediaFileAdminView(BaseModelView, model=File):
    name = "File"
    name_plural = "Files"
    icon = "fa-solid fa-file-audio"
    column_list = [
        File.id,
        File.type,
        File.size,
        File.available,
        File.public,
        File.owner_id,
    ]
    column_searchable_list = [File.path, File.source_url, File.hash]
    column_sortable_list = [File.id, File.path, File.size, File.created_at]
    column_default_sort = (File.id, True)
    column_formatters = {"access_token": mask_secret, File.id: format_instance_details_link}
    column_formatters_detail = {"access_token": mask_secret}
