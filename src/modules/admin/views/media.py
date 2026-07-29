from src.modules.db.models import File
from src.modules.admin.views.base import BaseModelView, mask_secret

__all__ = ("MediaFileAdminView",)


class MediaFileAdminView(BaseModelView, model=File):
    name = "Media File"
    name_plural = "Media Files"
    icon = "fa-solid fa-file-audio"
    column_list = [
        File.id,
        File.type,
        File.path,
        File.size,
        File.available,
        File.public,
        File.owner_id,
    ]
    column_searchable_list = [File.path, File.source_url, File.hash]
    column_sortable_list = [File.id, File.path, File.size, File.created_at]
    column_default_sort = (File.id, True)
    column_formatters = {"access_token": mask_secret}
    column_formatters_detail = {"access_token": mask_secret}
