import asyncio
import logging
from functools import cached_property
from typing import Any, Protocol, Literal, Callable

from litestar import Controller
from litestar.connection import Request
from litestar.openapi import OpenAPIController
from litestar.response import Template

from src.modules.common import constants as const
from src.modules.common.types import AppRequestMayBeAuthenticated
from src.modules.common.constants import AuthSkip
from src.modules.db import User
from src.modules.tasks.base import RQTask

__all__ = ("BaseViewController", "get_optional_user")
logger = logging.getLogger(__name__)


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
    async def _run_task(
        cls, app: TaskQueueApp, task_class: type[RQTask], *args: Any, **kwargs: Any
    ) -> None:
        """Run a task asynchronously."""
        logger.info("RUN task %s", task_class)
        task = task_class()
        kwargs["job_id"] = task_class.get_job_id(*args, **kwargs)
        await asyncio.to_thread(app.rq_queue.enqueue, task, *args, **kwargs)


class PodcastOpenAPIController(OpenAPIController):
    opt = {AuthSkip.SKIP_AUTH_WEB: True, AuthSkip.SKIP_AUTH_API: True}
    favicon_url = "/static/img/favicon.ico"
    swagger_css_url = "/static/css/swagger-ui.css"
    swagger_ui_bundle_js_url = "/static/js/swagger-ui-bundle.js"
    swagger_ui_standalone_preset_js_url = "/static/js/swagger-ui-standalone-preset.js"

    @cached_property
    def render_methods_map(
        self,
    ) -> dict[Literal["redoc", "swagger", "elements", "rapidoc"], Callable[[Request], bytes]]:
        """Map render method names to render methods.

        Returns:
            A mapping of string keys to render methods.
        """
        return {
            "redoc": self.render_swagger_ui,
            "swagger": self.render_swagger_ui,
        }
