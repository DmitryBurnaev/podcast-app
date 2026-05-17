from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from yt_dlp.utils import YoutubeDLError

from src.constants import EpisodeStatus, SourceType
from src.exceptions import DownloadingInterrupted, UserCancellationError
from src.modules.tasks.base import TaskResultCode
from src.modules.tasks.download import DownloadEpisodeTask, UploadedEpisodeTask
from src.modules.db.models import Episode
from src.tests.factories import make_episode, make_file, make_podcast
from src.tests.mocks import MockSession, MockStorageS3


def _episode_with_audio(**kwargs) -> Episode:
    watch_url = kwargs.pop("watch_url", ...)
    episode = make_episode(**kwargs)
    if watch_url is not ...:
        episode.watch_url = watch_url
    episode.audio = make_file(path="audio/source.mp3", size=123)
    episode.audio.source_url = "src://audio"
    episode.podcast = make_podcast()
    return episode


class TestDownloadEpisodeTaskRun:
    async def test_run__user_cancel__resets_episode_and_publishes_signal(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        task = DownloadEpisodeTask(db_session=MockSession())
        update_by_ids = AsyncMock()
        publish = AsyncMock()
        monkeypatch.setattr("src.modules.tasks.download.StorageS3", MockStorageS3)
        monkeypatch.setattr(
            "src.modules.tasks.download.EpisodeRepository",
            Mock(return_value=SimpleNamespace(update_by_ids=update_by_ids)),
        )
        monkeypatch.setattr(
            "src.modules.tasks.download.FileRepository",
            Mock(return_value=SimpleNamespace()),
        )
        monkeypatch.setattr(task, "perform_run", AsyncMock(side_effect=UserCancellationError()))
        monkeypatch.setattr(
            "src.modules.tasks.download.RedisClient",
            Mock(return_value=SimpleNamespace(async_publish=publish)),
        )

        result = await task.run(episode_id=5)

        assert result == TaskResultCode.CANCEL
        update_by_ids.assert_awaited_once_with([5], {"status": EpisodeStatus.NEW})
        publish.assert_awaited_once_with(
            channel=task.settings.redis.progress_pubsub_ch,
            message=task.settings.redis.progress_pubsub_signal,
        )

    async def test_run__unexpected_error__marks_episode_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        task = DownloadEpisodeTask(db_session=MockSession())
        update_by_ids = AsyncMock()
        monkeypatch.setattr("src.modules.tasks.download.StorageS3", MockStorageS3)
        monkeypatch.setattr(
            "src.modules.tasks.download.EpisodeRepository",
            Mock(return_value=SimpleNamespace(update_by_ids=update_by_ids)),
        )
        monkeypatch.setattr(
            "src.modules.tasks.download.FileRepository",
            Mock(return_value=SimpleNamespace()),
        )
        monkeypatch.setattr(task, "perform_run", AsyncMock(side_effect=RuntimeError("boom")))
        monkeypatch.setattr(
            "src.modules.tasks.download.RedisClient",
            Mock(return_value=SimpleNamespace(async_publish=AsyncMock())),
        )

        result = await task.run(episode_id=5)

        assert result == TaskResultCode.ERROR
        update_by_ids.assert_awaited_once_with([5], {"status": EpisodeStatus.ERROR})


class TestDownloadEpisodeTaskSteps:
    async def test_check_is_needed__already_downloaded__updates_and_skips(self) -> None:
        episode = _episode_with_audio()
        task = DownloadEpisodeTask(db_session=MockSession())
        task.storage = MockStorageS3()
        task.storage.get_file_size.return_value = episode.audio.size
        task._update_episodes = AsyncMock()
        task._update_files = AsyncMock()
        task._update_all_rss = AsyncMock()

        with pytest.raises(DownloadingInterrupted) as exc:
            await task._check_is_needed(episode)

        assert exc.value.code == TaskResultCode.SKIP
        task.storage.get_file_size.assert_awaited_once_with(dst_path=episode.audio.path)
        task._update_episodes.assert_awaited_once_with(
            episode,
            update_data={"status": EpisodeStatus.PUBLISHED, "published_at": episode.created_at},
        )
        task._update_files.assert_awaited_once_with(
            episode,
            {"size": episode.audio.size, "available": True},
        )
        task._update_all_rss.assert_awaited_once_with(episode.source_id)

    async def test_download_episode__upload_source_with_existing_path__returns_path(self) -> None:
        episode = _episode_with_audio(source_type=SourceType.UPLOAD)
        task = DownloadEpisodeTask(db_session=MockSession())

        result = await task._download_episode(episode)

        assert result == Path(episode.audio.path)

    async def test_download_episode__upload_source_without_path__fail(self) -> None:
        episode = _episode_with_audio(source_type=SourceType.UPLOAD)
        episode.audio.path = ""
        task = DownloadEpisodeTask(db_session=MockSession())

        with pytest.raises(DownloadingInterrupted) as exc:
            await task._download_episode(episode)

        assert exc.value.code == TaskResultCode.ERROR

    async def test_download_episode__missing_watch_url__fail(self) -> None:
        episode = _episode_with_audio(watch_url=None)
        task = DownloadEpisodeTask(db_session=MockSession())

        with pytest.raises(DownloadingInterrupted) as exc:
            await task._download_episode(episode)

        assert exc.value.code == TaskResultCode.ERROR

    async def test_download_episode__download_error__marks_related_records(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        episode = _episode_with_audio()
        task = DownloadEpisodeTask(db_session=MockSession())
        task._update_episodes = AsyncMock()
        task._update_files = AsyncMock()
        monkeypatch.setattr(
            "src.modules.tasks.download.cookie_file_ctx",
            lambda *args, **kwargs: _AsyncContext(None),
        )
        monkeypatch.setattr(
            "src.modules.tasks.download.common_utils.download_audio",
            AsyncMock(side_effect=YoutubeDLError("download failed")),
        )

        with pytest.raises(DownloadingInterrupted) as exc:
            await task._download_episode(episode)

        assert exc.value.code == TaskResultCode.ERROR
        task._update_episodes.assert_awaited_once_with(episode, {"status": EpisodeStatus.ERROR})
        task._update_files.assert_awaited_once_with(episode, {"available": False})

    async def test_process_file__youtube__sets_metadata(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        episode = _episode_with_audio(source_type=SourceType.YOUTUBE)
        ffmpeg_preparation = Mock()
        ffmpeg_set_metadata = Mock()
        monkeypatch.setattr(
            "src.modules.tasks.download.ffmpeg_utils.ffmpeg_preparation",
            ffmpeg_preparation,
        )
        monkeypatch.setattr(
            "src.modules.tasks.download.ffmpeg_utils.ffmpeg_set_metadata",
            ffmpeg_set_metadata,
        )

        await DownloadEpisodeTask._process_file(episode, tmp_path / "episode.mp3")

        ffmpeg_preparation.assert_called_once()
        ffmpeg_set_metadata.assert_called_once()

    async def test_upload_file__success__updates_path_and_returns_size(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        episode = _episode_with_audio()
        task = DownloadEpisodeTask(db_session=MockSession())
        task.storage = MockStorageS3()
        task.storage.get_file_size.return_value = 456
        task._update_files = AsyncMock()
        upload_episode = AsyncMock(return_value="audio/result.mp3")
        monkeypatch.setattr("src.modules.tasks.download.processing_utils.upload_episode", upload_episode)

        result = await task._upload_file(episode, tmp_path / "episode.mp3")

        assert result == 456
        task._update_files.assert_awaited_once_with(episode, {"path": "audio/result.mp3"})
        task.storage.get_file_size.assert_awaited_once_with("episode.mp3")

    async def test_upload_file__failure__marks_episode_error(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        episode = _episode_with_audio()
        task = DownloadEpisodeTask(db_session=MockSession())
        task._update_episodes = AsyncMock()
        monkeypatch.setattr(
            "src.modules.tasks.download.processing_utils.upload_episode",
            AsyncMock(return_value=None),
        )

        with pytest.raises(DownloadingInterrupted) as exc:
            await task._upload_file(episode, tmp_path / "episode.mp3")

        assert exc.value.code == TaskResultCode.ERROR
        task._update_episodes.assert_awaited_once_with(episode, {"status": EpisodeStatus.ERROR})

    async def test_update_all_rss__regenerates_sorted_podcast_ids(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        task = DownloadEpisodeTask(db_session=MockSession())
        task.episode_repository = SimpleNamespace(
            all=AsyncMock(
                return_value=[
                    make_episode(podcast_id=2),
                    make_episode(podcast_id=1),
                ]
            )
        )
        generate_rss_task = SimpleNamespace(run=AsyncMock())
        monkeypatch.setattr(
            "src.modules.tasks.download.GenerateRSSTask",
            Mock(return_value=generate_rss_task),
        )

        await task._update_all_rss("source")

        task.episode_repository.all.assert_awaited_once_with(source_id="source")
        generate_rss_task.run.assert_awaited_once_with(1, 2)


class TestUploadedEpisodeTask:
    async def test_perform_run__already_published__skip(self) -> None:
        episode = _episode_with_audio(status=EpisodeStatus.PUBLISHED)
        task = UploadedEpisodeTask(db_session=MockSession())
        task.storage = MockStorageS3()
        task.storage.get_file_size.return_value = episode.audio.size
        task.episode_repository = SimpleNamespace(get=AsyncMock(return_value=episode))

        with pytest.raises(DownloadingInterrupted) as exc:
            await task.perform_run(episode_id=1)

        assert exc.value.code == TaskResultCode.SKIP

    async def test_perform_run__remote_size_mismatch__error(self) -> None:
        episode = _episode_with_audio(status=EpisodeStatus.NEW)
        task = UploadedEpisodeTask(db_session=MockSession())
        task.storage = MockStorageS3()
        task.storage.get_file_size.return_value = 1
        task.episode_repository = SimpleNamespace(get=AsyncMock(return_value=episode))

        with pytest.raises(DownloadingInterrupted) as exc:
            await task.perform_run(episode_id=1)

        assert exc.value.code == TaskResultCode.ERROR

    async def test_perform_run__copies_and_publishes(self) -> None:
        episode = _episode_with_audio(status=EpisodeStatus.NEW)
        task = UploadedEpisodeTask(db_session=MockSession())
        task.storage = MockStorageS3()
        task.storage.get_file_size.side_effect = [episode.audio.size, 999]
        task.episode_repository = SimpleNamespace(
            get=AsyncMock(return_value=episode),
            update=AsyncMock(),
        )
        task.file_repository = SimpleNamespace(update=AsyncMock())
        task._copy_file = AsyncMock(return_value="audio/final.mp3")
        task._update_all_rss = AsyncMock()

        result = await task.perform_run(episode_id=1)

        assert result == TaskResultCode.SUCCESS
        task.episode_repository.update.assert_awaited_once_with(
            episode,
            status=EpisodeStatus.PUBLISHED,
            published_at=episode.created_at,
        )
        task.file_repository.update.assert_awaited_once_with(
            episode.audio,
            path="audio/final.mp3",
            size=999,
            available=True,
        )
        task.db_session.flush.assert_awaited_once_with()

    async def test_copy_file__failure__marks_episode_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        episode = _episode_with_audio()
        task = UploadedEpisodeTask(db_session=MockSession())
        task.episode_repository = SimpleNamespace(update=AsyncMock())
        monkeypatch.setattr(
            "src.modules.tasks.download.processing_utils.remote_copy_episode",
            AsyncMock(return_value=None),
        )

        with pytest.raises(DownloadingInterrupted) as exc:
            await task._copy_file(episode)

        assert exc.value.code == TaskResultCode.ERROR
        task.episode_repository.update.assert_awaited_once_with(
            episode,
            status=EpisodeStatus.ERROR,
        )


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, *args: object) -> None:
        return None
