"""Functional PostgreSQL coverage for the Podcasts API vertical slice."""

from collections.abc import Generator

import pytest
from litestar.middleware import AuthenticationResult
from litestar.testing import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.main import PodcastApp, make_app
from src.modules.db.models import Episode, File, Podcast, User
from src.modules.db.models.podcasts import EpisodeStatus
from src.tests.conftest import _make_settings
from src.tests.fakes import (
    FakeLifecycle,
    FakeStorage,
    FakeTaskQueue,
)
from src.tests.helpers import assert_error_response

pytestmark = pytest.mark.usefixtures("use_functional_session_factory")


@pytest.fixture
def podcast_api_client(
    db_user: User,
    mocked_app_lifecycle: FakeLifecycle,
    mocked_rq_queue: FakeTaskQueue,
    mocked_storage: FakeStorage,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue], None, None]:
    """Build a fresh app whose Podcast API uses real PostgreSQL UoWs."""

    async def authenticate_as_db_user(_: object, __: object) -> AuthenticationResult:
        return AuthenticationResult(user=db_user, auth=None)

    monkeypatch.setattr(
        "src.modules.auth.middlewares.APIAuthMiddleware.authenticate_request",
        authenticate_as_db_user,
    )
    app = make_app(settings=_make_settings(api_debug_mode=True))
    with TestClient(app=app, raise_server_exceptions=False) as client:
        yield client, mocked_storage, mocked_rq_queue


async def _create_podcast(session: AsyncSession, user: User, name: str) -> Podcast:
    podcast = Podcast(
        publish_id=Podcast.generate_publish_id(),
        name=name,
        description=f"Description for {name}",
        download_automatically=False,
        owner_id=user.id,
    )
    session.add(podcast)
    await session.commit()
    await session.refresh(podcast)
    return podcast


async def _create_episode(session: AsyncSession, user: User, podcast: Podcast) -> Episode:
    episode = Episode(
        title="Aggregated episode",
        source_id=f"episode-{podcast.id}",
        source_type="UPLOAD",
        podcast_id=podcast.id,
        owner_id=user.id,
        watch_url="",
        length=42,
        status=EpisodeStatus.PUBLISHED,
    )
    session.add(episode)
    await session.commit()
    await session.refresh(episode)
    return episode


class TestPodcastListCreateAPI:
    url = "/api/podcasts/"

    async def test_create__persists_for_current_user(
        self,
        podcast_api_client: tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, _, _ = podcast_api_client

        response = client.post(
            self.url,
            json={"name": "Created podcast", "description": "Persisted description"},
        )

        assert response.status_code == 201, response.text
        podcast_id = response.json()["id"]
        persisted = await functional_session.get(Podcast, podcast_id)
        assert persisted is not None
        assert persisted.owner_id == db_user.id
        assert persisted.name == "Created podcast"

    async def test_get_list__pagination_excludes_another_users_podcasts(
        self,
        podcast_api_client: tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        await _create_podcast(functional_session, db_user, "First")
        await _create_podcast(functional_session, db_user, "Second")
        other_user = User(
            email="other@podcast.dev",
            password="hashed-password",
            is_active=True,
            is_superuser=False,
        )
        functional_session.add(other_user)
        await functional_session.commit()
        await _create_podcast(functional_session, other_user, "Hidden")
        client, _, _ = podcast_api_client

        response = client.get(f"{self.url}?limit=1&offset=1&order_by=name")

        assert response.status_code == 200, response.text
        payload = response.json()
        assert payload["total"] == 2
        assert payload["offset"] == 1
        assert [item["name"] for item in payload["items"]] == ["Second"]

    @pytest.mark.parametrize("payload", [{}, {"name": ""}, {"name": "x" * 257}])
    async def test_create__invalid_request__does_not_persist(
        self,
        podcast_api_client: tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue],
        functional_session: AsyncSession,
        payload: dict[str, str],
    ) -> None:
        client, _, _ = podcast_api_client

        response = client.post(self.url, json=payload)

        assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )
        assert not list((await functional_session.scalars(select(Podcast))).all())

    async def test_get_list__episode_aggregation__returns_statistics(
        self,
        podcast_api_client: tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        podcast = await _create_podcast(functional_session, db_user, "With episode")
        await _create_episode(functional_session, db_user, podcast)
        client, _, _ = podcast_api_client

        response = client.get(self.url)

        assert response.status_code == 200, response.text
        item = response.json()["items"][0]
        assert item["id"] == podcast.id
        assert item["stat"]["episodes_count"] == 1
        assert item["stat"]["total_duration"] == 42


class TestPodcastDetailsAPI:
    url = "/api/podcasts/"

    async def test_get_details_and_update__foreign_podcast__fail(
        self,
        podcast_api_client: tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue],
        functional_session: AsyncSession,
    ) -> None:
        other_user = User(
            email="owner@podcast.dev",
            password="hashed-password",
            is_active=True,
            is_superuser=False,
        )
        functional_session.add(other_user)
        await functional_session.commit()
        foreign_podcast = await _create_podcast(functional_session, other_user, "Private")
        client, _, _ = podcast_api_client

        details = client.get(f"{self.url}{foreign_podcast.id}/")
        updated = client.patch(f"{self.url}{foreign_podcast.id}/", json={"name": "Stolen"})

        assert_error_response(
            details,
            status_code=404,
            code="NOT_FOUND",
            message=f"No instance with id {foreign_podcast.id} found",
        )
        assert_error_response(
            updated,
            status_code=404,
            code="NOT_FOUND",
            message=f"No instance with id {foreign_podcast.id} found",
        )

    async def test_update_and_delete__owned_podcast__persist_state(
        self,
        podcast_api_client: tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        podcast = await _create_podcast(functional_session, db_user, "Before update")
        podcast_id = podcast.id
        client, _, _ = podcast_api_client

        updated = client.patch(
            f"{self.url}{podcast_id}/",
            json={"name": "After update", "download_automatically": True},
        )

        assert updated.status_code == 200, updated.text
        assert updated.json()["name"] == "After update"
        functional_session.expire_all()
        persisted = await functional_session.get(Podcast, podcast_id)
        assert persisted is not None
        assert persisted.name == "After update"
        details = client.get(f"{self.url}{podcast_id}/")
        assert details.status_code == 200, details.text
        assert details.json()["name"] == "After update"
        deleted = client.delete(f"{self.url}{podcast_id}/")
        assert deleted.status_code == 204, deleted.text
        functional_session.expire_all()
        assert await functional_session.get(Podcast, podcast_id) is None

    async def test_delete__podcast_with_episode__removes_owned_episode(
        self,
        podcast_api_client: tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        podcast = await _create_podcast(functional_session, db_user, "Delete with episode")
        episode = await _create_episode(functional_session, db_user, podcast)
        episode_id = episode.id
        client, _, _ = podcast_api_client

        response = client.delete(f"{self.url}{podcast.id}/")

        assert response.status_code == 204, response.text
        functional_session.expire_all()
        assert await functional_session.get(Episode, episode_id) is None

    @pytest.mark.parametrize("method", ["get", "patch", "delete"])
    async def test_details_operations__missing_podcast__fail(
        self,
        podcast_api_client: tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue],
        method: str,
    ) -> None:
        client, _, _ = podcast_api_client

        url = f"{self.url}999/"
        response = (
            getattr(client, method)(url, json={})
            if method == "patch"
            else getattr(client, method)(url)
        )

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="No instance with id 999 found",
        )


class TestPodcastImageUploadAPI:
    url = "/api/podcasts/"

    async def test_upload__image__persists_file_and_records_storage_effect(
        self,
        podcast_api_client: tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        podcast = await _create_podcast(functional_session, db_user, "With image")
        podcast_id = podcast.id
        client, storage, _ = podcast_api_client

        response = client.post(
            f"{self.url}{podcast_id}/upload-image/",
            files={"file": ("cover.jpg", b"fake-image", "image/jpeg")},
        )

        assert response.status_code == 201, response.text
        functional_session.expire_all()
        persisted = await functional_session.get(Podcast, podcast_id)
        assert persisted is not None and persisted.image_id is not None
        image = await functional_session.get(File, persisted.image_id)
        assert image is not None
        assert storage.uploads
        assert image.path == storage.uploads[0].destination
        assert image.path.startswith("images/podcasts/")

    async def test_upload__storage_failure__does_not_attach_image(
        self,
        podcast_api_client: tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        podcast = await _create_podcast(functional_session, db_user, "Without image")
        podcast_id = podcast.id
        client, storage, _ = podcast_api_client
        storage.return_none = True

        response = client.post(
            f"{self.url}{podcast_id}/upload-image/",
            files={"file": ("cover.jpg", b"fake-image", "image/jpeg")},
        )

        assert response.status_code == 500, response.text
        functional_session.expire_all()
        persisted = await functional_session.get(Podcast, podcast_id)
        assert persisted is not None and persisted.image_id is None

    async def test_upload__missing_file__does_not_attach_image(
        self,
        podcast_api_client: tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        podcast = await _create_podcast(functional_session, db_user, "Without image")
        podcast_id = podcast.id
        client, _, _ = podcast_api_client

        response = client.post(f"{self.url}{podcast_id}/upload-image/", files={})

        assert response.status_code == 400, response.text
        functional_session.expire_all()
        persisted = await functional_session.get(Podcast, podcast_id)
        assert persisted is not None and persisted.image_id is None


class TestPodcastRSSGenerationAPI:
    url = "/api/podcasts/"

    async def test_generate_rss__owned_podcast__enqueues_task(
        self,
        podcast_api_client: tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        podcast = await _create_podcast(functional_session, db_user, "RSS")
        client, _, queue = podcast_api_client

        response = client.put(f"{self.url}{podcast.id}/generate-rss/")
        missing = client.put(f"{self.url}999/generate-rss/")

        assert response.status_code == 200, response.text
        assert response.json()["job_id"] == f"generatersstask_{podcast.id}__"
        assert len(queue.enqueued) == 1
        assert_error_response(
            missing,
            status_code=404,
            code="NOT_FOUND",
            message="No instance with id 999 found",
        )

    async def test_generate_rss__repeated_request__enqueues_one_task_per_request(
        self,
        podcast_api_client: tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        podcast = await _create_podcast(functional_session, db_user, "RSS")
        client, _, queue = podcast_api_client

        first = client.put(f"{self.url}{podcast.id}/generate-rss/")
        second = client.put(f"{self.url}{podcast.id}/generate-rss/")

        assert first.status_code == second.status_code == 200
        assert first.json()["job_id"] == second.json()["job_id"]
        assert len(queue.enqueued) == 2

    async def test_generate_rss__foreign_podcast__does_not_enqueue_task(
        self,
        podcast_api_client: tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue],
        functional_session: AsyncSession,
    ) -> None:
        other_user = User(
            email="other@podcast.dev",
            password="hashed-password",
            is_active=True,
            is_superuser=False,
        )
        functional_session.add(other_user)
        await functional_session.commit()
        podcast = await _create_podcast(functional_session, other_user, "Private RSS")
        client, _, queue = podcast_api_client

        response = client.put(f"{self.url}{podcast.id}/generate-rss/")

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message=f"No instance with id {podcast.id} found",
        )
        assert queue.enqueued == []
