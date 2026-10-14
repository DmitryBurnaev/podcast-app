"""Browser JSON contracts through real auth, UoWs and isolated PostgreSQL."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from unittest.mock import create_autospec
from uuid import uuid4

import pytest
from litestar.testing import TestClient
from rq.exceptions import NoSuchJobError
from rq.job import Job
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.main import PodcastApp, make_app
from src.modules.auth.tokens import AuthTokenType, TokenPayload, encode_jwt
from src.modules.common.constants import SourceType
from src.modules.common.types import OwnerScope
from src.modules.db.models import Episode, File, Podcast, User, UserSession
from src.modules.db.models.media import MediaType
from src.modules.db.models.podcasts import EpisodeStatus
from src.modules.db.repositories import EpisodeRepository
from src.modules.services import podcasts_operations
from src.modules.tasks import GenerateRSSTask
from src.modules.tasks.base import TaskResultCode
from src.modules.utils.common import SourceMediaInfo
from src.settings.app import AppSettings
from src.tests.conftest import _make_settings
from src.tests.fakes import (
    FakeLifecycle,
    FakeMediaSource,
    FakeRedis,
    FakeStorage,
    FakeTaskQueue,
)

pytestmark = pytest.mark.usefixtures("use_functional_session_factory")
SOURCE_URL = "https://www.youtube.com/watch?v=abcdefghijk"


@dataclass
class BrowserSession:
    client: TestClient[PodcastApp]
    settings: AppSettings
    bearer: str
    session_id: str


@pytest.fixture
async def browser_session(
    db_user: User,
    functional_session: AsyncSession,
    mocked_app_lifecycle: FakeLifecycle,
    mocked_rq_queue: FakeTaskQueue,
    mocked_redis: FakeRedis,
    mocked_storage: FakeStorage,
    mocked_media_source: FakeMediaSource,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[BrowserSession]:
    settings = _make_settings(api_debug_mode=False)
    monkeypatch.setattr("src.modules.auth.backends.get_app_settings", lambda: settings)
    monkeypatch.setattr("src.modules.services.episodes.get_app_settings", lambda: settings)
    monkeypatch.setattr("src.modules.tasks.base.get_app_settings", lambda: settings)
    session_id = str(uuid4())
    cookie, expires_at = encode_jwt(
        TokenPayload(db_user.id, session_id, AuthTokenType.COOKIE), settings, expires_in=3600
    )
    bearer, _ = encode_jwt(TokenPayload(db_user.id, session_id), settings, expires_in=3600)
    functional_session.add(
        UserSession(public_id=session_id, user_id=db_user.id, expired_at=expires_at)
    )
    await functional_session.commit()
    mocked_media_source.media_info[SOURCE_URL] = (
        "",
        SourceMediaInfo(
            SOURCE_URL, "abcdefghijk", "Description", "", "New episode", "Author", 60, []
        ),
    )
    with TestClient(make_app(settings=settings), raise_server_exceptions=False) as client:
        client.cookies.set(settings.auth.session_cookie_name, cookie)
        client.headers.update(
            {"Origin": str(client.base_url).rstrip("/"), "X-Requested-With": "XMLHttpRequest"}
        )
        yield BrowserSession(client, settings, bearer, session_id)


async def add_episode(
    session: AsyncSession, user: User, podcast: Podcast, status: EpisodeStatus = EpisodeStatus.NEW
) -> Episode:
    episode = Episode(
        title="Original",
        source_id=uuid4().hex[:11],
        source_type=SourceType.YOUTUBE,
        podcast_id=podcast.id,
        owner_id=user.id,
        status=status,
        length=60,
    )
    session.add(episode)
    await session.commit()
    await session.refresh(episode)
    return episode


class TestBrowserAuthentication:
    def test_cookie_authenticates_browser_routes_but_not_bearer_api(
        self, browser_session: BrowserSession
    ) -> None:
        client = browser_session.client
        assert client.get("/web-api/podcasts/").status_code == 200
        response = client.get("/api/podcasts/")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_MISSING"
        assert (
            client.get(
                "/api/podcasts/", headers={"Authorization": f"Bearer {browser_session.bearer}"}
            ).status_code
            == 200
        )

    @pytest.mark.parametrize(
        "path",
        ["/web-api/podcasts/", "/web-api/episodes/", "/web-api/progress/", "/podcasts/?fragment=1"],
    )
    def test_missing_cookie_returns_json_without_redirect(
        self, browser_session: BrowserSession, path: str
    ) -> None:
        client = browser_session.client
        client.cookies.clear()
        response = client.get(path, follow_redirects=False)
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_MISSING"
        assert "location" not in response.headers

    def test_expired_cookie_returns_json(
        self, browser_session: BrowserSession, db_user: User
    ) -> None:
        cookie, _ = encode_jwt(
            TokenPayload(db_user.id, browser_session.session_id, AuthTokenType.COOKIE),
            browser_session.settings,
            expires_in=-1,
        )
        browser_session.client.cookies.set(
            browser_session.settings.auth.session_cookie_name, cookie
        )
        response = browser_session.client.get("/web-api/episodes/")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "AUTH_INVALID"

    async def test_inactive_session_returns_json(
        self, browser_session: BrowserSession, functional_session: AsyncSession
    ) -> None:
        session = await functional_session.scalar(
            select(UserSession).where(UserSession.public_id == browser_session.session_id)
        )
        assert session is not None
        session.is_active = False
        await functional_session.commit()
        response = browser_session.client.get("/web-api/podcasts/")
        assert response.status_code == 401
        assert response.headers.get("location") is None

    @pytest.mark.parametrize(
        "origin,ajax",
        [
            (None, True),
            ("null", True),
            ("https://foreign.example", True),
            ("http://testserver.local", False),
        ],
    )
    async def test_cross_site_mutations_do_not_persist(
        self,
        browser_session: BrowserSession,
        functional_session: AsyncSession,
        origin: str | None,
        ajax: bool,
    ) -> None:
        client = browser_session.client
        client.headers.pop("Origin")
        client.headers.pop("X-Requested-With")
        headers = {"Origin": origin} if origin else {}
        if ajax:
            headers["X-Requested-With"] = "XMLHttpRequest"
        response = client.post("/web-api/podcasts/", json={"name": "Forbidden"}, headers=headers)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "FORBIDDEN"
        assert await functional_session.scalar(select(func.count(Podcast.id))) == 0


class TestBrowserPodcasts:
    async def test_create_update_and_delete_persist_owned_fields(
        self, browser_session: BrowserSession, functional_session: AsyncSession, db_user: User
    ) -> None:
        client = browser_session.client
        created = client.post(
            "/web-api/podcasts/", json={"name": "My podcast", "description": "Initial"}
        )
        assert created.status_code == 201, created.text
        data = created.json()
        assert data["download_automatically"] is False
        podcast_id = data["id"]
        response = client.patch(
            f"/web-api/podcasts/{podcast_id}/",
            json={"name": "Renamed", "description": "Changed", "download_automatically": True},
        )
        assert response.status_code == 200, response.text
        podcast = await functional_session.get(Podcast, podcast_id)
        assert podcast is not None
        assert (
            podcast.name,
            podcast.description,
            podcast.download_automatically,
            podcast.owner_id,
        ) == ("Renamed", "Changed", True, db_user.id)
        assert client.delete(f"/web-api/podcasts/{podcast_id}/").status_code == 204
        assert await functional_session.scalar(select(func.count(Podcast.id))) == 0

    @pytest.mark.parametrize("payload", [{"name": " "}, {"name": "x" * 257}, {"name": None}])
    def test_create_invalid_name_returns_structured_error(
        self, browser_session: BrowserSession, payload: dict[str, str | None]
    ) -> None:
        response = browser_session.client.post("/web-api/podcasts/", json=payload)
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_PARAMETERS"

    async def test_delete_with_active_episode_rolls_back_all_rows(
        self,
        browser_session: BrowserSession,
        functional_session: AsyncSession,
        db_user: User,
        db_podcast: Podcast,
    ) -> None:
        await add_episode(functional_session, db_user, db_podcast)
        await add_episode(functional_session, db_user, db_podcast, EpisodeStatus.DOWNLOADING)
        response = browser_session.client.delete(f"/web-api/podcasts/{db_podcast.id}/")
        assert response.status_code == 409
        assert await functional_session.scalar(select(func.count(Episode.id))) == 2
        assert await functional_session.scalar(select(func.count(Podcast.id))) == 1

    @pytest.mark.parametrize("method", ["get", "patch", "delete"])
    async def test_foreign_podcast_is_unavailable(
        self,
        browser_session: BrowserSession,
        functional_session: AsyncSession,
        db_podcast: Podcast,
        method: str,
    ) -> None:
        other = User(email="other@podcast.example", password="hash", is_active=True)
        functional_session.add(other)
        await functional_session.flush()
        db_podcast.owner_id = other.id
        await functional_session.commit()
        response = browser_session.client.request(
            method,
            f"/web-api/podcasts/{db_podcast.id}/",
            json={"name": "Changed"} if method == "patch" else None,
        )
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"


class TestBrowserEpisodes:
    async def test_concurrent_creation_returns_one_episode(
        self,
        browser_session: BrowserSession,
        functional_session: AsyncSession,
        db_podcast: Podcast,
        mocked_rq_queue: FakeTaskQueue,
    ) -> None:
        url = f"/web-api/podcasts/{db_podcast.id}/episodes/"
        responses = await asyncio.gather(
            asyncio.to_thread(browser_session.client.post, url, json={"sourceURL": SOURCE_URL}),
            asyncio.to_thread(browser_session.client.post, url, json={"sourceURL": SOURCE_URL}),
        )
        assert all(response.status_code == 201 for response in responses)
        assert responses[0].json()["id"] == responses[1].json()["id"]
        assert await functional_session.scalar(select(func.count(Episode.id))) == 1
        assert len(mocked_rq_queue.enqueued) == 1

    async def test_failed_cancellation_can_be_retried(
        self,
        browser_session: BrowserSession,
        functional_session: AsyncSession,
        db_user: User,
        db_podcast: Podcast,
        mocked_rq_queue: FakeTaskQueue,
    ) -> None:
        episode = await add_episode(
            functional_session, db_user, db_podcast, EpisodeStatus.DOWNLOADING
        )
        mocked_rq_queue.error = RuntimeError("private queue error")
        url = f"/web-api/episodes/{episode.id}/cancel-downloading/"
        response = browser_session.client.put(url)
        assert response.status_code == 503
        assert "private queue error" not in response.text
        await functional_session.refresh(episode)
        assert episode.status == EpisodeStatus.DOWNLOADING
        mocked_rq_queue.error = None
        assert browser_session.client.put(url).json()["status"] == "CANCELING"

    @pytest.mark.parametrize("payload", [{"title": None}, {"title": " "}, {}])
    async def test_invalid_edit_does_not_change_episode(
        self,
        browser_session: BrowserSession,
        functional_session: AsyncSession,
        db_user: User,
        db_podcast: Podcast,
        payload: dict[str, str | None],
    ) -> None:
        episode = await add_episode(functional_session, db_user, db_podcast)
        response = browser_session.client.patch(f"/web-api/episodes/{episode.id}/", json=payload)
        assert response.status_code == 400
        await functional_session.refresh(episode)
        assert episode.title == "Original"

    async def test_filtered_pagination_uses_audio_size_and_status(
        self, functional_session: AsyncSession, db_user: User, db_podcast: Podcast
    ) -> None:
        small = await add_episode(functional_session, db_user, db_podcast)
        large = await add_episode(functional_session, db_user, db_podcast, EpisodeStatus.ERROR)
        audio = File(
            type=MediaType.AUDIO,
            owner_id=db_user.id,
            size=2 * 1024 * 1024,
            available=False,
            path="",
            access_token="test",
        )
        functional_session.add(audio)
        await functional_session.flush()
        large.audio_id = audio.id
        await functional_session.commit()
        repository = EpisodeRepository(functional_session, scope=OwnerScope(db_user.id))
        items, total = await repository.all_paginated(
            statuses="ERROR",
            search="Original",
            podcast__name=db_podcast.name,
            audio__size__gte=1024 * 1024,
        )
        assert total == 1
        assert [item.id for item in items] == [large.id]
        assert small.id != large.id

    @pytest.mark.parametrize("automatic", [False, True])
    async def test_create_obeys_autodownload_and_reuses_existing_episode(
        self,
        browser_session: BrowserSession,
        functional_session: AsyncSession,
        db_podcast: Podcast,
        automatic: bool,
        mocked_rq_queue: FakeTaskQueue,
    ) -> None:
        db_podcast.download_automatically = automatic
        await functional_session.commit()
        url = f"/web-api/podcasts/{db_podcast.id}/episodes/"
        first = browser_session.client.post(url, json={"sourceURL": SOURCE_URL})
        assert first.status_code == 201, first.text
        second = browser_session.client.post(url, json={"sourceURL": SOURCE_URL})
        assert second.status_code == 201, second.text
        assert second.json()["id"] == first.json()["id"]
        assert first.json()["status"] == ("DOWNLOADING" if automatic else "NEW")
        assert await functional_session.scalar(select(func.count(Episode.id))) == 1
        assert len(mocked_rq_queue.enqueued) == (2 if automatic else 1)

    async def test_update_download_cancel_and_delete_state_transitions(
        self,
        browser_session: BrowserSession,
        functional_session: AsyncSession,
        db_user: User,
        db_podcast: Podcast,
        mocked_rq_queue: FakeTaskQueue,
        mocked_redis: FakeRedis,
    ) -> None:
        episode = await add_episode(functional_session, db_user, db_podcast)
        client = browser_session.client
        url = f"/web-api/episodes/{episode.id}/"
        response = client.patch(
            url, json={"title": "Edited", "author": "New author", "description": "Changed"}
        )
        assert response.status_code == 200, response.text
        await functional_session.refresh(episode)
        assert (episode.title, episode.author, episode.description) == (
            "Edited",
            "New author",
            "Changed",
        )
        assert client.put(url + "download/").json()["status"] == "DOWNLOADING"
        assert client.put(url + "download/").status_code == 409
        assert len(mocked_rq_queue.enqueued) == 1
        assert client.delete(url).status_code == 409
        cancelled = client.put(url + "cancel-downloading/")
        assert cancelled.status_code == 200, cancelled.text
        assert cancelled.json()["status"] == "CANCELING"
        assert len(mocked_rq_queue.cancelled) == 2
        assert mocked_redis.published
        assert client.put(url + "cancel-downloading/").status_code == 409
        await functional_session.refresh(episode)
        episode.status = EpisodeStatus.NEW
        await functional_session.commit()
        assert client.delete(url).status_code == 204
        assert await functional_session.scalar(select(func.count(Episode.id))) == 0

    async def test_download_queue_failure_can_be_retried(
        self,
        browser_session: BrowserSession,
        functional_session: AsyncSession,
        db_user: User,
        db_podcast: Podcast,
        mocked_rq_queue: FakeTaskQueue,
    ) -> None:
        episode = await add_episode(functional_session, db_user, db_podcast)
        mocked_rq_queue.error = RuntimeError("private queue detail")
        url = f"/web-api/episodes/{episode.id}/download/"
        response = browser_session.client.put(url)
        assert response.status_code == 503
        assert "private queue detail" not in response.text
        await functional_session.refresh(episode)
        assert episode.status == EpisodeStatus.ERROR
        mocked_rq_queue.error = None
        assert browser_session.client.put(url).status_code == 200

    async def test_creation_queue_failure_keeps_one_recoverable_episode(
        self,
        browser_session: BrowserSession,
        functional_session: AsyncSession,
        db_podcast: Podcast,
        mocked_rq_queue: FakeTaskQueue,
    ) -> None:
        db_podcast.download_automatically = True
        await functional_session.commit()
        mocked_rq_queue.error = RuntimeError("queue unavailable")
        response = browser_session.client.post(
            f"/web-api/podcasts/{db_podcast.id}/episodes/", json={"sourceURL": SOURCE_URL}
        )
        assert response.status_code == 503, response.text
        episode_id = response.json()["error"]["details"]["episode_id"]
        episode = await functional_session.get(Episode, episode_id)
        assert episode is not None and episode.status == EpisodeStatus.ERROR
        assert await functional_session.scalar(select(func.count(Episode.id))) == 1

    @pytest.mark.parametrize("payload", [{"sourceURL": " "}, {}, {"sourceURL": "unsupported"}])
    def test_invalid_source_returns_structured_error(
        self, browser_session: BrowserSession, db_podcast: Podcast, payload: dict[str, str]
    ) -> None:
        response = browser_session.client.post(
            f"/web-api/podcasts/{db_podcast.id}/episodes/", json=payload
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_PARAMETERS"

    @pytest.mark.parametrize(
        "suffix,method",
        [
            ("", "get"),
            ("", "patch"),
            ("", "delete"),
            ("download/", "put"),
            ("cancel-downloading/", "put"),
        ],
    )
    async def test_foreign_episode_is_unavailable(
        self,
        browser_session: BrowserSession,
        functional_session: AsyncSession,
        db_podcast: Podcast,
        suffix: str,
        method: str,
    ) -> None:
        other = User(email="episode-other@podcast.example", password="hash", is_active=True)
        functional_session.add(other)
        await functional_session.commit()
        episode = await add_episode(functional_session, other, db_podcast)
        response = browser_session.client.request(
            method,
            f"/web-api/episodes/{episode.id}/{suffix}",
            json={"title": "Forbidden"} if method == "patch" else None,
        )
        assert response.status_code == 404, response.text


class TestBrowserProgressAndRSS:
    async def test_progress_handles_unknown_size_and_stale_redis(
        self,
        browser_session: BrowserSession,
        functional_session: AsyncSession,
        db_user: User,
        db_podcast: Podcast,
        mocked_redis: FakeRedis,
    ) -> None:
        episode = await add_episode(
            functional_session, db_user, db_podcast, EpisodeStatus.DOWNLOADING
        )
        key = episode.audio_filename.partition(".")[0]
        mocked_redis.content[key] = {
            "event_key": key,
            "status": "DL_EPISODE_DOWNLOADING",
            "processed_bytes": 10,
            "total_bytes": 0,
        }
        response = browser_session.client.get(f"/web-api/progress/?episode_id={episode.id}")
        assert response.status_code == 200, response.text
        assert response.json()["progressItems"][0]["completed"] == 0
        episode.status = EpisodeStatus.ERROR
        await functional_session.commit()
        item = browser_session.client.get(f"/web-api/progress/?episode_id={episode.id}").json()[
            "progressItems"
        ][0]
        assert item["status"] == "ERROR"
        assert item["current_file_size"] == 0

    @pytest.mark.parametrize(
        "suffix,method", [("rss-task/", "get"), ("generate-rss/", "put"), ("episodes/", "post")]
    )
    async def test_foreign_podcast_tasks_and_creation_are_unavailable(
        self,
        browser_session: BrowserSession,
        functional_session: AsyncSession,
        db_podcast: Podcast,
        mocked_rq_queue: FakeTaskQueue,
        suffix: str,
        method: str,
    ) -> None:
        other = User(email="rss-other@podcast.example", password="hash", is_active=True)
        functional_session.add(other)
        await functional_session.flush()
        db_podcast.owner_id = other.id
        await functional_session.commit()
        response = browser_session.client.request(
            method,
            f"/web-api/podcasts/{db_podcast.id}/{suffix}",
            json={"sourceURL": SOURCE_URL} if method == "post" else None,
        )
        assert response.status_code == 404, response.text
        assert not mocked_rq_queue.enqueued

    def test_rss_queue_failure_returns_safe_error(
        self, browser_session: BrowserSession, db_podcast: Podcast, mocked_rq_queue: FakeTaskQueue
    ) -> None:
        mocked_rq_queue.error = RuntimeError("private queue detail")
        response = browser_session.client.put(f"/web-api/podcasts/{db_podcast.id}/generate-rss/")
        assert response.status_code == 503
        assert "private queue detail" not in response.text

    def test_mismatched_rss_job_is_not_reported(
        self, browser_session: BrowserSession, db_podcast: Podcast, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        job = create_autospec(Job, instance=True, spec_set=True)
        job.id = GenerateRSSTask.get_job_id(db_podcast.id)
        job.args = (db_podcast.id + 1,)
        monkeypatch.setattr(
            podcasts_operations.Job, "fetch", create_autospec(Job.fetch, return_value=job)
        )
        response = browser_session.client.get(f"/web-api/podcasts/{db_podcast.id}/rss-task/")
        assert response.json()["status"] == "unknown"

    async def test_progress_reports_terminal_db_status(
        self,
        browser_session: BrowserSession,
        functional_session: AsyncSession,
        db_user: User,
        db_podcast: Podcast,
    ) -> None:
        episode = await add_episode(
            functional_session, db_user, db_podcast, EpisodeStatus.PUBLISHED
        )
        response = browser_session.client.get(f"/web-api/progress/?episode_id={episode.id}")
        assert response.status_code == 200, response.text
        item = response.json()["progressItems"][0]
        assert item["status"] == "PUBLISHED"
        assert item["completed"] == 100
        assert browser_session.client.get("/web-api/progress/").json() == {"progressItems": []}

    def test_rss_start_enqueues_the_owned_podcast(
        self, browser_session: BrowserSession, db_podcast: Podcast, mocked_rq_queue: FakeTaskQueue
    ) -> None:
        response = browser_session.client.put(f"/web-api/podcasts/{db_podcast.id}/generate-rss/")
        assert response.status_code == 200, response.text
        assert response.json()["job_id"] == GenerateRSSTask.get_job_id(db_podcast.id)
        assert mocked_rq_queue.enqueued[-1].args == (db_podcast.id,)

    @pytest.mark.parametrize(
        "state,outcome,expected",
        [
            ("queued", None, "queued"),
            ("started", None, "running"),
            ("finished", TaskResultCode.SUCCESS, "success"),
            ("finished", TaskResultCode.ERROR, "error"),
            ("failed", None, "error"),
        ],
    )
    def test_rss_status_checks_task_result(
        self,
        browser_session: BrowserSession,
        db_podcast: Podcast,
        monkeypatch: pytest.MonkeyPatch,
        state: str,
        outcome: TaskResultCode | None,
        expected: str,
    ) -> None:
        job = create_autospec(Job, instance=True, spec_set=True)
        job.id = GenerateRSSTask.get_job_id(db_podcast.id)
        job.args = (db_podcast.id,)
        job.get_status.return_value = state
        job.return_value.return_value = outcome
        monkeypatch.setattr(
            podcasts_operations.Job, "fetch", create_autospec(Job.fetch, return_value=job)
        )
        response = browser_session.client.get(f"/web-api/podcasts/{db_podcast.id}/rss-task/")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == expected
        assert bool(response.json()["error"]) == (expected == "error")

    def test_missing_rss_job_is_unknown(
        self, browser_session: BrowserSession, db_podcast: Podcast, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            podcasts_operations.Job,
            "fetch",
            create_autospec(Job.fetch, side_effect=NoSuchJobError("missing")),
        )
        response = browser_session.client.get(f"/web-api/podcasts/{db_podcast.id}/rss-task/")
        assert response.status_code == 200, response.text
        assert response.json()["status"] == "unknown"
