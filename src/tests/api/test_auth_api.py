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
from src.modules.utils.common import utcnow

pytestmark = pytest.mark.usefixtures("use_functional_session_factory")


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
    with TestClient(app=make_app(settings=settings), raise_server_exceptions=False) as client:
        yield client, mocked_mailer, mocked_app_lifecycle


class TestAuthRegistrationSessionAPI:
    async def test_sign_up_sign_in_and_refresh__valid_invite__persist_user_session(
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

    async def test_sign_up__invalid_invite__does_not_create_user(
        self,
        auth_api_client: tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle],
        functional_session: AsyncSession,
    ) -> None:
        client, _, _ = auth_api_client

        response = client.post(
            "/api/auth/sign-up/",
            json={
                "email": "new@podcast.dev",
                "invite_token": "missing-token",
                "password_1": "new-password",
                "password_2": "new-password",
            },
        )

        assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )
        users = list((await functional_session.scalars(select(User))).all())
        assert [user.email for user in users] == ["functional-user@podcast.dev"]

    def test_sign_in__incorrect_password__fails(
        self, auth_api_client: tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle]
    ) -> None:
        client, _, _ = auth_api_client

        response = client.post(
            "/api/auth/sign-in/",
            json={"email": "functional-user@podcast.dev", "password": "incorrect"},
        )

        assert_error_response(
            response,
            status_code=401,
            code="AUTH_INVALID",
            message="Authentication credentials are invalid.",
        )


class TestAuthPasswordResetAPI:
    async def test_reset_password__existing_user__uses_mailer_without_leaking_token(
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

    def test_reset_password__unknown_user__does_not_send_mail(
        self, auth_api_client: tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle]
    ) -> None:
        client, mailer, _ = auth_api_client

        response = client.post("/api/auth/reset-password/", json={"email": "missing@podcast.dev"})

        assert response.status_code in {200, 201}, response.text
        assert response.json() == {"status": "ok"}
        assert mailer.sent == []


class TestAuthRefreshTokenAPI:
    def test_refresh_token__invalid_json_body__fail(
        self,
        auth_api_client: tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle],
    ) -> None:
        client, _, _ = auth_api_client

        response = client.post("/api/auth/refresh-token/", json={})

        assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )


class TestProfileAccessTokenAPI:
    async def test_profile_ips_and_access_token_lifecycle__owned_user__persists(
        self,
        auth_api_client: tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle],
        functional_session: AsyncSession,
    ) -> None:
        client, _, _ = auth_api_client
        profile = client.patch("/api/auth/me/", json={"email": "updated@podcast.dev"})
        assert profile.status_code == 200, profile.text

        me = client.get("/api/auth/me/", headers={"X-Real-IP": "203.0.113.10"})
        assert me.status_code == 200, me.text

        tokens = client.post(
            "/api/auth/access-tokens/", json={"name": "automation", "expires_in_days": 7}
        )
        assert tokens.status_code == 201, tokens.text

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

    async def test_update_me__duplicate_email__does_not_change_profile(
        self,
        auth_api_client: tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        db_user_id = db_user.id
        other_user = User(email="other@podcast.dev", password="hashed", is_active=True)
        functional_session.add(other_user)
        await functional_session.commit()
        client, _, _ = auth_api_client

        response = client.patch("/api/auth/me/", json={"email": "other@podcast.dev"})

        assert_error_response(
            response,
            status_code=409,
            code="CONFLICT",
            message="Requested operation conflicts with the current state.",
        )
        functional_session.expire_all()
        persisted = await functional_session.get(User, db_user_id)
        assert persisted is not None and persisted.email == "functional-user@podcast.dev"

    async def test_user_ips__delete_selected_ids__preserves_other_entries(
        self,
        auth_api_client: tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle],
        functional_session: AsyncSession,
    ) -> None:
        client, _, _ = auth_api_client
        assert client.get("/api/auth/me/", headers={"X-Real-IP": "203.0.113.10"}).status_code == 200
        assert client.get("/api/auth/me/", headers={"X-Real-IP": "203.0.113.11"}).status_code == 200
        listed = client.get("/api/auth/user-ips/")
        ids = [item["id"] for item in listed.json()["items"]]

        response = client.post("/api/auth/user-ips/delete/", json={"ids": [ids[0]]})

        assert response.status_code == 201, response.text
        functional_session.expire_all()
        ips = list((await functional_session.scalars(select(UserIP))).all())
        assert len(ips) == 1
        assert ips[0].id == ids[1]


class TestAuthInviteSystemAPI:
    def test_invite_and_health__external_fakes__record_effects(
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

    def test_health__redis_failure__returns_server_error(
        self,
        auth_api_client: tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        client, _, _ = auth_api_client

        async def fail_redis_check() -> None:
            raise RuntimeError("redis unavailable")

        monkeypatch.setattr("src.modules.api.misc.check_redis_connection", fail_redis_check)

        response = client.get("/api/system/health/")

        assert response.status_code == 500, response.text


class TestAuthAccessTokenAPI:
    def test_update__missing_access_token__does_not_expose(
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

    def test_create__invalid_access_token_payload__fails(
        self, auth_api_client: tuple[TestClient[PodcastApp], FakeMailer, FakeLifecycle]
    ) -> None:
        client, _, _ = auth_api_client

        response = client.post("/api/auth/access-tokens/", json={"name": "", "expires_in_days": 0})

        assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )


class TestUnauthenticatedRouteMatrixAPI:
    @pytest.mark.parametrize(
        ("path", "status_code"),
        [
            ("/api/system/info/", 200),
            ("/api/system/health/", 200),
            ("/api/schema/", 200),
            ("/static/css/podcast-app.css", 200),
            ("/login", 200),
            ("/m/not-a-media-token/", 404),
            ("/r/not-a-media-token/", 404),
        ],
    )
    def test_get_public_route__without_credentials__returns_expected_status(
        self,
        auth_required_client: TestClient[PodcastApp],
        monkeypatch: pytest.MonkeyPatch,
        path: str,
        status_code: int,
    ) -> None:
        async def check_redis_connection() -> None:
            return None

        monkeypatch.setattr("src.modules.api.misc.check_redis_connection", check_redis_connection)

        response = auth_required_client.get(path, follow_redirects=False)

        assert response.status_code == status_code, response.text

    @pytest.mark.parametrize(
        ("method", "path"),
        [
            ("GET", "/api/podcasts/"),
            ("GET", "/api/episodes/"),
            ("GET", "/api/cookies/"),
            ("GET", "/api/auth/me/"),
            ("GET", "/api/playlist/?url=https://example.com/playlist"),
            ("GET", "/api/progress/"),
            ("POST", "/api/media/upload/audio/"),
        ],
    )
    def test_business_api_route__without_credentials__returns_auth_error(
        self,
        auth_required_client: TestClient[PodcastApp],
        method: str,
        path: str,
    ) -> None:
        response = auth_required_client.request(method, path, follow_redirects=False)

        assert response.status_code == 401, response.text
        assert response.json()["error"]["code"] == "AUTH_MISSING"

    @pytest.mark.parametrize("path", ["/podcasts/", "/episodes/", "/profile"])
    def test_html_business_routes_redirect_to_login(
        self,
        auth_required_client: TestClient[PodcastApp],
        path: str,
    ) -> None:
        response = auth_required_client.get(path, follow_redirects=False)

        assert response.status_code == 302, response.text
        assert response.headers["location"] == "/login"
