from collections.abc import Callable
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from litestar.datastructures import Cookie
from litestar.response import Redirect

from src.modules.common.exceptions import AuthenticationError
from src.modules.auth.backends import SuccessLoginData
from src.modules.views.auth import AuthLoginController, AuthLogoutController
from src.tests.factories import make_user


def _request(
    *, query_params: dict[str, str] | None = None, form_data: dict[str, object] | None = None
) -> SimpleNamespace:
    return SimpleNamespace(
        query_params=query_params or {}, form=AsyncMock(return_value=form_data or {})
    )


class FakeWebAuthBackend:
    """Configurable replacement for the web authentication boundary."""

    def __init__(
        self,
        request: object,
        *,
        login_result: SuccessLoginData | None = None,
        login_error: Exception | None = None,
        logout_cookie: Cookie | None = None,
    ) -> None:
        self.request = request
        self.login_result = login_result
        self.login_error = login_error
        self.logout_cookie = logout_cookie

    @classmethod
    def new(
        cls,
        *,
        login_result: SuccessLoginData | None = None,
        login_error: Exception | None = None,
        logout_cookie: Cookie | None = None,
    ) -> Callable[[object], "FakeWebAuthBackend"]:
        return lambda request: cls(
            request,
            login_result=login_result,
            login_error=login_error,
            logout_cookie=logout_cookie,
        )

    async def login(self, *, email: str, password: str) -> SuccessLoginData:
        if self.login_error is not None:
            raise self.login_error
        assert self.login_result is not None
        return self.login_result

    async def logout(self) -> Cookie:
        assert self.logout_cookie is not None
        return self.logout_cookie


class TestAuthLoginController:
    async def test_login_page__anonymous__renders_login_template(self, monkeypatch) -> None:
        controller = AuthLoginController.__new__(AuthLoginController)
        template = object()
        controller.get_response_template = Mock(return_value=template)
        monkeypatch.setattr("src.modules.views.auth.get_optional_user", lambda request: None)

        result = await AuthLoginController.login_page.fn(
            controller, _request(query_params={"error": "invalid"})
        )

        assert result is template
        assert (
            controller.get_response_template.call_args.kwargs["context"]["login_error"] == "invalid"
        )

    async def test_login_page__authenticated__redirects_home(self, monkeypatch) -> None:
        controller = AuthLoginController.__new__(AuthLoginController)
        monkeypatch.setattr("src.modules.views.auth.get_optional_user", lambda request: make_user())

        result = await AuthLoginController.login_page.fn(controller, _request())

        assert isinstance(result, Redirect)
        assert result.url == "/"

    async def test_login__authentication_failure__redirects_with_error(self, monkeypatch) -> None:
        monkeypatch.setattr(
            "src.modules.views.auth.WebAuthBackend",
            FakeWebAuthBackend.new(login_error=AuthenticationError("Invalid credentials")),
        )
        monkeypatch.setattr("src.modules.views.auth.get_optional_user", lambda request: None)

        result = await AuthLoginController.login.fn(
            AuthLoginController.__new__(AuthLoginController),
            _request(form_data={"email": "user@podcast.dev", "password": "wrong"}),
        )

        assert result.url == "/login?error=Authentication failed."

    async def test_login__success__redirects_and_sets_cookie(self, monkeypatch) -> None:
        cookie = Cookie(key="podcast_session_id", value="jwt")

        monkeypatch.setattr(
            "src.modules.views.auth.WebAuthBackend",
            FakeWebAuthBackend.new(login_result=SuccessLoginData(user=make_user(), cookie=cookie)),
        )
        monkeypatch.setattr("src.modules.views.auth.get_optional_user", lambda request: None)

        result = await AuthLoginController.login.fn(
            AuthLoginController.__new__(AuthLoginController),
            _request(form_data={"email": "user@podcast.dev", "password": "secret"}),
        )

        assert result.url == "/"
        assert result.cookies == [cookie]


class TestAuthLogoutController:
    async def test_logout__clears_session_and_redirects(self, monkeypatch) -> None:
        clear_cookie = Cookie(key="podcast_session_id", value="", max_age=0)

        monkeypatch.setattr(
            "src.modules.views.auth.WebAuthBackend",
            FakeWebAuthBackend.new(logout_cookie=clear_cookie),
        )

        result = await AuthLogoutController.logout.fn(
            AuthLogoutController.__new__(AuthLogoutController), _request()
        )

        assert result.url == "/login"
        assert result.cookies == [clear_cookie]
