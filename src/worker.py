import asyncio
import sys
import logging
import logging.config

from redis import Redis
from rq import Worker
import sentry_sdk
from sentry_sdk.integrations.rq import RqIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from src.main import lifespan
from src.settings.app import AppSettings, get_app_settings


async def run_worker():
    """Runs RQ worker for consuming background tasks (like downloading providers tracks)"""
    settings: AppSettings = get_app_settings()
    logging.config.dictConfig(dict(settings.log.dict_config))

    if settings.sentry_dsn:
        sentry_logging = LoggingIntegration(level=logging.INFO, event_level=logging.ERROR)
        sentry_sdk.init(settings.sentry_dsn, integrations=[RqIntegration(), sentry_logging])

    queues = sys.argv[1:] or ["default"]

    async with lifespan(settings, start_msg_suffix="background workers (RQ)"):
        Worker(queues, connection=Redis(*settings.redis.connection_tuple)).work()


if __name__ == "__main__":
    asyncio.run(run_worker())
