from functools import lru_cache
from typing import Any

from litestar import Controller
from litestar.response import Template

from src import constants as const
from src.modules.tasks.base import RQTask

__all__ = ("BaseController",)


class BaseController(Controller):
    include_in_schema = False
    default_template_name = "base.html"
    login_template_name = "login.html"

    def get_response_template(self, template_name: str, context: dict[str, Any]) -> Template:
        template_name = template_name or self.default_template_name
        return Template(template_name=template_name, context=(self.get_base_context() | context))

    @staticmethod
    def get_base_context() -> dict[str, Any]:
        return {
            "current": "home",
            "navigation": const.NAVIGATION,
            "user_data": {
                "name": "Test User",
                "avatar": None,  # Can be extended with avatar URL later
            },
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
    def _run_task(cls, task: type[RQTask], *args: Any, **kwargs: Any) -> None:
        """Run a task asynchronously."""
        task_instance = task(*args, **kwargs)
        task_instance.run()
