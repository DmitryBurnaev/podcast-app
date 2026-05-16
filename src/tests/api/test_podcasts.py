from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from litestar.exceptions import HTTPException
from litestar.testing import TestClient

from src.main import PodcastApp
from src.modules.api.podcasts import PodcastAPIController
from src.modules.db.models import User
from src.tests.factories import make_episode, make_file, make_podcast
from src.tests.helpers import assert_error_response
from src.tests.mocks import MockUOW


@pytest.fixture
def podcast_repository(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    repository = SimpleNamespace(
        all_with_aggregations=AsyncMock(),
        create=AsyncMock(),
        delete=AsyncMock(),
        first=AsyncMock(),
        get_first_with_aggregations=AsyncMock(),
        update=AsyncMock(),
    )
    monkeypatch.setattr("src.modules.api.podcasts.SASessionUOW", lambda: MockUOW())
    monkeypatch.setattr("src.modules.api.podcasts.PodcastRepository", lambda session: repository)
    return repository


@pytest.fixture
def episode_repository(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    repository = SimpleNamespace(
        all=AsyncMock(return_value=[]),
        safe_delete=AsyncMock(return_value=None),
    )
    monkeypatch.setattr("src.modules.api.podcasts.EpisodeRepository", lambda session: repository)
    return repository


@pytest.fixture
def file_repository(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    repository = SimpleNamespace(create=AsyncMock())
    monkeypatch.setattr("src.modules.api.podcasts.FileRepository", lambda session: repository)
    return repository


class TestPodcastListCreateAPI:
    url = "/api/podcasts/"

    def test_get_list__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
    ) -> None:
        podcast = make_podcast(id=11, owner_id=current_user.id, episodes_count=2)
        podcast_repository.all_with_aggregations.return_value = ([podcast], 1)

        response = client.get(self.url)

        assert response.status_code == 200, response.text
        response_data = response.json()
        assert response_data["offset"] == 0
        assert response_data["total"] == 1
        assert response_data["items"][0]["id"] == podcast.id
        assert response_data["items"][0]["stat"]["episodes_count"] == 2
        podcast_repository.all_with_aggregations.assert_awaited_once_with(
            limit=10,
            offset=0,
            order_by="-created_at",
            owner_id=current_user.id,
        )

    def test_create__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
    ) -> None:
        podcast = make_podcast(id=12, owner_id=current_user.id, name="Created podcast")
        podcast_repository.create.return_value = podcast
        podcast_repository.get_first_with_aggregations.return_value = podcast

        response = client.post(
            self.url,
            json={"name": podcast.name, "description": podcast.description},
        )

        assert response.status_code == 201, response.text
        response_data = response.json()
        assert response_data["id"] == podcast.id
        assert response_data["name"] == podcast.name
        podcast_repository.create.assert_awaited_once()
        create_kwargs = podcast_repository.create.await_args.kwargs
        assert create_kwargs["name"] == podcast.name
        assert create_kwargs["owner_id"] == current_user.id

    def test_create__repository_returns_no_created_podcast__fail(
        self,
        client: TestClient[PodcastApp],
        podcast_repository: SimpleNamespace,
    ) -> None:
        podcast = make_podcast(id=13)
        podcast_repository.create.return_value = podcast
        podcast_repository.get_first_with_aggregations.return_value = None

        response = client.post(
            self.url,
            json={"name": podcast.name, "description": podcast.description},
        )

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="Podcast was not created",
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"name": ""},
            {"name": "x" * 257},
            {"name": "Podcast", "download_automatically": "not-bool"},
        ],
    )
    def test_create__invalid_request__fail(
        self,
        client: TestClient[PodcastApp],
        payload: dict[str, object],
    ) -> None:
        response = client.post(self.url, json=payload)

        assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )


class TestPodcastDetailsAPI:
    url = "/api/podcasts/{podcast_id}/"

    def test_get_details__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
    ) -> None:
        podcast = make_podcast(id=21, owner_id=current_user.id, episodes_count=3)
        podcast_repository.get_first_with_aggregations.return_value = podcast

        response = client.get(self.url.format(podcast_id=podcast.id))

        assert response.status_code == 200, response.text
        response_data = response.json()
        assert response_data["id"] == podcast.id
        assert response_data["stat"]["episodes_count"] == 3
        podcast_repository.get_first_with_aggregations.assert_awaited_once_with(
            ids=[podcast.id],
            owner_id=current_user.id,
        )

    def test_get_details__not_found__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
    ) -> None:
        podcast_repository.get_first_with_aggregations.return_value = None

        response = client.get(self.url.format(podcast_id=404))

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="Podcast with id 404 not found",
        )
        podcast_repository.get_first_with_aggregations.assert_awaited_once_with(
            ids=[404],
            owner_id=current_user.id,
        )

    @pytest.mark.parametrize(
        "payload",
        [
            {"name": ""},
            {"name": "x" * 257},
            {"download_automatically": "not-bool"},
        ],
    )
    def test_update__invalid_request__fail(
        self,
        client: TestClient[PodcastApp],
        payload: dict[str, object],
    ) -> None:
        response = client.patch(self.url.format(podcast_id=1), json=payload)

        assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )

    def test_update__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
    ) -> None:
        podcast = make_podcast(id=31, owner_id=current_user.id, name="Old name")
        updated_podcast = make_podcast(
            id=podcast.id,
            owner_id=current_user.id,
            name="Updated name",
            download_automatically=True,
        )
        podcast_repository.first.return_value = podcast
        podcast_repository.get_first_with_aggregations.return_value = updated_podcast

        response = client.patch(
            self.url.format(podcast_id=podcast.id),
            json={"name": updated_podcast.name, "download_automatically": True},
        )

        assert response.status_code == 200, response.text
        response_data = response.json()
        assert response_data["name"] == updated_podcast.name
        assert response_data["download_automatically"] is True
        podcast_repository.first.assert_awaited_once_with(
            id=podcast.id,
            owner_id=current_user.id,
        )
        podcast_repository.update.assert_awaited_once_with(
            podcast,
            name=updated_podcast.name,
            download_automatically=True,
        )

    def test_update__without_fields__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
    ) -> None:
        podcast = make_podcast(id=32, owner_id=current_user.id)
        podcast_repository.first.return_value = podcast
        podcast_repository.get_first_with_aggregations.return_value = podcast

        response = client.patch(self.url.format(podcast_id=podcast.id), json={})

        assert response.status_code == 200, response.text
        podcast_repository.update.assert_not_awaited()
        podcast_repository.get_first_with_aggregations.assert_awaited_once_with(
            ids=[podcast.id],
            owner_id=current_user.id,
        )

    def test_update__not_found__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
    ) -> None:
        podcast_repository.first.return_value = None

        response = client.patch(
            self.url.format(podcast_id=404),
            json={"name": "Updated name"},
        )

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="Podcast with id 404 not found",
        )
        podcast_repository.first.assert_awaited_once_with(id=404, owner_id=current_user.id)

    def test_update__aggregation_not_found__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
    ) -> None:
        podcast = make_podcast(id=33, owner_id=current_user.id)
        podcast_repository.first.return_value = podcast
        podcast_repository.get_first_with_aggregations.return_value = None

        response = client.patch(
            self.url.format(podcast_id=podcast.id),
            json={"name": "Updated name"},
        )

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message=f"Podcast with id {podcast.id} not found",
        )

    def test_delete__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        episode_repository: SimpleNamespace,
    ) -> None:
        podcast = make_podcast(id=41, owner_id=current_user.id)
        podcast_repository.first.return_value = podcast

        response = client.delete(self.url.format(podcast_id=podcast.id))

        assert response.status_code == 204, response.text
        podcast_repository.first.assert_awaited_once_with(
            id=podcast.id,
            owner_id=current_user.id,
        )
        episode_repository.all.assert_awaited_once_with(
            podcast_id=podcast.id,
            owner_id=current_user.id,
        )
        podcast_repository.delete.assert_awaited_once_with(podcast)

    def test_delete__deletes_episodes__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        episode_repository: SimpleNamespace,
    ) -> None:
        podcast = make_podcast(id=42, owner_id=current_user.id)
        episode = make_episode(id=43, owner_id=current_user.id, podcast_id=podcast.id)
        podcast_repository.first.return_value = podcast
        episode_repository.all.return_value = [episode]

        response = client.delete(self.url.format(podcast_id=podcast.id))

        assert response.status_code == 204, response.text
        episode_repository.safe_delete.assert_awaited_once_with(episode)
        podcast_repository.delete.assert_awaited_once_with(podcast)

    def test_delete__not_found__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        episode_repository: SimpleNamespace,
    ) -> None:
        podcast_repository.first.return_value = None

        response = client.delete(self.url.format(podcast_id=404))

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="Podcast with id 404 not found",
        )
        episode_repository.all.assert_not_awaited()

    async def test_upload_image__missing_file__fail(
        self,
        current_user: User,
    ) -> None:
        with pytest.raises(HTTPException) as exc_info:
            await PodcastAPIController.upload_image.fn(None, 1, {"unused": "value"}, current_user)

        assert exc_info.value.status_code == 400
        assert exc_info.value.detail == "Image file is required"

    def test_upload_image__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        file_repository: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        podcast = make_podcast(id=44, owner_id=current_user.id)
        image_file = make_file(id=45, owner_id=current_user.id)
        updated_podcast = make_podcast(id=podcast.id, owner_id=current_user.id)
        updated_podcast.image_id = image_file.id
        podcast_repository.first.return_value = podcast
        podcast_repository.get_first_with_aggregations.return_value = updated_podcast
        file_repository.create.return_value = image_file
        monkeypatch.setattr(
            "src.modules.api.podcasts.save_uploaded_file",
            AsyncMock(return_value="/tmp/podcast-cover.jpg"),
        )
        monkeypatch.setattr(
            "src.modules.api.podcasts.StorageS3",
            lambda: SimpleNamespace(upload_file=AsyncMock(return_value="podcasts/cover.jpg")),
        )
        monkeypatch.setattr("src.modules.api.podcasts.get_file_size", Mock(return_value=1024))

        response = client.post(
            f"/api/podcasts/{podcast.id}/upload-image/",
            files={"file": ("cover.jpg", b"image-content", "image/jpeg")},
        )

        assert response.status_code in {200, 201}, response.text
        file_repository.create.assert_awaited_once()
        podcast_repository.update.assert_awaited_once_with(podcast, image_id=image_file.id)

    def test_upload_image__storage_failure__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        podcast = make_podcast(id=46, owner_id=current_user.id)
        podcast_repository.first.return_value = podcast
        monkeypatch.setattr(
            "src.modules.api.podcasts.save_uploaded_file",
            AsyncMock(return_value="/tmp/podcast-cover.jpg"),
        )
        monkeypatch.setattr(
            "src.modules.api.podcasts.StorageS3",
            lambda: SimpleNamespace(upload_file=AsyncMock(return_value="")),
        )

        response = client.post(
            f"/api/podcasts/{podcast.id}/upload-image/",
            files={"file": ("cover.jpg", b"image-content", "image/jpeg")},
        )

        assert_error_response(
            response,
            status_code=500,
            code="INTERNAL_ERROR",
            message="Unable to upload podcast image",
        )

    def test_upload_image__preparation_error__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        podcast = make_podcast(id=47, owner_id=current_user.id)
        podcast_repository.first.return_value = podcast
        monkeypatch.setattr(
            "src.modules.api.podcasts._upload_podcast_image",
            AsyncMock(side_effect=ValueError("Bad image file")),
        )

        response = client.post(
            f"/api/podcasts/{podcast.id}/upload-image/",
            files={"file": ("cover.jpg", b"image-content", "image/jpeg")},
        )

        assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Bad image file",
        )

    def test_upload_image__aggregation_not_found__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
        file_repository: SimpleNamespace,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        podcast = make_podcast(id=48, owner_id=current_user.id)
        image_file = make_file(id=49, owner_id=current_user.id)
        podcast_repository.first.return_value = podcast
        podcast_repository.get_first_with_aggregations.return_value = None
        file_repository.create.return_value = image_file
        monkeypatch.setattr(
            "src.modules.api.podcasts.save_uploaded_file",
            AsyncMock(return_value="/tmp/podcast-cover.jpg"),
        )
        monkeypatch.setattr(
            "src.modules.api.podcasts.StorageS3",
            lambda: SimpleNamespace(upload_file=AsyncMock(return_value="podcasts/cover.jpg")),
        )
        monkeypatch.setattr("src.modules.api.podcasts.get_file_size", Mock(return_value=1024))

        response = client.post(
            f"/api/podcasts/{podcast.id}/upload-image/",
            files={"file": ("cover.jpg", b"image-content", "image/jpeg")},
        )

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message=f"Podcast with id {podcast.id} not found",
        )

    def test_generate_rss__ok(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
    ) -> None:
        podcast = make_podcast(id=51, owner_id=current_user.id)
        podcast_repository.first.return_value = podcast
        enqueue = Mock(return_value=None)
        client.app.rq_queue = SimpleNamespace(enqueue=enqueue)

        response = client.put(f"/api/podcasts/{podcast.id}/generate-rss/")

        assert response.status_code == 200, response.text
        assert response.json() == {"job_id": "generatersstask_51__"}
        podcast_repository.first.assert_awaited_once_with(
            id=podcast.id,
            owner_id=current_user.id,
        )
        assert enqueue.called

    def test_generate_rss__not_found__fail(
        self,
        client: TestClient[PodcastApp],
        current_user: User,
        podcast_repository: SimpleNamespace,
    ) -> None:
        podcast_repository.first.return_value = None

        response = client.put("/api/podcasts/404/generate-rss/")

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="Podcast with id 404 not found",
        )
        podcast_repository.first.assert_awaited_once_with(id=404, owner_id=current_user.id)
