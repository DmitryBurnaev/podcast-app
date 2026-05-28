from types import SimpleNamespace
from unittest.mock import Mock

from src.modules.views.system import AboutController


def _controller() -> AboutController:
    return AboutController.__new__(AboutController)


async def _get(
    controller: AboutController,
    request: SimpleNamespace,
    settings: SimpleNamespace,
) -> object:
    return await AboutController.get.fn(controller, request, settings)


class TestAboutController:
    async def test_get__passes_context_to_template(self) -> None:
        request = SimpleNamespace()
        settings = SimpleNamespace(app_version="test-version")
        template = object()
        controller = _controller()
        controller.get_response_template = Mock(return_value=template)

        result = await _get(controller, request, settings)

        assert result is template
        controller.get_response_template.assert_called_once_with(
            template_name="about.html",
            context={
                "title": "About",
                "current": "about",
                "version": "test-version",
            },
            request=request,
        )
