"""Functional PostgreSQL coverage for episode, media and cookie API workflows."""

from collections.abc import Generator
from typing import NamedTuple

import pytest
from litestar.middleware import AuthenticationResult
from litestar.testing import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.constants import SourceType
from src.main import PodcastApp, make_app
from src.modules.db.models import Episode, File, Podcast, User
from src.modules.db.models.media import MediaType
from src.modules.db.models.podcasts import EpisodeStatus
from src.modules.db.models.podcasts import Cookie
from src.modules.db.services import SASessionUOW
from src.modules.utils.common import SourceMediaInfo
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


class AudioMetadata(NamedTuple):
    duration: int
    title: str | None = None
    artist: str | None = None


@pytest.fixture
def episode_api_client(
    db_user: User,
    functional_session_factory: async_sessionmaker[AsyncSession],
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[
    tuple[TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource], None, None
]:
    """Build an app whose API works against isolated PostgreSQL and stateful fakes."""
    lifecycle = FakeLifecycle()
    queue = FakeTaskQueue()
    storage = FakeStorage()
    source = FakeMediaSource()
    redis = FakeRedis()
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
        cancel_task=queue.cancel_task,
        make_storage=lambda: storage,
        make_redis=lambda: redis,
        mailer=FakeMailer(),
        http_client=FakeHTTPClient(),
        media_source=source,
        media_processor=FakeMediaProcessor(metadata=AudioMetadata(duration=42, title="Upload")),
    )

    async def authenticate_as_db_user(_: object, __: object) -> AuthenticationResult:
        return AuthenticationResult(user=db_user, auth=None)

    monkeypatch.setattr(
        "src.modules.auth.middlewares.APIAuthMiddleware.authenticate_request",
        authenticate_as_db_user,
    )
    app = make_app(settings=_make_settings(api_debug_mode=True), providers=providers)
    with TestClient(app=app, raise_server_exceptions=False) as client:
        yield client, queue, storage, source


async def _create_podcast(session: AsyncSession, user: User, *, automatic: bool = False) -> Podcast:
    podcast = Podcast(
        publish_id=Podcast.generate_publish_id(),
        name="Functional podcast",
        description="Functional description",
        download_automatically=automatic,
        owner_id=user.id,
    )
    session.add(podcast)
    await session.commit()
    await session.refresh(podcast)
    return podcast


async def _create_episode(
    session: AsyncSession,
    user: User,
    podcast: Podcast,
    *,
    status: EpisodeStatus = EpisodeStatus.NEW,
    source_type: SourceType = SourceType.UPLOAD,
    cookie_id: int | None = None,
) -> Episode:
    episode = Episode(
        title="Functional episode",
        source_id=f"source-{podcast.id}-{status}",
        source_type=source_type,
        podcast_id=podcast.id,
        owner_id=user.id,
        watch_url="https://example.test/watch",
        length=30,
        status=status,
        cookie_id=cookie_id,
    )
    session.add(episode)
    await session.commit()
    await session.refresh(episode)
    return episode


class TestEpisodeAPI:
    async def test_url_creation_persists_source_episode_and_enqueues_tasks(
        self,
        episode_api_client: tuple[TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, queue, _, source = episode_api_client
        podcast = await _create_podcast(functional_session, db_user, automatic=True)
        url = "https://www.youtube.com/watch?v=abcDEF12345"
        source.media_info[url] = (
            "OK",
            SourceMediaInfo(
                watch_url=url,
                source_id="abcDEF12345",
                description="Description",
                thumbnail_url="https://images.test/cover.jpg",
                title="Episode title",
                author="Author",
                length=123,
                chapters=[],
            ),
        )

        response = client.post(
            f"/api/podcasts/{podcast.id}/episodes/", json={"sourceURL": f" {url} "}
        )

        assert response.status_code == 201, response.text
        episode_id = response.json()["id"]
        functional_session.expire_all()
        episode = await functional_session.get(Episode, episode_id)
        assert episode is not None
        assert episode.status == EpisodeStatus.DOWNLOADING
        assert episode.owner_id == db_user.id
        assert len(queue.enqueued) == 2
        assert [entry.task.__class__.__name__ for entry in queue.enqueued] == [
            "DownloadEpisodeTask",
            "DownloadEpisodeImageTask",
        ]
        assert source.extractions == [(url, False)]

    async def test_uploaded_creation_is_idempotent_and_persists_audio_and_cover(
        self,
        episode_api_client: tuple[TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, queue, _, _ = episode_api_client
        podcast = await _create_podcast(functional_session, db_user)
        payload = {
            "path": "tmp/audio.mp3",
            "size": 2048,
            "hash": "audiohash",
            "name": "episode.mp3",
            "meta": {"duration": 15, "album": "Album", "track": 3},
            "cover": {"path": "tmp/cover.jpg", "hash": "coverhash", "size": 128},
        }

        created = client.post(f"/api/podcasts/{podcast.id}/episodes/uploaded/", json=payload)
        repeated = client.post(f"/api/podcasts/{podcast.id}/episodes/uploaded/", json=payload)

        assert created.status_code == 201, created.text
        assert repeated.status_code == 201, repeated.text
        assert created.json()["id"] == repeated.json()["id"]
        functional_session.expire_all()
        episodes = list((await functional_session.scalars(select(Episode))).all())
        files = list((await functional_session.scalars(select(File))).all())
        assert len(episodes) == 1
        assert episodes[0].audio_id is not None and episodes[0].image_id is not None
        assert {file.type for file in files} == {MediaType.AUDIO, MediaType.IMAGE}
        assert len(queue.enqueued) == 1
        assert queue.enqueued[0].task.__class__.__name__ == "UploadedEpisodeTask"

    async def test_ownership_update_download_and_delete_transitions_are_persisted(
        self,
        episode_api_client: tuple[TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, queue, _, _ = episode_api_client
        podcast = await _create_podcast(functional_session, db_user)
        episode = await _create_episode(functional_session, db_user, podcast)
        foreign_user = User(email="other@podcast.dev", password="hashed", is_active=True)
        functional_session.add(foreign_user)
        await functional_session.commit()
        foreign_podcast = await _create_podcast(functional_session, foreign_user)
        foreign_episode = await _create_episode(functional_session, foreign_user, foreign_podcast)

        denied = client.patch(f"/api/episodes/{foreign_episode.id}/", json={"title": "Stolen"})
        updated = client.patch(f"/api/episodes/{episode.id}/", json={"title": "Updated"})
        downloaded = client.put(f"/api/episodes/{episode.id}/download/")
        conflict = client.put(f"/api/episodes/{episode.id}/download/")

        assert_error_response(
            denied, status_code=404, code="NOT_FOUND", message=f"Episode with id {foreign_episode.id} not found"
        )
        assert updated.status_code == 200, updated.text
        assert downloaded.status_code == 200, downloaded.text
        assert_error_response(conflict, status_code=409, code="CONFLICT", message="Episode is already in progress")
        episode_id = episode.id
        functional_session.expire_all()
        persisted = await functional_session.get(Episode, episode_id)
        assert persisted is not None and persisted.title == "Updated"
        assert persisted.status == EpisodeStatus.DOWNLOADING
        assert queue.enqueued[-1].task.__class__.__name__ == "UploadedEpisodeTask"
        cannot_delete = client.delete(f"/api/episodes/{episode_id}/")
        assert_error_response(
            cannot_delete,
            status_code=409,
            code="CONFLICT",
            message="Episode in progress cannot be deleted",
        )

    async def test_cancel_downloading_persists_transition_and_records_fake_effects(
        self,
        episode_api_client: tuple[TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, queue, _, _ = episode_api_client
        podcast = await _create_podcast(functional_session, db_user)
        episode = await _create_episode(
            functional_session, db_user, podcast, status=EpisodeStatus.DOWNLOADING
        )
        episode_id = episode.id

        response = client.put(f"/api/episodes/{episode_id}/cancel-downloading/")

        assert response.status_code == 200, response.text
        assert [task.__name__ for task, _, _ in queue.cancelled] == [
            "DownloadEpisodeTask",
            "DownloadEpisodeImageTask",
        ]
        redis = client.app.providers.make_redis()
        assert isinstance(redis, FakeRedis)
        assert len(redis.published) == 1
        functional_session.expire_all()
        persisted = await functional_session.get(Episode, episode_id)
        assert persisted is not None and persisted.status == EpisodeStatus.CANCELING


class TestCookieAPI:
    async def test_create_list_update_and_delete_persist_cookie_for_owner(
        self,
        episode_api_client: tuple[TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource],
        functional_session: AsyncSession,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(Cookie, "get_encrypted_data", lambda value: f"encrypted:{value}")
        client, _, _, _ = episode_api_client

        created = client.post(
            "/api/cookies/",
            data={"source_type": "youtube"},
            files={"file": ("cookies.txt", b"first", "text/plain")},
        )
        assert created.status_code == 201, created.text
        cookie_id = created.json()["id"]
        listed = client.get("/api/cookies/")
        updated = client.put(
            f"/api/cookies/{cookie_id}/",
            data={"source_type": "yandex"},
            files={"file": ("cookies.txt", b"second", "text/plain")},
        )

        assert listed.status_code == 200, listed.text
        assert [cookie["id"] for cookie in listed.json()] == [cookie_id]
        assert updated.status_code == 200, updated.text
        functional_session.expire_all()
        persisted = await functional_session.get(Cookie, cookie_id)
        assert persisted is not None
        assert persisted.source_type is SourceType.YANDEX
        assert persisted.data == "encrypted:second"
        deleted = client.delete(f"/api/cookies/{cookie_id}/")
        assert deleted.status_code == 204, deleted.text
        functional_session.expire_all()
        assert await functional_session.get(Cookie, cookie_id) is None

    async def test_cookie_delete_rejects_linked_episode_and_foreign_cookie(
        self,
        episode_api_client: tuple[TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, _, _, _ = episode_api_client
        cookie = Cookie(source_type=SourceType.YOUTUBE, data="encrypted", owner_id=db_user.id)
        functional_session.add(cookie)
        await functional_session.commit()
        await functional_session.refresh(cookie)
        podcast = await _create_podcast(functional_session, db_user)
        await _create_episode(functional_session, db_user, podcast, cookie_id=cookie.id)
        other = User(email="foreign@podcast.dev", password="hashed", is_active=True)
        functional_session.add(other)
        await functional_session.commit()
        foreign_cookie = Cookie(source_type=SourceType.YANDEX, data="encrypted", owner_id=other.id)
        functional_session.add(foreign_cookie)
        await functional_session.commit()
        await functional_session.refresh(foreign_cookie)

        linked = client.delete(f"/api/cookies/{cookie.id}/")
        foreign = client.get(f"/api/cookies/{foreign_cookie.id}/")

        assert_error_response(
            linked,
            status_code=409,
            code="CONFLICT",
            message="There are episodes related to this cookie.",
        )
        assert_error_response(
            foreign,
            status_code=404,
            code="NOT_FOUND",
            message="Requested object was not found.",
        )


class TestMediaUploadAPI:
    async def test_audio_and_image_uploads_use_fake_storage_and_media_processor(
        self,
        episode_api_client: tuple[TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource],
    ) -> None:
        client, _, storage, _ = episode_api_client

        audio = client.post(
            "/api/media/upload/audio/",
            files={"file": ("episode.mp3", b"audio-content", "audio/mpeg")},
        )
        image = client.post(
            "/api/media/upload/image/",
            files={"file": ("cover.jpg", b"image-content", "image/jpeg")},
        )

        assert audio.status_code == 201, audio.text
        assert image.status_code == 201, image.text
        assert audio.json()["size"] == len(b"audio-content")
        assert image.json()["preview_url"].startswith("fake-storage://")
        assert len(storage.uploads) == 2

    async def test_upload_failure_is_reported_without_external_storage(
        self,
        db_user: User,
        functional_session_factory: async_sessionmaker[AsyncSession],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        lifecycle = FakeLifecycle()
        storage = FakeStorage(return_none=True)
        providers = AppProviders(
            initialize_database=lifecycle.initialize_database,
            verify_database=lifecycle.verify_database,
            close_database=lifecycle.close_database,
            session_factory=lambda: functional_session_factory,
            uow_factory=lambda: SASessionUOW(session_factory=functional_session_factory),
            validate_storage_settings=lambda _: None,
            check_redis=lifecycle.check_redis,
            close_redis=lifecycle.close_redis,
            make_task_queue=FakeTaskQueue,
            cancel_task=lambda task_class, *args, **kwargs: task_class.cancel_task(*args, **kwargs),
            make_storage=lambda: storage,
            make_redis=FakeRedis,
            mailer=FakeMailer(),
            http_client=FakeHTTPClient(),
            media_source=FakeMediaSource(),
            media_processor=FakeMediaProcessor(metadata=AudioMetadata(duration=1)),
        )

        async def authenticate_as_db_user(_: object, __: object) -> AuthenticationResult:
            return AuthenticationResult(user=db_user, auth=None)

        monkeypatch.setattr(
            "src.modules.auth.middlewares.APIAuthMiddleware.authenticate_request",
            authenticate_as_db_user,
        )
        with TestClient(
            app=make_app(settings=_make_settings(api_debug_mode=True), providers=providers),
            raise_server_exceptions=False,
        ) as client:
            response = client.post(
                "/api/media/upload/image/",
                files={"file": ("cover.jpg", b"image-content", "image/jpeg")},
            )

        error = assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )
        assert error["details"] == {"file": "Could not upload image file."}
