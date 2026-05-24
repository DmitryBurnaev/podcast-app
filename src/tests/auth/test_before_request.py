from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from litestar.response import Redirect, Response

from src.modules.api.errors import AuthInvalidError
from src.modules.auth.before_request import _is_auth_exempt, browser_auth_gate
from src.tests.factories import make_user


def _request(
    *,
    path: str,
    method: str = "GET",
    current_user: object | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        method=method,
        url=SimpleNamespace(path=path),
        state=SimpleNamespace(current_user=current_user),
    )


class TestAuthExempt:
    @pytest.mark.parametrize(
        ("path", "method"),
        [
            ("/login", "GET"),
            ("/login", "POST"),
            ("/logout", "POST"),
            ("/m/token", "GET"),
            ("/r/token/", "HEAD"),
            ("/api/auth/sign-in", "POST"),
            ("/api/system/health", "GET"),
            ("/anything", "OPTIONS"),
        ],
    )
    def test_is_auth_exempt__true(self, path: str, method: str) -> None:
        assert _is_auth_exempt(_request(path=path, method=method)) is True

    @pytest.mark.parametrize(
        ("path", "method"),
        [
            ("/login", "PUT"),
            ("/logout", "GET"),
            ("/api/podcasts", "GET"),
            ("/podcasts", "GET"),
        ],
    )
    def test_is_auth_exempt__false(self, path: str, method: str) -> None:
        assert _is_auth_exempt(_request(path=path, method=method)) is False


class TestBrowserAuthGate:
    async def test_browser_auth_gate__exempt__ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        attach_current_user = AsyncMock()
        monkeypatch.setattr(
            "src.modules.auth.before_request.attach_current_user", attach_current_user
        )
        request = _request(path="/login")

        result = await browser_auth_gate(request, settings=SimpleNamespace())

        assert result is None
        attach_current_user.assert_awaited_once_with(request)

    async def test_browser_auth_gate__current_user__ok(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr("src.modules.auth.before_request.attach_current_user", AsyncMock())
        request = _request(path="/podcasts", current_user=make_user())

        result = await browser_auth_gate(request, settings=SimpleNamespace())

        assert result is None

    async def test_browser_auth_gate__api_debug__allows_anonymous(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("src.modules.auth.before_request.attach_current_user", AsyncMock())
        settings = SimpleNamespace(flags=SimpleNamespace(api_debug_mode=True))
        request = _request(path="/api/podcasts")

        result = await browser_auth_gate(request, settings=settings)

        assert result is None

    async def test_browser_auth_gate__api_bearer__sets_user(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("src.modules.auth.before_request.attach_current_user", AsyncMock())
        user = make_user()
        authenticated = SimpleNamespace(user=user)
        authenticate = AsyncMock(return_value=authenticated)
        monkeypatch.setattr(
            "src.modules.auth.before_request.authenticate_bearer_request",
            authenticate,
        )
        settings = SimpleNamespace(flags=SimpleNamespace(api_debug_mode=False))
        request = _request(path="/api/podcasts")

        result = await browser_auth_gate(request, settings=settings)

        assert result is None
        assert request.state.current_user is user
        assert request.state.api_auth is authenticated
        authenticate.assert_awaited_once_with(request, settings=settings)

    async def test_browser_auth_gate__api_error__returns_json_response(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("src.modules.auth.before_request.attach_current_user", AsyncMock())
        monkeypatch.setattr(
            "src.modules.auth.before_request.authenticate_bearer_request",
            AsyncMock(side_effect=AuthInvalidError(details="bad token")),
        )
        settings = SimpleNamespace(flags=SimpleNamespace(api_debug_mode=False))
        request = _request(path="/api/podcasts")

        result = await browser_auth_gate(request, settings=settings)

        assert isinstance(result, Response)
        assert result.status_code == 401

    async def test_browser_auth_gate__browser_anonymous__redirects_login(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr("src.modules.auth.before_request.attach_current_user", AsyncMock())
        request = _request(path="/podcasts")

        result = await browser_auth_gate(request, settings=SimpleNamespace())

        assert isinstance(result, Redirect)
