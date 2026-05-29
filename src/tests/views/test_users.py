from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.modules.views.users import ProfileController


def _controller() -> ProfileController:
    return ProfileController.__new__(ProfileController)


async def _get(controller: ProfileController, request: SimpleNamespace) -> object:
    return await ProfileController.get.fn(controller, request)


class TestProfileController:
    async def test_get__passes_context_to_template(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        request = SimpleNamespace()
        user = SimpleNamespace(display_name="Test User")
        template = object()
        controller = _controller()
        controller.get_response_template = Mock(return_value=template)
        monkeypatch.setattr("src.modules.views.users.get_current_user", Mock(return_value=user))

        result = await _get(controller, request)

        assert result is template
        controller.get_response_template.assert_called_once_with(
            template_name="profile.html",
            context={
                "title": "Profile",
                "current": "profile",
                "current_user": "Test User",
            },
            request=request,
        )
