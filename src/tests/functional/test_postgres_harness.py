"""Smoke tests for the isolated PostgreSQL functional-test harness."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.db.models import Podcast, User
from src.modules.db.services import SASessionUOW


class TestPostgreSQLHarness:
    async def test_entity_fixtures_persist_records_in_isolated_database(
        self,
        functional_session: AsyncSession,
        db_user: User,
        db_podcast: Podcast,
    ) -> None:
        persisted_podcast = await functional_session.scalar(
            select(Podcast).where(Podcast.id == db_podcast.id)
        )

        assert persisted_podcast is not None
        assert persisted_podcast.owner_id == db_user.id

    async def test_uow_commits_marked_transaction(
        self,
        functional_session: AsyncSession,
    ) -> None:
        async with SASessionUOW(functional_session) as uow:
            user = User(
                email="uow-user@podcast.dev",
                password="hashed-password",
                is_active=True,
                is_superuser=False,
            )
            functional_session.add(user)
            uow.mark_for_commit()

        persisted_user = await functional_session.scalar(
            select(User).where(User.email == "uow-user@podcast.dev")
        )

        assert persisted_user is not None
