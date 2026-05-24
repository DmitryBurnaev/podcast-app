import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.exceptions import FFMPegParseError, FFMPegPreparationError, UserCancellationError
from src.modules.db.models.podcasts import EpisodeChapter, EpisodeMetadata
from src.modules.utils.ffmpeg import (
    AudioMetaData,
    CoverMetaData,
    _get_error_details_from_exc,
    _get_file_hash,
    _human_time_to_sec,
    _raw_meta_to_dict,
    audio_cover,
    audio_metadata,
    execute_ffmpeg,
    ffmpeg_preparation,
    ffmpeg_set_metadata,
)


class TestFFmpegPreparation:
    def test_ffmpeg_preparation__ok(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        src_path = tmp_path / "episode.mp3"
        src_path.write_bytes(b"source")
        tmp_audio_path = tmp_path / "audio"
        tmp_audio_path.mkdir()
        settings = SimpleNamespace(tmp_audio_path=tmp_audio_path, ffmpeg_timeout=30)
        process = _FakeProcess()
        hooks = Mock()

        def run(command: list, **kwargs: object) -> SimpleNamespace:
            Path(command[-1]).write_bytes(b"prepared")
            return SimpleNamespace(stdout=b"ffmpeg ok")

        monkeypatch.setattr("src.modules.utils.ffmpeg.get_app_settings", lambda: settings)
        monkeypatch.setattr("src.modules.utils.ffmpeg.Process", Mock(return_value=process))
        monkeypatch.setattr(
            "src.modules.utils.ffmpeg.proc_utils.get_file_size", Mock(return_value=10)
        )
        monkeypatch.setattr("src.modules.utils.ffmpeg.common_utils.episode_process_hook", hooks)
        monkeypatch.setattr("src.modules.utils.ffmpeg.subprocess.run", run)

        ffmpeg_preparation(src_path)

        assert src_path.read_bytes() == b"prepared"
        process.start.assert_called_once_with()
        assert process.terminate.call_count == 1
        assert hooks.call_count == 2

    def test_ffmpeg_preparation__user_cancel__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        src_path = tmp_path / "episode.mp3"
        src_path.write_bytes(b"source")
        tmp_audio_path = tmp_path / "audio"
        tmp_audio_path.mkdir()
        settings = SimpleNamespace(tmp_audio_path=tmp_audio_path, ffmpeg_timeout=30)
        monkeypatch.setattr("src.modules.utils.ffmpeg.get_app_settings", lambda: settings)
        monkeypatch.setattr("src.modules.utils.ffmpeg.Process", Mock(return_value=_FakeProcess()))
        monkeypatch.setattr(
            "src.modules.utils.ffmpeg.proc_utils.get_file_size", Mock(return_value=10)
        )
        monkeypatch.setattr("src.modules.utils.ffmpeg.common_utils.episode_process_hook", Mock())
        monkeypatch.setattr(
            "src.modules.utils.ffmpeg.subprocess.run",
            Mock(side_effect=subprocess.CalledProcessError(returncode=255, cmd="ffmpeg")),
        )

        with pytest.raises(UserCancellationError):
            ffmpeg_preparation(src_path)

    def test_ffmpeg_preparation__missing_output__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        src_path = tmp_path / "episode.mp3"
        src_path.write_bytes(b"source")
        tmp_audio_path = tmp_path / "audio"
        tmp_audio_path.mkdir()
        settings = SimpleNamespace(tmp_audio_path=tmp_audio_path, ffmpeg_timeout=30)
        monkeypatch.setattr("src.modules.utils.ffmpeg.get_app_settings", lambda: settings)
        monkeypatch.setattr("src.modules.utils.ffmpeg.Process", Mock(return_value=_FakeProcess()))
        monkeypatch.setattr(
            "src.modules.utils.ffmpeg.proc_utils.get_file_size", Mock(return_value=10)
        )
        monkeypatch.setattr("src.modules.utils.ffmpeg.common_utils.episode_process_hook", Mock())
        monkeypatch.setattr(
            "src.modules.utils.ffmpeg.subprocess.run",
            Mock(return_value=SimpleNamespace(stdout=b"ok")),
        )

        with pytest.raises(FFMPegPreparationError, match="Failed to rename/remove tmp file"):
            ffmpeg_preparation(src_path)


class TestExecuteFFmpeg:
    def test_execute_ffmpeg__ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        settings = SimpleNamespace(ffmpeg_timeout=30)
        completed = SimpleNamespace(stdout=b"done")
        run = Mock(return_value=completed)
        monkeypatch.setattr("src.modules.utils.ffmpeg.get_app_settings", lambda: settings)
        monkeypatch.setattr("src.modules.utils.ffmpeg.subprocess.run", run)

        result = execute_ffmpeg(["ffmpeg", "-version"])

        assert result == "done"
        run.assert_called_once()

    @pytest.mark.parametrize(
        "exc",
        [
            subprocess.CalledProcessError(returncode=1, cmd="ffmpeg", output=b"bad", stderr=None),
            subprocess.TimeoutExpired(cmd="ffmpeg", timeout=1, output=b"timeout"),
        ],
    )
    def test_execute_ffmpeg__error__fail(
        self, monkeypatch: pytest.MonkeyPatch, exc: Exception
    ) -> None:
        settings = SimpleNamespace(ffmpeg_timeout=30)
        monkeypatch.setattr("src.modules.utils.ffmpeg.get_app_settings", lambda: settings)
        monkeypatch.setattr("src.modules.utils.ffmpeg.subprocess.run", Mock(side_effect=exc))

        with pytest.raises(FFMPegPreparationError):
            execute_ffmpeg(["ffmpeg"])


class TestMetadata:
    def test_ffmpeg_set_metadata__ok(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        src_path = tmp_path / "episode.mp3"
        src_path.write_bytes(b"audio")
        tmp_audio_path = tmp_path / "audio"
        tmp_meta_path = tmp_path / "meta"
        tmp_audio_path.mkdir()
        tmp_meta_path.mkdir()
        settings = SimpleNamespace(
            tmp_audio_path=tmp_audio_path,
            tmp_meta_path=tmp_meta_path,
            episode_chapters_title_length=8,
        )
        metadata = EpisodeMetadata(
            podcast_name="Podcast",
            episode_id=10,
            episode_title="Episode",
            episode_author="Author",
            episode_chapters=[EpisodeChapter(title="Very long chapter title", start=1, end=2)],
        )
        execute = Mock(return_value="title=Episode")
        move_file = Mock()
        delete_file = Mock()
        monkeypatch.setattr("src.modules.utils.ffmpeg.get_app_settings", lambda: settings)
        monkeypatch.setattr("src.modules.utils.ffmpeg.execute_ffmpeg", execute)
        monkeypatch.setattr("src.modules.utils.ffmpeg.proc_utils.move_file", move_file)
        monkeypatch.setattr("src.modules.utils.ffmpeg.proc_utils.delete_file", delete_file)

        ffmpeg_set_metadata(src_path, metadata)

        metadata_path = tmp_meta_path / "episode_10.txt"
        assert not metadata_path.exists() or "Very lon..." in metadata_path.read_text()
        execute.assert_called_once()
        delete_file.assert_called_once_with(metadata_path)
        move_file.assert_called_once_with(tmp_audio_path / "tmp_episode.mp3", src_path)

    def test_ffmpeg_set_metadata__title_missing__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        src_path = tmp_path / "episode.mp3"
        src_path.write_bytes(b"audio")
        tmp_audio_path = tmp_path / "audio"
        tmp_meta_path = tmp_path / "meta"
        tmp_audio_path.mkdir()
        tmp_meta_path.mkdir()
        settings = SimpleNamespace(
            tmp_audio_path=tmp_audio_path,
            tmp_meta_path=tmp_meta_path,
            episode_chapters_title_length=8,
        )
        metadata = EpisodeMetadata(
            podcast_name="Podcast",
            episode_id=10,
            episode_title="Episode",
            episode_author="Author",
            episode_chapters=[],
        )
        monkeypatch.setattr("src.modules.utils.ffmpeg.get_app_settings", lambda: settings)
        monkeypatch.setattr(
            "src.modules.utils.ffmpeg.execute_ffmpeg", Mock(return_value="no title")
        )

        with pytest.raises(RuntimeError, match="Episode title"):
            ffmpeg_set_metadata(src_path, metadata)

    def test_audio_metadata__ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "src.modules.utils.ffmpeg.execute_ffmpeg",
            Mock(
                return_value=(
                    "Metadata:\n"
                    "    title           : Episode\n"
                    "    artist          : Author\n"
                    "    album           : Podcast\n"
                    "Duration: 00:01:16"
                )
            ),
        )

        result = audio_metadata("episode.mp3")

        assert result == AudioMetaData(
            title=None,
            duration=76,
            album=None,
            author=None,
            track=None,
        )

    def test_audio_metadata__bad_output__fail(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(
            "src.modules.utils.ffmpeg.execute_ffmpeg", Mock(return_value="bad output")
        )

        with pytest.raises(FFMPegParseError):
            audio_metadata("episode.mp3")

    def test_audio_cover__ok(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        settings = SimpleNamespace(tmp_image_path=tmp_path)

        def execute(command: list[str]) -> str:
            Path(command[-1]).write_bytes(b"cover")
            return "ok"

        monkeypatch.setattr("src.modules.utils.ffmpeg.get_app_settings", lambda: settings)
        monkeypatch.setattr("src.modules.utils.ffmpeg.execute_ffmpeg", execute)
        monkeypatch.setattr(
            "src.modules.utils.ffmpeg.uuid.uuid4", Mock(return_value=SimpleNamespace(hex="abc"))
        )
        monkeypatch.setattr(
            "src.modules.utils.ffmpeg.proc_utils.get_file_size", Mock(return_value=5)
        )

        result = audio_cover(tmp_path / "episode.mp3")

        assert result is not None
        assert result == CoverMetaData(path=result.path, hash=result.hash, size=5)
        assert result.path.name == f"cover_{result.hash}.jpg"
        assert result.size == 5

    def test_audio_cover__ffmpeg_error__none(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        settings = SimpleNamespace(tmp_image_path=tmp_path)
        monkeypatch.setattr("src.modules.utils.ffmpeg.get_app_settings", lambda: settings)
        monkeypatch.setattr(
            "src.modules.utils.ffmpeg.execute_ffmpeg",
            Mock(side_effect=FFMPegPreparationError("broken")),
        )

        assert audio_cover(tmp_path / "episode.mp3") is None

    def test_raw_meta_to_dict__ok(self) -> None:
        assert _raw_meta_to_dict(
            "    album           : TestAlbum\nbad\n    artist          : Artist"
        ) == {
            "album": "TestAlbum",
            "artist": "Artist",
        }
        assert _raw_meta_to_dict(None) == {}

    @pytest.mark.parametrize(
        ("value", "expected"),
        [("00:01:16.75", 77), ("01:01:20.232443", 3680)],
    )
    def test_human_time_to_sec__ok(self, value: str, expected: int) -> None:
        assert _human_time_to_sec(value) == expected

    def test_get_file_hash__ok(self, tmp_path: Path) -> None:
        file_path = tmp_path / "file.bin"
        file_path.write_bytes(b"content")

        assert len(_get_file_hash(file_path)) == 32

    def test_get_error_details_from_exc__includes_stdout(self) -> None:
        exc = subprocess.CalledProcessError(returncode=1, cmd="ffmpeg")
        exc.stdout = b"stderr text"

        assert "stderr text" in _get_error_details_from_exc(exc)


class _FakeProcess:
    def __init__(self) -> None:
        self.start = Mock()
        self.terminate = Mock()
