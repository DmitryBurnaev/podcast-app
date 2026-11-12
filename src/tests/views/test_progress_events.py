from typing import cast
from unittest.mock import AsyncMock, MagicMock, create_autospec

import pytest
from jinja2 import Template
from litestar import Request
from litestar.testing import TestClient
from redis.asyncio.client import PubSub

from src.modules.common.types import AppRequest, OwnerScope
from src.main import PodcastApp
from src.modules.schemas.progress import ProgressEpisodeResponse, ProgressItemResponse
from src.modules.services.progress import ProgressOperations
from src.modules.services.redis import RedisClient
from src.modules.views import progress as progress_view
from src.settings.app import AppSettings


class TestProgressEvents:
    @pytest.mark.parametrize("path", ["/episodes/progress/events/", "/episodes/more/"])
    def test_browser_routes_require_session(
        self, auth_required_client: TestClient[PodcastApp], path: str
    ) -> None:
        response = auth_required_client.get(path, follow_redirects=False)
        assert response.status_code == 302
        assert response.headers["location"] == "/login"

    async def test_reconnected_stream_includes_owned_episode_already_finished(
        self, monkeypatch: pytest.MonkeyPatch, app_settings: AppSettings
    ) -> None:
        scope = OwnerScope(17)
        finished = ProgressItemResponse(
            status="PUBLISHED",
            completed=100,
            current_file_size=0,
            total_file_size=0,
            episode=ProgressEpisodeResponse(id=2, title="Episode", status="PUBLISHED"),
        )
        operations = create_autospec(ProgressOperations, instance=True, spec_set=True)
        operations.get_progress.side_effect = [
            {"progressItems": []},
            {"progressItems": [finished]},
        ]
        pubsub = create_autospec(PubSub, instance=True, spec_set=True)
        redis = create_autospec(RedisClient, instance=True, spec_set=True)
        redis.async_pubsub.return_value = pubsub
        template = create_autospec(Template, instance=True, spec_set=True)
        template.render.return_value = "finished"
        request = MagicMock(spec=Request)
        request.app.template_engine.get_template.return_value = template
        monkeypatch.setattr(progress_view, "RedisClient", lambda: redis)
        monkeypatch.setattr(progress_view, "ProgressOperations", lambda: operations)

        events = progress_view.progress_events(cast(AppRequest, request), scope, app_settings, 2)
        assert (await anext(events)).data == "finished"
        await events.aclose()
        assert operations.get_progress.await_args_list == [((scope,), {}), ((scope, 2), {})]
        pubsub.aclose.assert_awaited_once()

    @pytest.mark.parametrize("heartbeat", [False, True])
    async def test_stream_sends_terminal_state_and_closes_subscription(
        self, monkeypatch: pytest.MonkeyPatch, app_settings: AppSettings, heartbeat: bool
    ) -> None:
        scope = OwnerScope(17)
        active = ProgressItemResponse(
            status="DL_EPISODE_DOWNLOADING",
            completed=50,
            current_file_size=50,
            total_file_size=100,
            episode=ProgressEpisodeResponse(id=2, title="Episode", status="DOWNLOADING"),
        )
        finished = active.model_copy(
            update={
                "status": "PUBLISHED",
                "episode": active.episode.model_copy(update={"status": "PUBLISHED"}),
            }
        )
        operations = create_autospec(ProgressOperations, instance=True, spec_set=True)
        operations.get_progress.side_effect = [
            {"progressItems": [active]},
            {"progressItems": []},
            {"progressItems": [finished]},
        ]
        pubsub = create_autospec(PubSub, instance=True, spec_set=True)
        pubsub.get_message = AsyncMock(
            return_value=None if heartbeat else {"data": app_settings.redis.progress_pubsub_signal}
        )
        redis = create_autospec(RedisClient, instance=True, spec_set=True)
        redis.async_pubsub.return_value = pubsub
        template = create_autospec(Template, instance=True, spec_set=True)
        template.render.side_effect = ["active", "finished"]
        request = MagicMock(spec=Request)
        request.app.template_engine.get_template.return_value = template
        monkeypatch.setattr(progress_view, "RedisClient", lambda: redis)
        monkeypatch.setattr(progress_view, "ProgressOperations", lambda: operations)

        events = progress_view.progress_events(cast(AppRequest, request), scope, app_settings)
        assert (await anext(events)).data == "active"
        if heartbeat:
            assert (await anext(events)).comment == "keepalive"
        assert (await anext(events)).data == "finished"
        await events.aclose()

        assert operations.get_progress.await_args_list == [
            ((scope,), {}),
            ((scope,), {}),
            ((scope, 2), {}),
        ]
        pubsub.subscribe.assert_awaited_once_with(app_settings.redis.progress_pubsub_ch)
        pubsub.aclose.assert_awaited_once()
