"""Functional PostgreSQL coverage for episode, media and cookie API workflows."""

from collections.abc import Generator
from typing import NamedTuple

import pytest
from litestar.middleware import AuthenticationResult
from litestar.testing import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.common.constants import SourceType
from src.main import PodcastApp, make_app
from src.modules.db.models import Episode, File, Podcast, User
from src.modules.db.models.media import MediaType
from src.modules.db.models.podcasts import EpisodeStatus
from src.modules.db.models.podcasts import Cookie
from src.modules.utils.common import SourceMediaInfo
from src.tests.conftest import _make_settings
from src.tests.fakes import (
    FakeLifecycle,
    FakeMediaProcessor,
    FakeMediaSource,
    FakeRedis,
    FakeStorage,
    FakeTaskQueue,
)
from src.tests.helpers import assert_error_response

pytestmark = pytest.mark.usefixtures("use_functional_session_factory")


class AudioMetadata(NamedTuple):
    duration: int
    title: str | None = None
    artist: str | None = None


@pytest.fixture
def episode_api_client(
    db_user: User,
    mocked_app_lifecycle: FakeLifecycle,
    mocked_rq_queue: FakeTaskQueue,
    mocked_storage: FakeStorage,
    mocked_redis: FakeRedis,
    mocked_media_source: FakeMediaSource,
    mocked_media_processor: FakeMediaProcessor,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[
    tuple[TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource], None, None
]:
    """Build an app whose API works against isolated PostgreSQL and stateful fakes."""
    mocked_media_processor.metadata = AudioMetadata(duration=42, title="Upload")

    async def authenticate_as_db_user(_: object, __: object) -> AuthenticationResult:
        return AuthenticationResult(user=db_user, auth=None)

    monkeypatch.setattr(
        "src.modules.auth.middlewares.APIAuthMiddleware.authenticate_request",
        authenticate_as_db_user,
    )
    app = make_app(settings=_make_settings(api_debug_mode=True))
    with TestClient(app=app, raise_server_exceptions=False) as client:
        yield client, mocked_rq_queue, mocked_storage, mocked_media_source


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


class TestPodcastEpisodeCreateAPI:
    async def test_get_list__owned_podcast__excludes_other_podcast_episodes(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, _, _, _ = episode_api_client
        podcast = await _create_podcast(functional_session, db_user)
        episode = await _create_episode(functional_session, db_user, podcast)
        other_user = User(email="other@podcast.dev", password="hashed", is_active=True)
        functional_session.add(other_user)
        await functional_session.commit()
        other_podcast = await _create_podcast(functional_session, other_user)
        await _create_episode(functional_session, other_user, other_podcast)

        response = client.get(f"/api/podcasts/{podcast.id}/episodes/")

        assert response.status_code == 200, response.text
        assert response.json()["total"] == 1
        assert [item["id"] for item in response.json()["items"]] == [episode.id]

    async def test_get_list__foreign_podcast__fail(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
        functional_session: AsyncSession,
    ) -> None:
        client, _, _, _ = episode_api_client
        other_user = User(email="other@podcast.dev", password="hashed", is_active=True)
        functional_session.add(other_user)
        await functional_session.commit()
        podcast = await _create_podcast(functional_session, other_user)

        response = client.get(f"/api/podcasts/{podcast.id}/episodes/")

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message=f"Podcast with id {podcast.id} not found",
        )

    async def test_create_from_url__foreign_cached_source_and_extraction_failure__does_not_reuse(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        """A source cached by another owner must not become this user's fallback media."""
        client, queue, _, source = episode_api_client
        podcast = await _create_podcast(functional_session, db_user)
        foreign_user = User(email="other@podcast.dev", password="hashed", is_active=True)
        functional_session.add(foreign_user)
        await functional_session.commit()
        foreign_podcast = await _create_podcast(functional_session, foreign_user)
        foreign_episode = await _create_episode(
            functional_session,
            foreign_user,
            foreign_podcast,
            source_type=SourceType.YOUTUBE,
        )
        foreign_episode.source_id = "abcDEF12345"
        foreign_audio = File(
            type=MediaType.AUDIO,
            path="audio/foreign.mp3",
            size=128,
            owner_id=foreign_user.id,
            access_token=File.generate_token(),
        )
        foreign_image = File(
            type=MediaType.IMAGE,
            path="image/foreign.jpg",
            size=64,
            owner_id=foreign_user.id,
            access_token=File.generate_token(),
        )
        functional_session.add_all((foreign_audio, foreign_image))
        await functional_session.flush()
        foreign_episode.audio_id = foreign_audio.id
        foreign_episode.image_id = foreign_image.id
        await functional_session.commit()

        response = client.post(
            f"/api/podcasts/{podcast.id}/episodes/",
            json={"sourceURL": "https://www.youtube.com/watch?v=abcDEF12345"},
        )

        assert response.status_code == 500, response.text
        assert source.extractions == [("https://www.youtube.com/watch?v=abcDEF12345", False)]
        assert queue.enqueued == []
        functional_session.expire_all()
        episodes = list((await functional_session.scalars(select(Episode))).all())
        files = list((await functional_session.scalars(select(File))).all())
        assert [episode.id for episode in episodes] == [foreign_episode.id]
        assert [file.owner_id for file in files] == [foreign_user.id, foreign_user.id]

    async def test_create_from_url__automatic_podcast__persists_and_enqueues_tasks(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
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


class TestUploadedEpisodeAPI:
    async def test_create__repeated_payload__is_idempotent_and_persists_audio_and_cover(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
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

    async def test_get_uploaded__owned_file__returns_metadata(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, _, _, _ = episode_api_client
        podcast = await _create_podcast(functional_session, db_user)
        audio = File(
            type=MediaType.AUDIO,
            path="tmp/audio.mp3",
            size=42,
            hash="audiohash",
            owner_id=db_user.id,
            access_token=File.generate_token(),
        )
        functional_session.add(audio)
        await functional_session.commit()

        response = client.get(f"/api/podcasts/{podcast.id}/episodes/uploaded/audiohash/")

        assert response.status_code == 200, response.text
        assert response.json()["id"] == audio.id
        assert response.json()["hash"] == "audiohash"

    async def test_get_uploaded__missing_hash__fail(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, _, _, _ = episode_api_client
        podcast = await _create_podcast(functional_session, db_user)

        response = client.get(f"/api/podcasts/{podcast.id}/episodes/uploaded/missinghash/")

        assert_error_response(
            response,
            status_code=404,
            code="NOT_FOUND",
            message="Uploaded episode file with hash missinghash not found",
        )


class TestEpisodeLifecycleAPI:
    async def test_get_list__owned_episodes__is_paginated(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, _, _, _ = episode_api_client
        podcast = await _create_podcast(functional_session, db_user)
        first = await _create_episode(functional_session, db_user, podcast)
        second = await _create_episode(functional_session, db_user, podcast)
        second.source_id = "second-episode"
        await functional_session.commit()

        response = client.get("/api/episodes/?limit=1&offset=1&order_by=id")

        assert response.status_code == 200, response.text
        assert response.json()["total"] == 2
        assert [item["id"] for item in response.json()["items"]] == [second.id]
        assert first.id != second.id

    async def test_get_details__episode_with_chapters__returns_chapters(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, _, _, _ = episode_api_client
        podcast = await _create_podcast(functional_session, db_user)
        episode = await _create_episode(functional_session, db_user, podcast)
        episode.chapters = [{"title": "Start", "start": 0, "end": 10}]
        await functional_session.commit()

        response = client.get(f"/api/episodes/{episode.id}/")

        assert response.status_code == 200, response.text
        assert response.json()["chapters"] == [{"title": "Start", "start": 0, "end": 10}]

    async def test_update__empty_payload__fail_without_mutation(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, _, _, _ = episode_api_client
        podcast = await _create_podcast(functional_session, db_user)
        episode = await _create_episode(functional_session, db_user, podcast)
        episode_id = episode.id

        response = client.patch(f"/api/episodes/{episode_id}/", json={})

        assert response.status_code == 400, response.text
        functional_session.expire_all()
        persisted = await functional_session.get(Episode, episode_id)
        assert persisted is not None and persisted.title == "Functional episode"

    async def test_delete__finished_episode_with_unused_media__removes_records(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, _, _, _ = episode_api_client
        podcast = await _create_podcast(functional_session, db_user)
        audio = File(
            type=MediaType.AUDIO,
            path="audio/delete.mp3",
            size=128,
            owner_id=db_user.id,
            access_token=File.generate_token(),
        )
        functional_session.add(audio)
        await functional_session.commit()
        audio_id = audio.id
        episode = await _create_episode(functional_session, db_user, podcast)
        episode.audio_id = audio.id
        await functional_session.commit()
        episode_id = episode.id

        response = client.delete(f"/api/episodes/{episode_id}/")

        assert response.status_code == 204, response.text
        functional_session.expire_all()
        assert await functional_session.get(Episode, episode_id) is None
        assert await functional_session.get(File, audio_id) is None

    async def test_update_download_and_delete__ownership_and_transitions__persisted(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
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
            denied,
            status_code=404,
            code="NOT_FOUND",
            message=f"Episode with id {foreign_episode.id} not found",
        )
        assert updated.status_code == 200, updated.text
        assert downloaded.status_code == 200, downloaded.text
        assert_error_response(
            conflict, status_code=409, code="CONFLICT", message="Episode is already in progress"
        )
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


class TestEpisodeCancellationAPI:
    async def test_cancel_downloading__downloading_episode__persists_and_records_effects(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
        db_user: User,
        functional_session: AsyncSession,
        mocked_redis: FakeRedis,
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
        assert len(mocked_redis.published) == 1
        functional_session.expire_all()
        persisted = await functional_session.get(Episode, episode_id)
        assert persisted is not None and persisted.status == EpisodeStatus.CANCELING

    async def test_cancel_downloading__non_downloading_episode__fails_without_effects(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
        db_user: User,
        functional_session: AsyncSession,
        mocked_redis: FakeRedis,
    ) -> None:
        client, queue, _, _ = episode_api_client
        podcast = await _create_podcast(functional_session, db_user)
        episode = await _create_episode(functional_session, db_user, podcast)

        response = client.put(f"/api/episodes/{episode.id}/cancel-downloading/")

        assert response.status_code == 409, response.text
        assert queue.cancelled == []
        assert mocked_redis.published == []


class TestCookieLifecycleAPI:
    async def test_get_list__multiple_cookies_per_source__returns_latest_per_source(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
        db_user: User,
        functional_session: AsyncSession,
    ) -> None:
        client, _, _, _ = episode_api_client
        first = Cookie(source_type=SourceType.YOUTUBE, data="first", owner_id=db_user.id)
        functional_session.add(first)
        await functional_session.commit()
        latest = Cookie(source_type=SourceType.YOUTUBE, data="latest", owner_id=db_user.id)
        yandex = Cookie(source_type=SourceType.YANDEX, data="yandex", owner_id=db_user.id)
        functional_session.add_all((latest, yandex))
        await functional_session.commit()

        response = client.get("/api/cookies/")

        assert response.status_code == 200, response.text
        items = response.json()
        assert {item["id"] for item in items} == {latest.id, yandex.id}
        assert first.id not in {item["id"] for item in items}

    @pytest.mark.parametrize(
        "data, files, message",
        [
            ({}, {}, "Requested data is not valid."),
            (
                {"source_type": "unsupported"},
                {"file": ("cookie.txt", b"x", "text/plain")},
                "Requested data is not valid.",
            ),
            ({"source_type": "youtube"}, {}, "Invalid multipart/form-data"),
        ],
    )
    async def test_create__invalid_multipart__does_not_persist(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
        functional_session: AsyncSession,
        data: dict[str, str],
        files: dict[str, tuple[str, bytes, str]],
        message: str,
    ) -> None:
        client, _, _, _ = episode_api_client

        response = client.post("/api/cookies/", data=data, files=files)

        assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message=message,
        )
        assert not list((await functional_session.scalars(select(Cookie))).all())

    async def test_create_list_update_and_delete__owned_cookie__persists(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
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

    async def test_delete_and_get__linked_or_foreign_cookie__fail(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
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
    async def test_upload_audio_and_image__valid_files__use_external_fakes(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
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

    async def test_upload_image__storage_failure__returns_error_without_persistence(
        self,
        db_user: User,
        mocked_app_lifecycle: FakeLifecycle,
        mocked_rq_queue: FakeTaskQueue,
        mocked_storage: FakeStorage,
        mocked_media_processor: FakeMediaProcessor,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        mocked_storage.return_none = True
        mocked_media_processor.metadata = AudioMetadata(duration=1)

        async def authenticate_as_db_user(_: object, __: object) -> AuthenticationResult:
            return AuthenticationResult(user=db_user, auth=None)

        monkeypatch.setattr(
            "src.modules.auth.middlewares.APIAuthMiddleware.authenticate_request",
            authenticate_as_db_user,
        )
        with TestClient(
            app=make_app(settings=_make_settings(api_debug_mode=True)),
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

    @pytest.mark.parametrize(
        ("path", "files"),
        [
            ("/api/media/upload/audio/", {}),
            ("/api/media/upload/image/", {}),
            (
                "/api/media/upload/audio/",
                {"file": ("cover.jpg", b"image", "image/jpeg")},
            ),
            (
                "/api/media/upload/image/",
                {"file": ("episode.mp3", b"audio", "audio/mpeg")},
            ),
        ],
    )
    async def test_upload__missing_or_wrong_content_type__fails(
        self,
        episode_api_client: tuple[
            TestClient[PodcastApp], FakeTaskQueue, FakeStorage, FakeMediaSource
        ],
        path: str,
        files: dict[str, tuple[str, bytes, str]],
    ) -> None:
        client, _, storage, _ = episode_api_client

        response = client.post(path, files=files)

        assert_error_response(
            response,
            status_code=400,
            code="INVALID_PARAMETERS",
            message="Requested data is not valid.",
        )
        assert storage.uploads == []
