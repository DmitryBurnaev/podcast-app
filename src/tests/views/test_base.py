from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from litestar.response import Template

from src import constants as const
from src.modules.views.base import BaseController


class ChildControllerForTest(BaseController):
    pass


class QueueTaskForTest:
    @classmethod
    def get_job_id(cls, *args: object, **kwargs: object) -> str:
        return "queue-job-id"


def _controller() -> BaseController:
    return BaseController.__new__(BaseController)


class TestBaseControllerContext:
    @pytest.mark.parametrize(
        ("user", "is_authenticated", "user_data"),
        [
            (
                None,
                False,
                {"name": None, "email": None, "avatar": None},
            ),
            (
                SimpleNamespace(
                    email_local_part="test",
                    display_name="Test User",
                    email="test@podcast.dev",
                ),
                True,
                {"name": "test", "email": "test@podcast.dev", "avatar": None},
            ),
            (
                SimpleNamespace(
                    email_local_part="",
                    display_name="Fallback Name",
                    email="fallback@podcast.dev",
                ),
                True,
                {"name": "Fallback Name", "email": "fallback@podcast.dev", "avatar": None},
            ),
        ],
    )
    def test_get_base_context__ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: object | None,
        is_authenticated: bool,
        user_data: dict[str, str | None],
    ) -> None:
        request = SimpleNamespace()
        monkeypatch.setattr(
            "src.modules.views.base.get_current_user_or_none",
            Mock(return_value=user),
        )

        result = BaseController.get_base_context(request)

        assert result == {
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

    @pytest.mark.parametrize(
        ("template_name", "expected_template_name"),
        [("page.html", "page.html"), ("", "base.html")],
    )
    def test_get_response_template__merges_base_and_page_context(
        self,
        template_name: str,
        expected_template_name: str,
    ) -> None:
        request = SimpleNamespace()
        controller = _controller()
        controller.get_base_context = Mock(
            return_value={
                "current": "home",
                "shared": "base",
            }
        )

        result = controller.get_response_template(
            template_name=template_name,
            context={
                "current": "page",
                "specific": "context",
            },
            request=request,
        )

        assert isinstance(result, Template)
        assert result.template_name == expected_template_name
        assert result.context == {
            "current": "page",
            "shared": "base",
            "specific": "context",
        }
        controller.get_base_context.assert_called_once_with(request)


class TestBaseControllerHelpers:
    def test_get_controllers__includes_subclasses(self) -> None:
        BaseController.get_controllers.cache_clear()

        result = BaseController.get_controllers()

        assert ChildControllerForTest in result

    async def test_run_task__enqueues_task_with_job_id(self) -> None:
        enqueue = Mock(return_value=None)
        app = SimpleNamespace(rq_queue=SimpleNamespace(enqueue=enqueue))

        await BaseController._run_task(app, QueueTaskForTest, 10, force=True)

        enqueue.assert_called_once()
        task, episode_id = enqueue.call_args.args
        assert isinstance(task, QueueTaskForTest)
        assert episode_id == 10
        assert enqueue.call_args.kwargs == {
            "force": True,
            "job_id": "queue-job-id",
        }
