from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

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
        user = SimpleNamespace(display_name="Test User")
        request = SimpleNamespace(user=user)
        template = object()
        controller = _controller()
        controller.get_response_template = Mock(return_value=template)
        monkeypatch.setattr("src.modules.views.users.WebAuthBackend.register_user_ip", AsyncMock())

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
