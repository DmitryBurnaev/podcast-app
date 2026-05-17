from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.constants import EpisodeStatus, FileType
from src.modules.tasks.base import TaskResultCode
from src.modules.tasks.rss import GenerateRSSTask
from src.tests.factories import make_episode, make_file, make_podcast
from src.tests.mocks import MockSession, MockStorageS3


class TestGenerateRSSTaskRun:
    async def test_run__filters_requested_podcasts_and_returns_error_on_any_failure(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        podcasts = [make_podcast(id=1), make_podcast(id=2)]
        podcast_repository = SimpleNamespace(all=AsyncMock(return_value=podcasts))
        file_repository = SimpleNamespace()
        monkeypatch.setattr(
            "src.modules.tasks.rss.PodcastRepository",
            Mock(return_value=podcast_repository),
        )
        monkeypatch.setattr("src.modules.tasks.rss.FileRepository", Mock(return_value=file_repository))
        task = GenerateRSSTask(db_session=MockSession())
        task._generate = AsyncMock(
            side_effect=[
                {1: TaskResultCode.SUCCESS},
                {2: TaskResultCode.ERROR},
            ]
        )

        result = await task.run(1, 2)

        assert result == TaskResultCode.ERROR
        podcast_repository.all.assert_awaited_once_with(ids=[1, 2])

    async def test_run__all_generated__success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        podcast_repository = SimpleNamespace(all=AsyncMock(return_value=[make_podcast()]))
        monkeypatch.setattr(
            "src.modules.tasks.rss.PodcastRepository",
            Mock(return_value=podcast_repository),
        )
        monkeypatch.setattr("src.modules.tasks.rss.FileRepository", Mock(return_value=SimpleNamespace()))
        task = GenerateRSSTask(db_session=MockSession())
        task._generate = AsyncMock(return_value={1: TaskResultCode.SUCCESS})

        result = await task.run()

        assert result == TaskResultCode.SUCCESS
        podcast_repository.all.assert_awaited_once_with()


class TestGenerateRSSTaskGenerate:
    async def test_generate__existing_rss__updates_file(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        podcast = make_podcast(id=10)
        podcast.rss_id = 77
        local_path = tmp_path / "feed.xml"
        local_path.write_text("<rss />", encoding="utf-8")
        task = GenerateRSSTask(db_session=MockSession())
        task.storage = MockStorageS3()
        task.storage.upload_file.return_value = "rss/feed.xml"
        task.file_repository = SimpleNamespace(update_by_ids=AsyncMock())
        task.podcast_repository = SimpleNamespace(update_by_filters=AsyncMock())
        task._render_rss_to_file = AsyncMock(return_value=local_path)
        monkeypatch.setattr("src.modules.tasks.rss.get_file_size", Mock(return_value=55))

        result = await task._generate(podcast)

        assert result == {10: TaskResultCode.SUCCESS}
        task.file_repository.update_by_ids.assert_awaited_once_with(
            [77],
            value={"path": "rss/feed.xml", "size": 55, "available": True},
        )
        task.podcast_repository.update_by_filters.assert_not_awaited()

    async def test_generate__new_rss__creates_file_and_links_podcast(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        podcast = make_podcast(id=10)
        podcast.rss_id = None
        local_path = tmp_path / "feed.xml"
        local_path.write_text("<rss />", encoding="utf-8")
        rss_file = make_file(id=99, type=FileType.RSS, path="rss/feed.xml")
        task = GenerateRSSTask(db_session=MockSession())
        task.storage = MockStorageS3()
        task.storage.upload_file.return_value = "rss/feed.xml"
        task.file_repository = SimpleNamespace(create=AsyncMock(return_value=rss_file))
        task.podcast_repository = SimpleNamespace(update_by_filters=AsyncMock())
        task._render_rss_to_file = AsyncMock(return_value=local_path)
        monkeypatch.setattr("src.modules.tasks.rss.get_file_size", Mock(return_value=55))

        result = await task._generate(podcast)

        assert result == {10: TaskResultCode.SUCCESS}
        task.file_repository.create.assert_awaited_once_with(
            path="rss/feed.xml",
            size=55,
            available=True,
            type=FileType.RSS,
            owner_id=podcast.owner_id,
        )
        task.podcast_repository.update_by_filters.assert_awaited_once_with(
            filters={"id": podcast.id},
            value={"rss_id": rss_file.id},
        )

    async def test_generate__upload_failure__returns_error(self, tmp_path: Path) -> None:
        podcast = make_podcast(id=10)
        task = GenerateRSSTask(db_session=MockSession())
        task.storage = MockStorageS3()
        task.storage.upload_file.return_value = None
        task.file_repository = SimpleNamespace(update_by_ids=AsyncMock(), create=AsyncMock())
        task._render_rss_to_file = AsyncMock(return_value=tmp_path / "feed.xml")

        result = await task._generate(podcast)

        assert result == {10: TaskResultCode.ERROR}
        task.file_repository.update_by_ids.assert_not_awaited()
        task.file_repository.create.assert_not_awaited()

    async def test_render_rss_to_file__renders_published_episodes(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        template_dir = tmp_path / "templates" / "rss"
        template_dir.mkdir(parents=True)
        (template_dir / "feed_template.xml").write_text(
            "{{ podcast.name }}:{% for episode in episodes %}{{ episode.title }};{% endfor %}",
            encoding="utf-8",
        )
        rss_dir = tmp_path / "rss"
        rss_dir.mkdir()
        episodes = [make_episode(title="Published", status=EpisodeStatus.PUBLISHED)]
        episode_repository = SimpleNamespace(all=AsyncMock(return_value=episodes))
        monkeypatch.setattr(
            "src.modules.tasks.rss.EpisodeRepository",
            Mock(return_value=episode_repository),
        )
        task = GenerateRSSTask(db_session=MockSession())
        task.settings = SimpleNamespace(template_path=tmp_path / "templates", tmp_rss_path=rss_dir)
        podcast = make_podcast(id=10, name="Podcast")

        result = await task._render_rss_to_file(podcast)

        assert result == rss_dir / f"{podcast.publish_id}.xml"
        assert result.read_text(encoding="utf-8") == "Podcast:Published;"
        episode_repository.all.assert_awaited_once_with(
            podcast_id=podcast.id,
            status=EpisodeStatus.PUBLISHED,
            published_at__ne=None,
        )
