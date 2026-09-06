from litestar.testing import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.main import PodcastApp, make_app
from src.modules.admin.app import ADMIN_VIEWS
from src.modules.db.models import (
    Cookie,
    Episode,
    File,
    Podcast,
    User,
    UserAccessToken,
    UserInvite,
    UserIP,
    UserSession,
)
from src.settings.app import AppSettings
from src.tests.factories import make_user
from src.providers import AppProviders
from src.tests.fakes import (
    FakeHTTPClient,
    FakeLifecycle,
    FakeMailer,
    FakeMediaProcessor,
    FakeMediaSource,
    FakeRedis,
    FakeStorage,
    FakeTaskQueue,
)


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

    async def first(self, **filters: object) -> User | None:
        if self.user is None or filters.get("id") != self.user.id:
            return None
        return self.user


class FakeUserSessionRepository:
    def __init__(self) -> None:
        self.sessions: dict[str, object] = {}

    async def create(self, *, public_id: str, **_: object) -> None:
        self.sessions[public_id] = object()

    async def get_active_by_public_id(self, public_id: str) -> object | None:
        return self.sessions.get(public_id)

    async def deactivate_by_public_id(self, public_id: str) -> None:
        self.sessions.pop(public_id, None)


class FakeUserIPRepository:
    async def get_or_create(self, **_: object) -> None:
        return None


def make_admin_client(
    monkeypatch,
    settings: AppSettings,
    user: User | None = None,
) -> TestClient[PodcastApp]:
    session_factory = async_sessionmaker(class_=AsyncSession)
    session_repository = FakeUserSessionRepository()
    lifecycle = FakeLifecycle()
    queue = FakeTaskQueue()
    providers = AppProviders(
        initialize_database=lifecycle.initialize_database,
        verify_database=lifecycle.verify_database,
        close_database=lifecycle.close_database,
        session_factory=lambda: session_factory,
        uow_factory=MockUOW,
        validate_storage_settings=lambda _: None,
        check_redis=lifecycle.check_redis,
        close_redis=lifecycle.close_redis,
        make_task_queue=lambda _: queue,
        make_storage=FakeStorage,
        make_redis=FakeRedis,
        mailer=FakeMailer(),
        http_client=FakeHTTPClient(),
        media_source=FakeMediaSource(),
        media_processor=FakeMediaProcessor(),
    )

    monkeypatch.setattr("src.modules.auth.backends.SASessionUOW", lambda: MockUOW())
    monkeypatch.setattr(
        "src.modules.auth.backends.UserRepository",
        lambda session: FakeUserRepository(user),
    )
    monkeypatch.setattr(
        "src.modules.auth.backends.UserSessionRepository", lambda session: session_repository
    )
    monkeypatch.setattr(
        "src.modules.auth.backends.AuthUserSessionRepository", lambda session: session_repository
    )
    monkeypatch.setattr(
        "src.modules.auth.backends.UserIPRepository", lambda session: FakeUserIPRepository()
    )

    app = make_app(settings=settings, providers=providers)
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
            response = client.get("/padm/login")

        assert response.status_code == 200

    def test_dashboard__anonymous__redirects_to_login(
        self,
        app_settings: AppSettings,
        monkeypatch,
    ) -> None:
        with make_admin_client(monkeypatch, app_settings) as client:
            response = client.get("/padm/", follow_redirects=False)

        assert response.status_code == 302
        assert response.headers["location"].endswith("/padm/login")

    def test_login__superuser__ok(self, app_settings: AppSettings, monkeypatch) -> None:
        user = make_admin_user()
        with make_admin_client(monkeypatch, app_settings, user=user) as client:
            response = client.post(
                "/padm/login",
                data={"email": user.email, "password": "admin-password"},
                follow_redirects=False,
            )

        assert response.status_code == 302
        assert response.headers["location"].endswith("/padm/")
        assert "session" in response.cookies

    def test_login__inactive_user__rejected(
        self,
        app_settings: AppSettings,
        monkeypatch,
    ) -> None:
        user = make_admin_user(is_active=False)
        with make_admin_client(monkeypatch, app_settings, user=user) as client:
            response = client.post(
                "/padm/login",
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
                "/padm/login",
                data={"email": user.email, "password": "admin-password"},
                follow_redirects=False,
            )

        assert response.status_code == 400
        assert "Invalid credentials." in response.text


class TestAdminViews:
    def test_all_database_models_have_list_and_edit_views(self) -> None:
        expected_models = {
            Cookie,
            Episode,
            File,
            Podcast,
            User,
            UserAccessToken,
            UserInvite,
            UserIP,
            UserSession,
        }

        assert {view.model for view in ADMIN_VIEWS} == expected_models
        assert all(view.can_edit for view in ADMIN_VIEWS)

    def test_model_admin_url_names_match_view_identities(self) -> None:
        for view in ADMIN_VIEWS:
            assert view.model().admin_url_name == view.identity
