from unittest.mock import AsyncMock, create_autospec

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.common.constants import EpisodeStatus
from src.modules.common.types import OwnerScope
from src.modules.db.repositories import EpisodeRepository
from src.modules.db.services import SASessionUOW
from src.modules.services import episodes_operations
from src.modules.services.episodes_operations import EpisodeOperations
from src.modules.services.task_queue import TaskQueue
from src.tests.factories import make_episode


class TestEpisodeProgressReset:
    async def test_download_resets_old_progress_before_enqueuing(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        episode = make_episode(id=42, status=EpisodeStatus.NEW)
        uow = create_autospec(SASessionUOW, instance=True, spec_set=True)
        uow.session = create_autospec(AsyncSession, instance=True, spec_set=True)
        uow.__aenter__.return_value = uow
        repository = create_autospec(EpisodeRepository, instance=True, spec_set=True)
        repository.first_for_update.return_value = episode
        reset = AsyncMock()
        queue = create_autospec(TaskQueue, instance=True, spec_set=True)

        async def enqueue(*args: object, **kwargs: object) -> None:
            reset.assert_awaited_once_with(episode)

        monkeypatch.setattr(episodes_operations, "SASessionUOW", lambda: uow)
        monkeypatch.setattr(episodes_operations, "EpisodeRepository", lambda **_: repository)
        monkeypatch.setattr(episodes_operations, "reset_episode_progress", reset)
        monkeypatch.setattr(EpisodeOperations, "_run_task", enqueue)

        await EpisodeOperations().download(queue, episode.id, OwnerScope(episode.owner_id))

        repository.update.assert_awaited_once_with(episode, status=EpisodeStatus.DOWNLOADING)
        reset.assert_awaited_once_with(episode)
