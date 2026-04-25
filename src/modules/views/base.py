import asyncio
import logging
from functools import lru_cache
from typing import Any, Protocol

from litestar import Controller
from litestar.connection import Request
from litestar.response import Template

from src import constants as const
from src.modules.tasks.base import RQTask

__all__ = ("BaseController",)
logger = logging.getLogger(__name__)


class TaskQueueApp(Protocol):
    rq_queue: Any


class BaseController(Controller):
    include_in_schema = False
    default_template_name = "base.html"
    login_template_name = "login.html"

    def get_response_template(
        self,
        template_name: str,
        context: dict[str, Any],
        request: Request,
    ) -> Template:
        template_name = template_name or self.default_template_name
        base = self.get_base_context(request)
        return Template(template_name=template_name, context=(base | context))

    @staticmethod
    def get_base_context(request: Request) -> dict[str, Any]:
        current_user = getattr(request.state, "current_user", None)
        is_authenticated = current_user is not None
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
            "is_authenticated": is_authenticated,
            "user_data": user_data,
            "get_episode_status_color": const.get_episode_status_color,
            "get_episode_status_label": const.get_episode_status_label,
            "format_duration": const.format_duration,
            "format_file_size": const.format_file_size,
            "normalize_episode_status": const.normalize_episode_status,
        }

    @classmethod
    @lru_cache
    def get_controllers(cls) -> list[type["BaseController"]]:
        return [c for c in cls.__subclasses__()]

    @classmethod
    async def _run_task(
        cls, app: TaskQueueApp, task_class: type[RQTask], *args: Any, **kwargs: Any
    ) -> None:
        """Run a task asynchronously."""
        logger.info("RUN task %s", task_class)
        task = task_class()
        kwargs["job_id"] = task_class.get_job_id(*args, **kwargs)
        await asyncio.to_thread(app.rq_queue.enqueue, task, *args, **kwargs)
