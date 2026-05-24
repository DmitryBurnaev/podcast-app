from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from litestar.testing import TestClient

from src.constants import EpisodeStatus, SourceType
from src.main import PodcastApp
from src.modules.db.models import User
from src.tests.factories import make_episode, make_file, make_podcast
from src.tests.helpers import assert_error_response
from src.tests.mocks import MockUOW


@pytest.fixture
def episode_repository(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    repository = SimpleNamespace(
        all_paginated=AsyncMock(),
        create=AsyncMock(),
        first=AsyncMock(),
        safe_delete=AsyncMock(),
        update=AsyncMock(),
    )
    monkeypatch.setattr("src.modules.api.episodes.SASessionUOW", lambda: MockUOW())
    monkeypatch.setattr("src.modules.api.episodes.EpisodeRepository", lambda session: repository)
    return repository


@pytest.fixture
def podcast_repository(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    repository = SimpleNamespace(first=AsyncMock())
    monkeypatch.setattr("src.modules.api.episodes.PodcastRepository", lambda session: repository)
    return repository


@pytest.fixture
def file_repository(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    repository = SimpleNamespace(
        create=AsyncMock(),
        first=AsyncMock(),
    )
    monkeypatch.setattr("src.modules.api.episodes.FileRepository", lambda session: repository)
    return repository


class TestEpisodeListAPI:
    def test_get_list__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        episode_repository: SimpleNamespace,
    ) -> None:
        episode = make_episode(id=1, owner_id=current_user.id)
        episode_repository.all_paginated.return_value = ([episode], 1)

        response = client.get("/api/episodes/")

        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["id"] == episode.id
        episode_repository.all_paginated.assert_awaited_once_with(
            owner_id=current_user.id,
            limit=10,
            offset=0,
            order_by="-created_at",
        )

    def test_get_podcast_episodes__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        episode_repository: SimpleNamespace,
    ) -> None:
        podcast = make_podcast(id=10, owner_id=current_user.id)
        episode = make_episode(id=2, owner_id=current_user.id, podcast_id=podcast.id)
        podcast_repository.first.return_value = podcast
        episode_repository.all_paginated.return_value = ([episode], 1)

        response = client.get(f"/api/podcasts/{podcast.id}/episodes/")

        assert response.status_code == 200, response.text
        assert response.json()["items"][0]["podcast_id"] == podcast.id
        podcast_repository.first.assert_awaited_once_with(id=podcast.id, owner_id=current_user.id)
        episode_repository.all_paginated.assert_awaited_once_with(
            owner_id=current_user.id,
            podcast_id=podcast.id,
            limit=10,
            offset=0,
            order_by="-created_at",
        )

    def test_get_podcast_episodes__podcast_not_found__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        episode_repository: SimpleNamespace,
    ) -> None:
        podcast_repository.first.return_value = None

        response = client.get("/api/podcasts/404/episodes/")

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="Podcast with id 404 not found",
        )
        podcast_repository.first.assert_awaited_once_with(id=404, owner_id=current_user.id)
        episode_repository.all_paginated.assert_not_awaited()


class TestPodcastEpisodeCreateAPI:
    url = "/api/podcasts/{podcast_id}/episodes/"

    def test_create__ok_and_enqueue_downloads(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        episode_repository: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        podcast = make_podcast(id=14, owner_id=current_user.id, download_automatically=True)
        episode = make_episode(id=15, owner_id=current_user.id, podcast_id=podcast.id)
        creator = SimpleNamespace(create=AsyncMock(return_value=episode))
        podcast_repository.first.return_value = podcast
        client.app.rq_queue = SimpleNamespace(enqueue=Mock(return_value=None))
        monkeypatch.setattr("src.modules.api.episodes.EpisodeCreator", lambda **kwargs: creator)

        response = client.post(
            self.url.format(podcast_id=podcast.id),
            json={"sourceURL": " https://example.com/watch/episode "},
        )

        assert response.status_code == 201, response.text
        creator.create.assert_awaited_once_with(
            podcast_id=podcast.id,
            source_url="https://example.com/watch/episode",
        )
        episode_repository.update.assert_awaited_once_with(
            episode,
            status=EpisodeStatus.DOWNLOADING,
        )
        assert client.app.rq_queue.enqueue.call_count == 2

    def test_create__podcast_not_found__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        episode_repository: SimpleNamespace,
    ) -> None:
        podcast_repository.first.return_value = None

        response = client.post(
            self.url.format(podcast_id=404),
            json={"sourceURL": "https://example.com/watch/episode"},
        )

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="Podcast with id 404 not found",
        )
        podcast_repository.first.assert_awaited_once_with(id=404, owner_id=current_user.id)

    def test_create__creator_validation_error__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        episode_repository: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        podcast = make_podcast(id=15, owner_id=current_user.id)
        creator = SimpleNamespace(create=AsyncMock(side_effect=ValueError("Unsupported source")))
        podcast_repository.first.return_value = podcast
        monkeypatch.setattr("src.modules.api.episodes.EpisodeCreator", lambda **kwargs: creator)

        response = client.post(
            self.url.format(podcast_id=podcast.id),
            json={"sourceURL": "https://example.com/watch/episode"},
        )

        assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Unsupported source",
        )
        episode_repository.update.assert_not_awaited()

    def test_create_uploaded__existing_audio__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        episode_repository: SimpleNamespace,
        file_repository: SimpleNamespace,
    ) -> None:
        from src.constants import FileType

        podcast = make_podcast(id=16, owner_id=current_user.id)
        audio_file = make_file(id=17, owner_id=current_user.id, hash="uploadedhash")
        episode = make_episode(
            id=18,
            owner_id=current_user.id,
            podcast_id=podcast.id,
            source_id="upl_uploadedhas",
            source_type=SourceType.UPLOAD,
            status=EpisodeStatus.DOWNLOADING,
        )
        podcast_repository.first.return_value = podcast
        file_repository.first.return_value = audio_file
        episode_repository.first.return_value = None
        episode_repository.create.return_value = episode
        client.app.rq_queue = SimpleNamespace(enqueue=Mock(return_value=None))

        response = client.post(
            f"/api/podcasts/{podcast.id}/episodes/uploaded/",
            json={"hash": audio_file.hash, "name": "uploaded.mp3", "meta": {"duration": 15}},
        )

        assert response.status_code == 201, response.text
        file_repository.first.assert_awaited_once_with(
            hash=audio_file.hash,
            owner_id=current_user.id,
            type=FileType.AUDIO,
        )
        episode_repository.create.assert_awaited_once()
        assert client.app.rq_queue.enqueue.called

    def test_create_uploaded__missing_audio_without_upload_data__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        episode_repository: SimpleNamespace,
        file_repository: SimpleNamespace,
    ) -> None:
        from src.constants import FileType

        podcast = make_podcast(id=26, owner_id=current_user.id)
        podcast_repository.first.return_value = podcast
        file_repository.first.return_value = None

        response = client.post(
            f"/api/podcasts/{podcast.id}/episodes/uploaded/",
            json={"hash": "missinghash"},
        )

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="Uploaded episode file with hash missinghash not found",
        )
        file_repository.first.assert_awaited_once_with(
            hash="missinghash",
            owner_id=current_user.id,
            type=FileType.AUDIO,
        )
        episode_repository.create.assert_not_awaited()

    def test_create_uploaded__creates_audio_and_cover__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        episode_repository: SimpleNamespace,
        file_repository: SimpleNamespace,
    ) -> None:
        podcast = make_podcast(id=27, owner_id=current_user.id)
        audio_file = make_file(id=28, owner_id=current_user.id, hash="uploadedhash")
        image_file = make_file(id=29, owner_id=current_user.id, path="images/cover.jpg")
        episode = make_episode(
            id=30,
            owner_id=current_user.id,
            podcast_id=podcast.id,
            source_id="upl_uploadedhas",
            source_type=SourceType.UPLOAD,
        )
        podcast_repository.first.return_value = podcast
        file_repository.first.side_effect = [None, None]
        file_repository.create.side_effect = [audio_file, image_file]
        episode_repository.first.return_value = None
        episode_repository.create.return_value = episode
        client.app.rq_queue = SimpleNamespace(enqueue=Mock(return_value=None))

        response = client.post(
            f"/api/podcasts/{podcast.id}/episodes/uploaded/",
            json={
                "path": "tmp/uploaded.mp3",
                "size": 2048,
                "hash": audio_file.hash,
                "name": "episode.mp3",
                "meta": {"duration": 15, "album": "Album", "track": 3},
                "cover": {
                    "path": image_file.path,
                    "hash": image_file.hash,
                    "size": image_file.size,
                },
            },
        )

        assert response.status_code == 201, response.text
        assert file_repository.create.await_count == 2
        create_kwargs = episode_repository.create.await_args.kwargs
        assert create_kwargs["audio_id"] == audio_file.id
        assert create_kwargs["image_id"] == image_file.id
        assert create_kwargs["length"] == 15

    def test_create_uploaded__repository_returns_no_episode__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        episode_repository: SimpleNamespace,
        file_repository: SimpleNamespace,
    ) -> None:
        podcast = make_podcast(id=31, owner_id=current_user.id)
        audio_file = make_file(id=32, owner_id=current_user.id, hash="uploadedhash")
        podcast_repository.first.return_value = podcast
        file_repository.first.return_value = audio_file
        episode_repository.first.return_value = None
        episode_repository.create.return_value = None

        response = client.post(
            f"/api/podcasts/{podcast.id}/episodes/uploaded/",
            json={"hash": audio_file.hash, "name": "episode.mp3"},
        )

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message=f"Episode with hash {audio_file.hash} not found",
        )

    def test_get_uploaded__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        episode_repository: SimpleNamespace,
        file_repository: SimpleNamespace,
    ) -> None:
        from src.constants import FileType

        podcast = make_podcast(id=19, owner_id=current_user.id)
        uploaded_file = make_file(id=20, owner_id=current_user.id, hash="uploadedhash")
        podcast_repository.first.return_value = podcast
        file_repository.first.return_value = uploaded_file

        response = client.get(f"/api/podcasts/{podcast.id}/episodes/uploaded/{uploaded_file.hash}/")

        assert response.status_code == 200, response.text
        assert response.json()["hash"] == uploaded_file.hash
        file_repository.first.assert_awaited_once_with(
            hash=uploaded_file.hash,
            owner_id=current_user.id,
            type=FileType.AUDIO,
        )

    def test_get_uploaded__not_found__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        episode_repository: SimpleNamespace,
        file_repository: SimpleNamespace,
    ) -> None:
        podcast = make_podcast(id=33, owner_id=current_user.id)
        podcast_repository.first.return_value = podcast
        file_repository.first.return_value = None

        response = client.get(f"/api/podcasts/{podcast.id}/episodes/uploaded/missinghash/")

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="Uploaded episode file with hash missinghash not found",
        )


class TestEpisodeDetailsAPI:
    url = "/api/episodes/{episode_id}/"

    def test_get_details__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        episode_repository: SimpleNamespace,
    ) -> None:
        episode = make_episode(id=11, owner_id=current_user.id)
        episode_repository.first.return_value = episode

        response = client.get(self.url.format(episode_id=episode.id))

        assert response.status_code == 200, response.text
        assert response.json()["id"] == episode.id
        episode_repository.first.assert_awaited_once_with(id=episode.id, owner_id=current_user.id)

    def test_get_details__not_found__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        episode_repository: SimpleNamespace,
    ) -> None:
        episode_repository.first.return_value = None

        response = client.get(self.url.format(episode_id=404))

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="Episode with id 404 not found",
        )
        episode_repository.first.assert_awaited_once_with(id=404, owner_id=current_user.id)

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"title": ""},
            {"author": "x" * 257},
        ],
    )
    def test_update__invalid_request__fail(
        self,
        client: TestClient[PodcastApp],
        payload: dict[str, object],
    ) -> None:
        response = client.patch(self.url.format(episode_id=1), json=payload)

        assert response.status_code == 400, response.text

    def test_update__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        episode_repository: SimpleNamespace,
    ) -> None:
        episode = make_episode(id=12, owner_id=current_user.id)
        episode_repository.first.return_value = episode

        response = client.patch(
            self.url.format(episode_id=episode.id),
            json={"title": "Updated title"},
        )

        assert response.status_code == 200, response.text
        episode_repository.update.assert_awaited_once_with(episode, title="Updated title")

    def test_delete__in_progress__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        episode_repository: SimpleNamespace,
    ) -> None:
        episode = make_episode(id=13, owner_id=current_user.id)
        episode_repository.first.return_value = episode
        episode_repository.safe_delete.side_effect = ValueError(
            "Episode in progress cannot be deleted"
        )

        response = client.delete(self.url.format(episode_id=episode.id))

        assert_error_response(
            response,
            status_code=409,
            code="CONFLICT",
            message="Episode in progress cannot be deleted",
        )


class TestEpisodeActionsAPI:
    url = "/api/episodes/{episode_id}/{action}/"

    def test_download__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        episode_repository: SimpleNamespace,
    ) -> None:
        episode = make_episode(id=21, owner_id=current_user.id)
        episode_repository.first.return_value = episode
        client.app.rq_queue = SimpleNamespace(enqueue=Mock(return_value=None))

        response = client.put(self.url.format(episode_id=episode.id, action="download"))

        assert response.status_code == 200, response.text
        episode_repository.update.assert_awaited_once_with(
            episode,
            status=EpisodeStatus.DOWNLOADING,
        )
        assert client.app.rq_queue.enqueue.called

    def test_download__already_in_progress__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        episode_repository: SimpleNamespace,
    ) -> None:
        episode = make_episode(
            id=22,
            owner_id=current_user.id,
            status=EpisodeStatus.DOWNLOADING,
        )
        episode_repository.first.return_value = episode

        response = client.put(self.url.format(episode_id=episode.id, action="download"))

        assert_error_response(
            response,
            status_code=409,
            code="CONFLICT",
            message="Episode is already in progress",
        )

    def test_download__uploaded_episode__uses_uploaded_task(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        episode_repository: SimpleNamespace,
    ) -> None:
        episode = make_episode(id=23, owner_id=current_user.id, source_type=SourceType.UPLOAD)
        episode_repository.first.return_value = episode
        client.app.rq_queue = SimpleNamespace(enqueue=Mock(return_value=None))

        response = client.put(self.url.format(episode_id=episode.id, action="download"))

        assert response.status_code == 200, response.text
        enqueued_task = client.app.rq_queue.enqueue.call_args.args[0]
        assert enqueued_task.__class__.__name__ == "UploadedEpisodeTask"

    def test_cancel_downloading__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        episode_repository: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        episode = make_episode(
            id=24,
            owner_id=current_user.id,
            status=EpisodeStatus.DOWNLOADING,
        )
        episode_repository.first.return_value = episode
        download_cancel = Mock(return_value=None)
        image_cancel = Mock(return_value=None)
        publish_stop = AsyncMock(return_value=None)
        monkeypatch.setattr(
            "src.modules.api.episodes.tasks.DownloadEpisodeTask.cancel_task", download_cancel
        )
        monkeypatch.setattr(
            "src.modules.api.episodes.tasks.DownloadEpisodeImageTask.cancel_task",
            image_cancel,
        )
        monkeypatch.setattr("src.modules.api.episodes.publish_redis_stop_downloading", publish_stop)

        response = client.put(self.url.format(episode_id=episode.id, action="cancel-downloading"))

        assert response.status_code == 200, response.text
        episode_repository.update.assert_awaited_once_with(
            episode,
            status=EpisodeStatus.CANCELING,
        )
        download_cancel.assert_called_once_with(episode_id=episode.id)
        image_cancel.assert_called_once_with(episode_id=episode.id)
        publish_stop.assert_awaited_once_with(episode.id)

    def test_cancel_downloading__not_downloading__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        episode_repository: SimpleNamespace,
    ) -> None:
        episode = make_episode(id=25, owner_id=current_user.id, status=EpisodeStatus.NEW)
        episode_repository.first.return_value = episode

        response = client.put(self.url.format(episode_id=episode.id, action="cancel-downloading"))

        assert_error_response(
            response,
            status_code=409,
            code="CONFLICT",
            message="Episode is not downloading",
        )
