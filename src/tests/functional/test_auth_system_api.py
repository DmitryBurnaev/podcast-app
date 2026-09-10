"""Functional PostgreSQL coverage for auth, profile and system API workflows."""

from collections.abc import Generator

import pytest
from litestar.middleware import AuthenticationResult
from litestar.testing import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.main import PodcastApp, make_app
from src.modules.db.models import Podcast, User, UserInvite, UserSession
from src.modules.db.models.users import UserAccessToken, UserIP
from src.tests.conftest import _make_settings
from src.tests.fakes import (
    FakeLifecycle,
    FakeMailer,
    FakeTaskQueue,
)
from src.tests.helpers import assert_error_response
from src.utils import utcnow


@pytest.fixture
def auth_api_client(
    db_user: User,
    mocked_app_lifecycle: FakeLifecycle,
    mocked_rq_queue: FakeTaskQueue,
    mocked_mailer: FakeMailer,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle], None, None]:
    """Return an app using isolated PostgreSQL and its class-based external fakes."""
    settings = _make_settings(api_debug_mode=True)
    settings.flags.send_invites = True
    db_user.is_superuser = True

    async def authenticate_as_db_user(_: object, __: object) -> AuthenticationResult:
        return AuthenticationResult(user=db_user, auth={"session_id": None})

    monkeypatch.setattr(
        "src.modules.auth.middlewares.APIAuthMiddleware.authenticate_request",
        authenticate_as_db_user,
    )
    monkeypatch.setattr(
        "src.modules.api.misc.check_redis_connection",
        mocked_app_lifecycle.check_redis,
    )
    with TestClient(
        app=make_app(settings=settings), raise_server_exceptions=False
    ) as client:
        yield client, mocked_mailer, mocked_app_lifecycle


class TestAuthCoreAPI:
    async def test_sign_up_then_sign_in_and_refresh_persist_user_session(
        self,
        auth_api_client: tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, _, _ = auth_api_client
        invite = UserInvite(
            email="new@podcast.dev",
            token="invite-token",
            expired_at=utcnow().replace(year=utcnow().year + 1),
            owner_id=db_user.id,
            is_applied=False,
        )
        functional_session.add(invite)
        await functional_session.commit()
        invite_id = invite.id

        created = client.post(
            "/api/auth/sign-up/",
            json={
                "email": "new@podcast.dev",
                "invite_token": "invite-token",
                "password_1": "new-password",
                "password_2": "new-password",
            },
        )
        signed_in = client.post(
            "/api/auth/sign-in/", json={"email": "new@podcast.dev", "password": "new-password"}
        )
        refreshed = client.post(
            "/api/auth/refresh-token/", json={"refresh_token": signed_in.json()["refresh_token"]}
        )

        assert created.status_code == 201, created.text
        assert signed_in.status_code == 200, signed_in.text
        assert refreshed.status_code == 201 or refreshed.status_code == 200, refreshed.text
        assert refreshed.json()["access_token"] != signed_in.json()["access_token"]
        assert refreshed.json()["refresh_token"] != signed_in.json()["refresh_token"]
        functional_session.expire_all()
        users = list((await functional_session.scalars(select(User))).all())
        assert any(user.email == "new@podcast.dev" for user in users)
        persisted_invite = await functional_session.get(UserInvite, invite_id)
        assert persisted_invite is not None and persisted_invite.is_applied
        assert len(list((await functional_session.scalars(select(UserSession))).all())) >= 2
        assert len(list((await functional_session.scalars(select(Podcast))).all())) >= 1

    async def test_reset_password_uses_fake_mailer_without_leaking_token(
        self,
        auth_api_client: tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle],
    ) -> None:
        client, mailer, _ = auth_api_client

        response = client.post(
            "/api/auth/reset-password/", json={"email": "functional-user@podcast.dev"}
        )

        assert response.status_code == 201 or response.status_code == 200, response.text
        assert response.json() == {"status": "ok"}
        assert len(mailer.sent) == 1
        assert "token" not in response.text.lower()


class TestProfileAndTokenAPI:
    async def test_profile_ips_and_access_token_lifecycle_are_owned_and_persisted(
        self,
        auth_api_client: tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle],
        functional_session: AsyncSession,
    ) -> None:
        client, _, _ = auth_api_client
        profile = client.patch("/api/auth/me/", json={"email": "updated@podcast.dev"})
        me = client.get("/api/auth/me/", headers={"X-Real-IP": "203.0.113.10"})
        tokens = client.post(
            "/api/auth/access-tokens/", json={"name": "automation", "expires_in_days": 7}
        )
        token_id = tokens.json()["id"]
        listed = client.get("/api/auth/access-tokens/")
        updated = client.patch(f"/api/auth/access-tokens/{token_id}/", json={"enabled": False})
        deleted = client.delete(f"/api/auth/access-tokens/{token_id}/")

        assert profile.status_code == 200, profile.text
        assert me.status_code == 200, me.text
        assert tokens.status_code == 201, tokens.text
        assert "token" in tokens.json()
        assert listed.status_code == 200, listed.text
        assert listed.json()["items"][0]["id"] == token_id
        assert "token" not in listed.json()["items"][0]
        assert updated.status_code == 200, updated.text
        assert deleted.status_code == 204, deleted.text
        functional_session.expire_all()
        assert await functional_session.get(UserAccessToken, token_id) is None
        assert len(list((await functional_session.scalars(select(UserIP))).all())) == 1


class TestInviteAndSystemAPI:
    def test_invite_and_health_use_fake_mailer_and_redis_lifecycle(
        self, auth_api_client: tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle]
    ) -> None:
        client, mailer, lifecycle = auth_api_client
        invite = client.post("/api/auth/invites/", json={"email": "invited@podcast.dev"})
        health = client.get("/api/system/health/")
        info = client.get("/api/system/info/")

        assert invite.status_code == 201, invite.text
        assert len(mailer.sent) == 1
        assert health.status_code == 200, health.text
        assert info.json() == {"status": "ok", "vendors": ["test"]}
        assert lifecycle.calls.count("check_redis") >= 2

    def test_foreign_access_token_is_not_exposed(
        self, auth_api_client: tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle]
    ) -> None:
        client, _, _ = auth_api_client
        response = client.patch("/api/auth/access-tokens/999/", json={"name": "foreign"})
        assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )
