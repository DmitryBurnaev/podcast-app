from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from yt_dlp.utils import YoutubeDLError

from src.constants import EpisodeStatus, SourceType
from src.exceptions import InvalidRequestError
from src.modules.db.models.podcasts import EpisodeChapter
from src.modules.utils.common import (
    SourceInfo,
    _int_from_event,
    chapters_processing,
    download_audio,
    download_process_hook,
    extract_source_info,
    get_source_media_info,
)


class TestSourceInfo:
    def test_extract_source_info__upload_without_url(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("src.modules.utils.common.get_random_hash", Mock(return_value="abc123"))

        result = extract_source_info()

        assert result.id == "U-abc123"
        assert result.type == SourceType.UPLOAD

    @pytest.mark.parametrize(
        ("url", "playlist", "source_id", "source_type"),
        [
            (
                "https://www.youtube.com/watch?v=abcdefghijk",
                False,
                "abcdefghijk",
                SourceType.YOUTUBE,
            ),
            ("https://www.youtube.com/playlist?list=PL123", True, "PL123", SourceType.YOUTUBE),
            ("https://music.yandex.ru/album/1/track/12345", False, "12345", SourceType.YANDEX),
            ("https://music.yandex.ru/album/album-id", True, "album-id", SourceType.YANDEX),
        ],
    )
    def test_extract_source_info__known_sources(
        self,
        url: str,
        playlist: bool,
        source_id: str,
        source_type: SourceType,
    ) -> None:
        result = extract_source_info(url, playlist=playlist)

        assert result == SourceInfo(id=source_id, type=source_type, url=url)

    def test_extract_source_info__unsupported__fail(self) -> None:
        with pytest.raises(InvalidRequestError, match="not supported"):
            extract_source_info("https://example.com/video")

    def test_source_config_proxy_url__youtube_uses_settings(self, app_settings) -> None:
        app_settings.http_proxy_url = "http://proxy.local"

        assert extract_source_info("https://youtu.be/abcdefghijk").type == SourceType.YOUTUBE


class TestDownloadProcessHook:
    @pytest.mark.parametrize(
        ("event", "key", "expected"),
        [
            ({"value": 10}, "value", 10),
            ({"value": 10.9}, "value", 10),
            ({"value": "42"}, "value", 42),
            ({"value": "bad"}, "value", 0),
            ({}, "missing", 0),
        ],
    )
    def test_int_from_event__ok(self, event: dict, key: str, expected: int) -> None:
        assert _int_from_event(event, key) == expected

    def test_download_process_hook__ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        episode_process_hook = Mock()
        monkeypatch.setattr("src.modules.utils.common.episode_process_hook", episode_process_hook)

        download_process_hook(
            {
                "filename": "episode.mp3",
                "total_bytes_estimate": "100",
                "downloaded_bytes": 25.5,
            }
        )

        episode_process_hook.assert_called_once_with(
            status=EpisodeStatus.DL_EPISODE_DOWNLOADING,
            filename="episode.mp3",
            total_bytes=100,
            processed_bytes=25,
        )

    def test_download_process_hook__missing_filename__skip(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        episode_process_hook = Mock()
        monkeypatch.setattr("src.modules.utils.common.episode_process_hook", episode_process_hook)

        download_process_hook({"total_bytes": 100})

        episode_process_hook.assert_not_called()


class TestYtDlpIntegrationWrappers:
    async def test_download_audio__passes_params(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        settings = SimpleNamespace(tmp_audio_path=tmp_path)
        created: list[_FakeYoutubeDL] = []

        def youtube_dl_factory(params: dict) -> _FakeYoutubeDL:
            ydl = _FakeYoutubeDL(params)
            created.append(ydl)
            return ydl

        youtube_dl = Mock(side_effect=youtube_dl_factory)
        monkeypatch.setattr("src.modules.utils.common.get_app_settings", lambda: settings)
        monkeypatch.setattr("src.modules.utils.common.yt_dlp.YoutubeDL", youtube_dl)
        cookie_path = tmp_path / "cookies.txt"

        result = await download_audio(
            "https://source",
            filename="episode.mp3",
            cookie_path=cookie_path,
            proxy_url="http://proxy",
        )

        assert result == tmp_path / "episode.mp3"
        ydl = created[0]
        assert ydl.params["cookiefile"] == str(cookie_path)
        assert ydl.params["proxy"] == "http://proxy"
        ydl.download.assert_called_once_with(["https://source"])

    async def test_get_source_media_info__missing_url__fail(self) -> None:
        message, info = await get_source_media_info(
            SourceInfo(id="source", type=SourceType.YOUTUBE)
        )

        assert message == "Source URL is not specified"
        assert info is None

    async def test_get_source_media_info__ydl_error__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ydl = _FakeYoutubeDL(extract_error=YoutubeDLError("broken"))
        monkeypatch.setattr("src.modules.utils.common.yt_dlp.YoutubeDL", Mock(return_value=ydl))

        message, info = await get_source_media_info(
            SourceInfo(id="source", type=SourceType.YOUTUBE, url="https://source")
        )

        assert "broken" in message
        assert info is None

    async def test_get_source_media_info__ok(self, monkeypatch: pytest.MonkeyPatch) -> None:
        source_details = {
            "title": "Title",
            "description": "",
            "webpage_url": "https://watch",
            "id": "source",
            "thumbnail": "https://thumb",
            "uploader": "",
            "artist": "Artist",
            "duration": 120,
            "chapters": [{"title": "Intro", "start_time": 0, "end_time": 10}],
        }
        ydl = _FakeYoutubeDL(source_details=source_details)
        monkeypatch.setattr("src.modules.utils.common.yt_dlp.YoutubeDL", Mock(return_value=ydl))

        message, info = await get_source_media_info(
            SourceInfo(id="source", type=SourceType.YOUTUBE, url="https://source")
        )

        assert message == "OK"
        assert info is not None
        assert info.title == "Title"
        assert info.description == "Title"
        assert info.author == "Artist"
        assert info.chapters == [EpisodeChapter(title="Intro", start=0, end=10)]


class TestChaptersProcessing:
    def test_chapters_processing__empty__ok(self) -> None:
        assert chapters_processing(None) == []

    def test_chapters_processing__skips_invalid_items(self) -> None:
        result = chapters_processing(
            [
                {"title": "Intro", "start_time": 0, "end_time": 10},
                {"title": "Broken"},
            ]
        )

        assert result == [EpisodeChapter(title="Intro", start=0, end=10)]


class _FakeYoutubeDL:
    def __init__(
        self,
        params: dict | None = None,
        *,
        source_details: dict | None = None,
        extract_error: Exception | None = None,
    ) -> None:
        self.params = params or {}
        self.source_details = source_details or {}
        self.extract_error = extract_error
        self.download = Mock()

    def __enter__(self) -> "_FakeYoutubeDL":
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def extract_info(self, *args: object, **kwargs: object) -> dict:
        if self.extract_error:
            raise self.extract_error
        return self.source_details
