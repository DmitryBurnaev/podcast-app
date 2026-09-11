from unittest.mock import MagicMock, create_autospec

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.db.repositories import FileRepository, SystemScope
from src.tests.factories import make_file


class TestFileRepository:
    async def test_first_with_episodes__returns_eager_loaded_file(self) -> None:
        media_file = make_file(id=7)
        session = create_autospec(AsyncSession, instance=True)
        result = MagicMock()
        result.scalar_one_or_none.return_value = media_file
        session.execute.return_value = result

        found = await FileRepository(session, scope=SystemScope.ALL).first_with_episodes(7)

        assert found is media_file
        statement = session.execute.await_args.args[0]
        assert statement.compile().params == {"id_1": 7}

    async def test_all_by_path__excludes_ids_and_orders_results(self) -> None:
        files = [make_file(id=8), make_file(id=11)]
        session = create_autospec(AsyncSession, instance=True)
        result = MagicMock()
        result.all.return_value = files
        session.scalars.return_value = result

        found = await FileRepository(session, scope=SystemScope.ALL).all_by_path(
            "audio/shared.mp3",
            excluded_ids=(7, 10),
        )

        assert found == files
        statement = session.scalars.await_args.args[0]
        sql = str(statement)
        assert "media_files.path =" in sql
        assert "media_files.id NOT IN" in sql
        assert "ORDER BY media_files.id" in sql

    async def test_all_by_path__does_not_query_empty_paths(self) -> None:
        session = create_autospec(AsyncSession, instance=True)

        found = await FileRepository(session, scope=SystemScope.ALL).all_by_path("")

        assert found == []
        session.scalars.assert_not_awaited()
