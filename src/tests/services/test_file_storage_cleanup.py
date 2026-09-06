import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from src.modules.services.storage import (
    FileCleanupStatus,
    FileStorageCleanupService,
    StorageDeleteResult,
    StorageDeleteStatus,
)
from src.tests.factories import make_episode, make_file


class FakeQueryResult:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows

    def scalars(self) -> "FakeQueryResult":
        return self

    def unique(self) -> "FakeQueryResult":
        return self

    def all(self) -> list[Any]:
        return self.rows

    def tuples(self) -> "FakeQueryResult":
        return self

    def __iter__(self):
        return iter(self.rows)


class FakeSession:
    def __init__(self, execute_results: list[FakeQueryResult]) -> None:
        self.execute_results = execute_results

    async def execute(self, _: Any) -> FakeQueryResult:
        return self.execute_results.pop(0)

    async def scalars(self, _: Any) -> FakeQueryResult:
        return self.execute_results.pop(0)


class FakeUOW:
    def __init__(
        self,
        execute_results: list[FakeQueryResult],
        *,
        fail_on_exit: bool = False,
    ) -> None:
        self.session = FakeSession(execute_results)
        self.marked_for_commit = False
        self.fail_on_exit = fail_on_exit

    async def __aenter__(self) -> "FakeUOW":
        return self

    async def __aexit__(self, *_: object) -> None:
        if self.fail_on_exit:
            raise RuntimeError("database commit failed")
        return None

    def mark_for_commit(self) -> None:
        self.marked_for_commit = True


class FakeCleanupStorage:
    def __init__(self, results: dict[str, StorageDeleteResult] | None = None) -> None:
        self.results = results or {}
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0
        self.settings = SimpleNamespace(
            s3=SimpleNamespace(
                storage_url="https://access:secret@s3.example.test?token=hidden",
                bucket_name="podcast-test",
                region_name="test-region",
            )
        )

    async def delete_file_result(self, *, dst_path: str) -> StorageDeleteResult:
        self.calls.append(dst_path)
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return self.results.get(
            dst_path,
            StorageDeleteResult(status=StorageDeleteStatus.DELETED, response={"ok": True}),
        )


class TestFileStorageCleanupService:
    async def test_clear_files__runs_in_parallel_and_keeps_partial_success(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        first = make_file(id=1, path="audio/one.mp3", size=10)
        first.source_url = "https://provider.test/source"
        first.meta = {"preserved": True}
        second = make_file(id=2, path="audio/two.mp3", size=20)
        third = make_file(id=3, path="audio/three.mp3", size=30)
        episode = make_episode(id=11, source_id="source-11")
        episode.audio = first
        storage = FakeCleanupStorage(
            {
                second.path: StorageDeleteResult(
                    status=StorageDeleteStatus.FAILED,
                    error="S3 unavailable",
                )
            }
        )
        uow = FakeUOW([FakeQueryResult([first, second, third]), FakeQueryResult([])])
        service = FileStorageCleanupService(storage=storage, uow_factory=lambda: uow)

        with caplog.at_level(logging.DEBUG):
            result = await service.clear_files([1, 2, 3], concurrency=2)

        assert storage.max_active == 2
        assert result.cleared_ids == (1, 3)
        assert result.failed_ids == (2,)
        assert (first.path, first.size, first.available) == ("", 0, False)
        assert first.source_url == "https://provider.test/source"
        assert first.meta == {"preserved": True}
        assert first.hash == "file-hash"
        assert (third.path, third.size, third.available) == ("", 0, False)
        assert (second.path, second.size, second.available) == ("audio/two.mp3", 20, True)
        assert uow.marked_for_commit is True
        assert "endpoint=https://s3.example.test" in caplog.text
        assert "secret" not in caplog.text
        assert "token=hidden" not in caplog.text
        assert "episode_ids=[11]" in caplog.text
        assert "S3 unavailable" in caplog.text
        assert "File storage cleanup finished" in caplog.text

    async def test_clear_files__deletes_shared_selected_path_once(self) -> None:
        first = make_file(id=1, path="audio/shared.mp3", size=10)
        second = make_file(id=2, path="audio/shared.mp3", size=10)
        storage = FakeCleanupStorage()
        uow = FakeUOW([FakeQueryResult([first, second]), FakeQueryResult([])])
        service = FileStorageCleanupService(storage=storage, uow_factory=lambda: uow)

        result = await service.clear_files([1, 2])

        assert storage.calls == ["audio/shared.mp3"]
        assert result.cleared_ids == (1, 2)

    async def test_clear_files__shared_unselected_path_blocks_entire_batch(self) -> None:
        shared = make_file(id=1, path="audio/shared.mp3")
        independent = make_file(id=2, path="audio/independent.mp3")
        storage = FakeCleanupStorage()
        uow = FakeUOW(
            [
                FakeQueryResult([shared, independent]),
                FakeQueryResult([(99, "audio/shared.mp3")]),
            ]
        )
        service = FileStorageCleanupService(storage=storage, uow_factory=lambda: uow)

        result = await service.clear_files([1, 2])

        assert storage.calls == []
        assert result.failed_ids == (1, 2)
        assert "File IDs [99]" in (result.items[0].error or "")
        assert "Batch was blocked" in (result.items[1].error or "")
        assert shared.path == "audio/shared.mp3"
        assert independent.path == "audio/independent.mp3"

    async def test_clear_files__normalizes_ids_and_reports_missing_and_already_clear(
        self,
    ) -> None:
        cleared = make_file(id=1, path="", size=0, available=False)
        storage = FakeCleanupStorage()
        uow = FakeUOW([FakeQueryResult([cleared])])
        service = FileStorageCleanupService(storage=storage, uow_factory=lambda: uow)

        result = await service.clear_files([1, 1, 404])

        assert [item.file_id for item in result.items] == [1, 404]
        assert result.items[0].status == FileCleanupStatus.ALREADY_CLEAR
        assert result.items[1].status == FileCleanupStatus.FAILED
        assert storage.calls == []

    async def test_clear_files__treats_s3_not_found_as_cleared(self) -> None:
        file = make_file(id=1, path="audio/missing.mp3", size=10)
        storage = FakeCleanupStorage(
            {
                file.path: StorageDeleteResult(
                    status=StorageDeleteStatus.NOT_FOUND,
                    error="already absent",
                )
            }
        )
        uow = FakeUOW([FakeQueryResult([file]), FakeQueryResult([])])
        service = FileStorageCleanupService(storage=storage, uow_factory=lambda: uow)

        result = await service.clear_files([1])

        assert result.cleared_ids == (1,)
        assert file.path == ""

    async def test_clear_files__rejects_invalid_concurrency(self) -> None:
        storage = FakeCleanupStorage()
        service = FileStorageCleanupService(storage=storage)

        with pytest.raises(ValueError, match="concurrency"):
            await service.clear_files([1], concurrency=0)

    async def test_clear_files__does_not_return_success_when_database_commit_fails(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        file = make_file(id=1, path="audio/one.mp3")
        storage = FakeCleanupStorage()
        uow = FakeUOW(
            [FakeQueryResult([file]), FakeQueryResult([])],
            fail_on_exit=True,
        )
        service = FileStorageCleanupService(storage=storage, uow_factory=lambda: uow)

        with (
            caplog.at_level(logging.ERROR),
            pytest.raises(
                RuntimeError,
                match="database commit failed",
            ),
        ):
            await service.clear_files([1])

        assert storage.calls == ["audio/one.mp3"]
        assert "deleted_paths=['audio/one.mp3']" in caplog.text
