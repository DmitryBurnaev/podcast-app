from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from src.modules.admin.counters import AdminCounter


class TestAdminCounter:
    async def test_get_stat__includes_total_file_size(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            "src.modules.admin.counters.PodcastRepository",
            Mock(return_value=SimpleNamespace(get_total_count=AsyncMock(return_value=2))),
        )
        monkeypatch.setattr(
            "src.modules.admin.counters.EpisodeRepository",
            Mock(return_value=SimpleNamespace(get_total_count=AsyncMock(return_value=7))),
        )
        session = SimpleNamespace(scalar=AsyncMock(return_value=5 * 1024 * 1024))

        result = await AdminCounter.get_stat(session=session)

        assert result.total_podcasts == 2
        assert result.total_episodes == 7
        assert result.total_file_size == 5 * 1024 * 1024
        assert result.total_file_size_label == "5.00 MB"
