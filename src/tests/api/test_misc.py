from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from litestar.testing import TestClient

from src.constants import EpisodeStatus, SourceType
from src.main import PodcastApp
from src.modules.api.misc import _prepare_description
from src.modules.db.models import User
from src.tests.factories import make_episode, make_podcast
from src.tests.helpers import assert_error_response
from src.tests.mocks import MockUOW


@pytest.fixture
def misc_repositories(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    episode_repository = SimpleNamespace(first=AsyncMock())
    podcast_repository = SimpleNamespace(all=AsyncMock())
    monkeypatch.setattr("src.modules.api.misc.SASessionUOW", lambda: MockUOW())
    monkeypatch.setattr(
        "src.modules.api.misc.EpisodeRepository",
        lambda session: episode_repository,
    )
    monkeypatch.setattr(
        "src.modules.api.misc.PodcastRepository",
        lambda session: podcast_repository,
    )
    return SimpleNamespace(episodes=episode_repository, podcasts=podcast_repository)


class TestPlaylistAPI:
    url = "/api/playlist/"

    def test_get_playlist__ok(
        self,
        client: TestClient[PodcastApp],
        misc_repositories: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_info = SimpleNamespace(id="playlist-id", type=SourceType.YOUTUBE)
        monkeypatch.setattr(
            "src.modules.api.misc.common_utils.extract_source_info",
            lambda url, playlist: source_info,
        )

        @asynccontextmanager
        async def cookie_ctx(*args: object, **kwargs: object) -> AsyncIterator[SimpleNamespace]:
            yield SimpleNamespace(file_path="/tmp/cookies.txt")

        class FakeYoutubeDL:
            def __init__(self, params: dict) -> None:
                self.params = params

            def __enter__(self) -> "FakeYoutubeDL":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def extract_info(self, url: str, download: bool) -> dict:
                return {
                    "_type": "playlist",
                    "id": "playlist-id",
                    "title": "Playlist title",
                    "entries": [
                        {
                            "id": "video-id",
                            "title": "Episode title",
                            "description": "Episode description",
                            "thumbnails": [{"url": "https://img/cover.jpg"}],
                            "webpage_url": "https://example.com/video",
                        }
                    ],
                }

        monkeypatch.setattr("src.modules.api.misc.cookie_file_ctx", cookie_ctx)
        monkeypatch.setattr("src.modules.api.misc.yt_dlp.YoutubeDL", FakeYoutubeDL)

        response = client.get(self.url, params={"url": "https://example.com/playlist"})

        assert response.status_code == 200, response.text
        response_data = response.json()
        assert response_data["id"] == "playlist-id"
        assert response_data["entries"][0]["id"] == "video-id"
        assert response_data["entries"][0]["description"] == "Episode description"

    def test_get_playlist__source_parse_error__fail(
        self,
        client: TestClient[PodcastApp],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def raise_parse_error(url: str, playlist: bool) -> None:
            raise ValueError("bad playlist")

        monkeypatch.setattr(
            "src.modules.api.misc.common_utils.extract_source_info",
            raise_parse_error,
        )

        response = client.get(self.url, params={"url": "not-a-playlist"})

        error = assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )
        assert error["details"] == "bad playlist"

    def test_get_playlist__not_playlist__fail(
        self,
        client: TestClient[PodcastApp],
        misc_repositories: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_info = SimpleNamespace(id="video-id", type=SourceType.YOUTUBE)
        monkeypatch.setattr(
            "src.modules.api.misc.common_utils.extract_source_info",
            lambda url, playlist: source_info,
        )

        @asynccontextmanager
        async def cookie_ctx(*args: object, **kwargs: object) -> AsyncIterator[None]:
            yield None

        class FakeYoutubeDL:
            def __init__(self, params: dict) -> None:
                self.params = params

            def __enter__(self) -> "FakeYoutubeDL":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def extract_info(self, url: str, download: bool) -> dict:
                return {"_type": "video", "id": "video-id"}

        monkeypatch.setattr("src.modules.api.misc.cookie_file_ctx", cookie_ctx)
        monkeypatch.setattr("src.modules.api.misc.yt_dlp.YoutubeDL", FakeYoutubeDL)

        response = client.get(self.url, params={"url": "https://example.com/video"})

        error = assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )
        assert error["details"] == "It seems like incorrect playlist URL."

    def test_get_playlist__download_error__fail(
        self,
        client: TestClient[PodcastApp],
        misc_repositories: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source_info = SimpleNamespace(id="playlist-id", type=SourceType.YOUTUBE)
        monkeypatch.setattr(
            "src.modules.api.misc.common_utils.extract_source_info",
            lambda url, playlist: source_info,
        )

        @asynccontextmanager
        async def cookie_ctx(*args: object, **kwargs: object) -> AsyncIterator[None]:
            yield None

        class FakeYoutubeDL:
            def __init__(self, params: dict) -> None:
                self.params = params

            def __enter__(self) -> "FakeYoutubeDL":
                return self

            def __exit__(self, *args: object) -> None:
                return None

            def extract_info(self, url: str, download: bool) -> dict:
                from src.modules.api.misc import yt_dlp

                raise yt_dlp.utils.DownloadError("download failed")

        monkeypatch.setattr("src.modules.api.misc.cookie_file_ctx", cookie_ctx)
        monkeypatch.setattr("src.modules.api.misc.yt_dlp.YoutubeDL", FakeYoutubeDL)

        response = client.get(self.url, params={"url": "https://example.com/playlist"})

        error = assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )
        assert "Couldn't extract playlist:" in error["details"]


class TestProgressAPI:
    @pytest.mark.parametrize("query", ["", "?episode_id=10"])
    def test_get_progress__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        misc_repositories: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
        query: str,
    ) -> None:
        podcast = make_podcast(id=20, owner_id=current_user.id)
        episode = make_episode(
            id=10,
            owner_id=current_user.id,
            podcast_id=podcast.id,
            status=EpisodeStatus.DOWNLOADING,
        )
        misc_repositories.podcasts.all.return_value = [podcast]
        misc_repositories.episodes.first.return_value = episode
        get_in_progress = AsyncMock(return_value=[episode])
        check_state = AsyncMock(
            return_value=[
                {
                    "episode_id": episode.id,
                    "podcast_id": podcast.id,
                    "status": "DL_EPISODE_DOWNLOADING",
                    "completed": 50.0,
                    "current_file_size": 100,
                    "total_file_size": 200,
                }
            ]
        )
        monkeypatch.setattr("src.modules.api.misc.Episode.get_in_progress", get_in_progress)
        monkeypatch.setattr("src.modules.api.misc.check_state", check_state)

        response = client.get(f"/api/progress/{query}")

        assert response.status_code == 200, response.text
        response_data = response.json()
        assert response_data["progressItems"][0]["episode"]["id"] == episode.id
        assert response_data["progressItems"][0]["podcast"]["id"] == podcast.id
        misc_repositories.podcasts.all.assert_awaited_once_with(owner_id=current_user.id)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"description": "Real description"}, "Real description"),
        (
            {"playlist": "Album", "playlist_index": 2, "n_entries": 10},
            'Playlist "Album" | Track #2 of 10',
        ),
        ({}, ""),
    ],
)
def test_prepare_description(data: dict, expected: str) -> None:
    assert _prepare_description(data) == expected
