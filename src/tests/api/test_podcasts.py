from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from litestar.testing import TestClient

from src.main import PodcastApp
from src.modules.db.models import User
from src.tests.factories import make_podcast
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
