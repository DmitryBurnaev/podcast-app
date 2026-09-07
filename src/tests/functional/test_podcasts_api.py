"""Functional PostgreSQL coverage for the Podcasts API vertical slice."""

from collections.abc import Generator

import pytest
from litestar.middleware import AuthenticationResult
from litestar.testing import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.main import PodcastApp, make_app
from src.modules.db.models import File, Podcast, User
from src.modules.db.services import SASessionUOW
from src.providers import AppProviders
from src.tests.conftest import _make_settings
from src.tests.fakes import (
    FakeHTTPClient,
    FakeLifecycle,
    FakeMailer,
    FakeMediaProcessor,
    FakeMediaSource,
    FakeRedis,
    FakeStorage,
    FakeTaskQueue,
)
from src.tests.helpers import assert_error_response


@pytest.fixture
def podcast_api_client(
    db_user: User,
    functional_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[tuple[TestClient[PodcastApp], FakeStorage, FakeTaskQueue], None, None]:
    """Build a fresh app whose Podcast API uses real PostgreSQL UoWs."""
    lifecycle = FakeLifecycle()
    storage = FakeStorage()
    queue = FakeTaskQueue()
    providers = AppProviders(
        initialize_database=lifecycle.initialize_database,
        verify_database=lifecycle.verify_database,
        close_database=lifecycle.close_database,
        session_factory=lambda: functional_session_factory,
        uow_factory=lambda: SASessionUOW(session_factory=functional_session_factory),
        validate_storage_settings=lambda _: None,
        check_redis=lifecycle.check_redis,
        close_redis=lifecycle.close_redis,
        make_task_queue=lambda _: queue,
        make_storage=lambda: storage,
        make_redis=FakeRedis,
        mailer=FakeMailer(),
        http_client=FakeHTTPClient(),
        media_source=FakeMediaSource(),
        media_processor=FakeMediaProcessor(),
    )

    async def authenticate_as_db_user(_: object, __: object) -> AuthenticationResult:
        return AuthenticationResult(user=db_user, auth=None)

    monkeypatch.setattr(
        "src.modules.auth.middlewares.APIAuthMiddleware.authenticate_request",
        authenticate_as_db_user,
    )
    app = make_app(settings=_make_settings(api_debug_mode=True), providers=providers)
    with TestClient(app=app, raise_server_exceptions=False) as client:
        yield client, storage, queue


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


class TestPodcastAPI:
    url = "/api/podcasts/"

    async def test_create_persists_podcast_for_current_user(
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

    async def test_list_is_paginated_and_excludes_another_users_podcasts(
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

    async def test_details_and_update_reject_another_users_podcast(
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
            message=f"Podcast with id {foreign_podcast.id} not found",
        )
        assert_error_response(
            updated,
            status_code=404,
            code="NOT_FOUND",
            message=f"Podcast with id {foreign_podcast.id} not found",
        )

    async def test_update_and_delete_change_persisted_state(
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

    async def test_upload_image_persists_file_and_records_fake_storage_effect(
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

    async def test_generate_rss_enqueues_task_only_for_owned_podcast(
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
            message="Podcast with id 999 not found",
        )
