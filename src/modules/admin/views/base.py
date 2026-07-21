import logging
import datetime
from typing import ClassVar, TYPE_CHECKING

from sqladmin import ModelView, BaseView
from starlette.exceptions import HTTPException
from starlette.requests import Request
from starlette.responses import Response

if TYPE_CHECKING:
    from src.main import PodcastApp

logger = logging.getLogger(__name__)
type FormDataType = dict[str, str | int | datetime.datetime | None]
__all__ = ("BaseModelView",)


class BaseAPPView(BaseView):
    """Same as BaseView but annotated with app instance (will be passed while views registering)"""

    app: "PodcastApp"


class BaseModelView(ModelView):
    """Extended version of ModelView with extra settings and logic across all our views"""

    app: "PodcastApp"
    can_export = False
    is_async = True
    custom_post_create: ClassVar[bool] = False

    async def handle_post_create(self, request: Request, object_id: int) -> Response:
        if not self.custom_post_create:
            raise HTTPException(status_code=400, detail="Missing handle_post_create logic")

        request.scope["path_params"] = request.path_params | {"pk": object_id}
        model = await self.get_object_for_details(request=request)
        if not model:
            raise HTTPException(status_code=404, detail=f"Object #{object_id} not found")

        context = {"model_view": self, "model": model, "title": self.name}

        return await self.templates.TemplateResponse(request, self.details_template, context)
