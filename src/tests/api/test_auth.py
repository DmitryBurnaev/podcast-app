from types import SimpleNamespace
from datetime import timedelta
from unittest.mock import AsyncMock, Mock

import pytest
from litestar.testing import TestClient

from src.main import PodcastApp
from src.modules.api.auth import AuthAccessTokenAPIController, AuthAPIController
from src.modules.services.email import _send_invitation_email
from src.modules.schemas.auth import (
    ChangePasswordRequest,
    SignUpRequest,
    UserAccessTokenCreateRequest,
    UserInviteResponse,
)
from src.modules.db.models import User
from src.modules.db.models.users import UserAccessToken
from src.tests.factories import make_user
from src.tests.helpers import assert_error_response
from src.tests.mocks import MockUOW
from src.utils import hash_string, utcnow


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


class TestAuthAccountManagementAPI:
    async def test_sign_up__valid_invite__creates_user_and_session(
        self,
        app_settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = make_user(id=7)
        invite = SimpleNamespace(id=3)
        user_repository = SimpleNamespace(
            get_by_email=AsyncMock(return_value=None),
            create=AsyncMock(return_value=user),
        )
        invite_repository = SimpleNamespace(
            get_valid=AsyncMock(return_value=invite),
            update=AsyncMock(),
        )
        podcast_repository = SimpleNamespace(create=AsyncMock())
        create_user_session = AsyncMock(
            return_value=SimpleNamespace(access_token="access", refresh_token="refresh")
        )
        monkeypatch.setattr("src.modules.api.auth.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr("src.modules.api.auth.UserRepository", lambda session: user_repository)
        monkeypatch.setattr(
            "src.modules.api.auth.UserInviteRepository",
            lambda session: invite_repository,
        )
        monkeypatch.setattr(
            "src.modules.api.auth.PodcastRepository",
            lambda session: podcast_repository,
        )
        monkeypatch.setattr("src.modules.api.auth.create_user_session", create_user_session)
        monkeypatch.setattr("src.modules.api.auth.User.make_password", Mock(return_value="hashed"))

        response = await AuthAPIController.sign_up.fn(
            None,
            SignUpRequest(
                email="new@podcast.dev",
                invite_token="invite",
                password_1="secret",
                password_2="secret",
            ),
            app_settings,
        )

        assert response.model_dump() == {"access_token": "access", "refresh_token": "refresh"}
        invite_repository.get_valid.assert_awaited_once_with("invite", "new@podcast.dev")
        invite_repository.update.assert_awaited_once_with(invite, is_applied=True, user_id=7)
        podcast_repository.create.assert_awaited_once()
        create_user_session.assert_awaited_once_with(user, settings=app_settings)

    async def test_reset_password__does_not_expose_token(
        self,
        app_settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = make_user(id=7)
        repository = SimpleNamespace(get_by_email=AsyncMock(return_value=user))
        send_reset_email = AsyncMock()
        monkeypatch.setattr("src.modules.api.auth.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr("src.modules.api.auth.UserRepository", lambda session: repository)
        monkeypatch.setattr("src.modules.api.auth._send_reset_password_email", send_reset_email)

        response = await AuthAPIController.reset_password.fn(
            None,
            SimpleNamespace(email="user@podcast.dev"),
            app_settings,
        )

        assert response == {"ok": True}
        send_reset_email.assert_awaited_once()
        assert send_reset_email.await_args.args[0] is user

    async def test_change_password__updates_hash_and_deactivates_sessions(
        self,
        app_settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        user = make_user(id=7)
        user_repository = SimpleNamespace(
            first=AsyncMock(return_value=user),
            update=AsyncMock(),
        )
        session_repository = SimpleNamespace(deactivate_for_user=AsyncMock())
        monkeypatch.setattr("src.modules.api.auth.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr("src.modules.api.auth.UserRepository", lambda session: user_repository)
        monkeypatch.setattr(
            "src.modules.api.auth.UserSessionRepository",
            lambda session: session_repository,
        )
        monkeypatch.setattr(
            "src.modules.api.auth.decode_jwt",
            Mock(return_value={"user_id": user.id}),
        )
        monkeypatch.setattr("src.modules.api.auth.User.make_password", Mock(return_value="hashed"))

        response = await AuthAPIController.change_password.fn(
            None,
            ChangePasswordRequest(token="reset-token", password_1="new", password_2="new"),
            app_settings,
        )

        assert response == {"ok": True}
        user_repository.update.assert_awaited_once_with(user, password="hashed")
        session_repository.deactivate_for_user.assert_awaited_once_with(7)

    async def test_create_access_token__stores_hash_and_returns_raw_token(
        self,
        current_user: User,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        raw_token = UserAccessToken.generate_token()
        access_token = SimpleNamespace(
            id=3,
            name="automation",
            enabled=True,
            expires_in=utcnow() + timedelta(days=30),
            created_at=utcnow(),
        )
        repository = SimpleNamespace(create=AsyncMock(return_value=access_token))
        monkeypatch.setattr("src.modules.api.auth.SASessionUOW", lambda: MockUOW())
        monkeypatch.setattr(
            "src.modules.api.auth.UserAccessTokenRepository",
            lambda session: repository,
        )
        monkeypatch.setattr(
            "src.modules.db.models.users.UserAccessToken.generate_token", lambda: raw_token
        )

        response = await AuthAccessTokenAPIController.create_access_token.fn(
            None,
            UserAccessTokenCreateRequest(name="automation", expires_in_days=30),
            current_user,
        )

        assert response.token == raw_token
        assert repository.create.await_args.kwargs["token"] == hash_string(raw_token)


class TestAuthEmail:
    async def test_send_invitation_email__contains_encoded_signup_link(
        self,
        app_settings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        send_email = AsyncMock()
        monkeypatch.setattr("src.modules.services.email.send_email", send_email)
        invite = UserInviteResponse(
            id=1,
            email="new@podcast.dev",
            token="invite-token",
            is_applied=False,
            expired_at=utcnow() + timedelta(days=1),
        )

        await _send_invitation_email(invite, settings=app_settings)

        kwargs = send_email.await_args.kwargs
        assert kwargs["recipient_email"] == "new@podcast.dev"
        assert "/sign-up/?i=" in kwargs["html_content"]
