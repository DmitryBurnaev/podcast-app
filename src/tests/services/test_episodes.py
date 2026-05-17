from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.constants import FileType, SourceType
from src.exceptions import SourceFetchError
from src.modules.db.models.podcasts import EpisodeChapter
from src.modules.services.episodes import EpisodeCreator
from src.modules.utils.common import SourceInfo, SourceMediaInfo
from src.tests.factories import make_episode, make_file
from src.tests.mocks import MockSession


class TestEpisodeCreatorCreate:
    async def test_create__same_episode_in_podcast__returns_existing(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        existing_episode = make_episode(podcast_id=10, source_id="source")
        episode_repository = SimpleNamespace(
            all=AsyncMock(return_value=[existing_episode]),
            create=AsyncMock(),
        )
        monkeypatch.setattr(
            "src.modules.services.episodes.EpisodeRepository",
            Mock(return_value=episode_repository),
        )
        monkeypatch.setattr(
            "src.modules.services.episodes.PodcastRepository",
            Mock(return_value=SimpleNamespace()),
        )
        monkeypatch.setattr(
            "src.modules.services.episodes.common_utils.extract_source_info",
            Mock(return_value=SourceInfo(id="source", type=SourceType.YOUTUBE)),
        )
        creator = EpisodeCreator(db_session=MockSession(), user_id=1)

        result = await creator.create(podcast_id=10, source_url="https://example.com")

        assert result is existing_episode
        episode_repository.create.assert_not_awaited()

    async def test_create__new_episode__creates_and_attaches_files(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        created_episode = make_episode(id=99)
        audio_file = make_file(id=1)
        image_file = make_file(id=2)
        episode_repository = SimpleNamespace(all=AsyncMock(return_value=[]), create=AsyncMock(return_value=created_episode))
        monkeypatch.setattr(
            "src.modules.services.episodes.EpisodeRepository",
            Mock(return_value=episode_repository),
        )
        monkeypatch.setattr(
            "src.modules.services.episodes.PodcastRepository",
            Mock(return_value=SimpleNamespace()),
        )
        monkeypatch.setattr(
            "src.modules.services.episodes.common_utils.extract_source_info",
            Mock(return_value=SourceInfo(id="source", type=SourceType.YOUTUBE)),
        )
        creator = EpisodeCreator(db_session=MockSession(), user_id=7)
        creator._get_episode_data = AsyncMock(
            return_value={
                "source_id": "source",
                "source_type": SourceType.YOUTUBE,
                "watch_url": "https://watch",
                "title": "Title",
                "description": "Description",
                "author": "Author",
                "length": 60,
                "chapters": None,
                "audio_id": audio_file.id,
                "image_id": image_file.id,
                "audio": audio_file,
                "image": image_file,
            }
        )

        result = await creator.create(podcast_id=10, source_url="https://example.com")

        assert result is created_episode
        assert result.audio is audio_file
        assert result.image is image_file
        episode_repository.create.assert_awaited_once_with(
            source_id="source",
            source_type=SourceType.YOUTUBE,
            watch_url="https://watch",
            title="Title",
            description="Description",
            author="Author",
            length=60,
            chapters=None,
            podcast_id=10,
            owner_id=7,
            image_id=image_file.id,
            audio_id=audio_file.id,
        )


class TestEpisodeCreatorData:
    def test_replace_special_symbols__render_links_enabled__keeps_text(self) -> None:
        creator = EpisodeCreator.__new__(EpisodeCreator)
        creator.settings = SimpleNamespace(render_links=True)

        result = creator._replace_special_symbols("see https://example.com?a=1 #tag")

        assert result == "see https://example.com?a=1 #tag"

    def test_replace_special_symbols__render_links_disabled__masks_links(self) -> None:
        creator = EpisodeCreator.__new__(EpisodeCreator)
        creator.settings = SimpleNamespace(render_links=False)

        result = creator._replace_special_symbols("see https://example.com/path #tag & more")

        assert result == "see [LINK] tag  more"

    async def test_get_episode_data__source_info__creates_data_from_external_source(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        audio_file = make_file(id=1)
        image_file = make_file(id=2)
        cookie = SimpleNamespace(id=44, file_path=Path("/tmp/cookies.txt"))
        source_info = SourceInfo(id="source", type=SourceType.YOUTUBE)
        source_media_info = SourceMediaInfo(
            watch_url="https://watch",
            source_id="source",
            description="Description <unsafe>",
            thumbnail_url="https://thumb",
            title="Title & more",
            author="Author",
            length=120,
            chapters=[EpisodeChapter(title="Intro", start=0, end=10)],
        )
        creator = EpisodeCreator.__new__(EpisodeCreator)
        creator.db_session = MockSession()
        creator.user_id = 7
        creator.podcast_id = 10
        creator.source_info = source_info
        creator.settings = SimpleNamespace(render_links=False)
        creator._create_files = AsyncMock(return_value=(audio_file, image_file))
        monkeypatch.setattr(
            "src.modules.services.episodes.cookie_file_ctx",
            lambda *args, **kwargs: _AsyncContext(cookie),
        )
        monkeypatch.setattr(
            "src.modules.services.episodes.common_utils.get_source_media_info",
            AsyncMock(return_value=(None, source_media_info)),
        )

        result = await creator._get_episode_data(same_episode=None)

        assert result["source_id"] == "source"
        assert result["watch_url"] == "https://watch"
        assert result["title"] == "Title  more"
        assert result["description"] == "Description unsafe"
        assert result["chapters"] == [{"title": "Intro", "start": 0, "end": 10}]
        assert result["cookie_id"] == 44
        assert result["audio"] is audio_file
        assert result["image"] is image_file
        assert source_info.cookie_path == cookie.file_path

    async def test_get_episode_data__same_episode_fallback__copies_data(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        same_episode = make_episode(source_id="source", source_type=SourceType.YOUTUBE)
        same_episode.image_id = 11
        same_episode.audio_id = 12
        same_episode.cookie_id = 13
        same_episode.image = make_file(id=11)
        same_episode.audio = make_file(id=12)
        copied_audio = make_file(id=21)
        copied_image = make_file(id=22)
        creator = EpisodeCreator.__new__(EpisodeCreator)
        creator.db_session = MockSession()
        creator.user_id = 7
        creator.podcast_id = 10
        creator.source_info = SourceInfo(id="source", type=SourceType.YOUTUBE)
        creator._create_files = AsyncMock(return_value=(copied_audio, copied_image))
        monkeypatch.setattr(
            "src.modules.services.episodes.cookie_file_ctx",
            lambda *args, **kwargs: _AsyncContext(None),
        )
        monkeypatch.setattr(
            "src.modules.services.episodes.common_utils.get_source_media_info",
            AsyncMock(return_value=("missing", None)),
        )

        result = await creator._get_episode_data(same_episode=same_episode)

        assert result["source_id"] == same_episode.source_id
        assert result["podcast_id"] == 10
        assert result["owner_id"] == 7
        assert result["cookie_id"] is None
        assert result["audio"] is copied_audio
        assert result["image"] is copied_image

    async def test_get_episode_data__no_source_and_no_same_episode__fail(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        creator = EpisodeCreator.__new__(EpisodeCreator)
        creator.db_session = MockSession()
        creator.user_id = 7
        creator.podcast_id = 10
        creator.source_info = SourceInfo(id="source", type=SourceType.YOUTUBE)
        monkeypatch.setattr(
            "src.modules.services.episodes.cookie_file_ctx",
            lambda *args, **kwargs: _AsyncContext(None),
        )
        monkeypatch.setattr(
            "src.modules.services.episodes.common_utils.get_source_media_info",
            AsyncMock(return_value=("missing", None)),
        )

        with pytest.raises(SourceFetchError):
            await creator._get_episode_data(same_episode=None)


class TestEpisodeCreatorFiles:
    async def test_create_files__same_episode__copies_image_and_audio(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        same_episode = make_episode()
        same_episode.image_id = 11
        same_episode.audio_id = 12
        image_file = make_file(id=21)
        audio_file = make_file(id=22)
        file_repository = SimpleNamespace(copy=AsyncMock(side_effect=[image_file, audio_file]))
        monkeypatch.setattr(
            "src.modules.services.episodes.FileRepository",
            Mock(return_value=file_repository),
        )
        creator = EpisodeCreator.__new__(EpisodeCreator)
        creator.db_session = MockSession()
        creator.user_id = 7

        result = await creator._create_files(same_episode=same_episode, source_info=None)

        assert result == (audio_file, image_file)
        assert file_repository.copy.await_args_list[0].kwargs == {"owner_id": 7, "file_id": 11}
        assert file_repository.copy.await_args_list[1].kwargs == {
            "file_id": 12,
            "owner_id": 7,
            "available": False,
        }

    async def test_create_files__same_episode_without_file_ids__fail(self) -> None:
        same_episode = make_episode()
        creator = EpisodeCreator.__new__(EpisodeCreator)
        creator.db_session = MockSession()
        creator.user_id = 7

        with pytest.raises(RuntimeError, match="missing image/audio"):
            await creator._create_files(same_episode=same_episode, source_info=None)

    async def test_create_files__source_info__creates_image_and_audio(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        image_file = make_file(id=21)
        audio_file = make_file(id=22)
        file_repository = SimpleNamespace(create=AsyncMock(side_effect=[image_file, audio_file]))
        monkeypatch.setattr(
            "src.modules.services.episodes.FileRepository",
            Mock(return_value=file_repository),
        )
        creator = EpisodeCreator.__new__(EpisodeCreator)
        creator.db_session = MockSession()
        creator.user_id = 7
        source_info = SourceMediaInfo(
            watch_url="https://watch",
            source_id="source",
            description="Description",
            thumbnail_url="https://thumb",
            title="Title",
            author="Author",
            length=120,
            chapters=[],
        )

        result = await creator._create_files(same_episode=None, source_info=source_info)

        assert result == (audio_file, image_file)
        assert file_repository.create.await_args_list[0].kwargs["type"] == FileType.IMAGE
        assert file_repository.create.await_args_list[0].kwargs["source_url"] == "https://thumb"
        assert file_repository.create.await_args_list[1].kwargs["type"] == FileType.AUDIO
        assert file_repository.create.await_args_list[1].kwargs["source_url"] == "https://watch"

    async def test_create_files__without_source__fail(self) -> None:
        creator = EpisodeCreator.__new__(EpisodeCreator)
        creator.db_session = MockSession()
        creator.user_id = 7
        creator.source_info = SourceInfo(id="source", type=SourceType.YOUTUBE)

        with pytest.raises(SourceFetchError):
            await creator._create_files(same_episode=None, source_info=None)


class _AsyncContext:
    def __init__(self, value: object) -> None:
        self.value = value

    async def __aenter__(self) -> object:
        return self.value

    async def __aexit__(self, *args: object) -> None:
        return None
