from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.modules.tasks.base import RQTask, TaskResultCode
from src.settings.app import AppSettings
from src.tests.mocks import MockSession, MockUOW


class SuccessfulTaskForTest(RQTask):
    async def run(self, *args: object, **kwargs: object) -> TaskResultCode:
        return TaskResultCode.SUCCESS


class FailingTaskForTest(RQTask):
    async def run(self, *args: object, **kwargs: object) -> TaskResultCode:
        raise RuntimeError("Oops")


class ChildTaskForTest(SuccessfulTaskForTest):
    pass


class TestRQTaskMetadata:
    def test_tasks__eq__ok(self, app_settings: AppSettings) -> None:
        assert SuccessfulTaskForTest() == SuccessfulTaskForTest()

    def test_tasks__eq__another_task__fail(self, app_settings: AppSettings) -> None:
        assert SuccessfulTaskForTest() != FailingTaskForTest()

    def test_name__ok(self, app_settings: AppSettings) -> None:
        assert SuccessfulTaskForTest().name == "SuccessfulTaskForTest"

    def test_get_subclasses__includes_recursive_children(self) -> None:
        task_classes = list(RQTask.get_subclasses())

        assert SuccessfulTaskForTest in task_classes
        assert ChildTaskForTest in task_classes

    @pytest.mark.parametrize(
        ("args", "kwargs", "expected"),
        [
            ((1, 2), {"kwarg": 123}, "successfultaskfortest_1_2_kwarg=123_"),
            ((), {}, "successfultaskfortest___"),
            (("episode",), {"force": True, "attempt": 2}, "successfultaskfortest_episode_force=True_attempt=2_"),
        ],
    )
    def test_get_job_id__ok(
        self,
        args: tuple[object, ...],
        kwargs: dict[str, object],
        expected: str,
    ) -> None:
        assert SuccessfulTaskForTest.get_job_id(*args, **kwargs) == expected

    def test_db_session__without_session__fail(self, app_settings: AppSettings) -> None:
        task = SuccessfulTaskForTest()

        with pytest.raises(RuntimeError, match="No database session available"):
            _ = task.db_session

    def test_task_context__without_context__fail(self, app_settings: AppSettings) -> None:
        task = SuccessfulTaskForTest()

        with pytest.raises(RuntimeError, match="No task context available"):
            _ = task.task_context


class TestRQTaskRun:
    async def test_perform_and_run__success__commits_transaction(
        self,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = MockSession()
        monkeypatch.setattr("src.modules.tasks.base.SASessionUOW", lambda: MockUOW(session))
        task = SuccessfulTaskForTest()

        result = await task._perform_and_run(1, force=True)

        assert result == TaskResultCode.SUCCESS
        assert task.db_session is session
        assert task.task_context.job_id == "successfultaskfortest_1_force=True_"
        session.commit.assert_awaited_once_with()
        session.rollback.assert_not_awaited()

    async def test_perform_and_run__error__rolls_back_transaction(
        self,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        session = MockSession()
        monkeypatch.setattr("src.modules.tasks.base.SASessionUOW", lambda: MockUOW(session))
        task = FailingTaskForTest()

        result = await task._perform_and_run(2)

        assert result == TaskResultCode.ERROR
        assert task.db_session is session
        session.commit.assert_not_awaited()
        session.rollback.assert_awaited_once_with()

    def test_call__wraps_lifespan_and_closes_resources(
        self,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        task = SuccessfulTaskForTest()
        perform_and_run = AsyncMock(return_value=TaskResultCode.SUCCESS)
        initialize_database = AsyncMock(return_value=None)
        close_database = AsyncMock(return_value=None)
        close_async_redis_connection = AsyncMock(return_value=None)
        monkeypatch.setattr(task, "_perform_and_run", perform_and_run)
        monkeypatch.setattr("src.modules.tasks.base.initialize_database", initialize_database)
        monkeypatch.setattr("src.modules.tasks.base.close_database", close_database)
        monkeypatch.setattr(
            "src.modules.tasks.base.close_async_redis_connection",
            close_async_redis_connection,
        )

        result = task(episode_id=10)

        assert result == TaskResultCode.SUCCESS
        initialize_database.assert_awaited_once_with()
        perform_and_run.assert_awaited_once_with(episode_id=10)
        close_database.assert_awaited_once_with()
        close_async_redis_connection.assert_awaited_once_with()


class TestRQTaskCancel:
    def test_cancel_task__ok(
        self,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        job = SimpleNamespace(cancel=Mock(return_value=None))
        fetch = Mock(return_value=job)
        sync_redis = object()
        monkeypatch.setattr("src.modules.tasks.base.Job.fetch", fetch)
        monkeypatch.setattr(
            "src.modules.tasks.base.RedisClient",
            lambda: SimpleNamespace(sync_redis=sync_redis),
        )

        SuccessfulTaskForTest.cancel_task(1, 2, kwarg=123)

        fetch.assert_called_once_with(
            "successfultaskfortest_1_2_kwarg=123_",
            connection=sync_redis,
        )
        job.cancel.assert_called_once_with()

    def test_cancel_task__fetch_error__does_not_raise(
        self,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        fetch = Mock(side_effect=RuntimeError("Redis unavailable"))
        monkeypatch.setattr("src.modules.tasks.base.Job.fetch", fetch)
        monkeypatch.setattr(
            "src.modules.tasks.base.RedisClient",
            lambda: SimpleNamespace(sync_redis=object()),
        )

        SuccessfulTaskForTest.cancel_task(1)

        fetch.assert_called_once()
