import enum
import asyncio
import logging

from rq.job import Job
from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.db import SASessionUOW, close_database, initialize_database
from src.modules.services.redis import RedisClient, close_async_redis_connection
from src.modules.utils.processing import TaskContext
from src.settings.app import AppSettings, get_app_settings

logger = logging.getLogger(__name__)


class TaskResultCode(enum.StrEnum):
    SUCCESS = "SUCCESS"
    SKIP = "SKIP"
    ERROR = "ERROR"
    CANCEL = "CANCEL"
    PENDING = "PENDING"


class RQTask:
    """Base class for RQ tasks implementation."""

    def __init__(self, db_session: AsyncSession | None = None):
        self.db_session: AsyncSession | None = db_session
        self.task_context: TaskContext | None = None
        self.settings: AppSettings = get_app_settings()

    async def run(self, *args, **kwargs):
        """We need to override this method to implement main task logic"""
        raise NotImplementedError

    def __call__(self, *args, **kwargs) -> TaskResultCode:
        logger.info("==== STARTED task %s ====", self.name)

        # RQ runs each job in asyncio.run(); async engine must bind to that loop, not the
        # worker's outer lifespan loop (see worker.py DbStartMode.VERIFY).
        async def _run_with_db() -> TaskResultCode:
            await initialize_database()
            try:
                return await self._perform_and_run(*args, **kwargs)
            finally:
                await close_database()
                await close_async_redis_connection()

        finish_code = asyncio.run(_run_with_db())
        logger.info("==== SUCCESS task %s | code %s ====", self.name, finish_code)
        return finish_code

    def __eq__(self, other):
        """Can be used for test's simplify"""
        return isinstance(other, self.__class__) and self.__class__ == other.__class__

    async def _perform_and_run(self, *args, **kwargs) -> TaskResultCode:
        """Allows calling `self.run` in transaction block with catching any exceptions"""

        self.task_context = self._prepare_task_context(*args, **kwargs)

        try:
            async with SASessionUOW() as uow:
                self.db_session = uow.session
                result = await self.run(*args, **kwargs)
                await self.db_session.commit()

        except Exception as exc:
            if self.db_session:
                await self.db_session.rollback()

            result = TaskResultCode.ERROR
            logger.exception("Couldn't perform task %s | error %r", self.name, exc)

        return result

    @property
    def name(self):
        return self.__class__.__name__

    @classmethod
    def get_subclasses(cls):
        for subclass in cls.__subclasses__():
            yield from subclass.get_subclasses()
            yield subclass

    @classmethod
    def get_job_id(cls, *task_args, **task_kwargs) -> str:
        kw_pairs = [f"{key}={value}" for key, value in task_kwargs.items()]
        return f"{cls.__name__.lower()}_{'_'.join(map(str, task_args))}_{'_'.join(kw_pairs)}_"

    @classmethod
    def cancel_task(cls, *task_args, **task_kwargs) -> None:
        job_id = cls.get_job_id(*task_args, **task_kwargs)
        logger.warning("Trying to cancel task %s", job_id)
        try:
            job = Job.fetch(job_id, connection=RedisClient().sync_redis)
            job.cancel()
        except Exception as exc:
            logger.exception("Couldn't cancel task %s: %r", job_id, exc)
        else:
            logger.info("Canceled task %s", job_id)

    def _prepare_task_context(self, *args, **kwargs) -> TaskContext:
        return TaskContext(job_id=self.get_job_id(*args, **kwargs))
