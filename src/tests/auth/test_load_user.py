from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.modules.auth.load_user import (
    attach_current_user,
    get_current_user,
    get_current_user_or_none,
)
from src.tests.factories import make_user
from src.tests.mocks import MockUOW


class TestCurrentUserHelpers:
    def test_get_current_user_or_none__ok(self) -> None:
        user = make_user()
        request = SimpleNamespace(state=SimpleNamespace(current_user=user))

        assert get_current_user_or_none(request) is user

    def test_get_current_user_or_none__not_user__none(self) -> None:
        request = SimpleNamespace(state=SimpleNamespace(current_user=object()))

        assert get_current_user_or_none(request) is None

    def test_get_current_user__missing__fail(self) -> None:
        request = SimpleNamespace(state=SimpleNamespace(current_user=None))

        with pytest.raises(RuntimeError, match="Current user is not available"):
            get_current_user(request)


class TestAttachCurrentUser:
    async def test_attach_current_user__missing_app_settings__skip(self) -> None:
        request = SimpleNamespace(app=object(), cookies={}, state=SimpleNamespace())

        await attach_current_user(request)

        assert request.state.current_user is None

    async def test_attach_current_user__missing_cookie__skip(self) -> None:
        settings = SimpleNamespace(auth=SimpleNamespace(session_cookie_name="sid"))
        request = SimpleNamespace(app=SimpleNamespace(settings=settings), cookies={}, state=SimpleNamespace())

        await attach_current_user(request)

        assert request.state.current_user is None

    async def test_attach_current_user__active_session__sets_user(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = make_user()
        repo = SimpleNamespace(get_active_with_user=AsyncMock(return_value=(object(), user)))
        settings = SimpleNamespace(auth=SimpleNamespace(session_cookie_name="sid"))
        request = SimpleNamespace(
            app=SimpleNamespace(settings=settings),
            cookies={"sid": "public-id"},
            state=SimpleNamespace(),
        )
        monkeypatch.setattr("src.modules.auth.load_user.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.auth.load_user.UserSessionRepository",
            Mock(return_value=repo),
        )

        await attach_current_user(request)

        assert request.state.current_user is user
        repo.get_active_with_user.assert_awaited_once_with("public-id")

    async def test_attach_current_user__repository_error__keeps_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        repo = SimpleNamespace(get_active_with_user=AsyncMock(side_effect=RuntimeError("db")))
        settings = SimpleNamespace(auth=SimpleNamespace(session_cookie_name="sid"))
        request = SimpleNamespace(
            app=SimpleNamespace(settings=settings),
            cookies={"sid": "public-id"},
            state=SimpleNamespace(),
        )
        monkeypatch.setattr("src.modules.auth.load_user.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.auth.load_user.UserSessionRepository",
            Mock(return_value=repo),
        )

        await attach_current_user(request)

        assert request.state.current_user is None
