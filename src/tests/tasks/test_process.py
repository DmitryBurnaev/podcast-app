from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.exceptions import MaxAttemptsReached, NotFoundError
from src.modules.db.models.media import MediaType
from src.modules.tasks.base import TaskResultCode
from src.modules.tasks.process import (
    ApplyMetadataEpisodeTask,
    BaseEpisodePostProcessTask,
    DownloadEpisodeImageTask,
)
from src.tests.factories import make_episode, make_file, make_podcast
from src.tests.mocks import MockSession, MockStorageS3


class FailingPostProcessTask(BaseEpisodePostProcessTask):
    async def perform_run(self, episode_id: int) -> TaskResultCode:
        raise RuntimeError("boom")


class TestBaseEpisodePostProcessTask:
    async def test_run__perform_error__returns_error(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.modules.tasks.process.StorageS3", MockStorageS3)
        task = FailingPostProcessTask(db_session=MockSession())

        result = await task.run(episode_id=1)

        assert result == TaskResultCode.ERROR


class TestDownloadEpisodeImageTask:
    async def test_perform_run__no_episodes__ok(self) -> None:
        task = DownloadEpisodeImageTask(db_session=MockSession())
        task.episode_repository = SimpleNamespace(all=AsyncMock(return_value=[]))

        result = await task.perform_run(episode_id=0)

        assert result == TaskResultCode.SUCCESS

    async def test_perform_run__existing_storage_image__skips_update(
        self,
        app_settings,
    ) -> None:
        episode = make_episode()
        episode.image = make_file(
            type=MediaType.IMAGE,
            path=f"{app_settings.s3.bucket_images_path}1.jpg",
        )
        task = DownloadEpisodeImageTask(db_session=MockSession())
        task.episode_repository = SimpleNamespace(all=AsyncMock(return_value=[episode]))
        task.file_repository = SimpleNamespace(update=AsyncMock())

        result = await task.perform_run(episode_id=1)

        assert result == TaskResultCode.SUCCESS
        task.file_repository.update.assert_not_awaited()

    async def test_perform_run__downloads_uploads_and_updates_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        episode = make_episode()
        episode.image = make_file(type=MediaType.IMAGE, path="")
        local_cover = tmp_path / "cover.jpg"
        task = DownloadEpisodeImageTask(db_session=MockSession())
        task.episode_repository = SimpleNamespace(all=AsyncMock(return_value=[episode]))
        task.file_repository = SimpleNamespace(update=AsyncMock())
        monkeypatch.setattr(task, "_download_and_crop_image", AsyncMock(return_value=local_cover))
        monkeypatch.setattr(task, "_upload_cover", AsyncMock(return_value="images/episodes/1.jpg"))
        monkeypatch.setattr("src.modules.tasks.process.get_file_size", Mock(return_value=321))

        result = await task.perform_run(episode_id=1)

        assert result == TaskResultCode.SUCCESS
        task.file_repository.update.assert_awaited_once_with(
            instance=episode.image,
            path="images/episodes/1.jpg",
            available=True,
            public=False,
            size=321,
        )

    async def test_perform_run__missing_download__marks_file_unavailable(self) -> None:
        episode = make_episode()
        episode.image = make_file(type=MediaType.IMAGE, path="")
        task = DownloadEpisodeImageTask(db_session=MockSession())
        task.episode_repository = SimpleNamespace(all=AsyncMock(return_value=[episode]))
        task.file_repository = SimpleNamespace(update=AsyncMock())
        task._download_and_crop_image = AsyncMock(return_value=None)

        result = await task.perform_run(episode_id=1)

        assert result == TaskResultCode.SUCCESS
        task.file_repository.update.assert_awaited_once_with(
            instance=episode.image,
            path="",
            available=False,
            public=False,
            size=None,
        )

    async def test_download_and_crop_image__not_found__returns_none(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        episode = make_episode()
        episode.image = make_file(type=MediaType.IMAGE, path="")
        monkeypatch.setattr(
            "src.modules.tasks.process.download_content",
            AsyncMock(side_effect=NotFoundError("missing")),
        )

        result = await DownloadEpisodeImageTask._download_and_crop_image(episode)

        assert result is None

    async def test_download_and_crop_image__ok(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        episode = make_episode()
        episode.image = make_file(type=MediaType.IMAGE, path="")
        downloaded = tmp_path / "source.jpg"
        ffmpeg_preparation = Mock()
        monkeypatch.setattr(
            "src.modules.tasks.process.download_content",
            AsyncMock(return_value=downloaded),
        )
        monkeypatch.setattr(
            "src.modules.tasks.process.ffmpeg.ffmpeg_preparation", ffmpeg_preparation
        )

        result = await DownloadEpisodeImageTask._download_and_crop_image(episode)

        assert result == downloaded
        ffmpeg_preparation.assert_called_once_with(
            src_path=downloaded,
            ffmpeg_params=["-vf", "scale=600:-1"],
        )

    async def test_upload_cover__retries_until_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        episode = make_episode(source_id="src")
        task = DownloadEpisodeImageTask(db_session=MockSession())
        task.storage = MockStorageS3()
        task.storage.upload_file.side_effect = [None, "images/episodes/src.png"]
        sleep = AsyncMock()
        monkeypatch.setattr("src.modules.tasks.process.asyncio.sleep", sleep)

        result = await task._upload_cover(episode, tmp_path / "cover.jpg")

        assert result == "images/episodes/src.png"
        assert task.storage.upload_file.await_count == 2
        sleep.assert_awaited_once_with(2)

    async def test_upload_cover__max_attempts__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        episode = make_episode(source_id="src")
        task = DownloadEpisodeImageTask(db_session=MockSession())
        task.MAX_UPLOAD_ATTEMPT = 2
        task.storage = MockStorageS3()
        task.storage.upload_file.return_value = None
        monkeypatch.setattr("src.modules.tasks.process.asyncio.sleep", AsyncMock())

        with pytest.raises(MaxAttemptsReached):
            await task._upload_cover(episode, tmp_path / "cover.jpg")


class TestApplyMetadataEpisodeTask:
    async def test_perform_run__episode_not_found__error(self) -> None:
        task = ApplyMetadataEpisodeTask(db_session=MockSession())
        task.episode_repository = SimpleNamespace(first=AsyncMock(return_value=None))

        result = await task.perform_run(episode_id=1)

        assert result == TaskResultCode.ERROR

    async def test_perform_run__without_chapters__skip(self) -> None:
        task = ApplyMetadataEpisodeTask(db_session=MockSession())
        task.episode_repository = SimpleNamespace(first=AsyncMock(return_value=make_episode()))

        result = await task.perform_run(episode_id=1)

        assert result == TaskResultCode.SKIP

    async def test_perform_run__applies_metadata_and_uploads(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        episode = make_episode()
        episode.audio = make_file(path="audio/source.mp3")
        episode.podcast = make_podcast()
        episode.chapters = [{"title": "Intro", "start": "00:00:00", "end": "00:01:00"}]
        local_path = tmp_path / "episode.mp3"
        task = ApplyMetadataEpisodeTask(db_session=MockSession())
        task.episode_repository = SimpleNamespace(first=AsyncMock(return_value=episode))
        monkeypatch.setattr(task, "_download_episode", AsyncMock(return_value=local_path))
        monkeypatch.setattr(task, "_upload_episode", AsyncMock(return_value="audio/episode.mp3"))
        ffmpeg_set_metadata = Mock()
        monkeypatch.setattr(
            "src.modules.tasks.process.ffmpeg.ffmpeg_set_metadata",
            ffmpeg_set_metadata,
        )

        result = await task.perform_run(episode_id=1)

        assert result == TaskResultCode.SUCCESS
        ffmpeg_set_metadata.assert_called_once()
        task._upload_episode.assert_awaited_once_with(episode=episode, tmp_path=local_path)

    async def test_download_episode__missing_storage_file__fail(self) -> None:
        episode = make_episode(source_id="src")
        episode.audio = make_file(path="audio/src.mp3")
        task = ApplyMetadataEpisodeTask(db_session=MockSession())
        task.storage = MockStorageS3()
        task.storage.download_file.return_value = None

        with pytest.raises(FileNotFoundError):
            await task._download_episode(episode)

    async def test_upload_episode__retries_until_success(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        episode = make_episode()
        episode.audio = make_file(path="audio/source.mp3")
        task = ApplyMetadataEpisodeTask(db_session=MockSession())
        task.storage = MockStorageS3()
        task.storage.upload_file.side_effect = [None, "audio/result.mp3"]
        sleep = AsyncMock()
        monkeypatch.setattr("src.modules.tasks.process.asyncio.sleep", sleep)

        result = await task._upload_episode(episode, tmp_path / "episode.mp3")

        assert result == "audio/result.mp3"
        assert task.storage.upload_file.await_count == 2
        sleep.assert_awaited_once_with(2)
