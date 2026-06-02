from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from uuid import UUID

import pytest
from litestar.response import Redirect

from src.modules.views.auth import AuthController
from src.tests.factories import make_user
from src.tests.mocks import MockUOW


def _controller() -> AuthController:
    return AuthController.__new__(AuthController)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        auth=SimpleNamespace(
            session_cookie_name="session-id",
            session_ttl_seconds=3600,
        ),
        auth_cookie_secure_effective=Mock(return_value=True),
    )


def _request(
    *,
    query_params: dict[str, str] | None = None,
    form_data: dict[str, object] | None = None,
    cookies: dict[str, str] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        query_params=query_params or {},
        form=AsyncMock(return_value=form_data or {}),
        cookies=cookies or {},
    )


async def _login_page(controller: AuthController, request: SimpleNamespace) -> object:
    return await AuthController.login_page.fn(controller, request)


async def _login_submit(controller: AuthController, request: SimpleNamespace) -> Redirect:
    return await AuthController.login_submit.fn(controller, request)


async def _logout(controller: AuthController, request: SimpleNamespace) -> Redirect:
    return await AuthController.logout.fn(controller, request)


class TestAuthLoginPage:
    async def test_login_page__anonymous__passes_context_to_template(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        request = _request(query_params={"error": "invalid"})
        template = object()
        controller = _controller()
        controller.get_response_template = Mock(return_value=template)
        monkeypatch.setattr(
            "src.modules.views.auth.get_current_user_or_none", Mock(return_value=None)
        )

        result = await _login_page(controller, request)

        assert result is template
        controller.get_response_template.assert_called_once_with(
            template_name="login.html",
            context={
                "title": "Sign in",
                "current": "home",
                "login_error": "invalid",
            },
            request=request,
        )

    async def test_login_page__authenticated__redirects_home(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        controller = _controller()
        monkeypatch.setattr(
            "src.modules.views.auth.get_current_user_or_none",
            Mock(return_value=make_user()),
        )

        result = await _login_page(controller, _request())

        assert isinstance(result, Redirect)
        assert result.url == "/"


class TestAuthLoginSubmit:
    @pytest.mark.parametrize(
        "form_data",
        [
            {},
            {"email": "", "password": "secret"},
            {"email": "user@podcast.dev", "password": None},
            {"email": "   ", "password": "secret"},
        ],
    )
    async def test_login_submit__missing_credentials__redirects_missing(
        self,
        monkeypatch: pytest.MonkeyPatch,
        form_data: dict[str, object],
    ) -> None:
        controller = _controller()
        monkeypatch.setattr("src.modules.views.auth.get_app_settings", _settings)

        result = await _login_submit(controller, _request(form_data=form_data))

        assert result.url == "/login?error=missing"

    @pytest.mark.parametrize(
        "user",
        [
            None,
            SimpleNamespace(id=7, verify_password=Mock(return_value=False)),
        ],
    )
    async def test_login_submit__invalid_credentials__redirects_invalid(
        self,
        monkeypatch: pytest.MonkeyPatch,
        user: object | None,
    ) -> None:
        uow = MockUOW()
        user_repository = SimpleNamespace(get_by_email=AsyncMock(return_value=user))
        monkeypatch.setattr("src.modules.views.auth.get_app_settings", _settings)
        monkeypatch.setattr("src.modules.views.auth.SASessionUOW", lambda: uow)
        monkeypatch.setattr(
            "src.modules.views.auth.UserRepository",
            Mock(return_value=user_repository),
        )

        result = await _login_submit(
            _controller(),
            _request(form_data={"email": " user@podcast.dev ", "password": "secret"}),
        )

        assert result.url == "/login?error=invalid"
        user_repository.get_by_email.assert_awaited_once_with("user@podcast.dev")
        uow.mark_for_commit.assert_not_called()
        if user is not None:
            user.verify_password.assert_called_once_with("secret")

    async def test_login_submit__valid_credentials__creates_session_and_cookie(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _settings()
        uow = MockUOW()
        user = SimpleNamespace(id=7, verify_password=Mock(return_value=True))
        user_repository = SimpleNamespace(get_by_email=AsyncMock(return_value=user))
        session_repository = SimpleNamespace(create=AsyncMock(return_value=object()))
        monkeypatch.setattr("src.modules.views.auth.get_app_settings", lambda: settings)
        monkeypatch.setattr("src.modules.views.auth.SASessionUOW", lambda: uow)
        monkeypatch.setattr(
            "src.modules.views.auth.UserRepository",
            Mock(return_value=user_repository),
        )
        monkeypatch.setattr(
            "src.modules.views.auth.UserSessionRepository",
            Mock(return_value=session_repository),
        )
        monkeypatch.setattr(
            "src.modules.views.auth.uuid.uuid4",
            Mock(return_value=UUID("12345678-1234-5678-1234-567812345678")),
        )

        result = await _login_submit(
            _controller(),
            _request(form_data={"email": "user@podcast.dev", "password": "secret"}),
        )

        assert result.url == "/"
        user_repository.get_by_email.assert_awaited_once_with("user@podcast.dev")
        user.verify_password.assert_called_once_with("secret")
        session_repository.create.assert_awaited_once()
        create_kwargs = session_repository.create.await_args.kwargs
        assert create_kwargs["public_id"] == "12345678-1234-5678-1234-567812345678"
        assert create_kwargs["user_id"] == 7
        assert create_kwargs["refresh_token"] is None
        assert create_kwargs["is_active"] is True
        assert create_kwargs["expired_at"] > create_kwargs["created_at"]
        assert create_kwargs["refreshed_at"] == create_kwargs["created_at"]
        uow.mark_for_commit.assert_called_once_with()

        cookie = result.cookies[0]
        assert cookie.key == "session-id"
        assert cookie.value == "12345678-1234-5678-1234-567812345678"
        assert cookie.max_age == 3600
        assert cookie.httponly is True
        assert cookie.secure is True
        assert cookie.samesite == "lax"
        assert cookie.path == "/"
        settings.auth_cookie_secure_effective.assert_called_once_with()


class TestAuthLogout:
    async def test_logout__without_session_cookie__clears_cookie(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _settings()
        monkeypatch.setattr("src.modules.views.auth.get_app_settings", lambda: settings)

        result = await _logout(_controller(), _request())

        assert result.url == "/login"
        cookie = result.cookies[0]
        assert cookie.key == "session-id"
        assert cookie.value == ""
        assert cookie.max_age == 0
        assert cookie.httponly is True
        assert cookie.secure is True
        assert cookie.samesite == "lax"
        assert cookie.path == "/"
        settings.auth_cookie_secure_effective.assert_called_once_with()

    async def test_logout__with_session_cookie__deactivates_session_and_clears_cookie(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        settings = _settings()
        uow = MockUOW()
        session_repository = SimpleNamespace(deactivate_by_public_id=AsyncMock(return_value=None))
        monkeypatch.setattr("src.modules.views.auth.get_app_settings", lambda: settings)
        monkeypatch.setattr("src.modules.views.auth.SASessionUOW", lambda: uow)
        monkeypatch.setattr(
            "src.modules.views.auth.UserSessionRepository",
            Mock(return_value=session_repository),
        )

        result = await _logout(
            _controller(),
            _request(cookies={"session-id": "public-session-id"}),
        )

        assert result.url == "/login"
        session_repository.deactivate_by_public_id.assert_awaited_once_with("public-session-id")
        uow.mark_for_commit.assert_called_once_with()
        assert result.cookies[0].max_age == 0
