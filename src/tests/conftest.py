from collections.abc import Generator
import asyncio
import os
import re
from dataclasses import dataclass
from typing import AsyncGenerator, Any, Iterator

from litestar.middleware import AuthenticationResult
from litestar.testing import TestClient
from pydantic import SecretStr
import asyncpg  # type: ignore
import pytest
import pytest_asyncio
import sqlalchemy
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ProgrammingError, OperationalError
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool
from sqlalchemy.util import concurrency

from src import main as app_main
from src.main import DbStartMode, PodcastApp, make_app
from src.modules import tasks
from src.modules.services import email as email_service
from src.modules.utils import common as common_utils
from src.modules.utils import ffmpeg
from src.settings.app import AppSettings, FlagsSettings
from src.settings.log import LogSettings
from src.tests.factories import make_user
from src.tests.fakes import (
    FakeLifecycle,
    FakeMailer,
    FakeMediaProcessor,
    FakeMediaSource,
    FakeRedis,
    FakeStorage,
    FakeTaskQueue,
)
from src.tests.mocks import mock_target_class
from src.modules.db.models import BaseModel, User
from src.settings.db import DBSettings
from src.tests.helpers import make_db_session

_DATABASE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _make_settings(*, api_debug_mode: bool) -> AppSettings:
    return AppSettings(
        app_secret_key=SecretStr("test-secret-key" * 8),
        app_version="test",
        flags=FlagsSettings(debug_mode=True, api_debug_mode=api_debug_mode),
        log=LogSettings(format="[%(levelname)s] %(message)s"),
        api_docs_enabled=False,
    )


@pytest.fixture
def app_settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    settings = _make_settings(api_debug_mode=True)
    monkeypatch.setattr("src.main.get_app_settings", lambda: settings)
    return settings


@pytest.fixture
def auth_required_settings(monkeypatch: pytest.MonkeyPatch) -> AppSettings:
    settings = _make_settings(api_debug_mode=False)
    monkeypatch.setattr("src.main.get_app_settings", lambda: settings)
    return settings


@pytest.fixture
def current_user() -> User:
    return make_user()


@pytest.fixture
def mocked_app_lifecycle(monkeypatch: pytest.MonkeyPatch) -> FakeLifecycle:
    """Keep app startup real while replacing network/process lifecycle boundaries."""
    lifecycle = FakeLifecycle()
    monkeypatch.setitem(
        app_main._DB_STARTUP_CHECKS,
        DbStartMode.INIT,
        lifecycle.initialize_database,
    )
    monkeypatch.setitem(
        app_main._DB_STARTUP_CHECKS,
        DbStartMode.VERIFY,
        lifecycle.verify_database,
    )
    monkeypatch.setattr(app_main, "validate_s3_settings", lambda _: None)
    monkeypatch.setattr(app_main, "check_redis_connection", lifecycle.check_redis)
    monkeypatch.setattr(app_main, "close_database", lifecycle.close_database)
    monkeypatch.setattr(app_main, "close_async_redis_connection", lifecycle.close_redis)
    monkeypatch.setattr(app_main, "make_admin", lambda _: None)
    return lifecycle


@pytest.fixture
def mocked_rq_queue(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeTaskQueue]:
    queue = FakeTaskQueue()

    for task_class in (tasks.DownloadEpisodeTask, tasks.DownloadEpisodeImageTask):
        monkeypatch.setattr(
            task_class,
            "cancel_task",
            classmethod(lambda cls, *args, **kwargs: queue.cancel_task(cls, *args, **kwargs)),
        )

    yield from mock_target_class(queue, monkeypatch)


@pytest.fixture
def mocked_storage(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeStorage]:
    yield from mock_target_class(FakeStorage(), monkeypatch)


@pytest.fixture
def mocked_redis(monkeypatch: pytest.MonkeyPatch) -> Iterator[FakeRedis]:
    yield from mock_target_class(FakeRedis(), monkeypatch)


@pytest.fixture
def mocked_mailer(monkeypatch: pytest.MonkeyPatch) -> FakeMailer:
    mailer = FakeMailer()
    monkeypatch.setattr(email_service, "send_email", mailer.send)
    return mailer


@pytest.fixture
def mocked_media_source(monkeypatch: pytest.MonkeyPatch) -> FakeMediaSource:
    source = FakeMediaSource()
    monkeypatch.setattr(common_utils, "get_source_media_info", source.get_source_media_info)
    return source


@pytest.fixture
def mocked_media_processor(monkeypatch: pytest.MonkeyPatch) -> FakeMediaProcessor:
    processor = FakeMediaProcessor()
    monkeypatch.setattr(ffmpeg, "audio_metadata", processor.audio_metadata)
    monkeypatch.setattr(ffmpeg, "audio_cover", processor.audio_cover)
    return processor


@pytest.fixture
def app(
    app_settings: AppSettings,
    current_user: User,
    mocked_app_lifecycle: FakeLifecycle,
    mocked_rq_queue: FakeTaskQueue,
    monkeypatch: pytest.MonkeyPatch,
) -> PodcastApp:
    async def authenticate_as_current_user(_: object, __: object) -> AuthenticationResult:
        return AuthenticationResult(user=current_user, auth=None)

    monkeypatch.setattr(
        "src.modules.auth.middlewares.APIAuthMiddleware.authenticate_request",
        authenticate_as_current_user,
    )
    return make_app(settings=app_settings)


@pytest.fixture
def auth_required_app(
    auth_required_settings: AppSettings,
    mocked_app_lifecycle: FakeLifecycle,
    mocked_rq_queue: FakeTaskQueue,
) -> PodcastApp:
    return make_app(settings=auth_required_settings)


@pytest.fixture
def client(app: PodcastApp) -> Generator[TestClient[PodcastApp], None, None]:
    yield TestClient(app=app, raise_server_exceptions=False)


@pytest.fixture
def auth_required_client(
    auth_required_app: PodcastApp,
) -> Generator[TestClient[PodcastApp], None, None]:
    yield TestClient(app=auth_required_app, raise_server_exceptions=False)


@pytest_asyncio.fixture
async def dbs() -> AsyncGenerator[Any, Any]:
    async with make_db_session() as db_session:
        yield db_session


def db_prep() -> str:
    print("Dropping the old test db…")
    settings = DBSettings()
    db_name = settings.name_test
    engine = sqlalchemy.create_engine(settings.database_dsn)
    conn = engine.connect()

    def exec_sql(query: str):
        return conn.execute(sqlalchemy.text(query))

    db_exists = conn.execute(
        sqlalchemy.text(f"SELECT 1 FROM pg_database WHERE datname = '{db_name}'")
    ).scalar()
    try:
        conn = conn.execution_options(autocommit=False)
        exec_sql("ROLLBACK")
        # exec_sql(f"DROP DATABASE {db_name}")
    except ProgrammingError:
        print("Could not drop the database, probably does not exist.")
        exec_sql("ROLLBACK")
    except OperationalError:
        print("Could not drop database because it’s being accessed by other users")
        exec_sql("ROLLBACK")

    if not db_exists:
        print(f"Test db is about to create {db_name}")
        exec_sql(f"CREATE DATABASE {db_name}")

    try:
        exec_sql(f"CREATE USER {settings.user} WITH ENCRYPTED PASSWORD '{settings.password}'")
    except Exception as e:
        print(f"User already exists. ({e})")
        exec_sql(f"GRANT ALL PRIVILEGES ON DATABASE {db_name} TO {settings.user}")

    conn.close()
    return db_name


@pytest_asyncio.fixture(autouse=True, scope="session")
async def db_migration():
    settings = DBSettings()

    def create_tables():
        db_prep()
        print(f"Creating tables in DB '{settings.name_test}' ... ")
        engine = sqlalchemy.create_engine(settings.database_dsn_test)
        BaseModel.metadata.create_all(engine)

    await concurrency.greenlet_spawn(create_tables)
    print("DB and tables were successful created.")
