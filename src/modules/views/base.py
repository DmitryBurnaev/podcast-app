import asyncio
import logging
from functools import lru_cache
from typing import Any, Protocol, Self

from litestar import Controller
from litestar.connection import Request
from litestar.datastructures import State
from litestar.response import Template

from src import constants as const
from src.constants import AuthSkip
from src.modules.auth.backend import TokenData
from src.modules.db import User
from src.modules.tasks.base import RQTask

__all__ = ("BaseViewController", "get_optional_user")
logger = logging.getLogger(__name__)
type AppRequest = Request[User, TokenData, State]
type AppRequestMayBeAuthenticated = Request[User | None, TokenData | None, State]


def get_optional_user(request: Request) -> User | None:
    """Return authenticated user when auth middleware populated the request scope."""
    user = request.scope.get("user")
    return user if isinstance(user, User) else None


class TaskQueueApp(Protocol):
    rq_queue: Any


class BaseViewController(Controller):
    include_in_schema = False
    default_template_name = "base.html"
    base_auth_opt: dict[str, bool] = {AuthSkip.SKIP_AUTH_API: True}
    opt = base_auth_opt

    def get_response_template(
        self,
        template_name: str,
        context: dict[str, Any],
        request: Request,
    ) -> Template:
        """Build a template response with shared base context."""
        template_name = template_name or self.default_template_name
        base = self.get_base_context(request)
        return Template(template_name=template_name, context=(base | context))

    @staticmethod
    def get_base_context(request: AppRequestMayBeAuthenticated) -> dict[str, Any]:
        """Return context values shared by all HTML views."""
        current_user = get_optional_user(request)
        user_data: dict[str, Any] = {
            "name": None,
            "email": None,
            "avatar": None,
        }
        if current_user is not None:
            user_data = {
                "name": current_user.email_local_part or current_user.display_name,
                "email": current_user.email,
                "avatar": None,
            }

        return {
            "current": "home",
            "navigation": const.NAVIGATION,
            "is_authenticated": current_user is not None,
            "user_data": user_data,
            "get_episode_status_color": const.get_episode_status_color,
            "get_episode_status_label": const.get_episode_status_label,
            "format_duration": const.format_duration,
            "format_file_size": const.format_file_size,
            "normalize_episode_status": const.normalize_episode_status,
        }

    @classmethod
    @lru_cache
    def get_controllers(cls) -> list[type[Self]]:
        """Return concrete HTML controllers registered under this base controller."""
        return [c for c in cls.__subclasses__()]  # noqa

    @classmethod
    async def _run_task(
        cls, app: TaskQueueApp, task_class: type[RQTask], *args: Any, **kwargs: Any
    ) -> None:
        """Run a task asynchronously."""
        logger.info("RUN task %s", task_class)
        task = task_class()
        kwargs["job_id"] = task_class.get_job_id(*args, **kwargs)
        await asyncio.to_thread(app.rq_queue.enqueue, task, *args, **kwargs)
