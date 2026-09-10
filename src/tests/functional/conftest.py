"""PostgreSQL fixtures for functional tests.

The harness never uses ``DB_NAME`` for writes.  A caller must explicitly set
``TEST_DB_NAME``; xdist workers receive deterministic, separate databases.
"""

import asyncio
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass

import asyncpg
import pytest
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from src.modules.db import session as db_session
from src.modules.db.models import BaseModel, Podcast, User
from src.modules.db.repositories import UserRepository
from src.modules.db.services import SASessionUOW
from src.settings.db import DBSettings

_DATABASE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


@dataclass(frozen=True)
class TestDatabase:
    """Connection details for one isolated test database."""

    url: URL
    name: str


def _worker_database_name(base_name: str, worker_id: str) -> str:
    if worker_id in {"master", ""}:
        return base_name
    return f"{base_name}_{worker_id}"


def _test_database(pytestconfig: pytest.Config) -> TestDatabase:
    base_name = os.environ.get("TEST_DB_NAME")
    if not base_name:
        raise RuntimeError("TEST_DB_NAME must be set before running functional tests.")

    settings = DBSettings()
    if base_name == settings.name:
        raise RuntimeError("TEST_DB_NAME must differ from DB_NAME.")

    worker_id = getattr(pytestconfig, "workerinput", {}).get("workerid", "master")
    database_name = _worker_database_name(base_name, worker_id)
    if not _DATABASE_NAME_RE.fullmatch(database_name):
        raise RuntimeError("TEST_DB_NAME may contain only letters, digits, and underscores.")

    url = make_url(settings.database_dsn).set(database=database_name)
    return TestDatabase(url=url, name=database_name)


async def _create_database_if_needed(database: TestDatabase) -> None:
    admin_url = database.url.set(drivername="postgresql", database="postgres")
    connection = await asyncpg.connect(admin_url.render_as_string(hide_password=False))
    try:
        exists = await connection.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", database.name
        )
        if not exists:
            await connection.execute(f'CREATE DATABASE "{database.name}"')
    finally:
        await connection.close()


def _run_migrations(database: TestDatabase) -> None:
    environment = os.environ.copy()
    environment["DB_NAME"] = database.name
    subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        check=True,
        env=environment,
    )


@pytest.fixture(scope="session")
def test_database(pytestconfig: pytest.Config) -> Iterator[TestDatabase]:
    """Provision one protected PostgreSQL database and migrate it once."""
    database = _test_database(pytestconfig)
    asyncio.run(_create_database_if_needed(database))
    _run_migrations(database)
    yield database


@pytest.fixture(scope="session")
def functional_engine(test_database: TestDatabase) -> Iterator[AsyncEngine]:
    engine = create_async_engine(test_database.url, poolclass=NullPool)
    yield engine
    asyncio.run(engine.dispose())


@pytest.fixture(scope="session")
def functional_session_factory(
    functional_engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(functional_engine, expire_on_commit=False)


@pytest.fixture(autouse=True)
def use_functional_session_factory(
    functional_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Route every production-created UOW to the isolated functional database."""
    monkeypatch.setattr(
        db_session,
        "get_session_factory",
        lambda: functional_session_factory,
    )


async def _truncate_all_tables(engine: AsyncEngine) -> None:
    table_names = ", ".join(f'"{table.name}"' for table in BaseModel.metadata.sorted_tables)
    async with engine.begin() as connection:
        await connection.execute(text(f"TRUNCATE TABLE {table_names} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def functional_session(
    functional_engine: AsyncEngine,
    functional_session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """Yield a clean database session and reset every table around the test."""
    await _truncate_all_tables(functional_engine)
    async with functional_session_factory() as session:
        yield session
        await session.rollback()
    await _truncate_all_tables(functional_engine)


@pytest.fixture
async def functional_uow(functional_session: AsyncSession) -> AsyncIterator[SASessionUOW]:
    async with SASessionUOW(functional_session) as uow:
        yield uow


@pytest.fixture
async def db_user(functional_session: AsyncSession) -> User:
    user = User(
        email="functional-user@podcast.dev",
        password="hashed-password",
        is_active=True,
        is_superuser=False,
    )
    functional_session.add(user)
    await functional_session.commit()
    await functional_session.refresh(user)
    return user


@pytest.fixture
async def db_podcast(functional_session: AsyncSession, db_user: User) -> Podcast:
    podcast = Podcast(
        publish_id=Podcast.generate_publish_id(),
        name="Functional podcast",
        description="Functional podcast description",
        download_automatically=False,
        owner_id=db_user.id,
    )
    functional_session.add(podcast)
    await functional_session.commit()
    await functional_session.refresh(podcast)
    return podcast


@pytest.fixture
def user_repository(functional_session: AsyncSession) -> UserRepository:
    return UserRepository(functional_session)
