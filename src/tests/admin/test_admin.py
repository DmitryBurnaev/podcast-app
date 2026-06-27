from unittest.mock import AsyncMock

from litestar.testing import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import src.main as main
from src.main import DbStartMode, PodcastApp, make_app
from src.modules.db.models import User
from src.settings.app import AppSettings
from src.tests.factories import make_user


class MockUOW:
    session = object()

    async def __aenter__(self) -> "MockUOW":
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class FakeUserRepository:
    def __init__(self, user: User | None) -> None:
        self.user = user

    async def get_by_email(self, email: str) -> User | None:
        return self.user if self.user and self.user.email == email else None


def make_admin_client(
    monkeypatch,
    settings: AppSettings,
    user: User | None = None,
) -> TestClient[PodcastApp]:
    session_factory = async_sessionmaker(class_=AsyncSession)

    monkeypatch.setattr("src.main.initialize_database", AsyncMock(return_value=None))
    monkeypatch.setitem(main._DB_STARTUP_CHECKS, DbStartMode.INIT, AsyncMock(return_value=None))
    monkeypatch.setattr("src.main.close_database", AsyncMock(return_value=None))
    monkeypatch.setattr("src.main.check_redis_connection", AsyncMock(return_value=None))
    monkeypatch.setattr("src.main.close_async_redis_connection", AsyncMock(return_value=None))
    monkeypatch.setattr("src.main.validate_s3_settings", lambda _: None)
    monkeypatch.setattr(
        "src.modules.admin.application.get_session_factory", lambda: session_factory
    )
    monkeypatch.setattr("src.modules.admin.auth.SASessionUOW", lambda: MockUOW())
    monkeypatch.setattr(
        "src.modules.admin.auth.UserRepository",
        lambda session: FakeUserRepository(user),
    )

    app = make_app(settings=settings)
    return TestClient(app=app, raise_server_exceptions=False)


def make_admin_user(
    *,
    is_active: bool = True,
    is_superuser: bool = True,
    password: str = "admin-password",
) -> User:
    user = make_user(
        email="admin@podcast.dev",
        is_active=is_active,
        is_superuser=is_superuser,
    )
    user.password = User.make_password(password)
    return user


class TestAdminIntegration:
    def test_login_page__ok(self, app_settings: AppSettings, monkeypatch) -> None:
        with make_admin_client(monkeypatch, app_settings) as client:
            response = client.get("/admin/login")

        assert response.status_code == 200
        assert "Login to Podcast App Admin" in response.text

    def test_dashboard__anonymous__redirects_to_login(
        self,
        app_settings: AppSettings,
        monkeypatch,
    ) -> None:
        with make_admin_client(monkeypatch, app_settings) as client:
            response = client.get("/admin/", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"].endswith("/admin/login")

    def test_login__superuser__ok(self, app_settings: AppSettings, monkeypatch) -> None:
        user = make_admin_user()
        with make_admin_client(monkeypatch, app_settings, user=user) as client:
            response = client.post(
                "/admin/login",
                data={"email": user.email, "password": "admin-password"},
                follow_redirects=False,
            )

        assert response.status_code == 302
        assert response.headers["location"].endswith("/admin/")
        assert "podcast_admin_session" in response.cookies

    def test_login__inactive_user__rejected(
        self,
        app_settings: AppSettings,
        monkeypatch,
    ) -> None:
        user = make_admin_user(is_active=False)
        with make_admin_client(monkeypatch, app_settings, user=user) as client:
            response = client.post(
                "/admin/login",
                data={"email": user.email, "password": "admin-password"},
                follow_redirects=False,
            )

        assert response.status_code == 400
        assert "Invalid credentials." in response.text

    def test_login__regular_user__rejected(
        self,
        app_settings: AppSettings,
        monkeypatch,
    ) -> None:
        user = make_admin_user(is_superuser=False)
        with make_admin_client(monkeypatch, app_settings, user=user) as client:
            response = client.post(
                "/admin/login",
                data={"email": user.email, "password": "admin-password"},
                follow_redirects=False,
            )

        assert response.status_code == 400
        assert "Invalid credentials." in response.text
