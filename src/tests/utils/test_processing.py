import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from litestar.datastructures import UploadFile

from src.constants import EpisodeStatus
from src.exceptions import UserCancellationError
from src.modules.utils import processing
from src.modules.utils.processing import (
    TaskContext,
    check_state,
    delete_file,
    episode_process_hook,
    get_file_size,
    move_file,
    publish_redis_stop_downloading,
    remote_copy_episode,
    save_uploaded_file,
    upload_episode,
    upload_process_hook,
)
from src.settings.app import AppSettings
from src.tests.factories import make_episode, make_file
from src.tests.mocks import MockRedisClient, MockStorageS3


@pytest.fixture(autouse=True)
def clear_task_context_cache() -> None:
    TaskContext.create_from_redis.cache_clear()


class TestFileUtils:
    def test_move_file__ok(self, tmp_path: Path) -> None:
        source = tmp_path / "source.txt"
        destination = tmp_path / "destination.txt"
        source.write_text("test content")

        move_file(source, destination)

        assert not source.exists()
        assert destination.read_text() == "test content"

    def test_move_file__missing_source__skip(self, tmp_path: Path) -> None:
        destination = tmp_path / "destination.txt"

        move_file(tmp_path / "missing.txt", destination)

        assert not destination.exists()

    def test_move_file__replaces_existing_destination(self, tmp_path: Path) -> None:
        source = tmp_path / "source.txt"
        destination = tmp_path / "destination.txt"
        source.write_text("new content")
        destination.write_text("old content")

        move_file(source, destination)

        assert destination.read_text() == "new content"

    def test_delete_file__ok(self, tmp_path: Path) -> None:
        source = tmp_path / "source.txt"
        source.write_text("test content")

        delete_file(source)

        assert not source.exists()

    def test_delete_file__none__skip(self) -> None:
        delete_file(None)

    def test_get_file_size__ok(self, tmp_path: Path) -> None:
        source = tmp_path / "source.txt"
        source.write_bytes(b"test content")

        assert get_file_size(source) == len(b"test content")

    def test_get_file_size__missing__returns_zero(self, tmp_path: Path) -> None:
        assert get_file_size(tmp_path / "missing.txt") == 0


class TestTaskContext:
    def test_save_to_redis__ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = MockRedisClient()
        monkeypatch.setattr("src.modules.utils.processing.RedisClient", lambda: redis)

        TaskContext(job_id="job-1").save_to_redis("episode.mp3")

        redis.set.assert_called_once_with("jobid_for_file__episode.mp3", "job-1")

    def test_create_from_redis__ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        redis = MockRedisClient(content={"jobid_for_file__episode.mp3": "job-1"})
        monkeypatch.setattr("src.modules.utils.processing.RedisClient", lambda: redis)

        context = TaskContext.create_from_redis("episode.mp3")

        assert context == TaskContext(job_id="job-1")

    @pytest.mark.parametrize(("job_status", "expected"), [("canceled", True), ("queued", False)])
    def test_task_canceled__ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
        job_status: str,
        expected: bool,
    ) -> None:
        job = SimpleNamespace(id="job-1", get_status=Mock(return_value=job_status))
        fetch = Mock(return_value=job)
        sync_redis = object()
        monkeypatch.setattr("src.modules.utils.processing.Job.fetch", fetch)
        monkeypatch.setattr(
            "src.modules.utils.processing.RedisClient",
            lambda: SimpleNamespace(sync_redis=sync_redis),
        )

        assert TaskContext(job_id="job-1").task_canceled() is expected
        fetch.assert_called_once_with("job-1", connection=sync_redis)


class TestProgressState:
    async def test_check_state__current_progress__ok(
        self,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        episode = make_episode(id=10, podcast_id=20, status=EpisodeStatus.DOWNLOADING)
        episode.audio = make_file(path="audio/episode.mp3")
        redis = MockRedisClient(
            content={
                "episode": {
                    "event_key": "episode",
                    "status": EpisodeStatus.DL_EPISODE_DOWNLOADING,
                    "processed_bytes": 25,
                    "total_bytes": 100,
                }
            }
        )
        monkeypatch.setattr("src.modules.utils.processing.RedisClient", lambda: redis)

        assert await check_state([episode]) == [
            {
                "status": EpisodeStatus.DL_EPISODE_DOWNLOADING,
                "episode_id": episode.id,
                "podcast_id": episode.podcast_id,
                "completed": 25.0,
                "current_file_size": 25,
                "total_file_size": 100,
            }
        ]

    async def test_check_state__pending__ok(
        self,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        episode = make_episode(id=11, podcast_id=21, status=EpisodeStatus.DOWNLOADING)
        episode.audio = make_file(path="audio/pending.mp3")
        redis = MockRedisClient(content={})
        redis.async_get_many.return_value = {}
        monkeypatch.setattr("src.modules.utils.processing.RedisClient", lambda: redis)

        result = await check_state([episode])

        assert result[0]["status"] == EpisodeStatus.DL_PENDING
        assert result[0]["completed"] == 0

    async def test_check_state__error__ok(
        self,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        episode = make_episode(id=12, podcast_id=22, status=EpisodeStatus.ERROR)
        episode.audio = make_file(path="audio/error.mp3")
        redis = MockRedisClient(content={})
        redis.async_get_many.return_value = {}
        monkeypatch.setattr("src.modules.utils.processing.RedisClient", lambda: redis)

        result = await check_state([episode])

        assert result[0]["status"] == EpisodeStatus.ERROR
        assert result[0]["current_file_size"] == 0
        assert result[0]["total_file_size"] == 0


class TestProcessHooks:
    def test_episode_process_hook__ok(
        self,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        redis = MockRedisClient(content={})
        monkeypatch.setattr("src.modules.utils.processing.RedisClient", lambda: redis)

        episode_process_hook(
            EpisodeStatus.DL_EPISODE_DOWNLOADING,
            "episode.mp3",
            total_bytes=100,
            processed_bytes=25,
        )

        redis.set.assert_called_once_with(
            "episode",
            {
                "event_key": "episode",
                "status": str(EpisodeStatus.DL_EPISODE_DOWNLOADING),
                "processed_bytes": 25,
                "total_bytes": 100,
            },
            ttl=app_settings.download_event_redis_ttl,
        )
        redis.publish.assert_called_once_with(
            channel=app_settings.redis.progress_pubsub_ch,
            message=app_settings.redis.progress_pubsub_signal,
        )

    def test_episode_process_hook__chunk_increments_existing_progress(
        self,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        redis = MockRedisClient(content={"episode": {"processed_bytes": 25, "total_bytes": 100}})
        monkeypatch.setattr("src.modules.utils.processing.RedisClient", lambda: redis)

        episode_process_hook(EpisodeStatus.DL_EPISODE_UPLOADING, "episode.mp3", chunk=10)

        assert redis.set.call_args.args[1]["processed_bytes"] == 35
        assert redis.set.call_args.args[1]["total_bytes"] == 100

    def test_episode_process_hook__canceled_download__fail(
        self,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        redis = MockRedisClient(content={})
        task_context = SimpleNamespace(job_id="job-1", task_canceled=Mock(return_value=True))
        monkeypatch.setattr("src.modules.utils.processing.RedisClient", lambda: redis)
        monkeypatch.setattr(
            "src.modules.utils.processing.TaskContext.create_from_redis",
            Mock(return_value=task_context),
        )

        with pytest.raises(UserCancellationError, match="job-1"):
            episode_process_hook(EpisodeStatus.DL_EPISODE_UPLOADING, "episode.mp3")

    def test_episode_process_hook__canceled_postprocessing__kills_process(
        self,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        redis = MockRedisClient(content={})
        kill_process = Mock(return_value=None)
        task_context = SimpleNamespace(job_id="job-1", task_canceled=Mock(return_value=True))
        monkeypatch.setattr("src.modules.utils.processing.RedisClient", lambda: redis)
        monkeypatch.setattr("src.modules.utils.processing.kill_process", kill_process)
        monkeypatch.setattr(
            "src.modules.utils.processing.TaskContext.create_from_redis",
            Mock(return_value=task_context),
        )

        episode_process_hook(
            EpisodeStatus.DL_EPISODE_POSTPROCESSING,
            "episode.mp3",
            processing_filepath="/tmp/episode.mp3",
        )

        kill_process.assert_called_once_with(grep="ffmpeg -y -i /tmp/episode.mp3")

    def test_upload_process_hook__delegates_to_episode_hook(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        episode_hook = Mock(return_value=None)
        monkeypatch.setattr("src.modules.utils.processing.episode_process_hook", episode_hook)

        upload_process_hook("episode.mp3", 64)

        episode_hook.assert_called_once_with(
            filename="episode.mp3",
            status=EpisodeStatus.DL_EPISODE_UPLOADING,
            chunk=64,
        )

    def test_post_processing_process_hook__ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        episode_hook = Mock(return_value=None)
        monkeypatch.setattr("src.modules.utils.processing.episode_process_hook", episode_hook)
        monkeypatch.setattr("src.modules.utils.processing.get_file_size", Mock(return_value=100))
        monkeypatch.setattr("src.modules.utils.processing.time.sleep", Mock(return_value=None))

        processing.post_processing_process_hook(
            filename="episode.mp3",
            target_path="/tmp/result.mp3",
            total_bytes=100,
            src_file_path="/tmp/source.mp3",
        )

        episode_hook.assert_called_once_with(
            filename="episode.mp3",
            status=EpisodeStatus.DL_EPISODE_POSTPROCESSING,
            total_bytes=100,
            processed_bytes=100,
            processing_filepath="/tmp/source.mp3",
        )


class TestKillProcess:
    def test_kill_process__kills_matching_process(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ps_output = "\n".join(
            [
                "USER PID %CPU %MEM VSZ RSS TT STAT STARTED TIME COMMAND",
                "user 1001 0.0 0.0 0 0 ?? S 10:00AM 0:00.01 ffmpeg -y -i /tmp/episode.mp3",
                "user 1002 0.0 0.0 0 0 ?? S 10:00AM 0:00.01 python worker.py",
                "user 1003 0.0 0.0 0 0 ?? S 10:00AM 0:00.01 ffmpeg -y -i /tmp/other.mp3",
            ]
        )
        run = Mock(return_value=SimpleNamespace(stdout=ps_output))
        kill = Mock(return_value=None)
        monkeypatch.setattr("src.modules.utils.processing.subprocess.run", run)
        monkeypatch.setattr("src.modules.utils.processing.os.kill", kill)
        monkeypatch.setattr("src.modules.utils.processing.os.getpid", Mock(return_value=9999))

        processing.kill_process(grep="ffmpeg -y -i /tmp/episode.mp3")

        run.assert_called_once_with(
            ["ps", "aux"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        )
        kill.assert_called_once_with(1001, processing.signal.SIGTERM)

    def test_kill_process__skips_current_process(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ps_output = "\n".join(
            [
                "USER PID %CPU %MEM VSZ RSS TT STAT STARTED TIME COMMAND",
                "user 1001 0.0 0.0 0 0 ?? S 10:00AM 0:00.01 ffmpeg -y -i /tmp/episode.mp3",
            ]
        )
        monkeypatch.setattr(
            "src.modules.utils.processing.subprocess.run",
            Mock(return_value=SimpleNamespace(stdout=ps_output)),
        )
        kill = Mock(return_value=None)
        monkeypatch.setattr("src.modules.utils.processing.os.kill", kill)
        monkeypatch.setattr("src.modules.utils.processing.os.getpid", Mock(return_value=1001))

        processing.kill_process(grep="ffmpeg -y -i /tmp/episode.mp3")

        kill.assert_not_called()

    def test_kill_process__no_matching_process__skips_kill(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ps_output = "\n".join(
            [
                "USER PID %CPU %MEM VSZ RSS TT STAT STARTED TIME COMMAND",
                "user 1001 0.0 0.0 0 0 ?? S 10:00AM 0:00.01 python worker.py",
            ]
        )
        monkeypatch.setattr(
            "src.modules.utils.processing.subprocess.run",
            Mock(return_value=SimpleNamespace(stdout=ps_output)),
        )
        kill = Mock(return_value=None)
        monkeypatch.setattr("src.modules.utils.processing.os.kill", kill)

        processing.kill_process(grep="ffmpeg -y -i /tmp/episode.mp3")

        kill.assert_not_called()


class TestUploadHelpers:
    async def test_upload_episode__ok(
        self,
        app_settings: AppSettings,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "episode.mp3"
        source.write_bytes(b"audio")
        storage = MockStorageS3()
        episode_hook = Mock(return_value=None)
        monkeypatch.setattr("src.modules.utils.processing.StorageS3", lambda: storage)
        monkeypatch.setattr("src.modules.utils.processing.episode_process_hook", episode_hook)

        result = await upload_episode(source)

        assert result == "remote/uploaded.mp3"
        storage.upload_file.assert_awaited_once()
        assert storage.upload_file.await_args is not None
        upload_kwargs = storage.upload_file.await_args.kwargs
        assert upload_kwargs["src_path"] == str(source)
        assert upload_kwargs["dst_path"] == app_settings.s3.bucket_audio_path
        assert callable(upload_kwargs["callback"])

    async def test_upload_episode__storage_failure__returns_none(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "episode.mp3"
        source.write_bytes(b"audio")
        storage = MockStorageS3()
        storage.upload_file.return_value = ""
        episode_hook = Mock(return_value=None)
        monkeypatch.setattr("src.modules.utils.processing.StorageS3", lambda: storage)
        monkeypatch.setattr("src.modules.utils.processing.episode_process_hook", episode_hook)

        assert await upload_episode(source) is None
        assert episode_hook.call_args_list[-1].kwargs == {
            "filename": "episode.mp3",
            "status": EpisodeStatus.ERROR,
            "processed_bytes": 0,
        }

    async def test_remote_copy_episode__ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        storage = MockStorageS3()
        episode_hook = Mock(return_value=None)
        monkeypatch.setattr("src.modules.utils.processing.StorageS3", lambda: storage)
        monkeypatch.setattr("src.modules.utils.processing.episode_process_hook", episode_hook)

        result = await remote_copy_episode("tmp/episode.mp3", "audio/episode.mp3", 100)

        assert result == "remote/copied.mp3"
        storage.copy_file.assert_awaited_once_with(
            src_path="tmp/episode.mp3",
            dst_path="audio/episode.mp3",
        )

    async def test_remote_copy_episode__storage_failure__returns_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        storage = MockStorageS3()
        storage.copy_file.return_value = ""
        episode_hook = Mock(return_value=None)
        monkeypatch.setattr("src.modules.utils.processing.StorageS3", lambda: storage)
        monkeypatch.setattr("src.modules.utils.processing.episode_process_hook", episode_hook)

        assert await remote_copy_episode("tmp/episode.mp3", "audio/episode.mp3", 100) is None
        assert episode_hook.call_args_list[-1].kwargs == {
            "filename": "episode.mp3",
            "status": EpisodeStatus.ERROR,
            "processed_bytes": 0,
        }

    async def test_save_uploaded_file__ok(self, tmp_path: Path) -> None:
        uploaded_file = UploadFile(
            content_type="audio/mpeg",
            filename="episode.mp3",
            file_data=b"audio",
        )

        result = await save_uploaded_file(
            uploaded_file,
            prefix="uploaded_",
            max_file_size=10,
            tmp_path=tmp_path,
        )

        assert result == tmp_path / "uploaded_.mp3"
        assert result.read_bytes() == b"audio"

    @pytest.mark.parametrize(
        ("file_data", "max_file_size", "message"),
        [
            (b"", 10, "result file-size is less than allowed"),
            (b"too-large", 3, "result file-size is more than allowed"),
        ],
    )
    async def test_save_uploaded_file__invalid_size__fail(
        self,
        tmp_path: Path,
        file_data: bytes,
        max_file_size: int,
        message: str,
    ) -> None:
        uploaded_file = UploadFile(
            content_type="audio/mpeg",
            filename="episode.mp3",
            file_data=file_data,
        )

        with pytest.raises(ValueError, match=message):
            await save_uploaded_file(
                uploaded_file,
                prefix="uploaded_",
                max_file_size=max_file_size,
                tmp_path=tmp_path,
            )

    async def test_publish_redis_stop_downloading__ok(
        self,
        app_settings: AppSettings,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        redis = MockRedisClient()
        monkeypatch.setattr("src.modules.utils.processing.RedisClient", lambda: redis)

        await publish_redis_stop_downloading(episode_id=123)

        redis.async_publish.assert_awaited_once_with(
            channel=app_settings.redis.stop_downloading_pubsub_ch,
            message=json.dumps({"episode_id": 123}),
        )
