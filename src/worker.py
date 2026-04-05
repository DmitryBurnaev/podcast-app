import asyncio
import sys
import logging
import logging.config

from redis import Redis
from rq import Worker
import sentry_sdk
from sentry_sdk.integrations.rq import RqIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from src.main import DbStartMode, lifespan
from src.settings.app import AppSettings, get_app_settings


async def run_worker():
    """Runs RQ worker for consuming background tasks (like downloading providers tracks)"""
    settings: AppSettings = get_app_settings()
    logging.config.dictConfig(dict(settings.log.dict_config))

    if settings.sentry_dsn:
        sentry_logging = LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
        sentry_sdk.init(settings.sentry_dsn, integrations=[RqIntegration(), sentry_logging])

    # Must match PodcastApp.rq_queue (settings.rq_queue_name); CLI args override for multi-queue setups.
    queue_names = sys.argv[1:] or [settings.rq_queue_name]

    async with lifespan(
        settings,
        start_msg_suffix="background workers (RQ)",
        db_start_mode=DbStartMode.VERIFY,
    ):
        Worker(queue_names, connection=Redis(*settings.redis.connection_tuple)).work()


if __name__ == "__main__":
    asyncio.run(run_worker())
