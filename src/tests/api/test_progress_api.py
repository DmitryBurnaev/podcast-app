from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from litestar.testing import TestClient

from src.modules.common.constants import EpisodeStatus, SourceType
from src.main import PodcastApp
from src.modules.api import API_CONTROLLERS
from src.modules.api.base import BaseApiController
from src.modules.api import misc as misc_api
from src.modules.api.misc import _prepare_description
from src.modules.db.models import User
from src.tests.factories import make_episode, make_podcast
from src.tests.fakes import FakeYoutubeDL
from src.tests.helpers import assert_error_response
from src.tests.mocks import MockUOW


def test_api_controllers__is_explicit_and_contains_only_api_controllers() -> None:
    assert API_CONTROLLERS
    assert all(issubclass(controller, BaseApiController) for controller in API_CONTROLLERS)
    assert len(API_CONTROLLERS) == len(set(API_CONTROLLERS))


@pytest.fixture
def misc_repositories(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleNamespace]:
    episode_repository = SimpleNamespace(first=AsyncMock())
    podcast_repository = SimpleNamespace(all=AsyncMock())
    with monkeypatch.context() as patch:
        patch.setattr(misc_api, "SASessionUOW", lambda: MockUOW())
        patch.setattr(
            misc_api,
            "EpisodeRepository",
            lambda session, **_: episode_repository,
        )
        patch.setattr(
            misc_api,
            "PodcastRepository",
            lambda session, **_: podcast_repository,
        )
        yield SimpleNamespace(episodes=episode_repository, podcasts=podcast_repository)


@pytest.fixture
def mock_source_info(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., None]]:
    with monkeypatch.context() as patch:

        def configure(*, result: object | None = None, error: Exception | None = None) -> None:
            def extract_source_info(url: str, playlist: bool) -> object:
                if error is not None:
                    raise error
                return result

            patch.setattr(misc_api.common_utils, "extract_source_info", extract_source_info)

        yield configure


@pytest.fixture
def mock_cookie_file_context(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[str | None], None]]:
    with monkeypatch.context() as patch:

        def configure(file_path: str | None) -> None:
            @asynccontextmanager
            async def cookie_file_context(
                *_: object, **__: object
            ) -> AsyncIterator[SimpleNamespace | None]:
                yield SimpleNamespace(file_path=file_path) if file_path is not None else None

            patch.setattr(misc_api, "cookie_file_ctx", cookie_file_context)

        yield configure


@pytest.fixture
def mock_youtube_dl(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[..., None]]:
    with monkeypatch.context() as patch:

        def configure(
            *, result: dict[str, Any] | None = None, error: Exception | None = None
        ) -> None:
            patch.setattr(
                misc_api.yt_dlp, "YoutubeDL", FakeYoutubeDL.new(result=result, error=error)
            )

        yield configure


@pytest.fixture
def mock_progress_state(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[Callable[[object], AsyncMock]]:
    with monkeypatch.context() as patch:

        def configure(result: object) -> AsyncMock:
            check_state = AsyncMock(return_value=result)
            patch.setattr(misc_api, "check_state", check_state)
            return check_state

        yield configure


class TestPlaylistRetrieveAPI:
    url = "/api/playlist/"

    def test_get_playlist__valid_playlist__ok(
        self,
        client: TestClient[PodcastApp],
        misc_repositories: SimpleNamespace,
        mock_source_info: Callable[..., None],
        mock_cookie_file_context: Callable[[str | None], None],
        mock_youtube_dl: Callable[..., None],
    ) -> None:
        source_info = SimpleNamespace(id="playlist-id", type=SourceType.YOUTUBE)
        mock_source_info(result=source_info)
        mock_cookie_file_context("/tmp/cookies.txt")
        mock_youtube_dl(
            result={
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
        )

        response = client.get(self.url, params={"url": "https://example.com/playlist"})

        assert response.status_code == 200, response.text
        response_data = response.json()
        assert response_data["id"] == "playlist-id"
        assert response_data["entries"][0]["id"] == "video-id"
        assert response_data["entries"][0]["description"] == "Episode description"

    def test_get_playlist__source_parse_error__fail(
        self,
        client: TestClient[PodcastApp],
        mock_source_info: Callable[..., None],
    ) -> None:
        mock_source_info(error=ValueError("bad playlist"))

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
        mock_source_info: Callable[..., None],
        mock_cookie_file_context: Callable[[str | None], None],
        mock_youtube_dl: Callable[..., None],
    ) -> None:
        source_info = SimpleNamespace(id="video-id", type=SourceType.YOUTUBE)
        mock_source_info(result=source_info)
        mock_cookie_file_context(None)
        mock_youtube_dl(result={"_type": "video", "id": "video-id"})

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
        mock_source_info: Callable[..., None],
        mock_cookie_file_context: Callable[[str | None], None],
        mock_youtube_dl: Callable[..., None],
    ) -> None:
        source_info = SimpleNamespace(id="playlist-id", type=SourceType.YOUTUBE)
        mock_source_info(result=source_info)
        mock_cookie_file_context(None)
        mock_youtube_dl(error=misc_api.yt_dlp.utils.DownloadError("download failed"))

        response = client.get(self.url, params={"url": "https://example.com/playlist"})

        error = assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )
        assert "Couldn't extract playlist:" in error["details"]


class TestProgressRetrieveAPI:
    def test_get_progress__no_active_episodes__returns_empty_items(
        self,
        client: TestClient[PodcastApp],
        misc_repositories: SimpleNamespace,
        mock_progress_state: Callable[[object], AsyncMock],
    ) -> None:
        misc_repositories.podcasts.all.return_value = []
        misc_repositories.episodes.all_in_progress = AsyncMock(return_value=[])
        check_state = mock_progress_state([])

        response = client.get("/api/progress/")

        assert response.status_code == 200, response.text
        assert response.json() == {"progressItems": []}
        check_state.assert_awaited_once_with([])

    @pytest.mark.parametrize("query", ["", "?episode_id=10"])
    def test_get_progress__configured_state__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        misc_repositories: SimpleNamespace,
        mock_progress_state: Callable[[object], AsyncMock],
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
        misc_repositories.episodes.all_in_progress = AsyncMock(return_value=[episode])
        mock_progress_state(
            [
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

        response = client.get(f"/api/progress/{query}")

        assert response.status_code == 200, response.text
        response_data = response.json()
        assert response_data["progressItems"][0]["episode"]["id"] == episode.id
        assert response_data["progressItems"][0]["podcast"]["id"] == podcast.id
        misc_repositories.podcasts.all.assert_awaited_once_with()


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
