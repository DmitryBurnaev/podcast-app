from litestar import get
from litestar.di import NamedDependency
from litestar.params import FromQuery

from src.modules.common.types import OwnerScope
from src.modules.schemas.progress import ProgressItemResponse
from src.modules.services.progress import ProgressOperations
from src.modules.web_api.base import WebJSONController


class ProgressWebController(WebJSONController):
    path = "/web-api/progress"

    @get("/")
    async def get_progress(
        self,
        user_scope: NamedDependency[OwnerScope],
        episode_id: FromQuery[int | None] = None,
    ) -> dict[str, list[ProgressItemResponse]]:
        return await ProgressOperations().get_progress(user_scope, episode_id)
