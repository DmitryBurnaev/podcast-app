from collections.abc import AsyncIterator

from litestar import get
from litestar.di import NamedDependency
from litestar.response import ServerSentEvent
from litestar.response.sse import ServerSentEventMessage

from src.modules.common.constants import get_episode_status_label
from src.modules.common.types import AppRequest, OwnerScope
from src.modules.services.progress import ProgressOperations
from src.modules.services.redis import RedisClient
from src.modules.views.base import BaseViewController
from src.settings.app import AppSettings


async def progress_events(
    request: AppRequest, user_scope: OwnerScope, settings: AppSettings
) -> AsyncIterator[ServerSentEventMessage]:
    """Send owned progress and terminal states; release the Redis subscription on disconnect."""
    pubsub = RedisClient().async_pubsub(ignore_subscribe_messages=True)
    channel = settings.redis.progress_pubsub_ch
    previous: set[int] = set()
    try:
        await pubsub.subscribe(channel)
        while True:
            active = (await ProgressOperations().get_progress(user_scope))["progressItems"]
            active_ids = {item.episode.id for item in active}
            completed = []
            for episode_id in previous - active_ids:
                completed.extend(
                    (await ProgressOperations().get_progress(user_scope, episode_id))[
                        "progressItems"
                    ]
                )
            previous = active_ids
            engine = request.app.template_engine
            if engine is None:
                raise RuntimeError("Template engine is not configured")
            template = engine.get_template("snippets/episode_status.html")
            html = "".join(
                template.render(
                    episode=item.episode,
                    progress=item,
                    oob=True,
                    get_episode_status_label=get_episode_status_label,
                )
                for item in [*active, *completed]
            )
            if html:
                yield ServerSentEventMessage(event="progress", data=html)
            while True:
                message = await pubsub.get_message(timeout=15.0)
                if message is None:
                    yield ServerSentEventMessage(comment="keepalive", data=None)
                    # The worker may publish before its transaction commits.
                    # Recheck on the next heartbeat to catch that final transition.
                    break
                elif message.get("data") in (
                    settings.redis.progress_pubsub_signal,
                    settings.redis.progress_pubsub_signal.encode(),
                ):
                    break
    finally:
        await pubsub.aclose()


class ProgressEventsController(BaseViewController):
    @get("/episodes/progress/events/")
    async def events(
        self,
        request: AppRequest,
        user_scope: NamedDependency[OwnerScope],
        settings: NamedDependency[AppSettings],
    ) -> ServerSentEvent:
        return ServerSentEvent(progress_events(request, user_scope, settings))
