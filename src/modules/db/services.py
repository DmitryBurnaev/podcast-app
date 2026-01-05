import logging
from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession

from src.modules.db import session as db_session

logger = logging.getLogger(__name__)


class SASessionUOW:
    """
    Unit Of Work around SQLAlchemy-session related items: repositories, ops

    This UOW can work in two modes:
    1. Standalone mode: creates its own session from session factory
    2. Dependency mode: accepts a session from FastAPI dependency injection

    In both modes, it provides explicit transaction control for atomic operations.

    Examples:
        # Standalone mode
        async with SASessionUOW() as uow:
            user_repo = UserRepository(session=uow.session)
            token_repo = TokenRepository(session=uow.session)

            user = await user_repo.create(user_data)
            token = await token_repo.create(token_data)
            uow.mark_for_commit()

        # Dependency mode
        async def endpoint(uow: SASessionUOW = Depends(get_uow_with_session)):
            async with uow:
                vendor_repo = VendorRepository(session=uow.session)
                vendors = await vendor_repo.all()
                uow.mark_for_commit()
    """

    def __init__(self, session: AsyncSession | None = None) -> None:
        """
        Initialize UOW with optional session.

        Args:
            session: If provided, uses this session (dependency injection mode)
                    If None, creates new session from factory (standalone mode)
        """
        self.__need_to_commit: bool = False
        self.__owns_session: bool = False
        if session is None:
            # Standalone mode: create new session
            session_factory = db_session.get_session_factory()
            self.__session: AsyncSession = session_factory()
            self.__owns_session = True
        else:
            # Dependency mode: use provided session
            self.__session = session

    async def __aenter__(self) -> Self:
        """Enter transaction context and start transaction if needed."""
        logger.debug("[DB] Entering UOW transaction block")

        # Start transaction if we own the session or if no transaction is active
        if self.__owns_session or not self.__session.in_transaction():
            await self.__session.begin()
            logger.debug("[DB] Started new transaction")

        return self

    async def __aexit__(
        self,
        exc_type: type[Exception] | None,
        exc_val: Exception | None,
        exc_tb: TracebackType | None,
    ) -> None:
        """Exit transaction context with proper cleanup."""
        if not self.__session:
            logger.debug("[DB] Session already closed")
            return

        try:
            # Flush any pending changes
            await self.__session.flush()

            # Handle transaction based on ownership and commit flag
            if self.__owns_session:
                # We own the session - handle commit/rollback
                if self.__need_to_commit and exc_type is None:
                    await self.commit()

                elif exc_type is not None:
                    await self.rollback()

                else:
                    # No explicit commit needed, but no error - commit by default
                    await self.commit()

                await self.__session.close()
                logger.debug("[DB] Session closed")

            else:
                # Dependency mode - let the dependency handle session lifecycle,
                # but we can still control transaction
                if self.__need_to_commit and exc_type is None:
                    await self.commit()

                elif exc_type is not None:
                    await self.rollback()

                # Don't close session - dependency will handle it

        except Exception as exc:
            logger.error("[DB] Error during UOW cleanup: %r", exc)
            if self.__owns_session:
                await self.__session.close()

            raise

    @property
    def session(self) -> AsyncSession:
        """Provide the current session for repository operations."""
        return self.__session

    async def commit(self) -> None:
        """Explicitly commit the current transaction."""
        try:
            logger.debug("[DB] Committing transaction...")
            await self.session.commit()
            self.__need_to_commit = False
            logger.debug("[DB] Transaction committed successfully")
        except Exception as exc:
            logger.error("[DB] Failed to commit transaction", exc_info=exc)
            await self.rollback()
            raise exc

    async def rollback(self) -> None:
        """Explicitly rollback the current transaction."""
        try:
            logger.debug("[DB] Rolling back transaction...")
            await self.session.rollback()
            self.__need_to_commit = False
            logger.debug("[DB] Transaction rolled back successfully")
        except Exception as exc:
            logger.error("[DB] Failed to rollback transaction", exc_info=exc)
            raise exc

    @property
    def need_to_commit(self) -> bool:
        """Check if transaction needs to be committed."""
        return self.__need_to_commit

    @need_to_commit.setter
    def need_to_commit(self, value: bool) -> None:
        """Set whether transaction should be committed on exit."""
        self.__need_to_commit = value

    @property
    def owns_session(self) -> bool:
        """Check if UOW owns the session (standalone mode)."""
        return self.__owns_session

    def mark_for_commit(self) -> None:
        """Convenience method to mark transaction for commit."""
        self.__need_to_commit = True
