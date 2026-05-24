from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from litestar.testing import TestClient

from src.main import PodcastApp
from src.modules.api.auth import AuthAPIController
from src.modules.db.models import User
from src.tests.helpers import assert_error_response
from src.tests.mocks import MockUOW


class TestAuthSignInAPI:
    url = "/api/auth/sign-in/"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"email": "not-an-email", "password": "secret"},
            {"email": "user@podcast.dev", "password": ""},
        ],
    )
    def test_sign_in__invalid_request__fail(
        self,
        client: TestClient[PodcastApp],
        payload: dict[str, str],
    ) -> None:
        response = client.post(self.url, json=payload)

        assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )

    def test_sign_in__unknown_user__fail(
        self,
        client: TestClient[PodcastApp],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user_repository = SimpleNamespace(get_by_email=AsyncMock(return_value=None))
        monkeypatch.setattr("src.modules.api.auth.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr("src.modules.api.auth.UserRepository", lambda session: user_repository)

        response = client.post(
            self.url,
            json={"email": "user@podcast.dev", "password": "secret"},
        )

        assert_error_response(
            response,
            status_code=401,
            code="AUTH_INVALID",
            message="Authentication credentials are invalid.",
        )
        user_repository.get_by_email.assert_awaited_once_with("user@podcast.dev")

    def test_sign_in__ok(
        self,
        client: TestClient[PodcastApp],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = SimpleNamespace(
            id=1,
            is_active=True,
            verify_password=Mock(return_value=True),
        )
        user_repository = SimpleNamespace(get_by_email=AsyncMock(return_value=user))
        token_collection = SimpleNamespace(
            access_token="access-token", refresh_token="refresh-token"
        )
        create_user_session = AsyncMock(return_value=token_collection)
        monkeypatch.setattr("src.modules.api.auth.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr("src.modules.api.auth.UserRepository", lambda session: user_repository)
        monkeypatch.setattr("src.modules.api.auth.create_user_session", create_user_session)

        response = client.post(
            self.url,
            json={"email": "user@podcast.dev", "password": "secret"},
        )

        assert response.status_code == 201 or response.status_code == 200, response.text
        assert response.json() == {"access_token": "access-token", "refresh_token": "refresh-token"}
        create_user_session.assert_awaited_once()
        user.verify_password.assert_called_once_with("secret")


class TestAuthRefreshTokenAPI:
    url = "/api/auth/refresh-token/"

    @pytest.mark.parametrize("payload", [{}, {"refresh_token": ""}])
    def test_refresh_token__invalid_request__fail(
        self,
        client: TestClient[PodcastApp],
        payload: dict[str, str],
    ) -> None:
        response = client.post(self.url, json=payload)

        assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )

    def test_refresh_token__ok(
        self,
        client: TestClient[PodcastApp],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        token_collection = SimpleNamespace(
            access_token="new-access-token",
            refresh_token="new-refresh-token",
        )
        refresh_user_session = AsyncMock(return_value=token_collection)
        monkeypatch.setattr("src.modules.api.auth.refresh_user_session", refresh_user_session)

        response = client.post(self.url, json={"refresh_token": "refresh-token"})

        assert response.status_code == 201 or response.status_code == 200, response.text
        assert response.json() == {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
        }
        refresh_user_session.assert_awaited_once()


class TestAuthSessionAPI:
    def test_sign_out__without_api_session__ok(self, client: TestClient[PodcastApp]) -> None:
        response = client.delete("/api/auth/sign-out/")

        assert response.status_code == 200, response.text
        assert response.json() == {"ok": True}

    async def test_sign_out__with_api_session__deactivates_session(
        self,
        current_user: User,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session_repository = SimpleNamespace(deactivate_by_public_id=AsyncMock(return_value=None))
        request = SimpleNamespace(
            state=SimpleNamespace(api_auth=SimpleNamespace(session_id="session-public-id"))
        )
        monkeypatch.setattr("src.modules.api.auth.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.api.auth.UserSessionRepository",
            lambda session: session_repository,
        )

        response = await AuthAPIController.sign_out.fn(None, current_user, request)

        assert response == {"ok": True}
        session_repository.deactivate_by_public_id.assert_awaited_once_with("session-public-id")

    def test_me__ok(self, client: TestClient[PodcastApp]) -> None:
        response = client.get("/api/auth/me/")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "id": 1,
            "email": "user@podcast.dev",
            "is_active": True,
            "is_superuser": False,
        }
