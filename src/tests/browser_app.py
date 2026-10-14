"""Local browser validation app: real PostgreSQL/auth, fake external services.

Run with uvicorn src.tests.browser_app:make_browser_app --factory on port 8019.
Requires TEST_DB_NAME starting with podcast_ajax_browser_ and normal DB settings.
The seeded account browser@podcast.example / browser-check is test data only.
"""

import asyncio
from io import BytesIO
import os
from pathlib import Path
import re
import wave

from litestar import get
from litestar.response import Response
from pydantic import SecretStr
import pytest
from rq.exceptions import NoSuchJobError
from rq.job import Job, JobStatus
from sqlalchemy import select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src import main
from src.modules.common.constants import AuthSkip
from src.modules.db.models import BaseModel, Episode, File, Podcast, User
from src.modules.db.models.media import MediaType
from src.modules.db.models.podcasts import EpisodeStatus
from src.modules.tasks import DownloadEpisodeTask, DownloadEpisodeImageTask, GenerateRSSTask
from src.modules.tasks.base import RQTask, TaskResultCode
from src.modules.utils import common
from src.modules.utils.common import SourceInfo, SourceMediaInfo, utcnow
from src.settings.app import AppSettings, FlagsSettings
from src.settings.db import DBSettings
from src.tests.fakes import EnqueuedTask, FakeMediaSource, FakeRedis, FakeStorage, FakeTaskQueue
from src.tests.mocks import mock_target_class


class BrowserJob(Job):
    def __init__(self, job_id: str, podcast_id: int) -> None:
        self.browser_id = job_id
        self.podcast_id = podcast_id
        self.state = JobStatus.QUEUED
        self.outcome: TaskResultCode | None = None

    @property
    def id(self) -> str:
        return self.browser_id

    @id.setter
    def id(self, value: str) -> None:
        self.browser_id = value

    @property
    def args(self) -> tuple[int]:
        return (self.podcast_id,)

    @args.setter
    def args(self, value: tuple[object, ...]) -> None:
        self.podcast_id = int(str(value[0]))

    def get_status(self, refresh: bool = True) -> JobStatus:
        return self.state

    def return_value(self, refresh: bool = False) -> TaskResultCode | None:
        return self.outcome


class BrowserSource(FakeMediaSource):
    async def get_source_media_info(self, source_info: SourceInfo) -> tuple[str, SourceMediaInfo]:
        await asyncio.sleep(4)
        return "", SourceMediaInfo(str(source_info.url), source_info.id, "Local browser fixture", "", "Browser episode", "Fixture author", 20, [])


class BrowserStorage(FakeStorage):
    async def get_presigned_url(self, remote_path: str) -> str:
        return "http://127.0.0.1:8019/__browser__/feed.xml" if remote_path.endswith(".xml") else "http://127.0.0.1:8019/__browser__/audio.wav"


class BrowserQueue(FakeTaskQueue):
    def __init__(self, factory: async_sessionmaker[AsyncSession], redis: FakeRedis) -> None:
        super().__init__()
        self.factory = factory
        self.redis = redis
        self.loop: asyncio.AbstractEventLoop | None = None
        self.jobs: dict[str, BrowserJob] = {}

    def enqueue(self, task: RQTask, *args: object, **kwargs: object) -> EnqueuedTask:
        entry = super().enqueue(task, *args, **kwargs)
        if self.loop is not None:
            if isinstance(task, GenerateRSSTask):
                podcast_id = int(str(args[0]))
                job = BrowserJob(str(kwargs["job_id"]), podcast_id)
                self.jobs[job.id] = job
                asyncio.run_coroutine_threadsafe(self.finish_rss(job), self.loop)
            elif isinstance(task, DownloadEpisodeTask):
                asyncio.run_coroutine_threadsafe(self.finish_download(int(str(kwargs["episode_id"]))), self.loop)
        return entry

    async def finish_download(self, episode_id: int) -> None:
        for step in range(5):
            await asyncio.sleep(1)
            async with self.factory() as session:
                episode = await session.scalar(select(Episode).where(Episode.id == episode_id))
                if episode is None:
                    return
                if episode.status == EpisodeStatus.CANCELING:
                    episode.status = EpisodeStatus.NEW
                    await session.commit()
                    return
                key = episode.audio_filename.partition(".")[0]
                self.redis.content[key] = {"event_key": key, "status": "DL_EPISODE_DOWNLOADING", "processed_bytes": step * 8000, "total_bytes": 40000}
                if step == 4:
                    episode.audio.path = "fixtures/browser.wav"
                    episode.audio.available = True
                    episode.audio.size = 320044
                    episode.status = EpisodeStatus.PUBLISHED
                    episode.published_at = utcnow()
                    await session.commit()

    async def finish_rss(self, job: BrowserJob) -> None:
        job.state = JobStatus.STARTED
        await asyncio.sleep(4)
        async with self.factory() as session:
            podcast = await session.scalar(select(Podcast).where(Podcast.id == job.podcast_id))
            if podcast is None:
                job.outcome = TaskResultCode.ERROR
            else:
                if podcast.rss_id is None:
                    file = File(type=MediaType.RSS, owner_id=podcast.owner_id, path="fixtures/browser.xml", available=True, size=50, access_token=File.generate_token())
                    session.add(file)
                    await session.flush()
                    podcast.rss_id = file.id
                await session.commit()
                job.outcome = TaskResultCode.SUCCESS
        job.state = JobStatus.FINISHED


def make_browser_app() -> main.PodcastApp:
    name = os.environ.get("TEST_DB_NAME", "")
    database_settings = DBSettings()
    if not name.startswith("podcast_ajax_browser_") or not re.fullmatch(r"[a-zA-Z0-9_]+", name) or name == database_settings.name:
        raise RuntimeError("Use a separate TEST_DB_NAME starting with podcast_ajax_browser_.")
    url = make_url(database_settings.database_dsn).set(database=name)
    engine = create_async_engine(url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    patch = pytest.MonkeyPatch()
    settings = AppSettings(service_url="http://127.0.0.1:8019/", app_version="browser-check", app_secret_key=SecretStr("browser-fixture-key-" * 8), flags=FlagsSettings(debug_mode=True, api_debug_mode=False), media_cache_dir=Path("/private/tmp/podcast-browser-cache"))
    redis = FakeRedis()
    storage = BrowserStorage()
    queue = BrowserQueue(factory, redis)
    source = BrowserSource()
    # Keep the patch installers alive for the lifetime of this separate process.
    installers = [mock_target_class(fake, patch) for fake in (redis, storage, queue)]
    for installer in installers:
        next(installer)
    patch.setattr("src.modules.db.session.get_session_factory", lambda: factory)
    for module in ("src.main", "src.modules.auth.backends", "src.modules.db.models.media", "src.modules.db.models.podcasts", "src.modules.services.episodes", "src.modules.services.cover", "src.modules.tasks.base", "src.modules.utils.common"):
        patch.setattr(f"{module}.get_app_settings", lambda: settings)
    patch.setattr(common, "get_source_media_info", source.get_source_media_info)
    patch.setattr(DownloadEpisodeTask, "cancel_task", classmethod(lambda cls, *args, **kwargs: queue.cancel_task(cls, *args, **kwargs)))
    patch.setattr(DownloadEpisodeImageTask, "cancel_task", classmethod(lambda cls, *args, **kwargs: queue.cancel_task(cls, *args, **kwargs)))

    def fetch_job(job_id: str, connection: object = None, serializer: object = None) -> Job:
        if job_id not in queue.jobs:
            raise NoSuchJobError(job_id)
        return queue.jobs[job_id]

    patch.setattr(Job, "fetch", fetch_job)

    async def initialize() -> None:
        queue.loop = asyncio.get_running_loop()
        admin = create_async_engine(url.set(database="postgres"), isolation_level="AUTOCOMMIT", poolclass=NullPool)
        try:
            async with admin.connect() as connection:
                if not await connection.scalar(text("SELECT 1 FROM pg_database WHERE datname = :name"), {"name": name}):
                    await connection.execute(text(f'CREATE DATABASE "{name}"'))
        finally:
            await admin.dispose()
        async with engine.begin() as connection:
            await connection.run_sync(BaseModel.metadata.create_all)
        async with factory() as session:
            user = await session.scalar(select(User).where(User.email == "browser@podcast.example"))
            if user is None:
                user = User(email="browser@podcast.example", password=User.make_password("browser-check"), is_active=True, is_superuser=False)
                session.add(user)
                await session.flush()
                podcast = Podcast(name="Browser podcast", description="Isolated browser validation", publish_id=Podcast.generate_publish_id(), owner_id=user.id, download_automatically=False)
                session.add(podcast)
                await session.commit()

    async def noop() -> None:
        return None

    for mode in main.DbStartMode:
        patch.setitem(main._DB_STARTUP_CHECKS, mode, initialize)
    patch.setattr(main, "validate_s3_settings", lambda _: None)
    patch.setattr(main, "check_redis_connection", noop)
    patch.setattr(main, "close_async_redis_connection", noop)
    patch.setattr(main, "close_database", engine.dispose)
    patch.setattr(main, "make_admin", lambda _: None)
    app = main.make_app(settings=settings)

    @get("/__browser__/audio.wav", opt={AuthSkip.SKIP_AUTH_API: True, AuthSkip.SKIP_AUTH_WEB: True})
    async def audio() -> Response[bytes]:
        buffer = BytesIO()
        with wave.open(buffer, "wb") as writer:
            writer.setnchannels(1)
            writer.setsampwidth(2)
            writer.setframerate(8000)
            writer.writeframes(b"\x00\x00" * 160000)
        return Response(buffer.getvalue(), media_type="audio/wav")

    @get("/__browser__/feed.xml", opt={AuthSkip.SKIP_AUTH_API: True, AuthSkip.SKIP_AUTH_WEB: True})
    async def feed() -> Response[str]:
        return Response('<rss version="2.0"><channel><title>Browser feed</title></channel></rss>', media_type="application/xml")

    app.register(audio)
    app.register(feed)
    return app
