import logging

import sqlalchemy as sa
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    create_async_engine,
    async_sessionmaker,
    AsyncEngine,
    close_all_sessions,
)

from src.exceptions import DatabaseError
from src.settings.db import get_db_settings
from src.utils import singleton

logger = logging.getLogger(__name__)
type sm_type = async_sessionmaker[AsyncSession]


@singleton
class AsyncDBConnectors:
    """
    Singleton class that handles database connections
    (default session factory and prepared settings).
    """

    def __init__(self) -> None:
        self.engine: AsyncEngine | None = None
        self.session_factory: sm_type | None = None
        self.settings = get_db_settings()
        self.exc: Exception | None = None

    async def init_connection(self) -> None:
        """Initialize database engine and session factory"""
        logger.info("[DB] Initializing database engine and session factory...")

        try:
            extra_kwargs: dict[str, str | int] = {"echo": self.settings.echo}
            if self.settings.pool_min_size:
                extra_kwargs["pool_size"] = self.settings.pool_min_size

            if self.settings.pool_max_size:
                extra_kwargs["max_overflow"] = self.settings.pool_max_size - (
                    self.settings.pool_min_size or 5
                )

            engine = create_async_engine(self.settings.database_dsn, **extra_kwargs)
            session_factory = async_sessionmaker(
                bind=engine,
                expire_on_commit=False,
                class_=AsyncSession,
            )

            self.engine = engine
            self.session_factory = session_factory
            await self._ping_connection()
            logger.info("[DB] Database engine and session factory initialized successfully")

        except Exception as e:
            logger.error("[DB] Failed to initialize database: %r", e)
            await self.close_connection()
            raise

    async def _ping_connection(self) -> None:
        """Try to acquire a connection and execute a simple query to ensure DB is alive"""
        logger.info("[DB] Pinging connection to database...")
        if not self.engine:
            logger.error("[DB] Engine is not initialized, cannot ping database")
            raise RuntimeError("Engine is not initialized, cannot ping database")

        try:
            async with self.engine.connect() as conn:
                await conn.execute(sa.text("SELECT 1"))

        except Exception as exc:
            logger.error("[DB] Failed to ping database: %r", exc)
            self.exc = exc
            raise DatabaseError("Failed to ping database") from exc

        else:
            logger.info("[DB] Connection to database pinged successfully")

    async def close_connection(self) -> None:
        """Close database engine and session factory"""
        logger.debug("[DB] Closing database connection...")

        try:
            if self.session_factory:
                logger.debug("[DB] Closing all async sessions...")
                await close_all_sessions()

            if self.engine:
                await self.engine.dispose(close=True)

            if self.exc:
                logger.warning("[DB] Database engine closed emergency")
            else:
                logger.info("[DB] Database engine closed successfully")

        except Exception as exc:
            logger.error("[DB] Failed to close database connection: %r", exc)
            raise


_db_connectors = AsyncDBConnectors()


def get_session_factory() -> sm_type:
    """Get the session factory instance from current context"""
    session_factory = _db_connectors.session_factory
    if session_factory is None:
        logger.warning("[DB] Session factory not initialized!")
        raise RuntimeError(
            "Session factory not initialized. Make sure lifespan is properly set up."
        )

    return session_factory


async def initialize_database() -> None:
    """Initialize database engine and session factory in current context"""
    await _db_connectors.init_connection()


async def close_database() -> None:
    """Close database engine and cleanup resources from current context"""
    await _db_connectors.close_connection()
