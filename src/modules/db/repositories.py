"""DB-specific module that provides specific operations on the database."""

import logging
from typing import (
    Generic,
    TypeVar,
    Any,
    TypedDict,
    Sequence,
    ParamSpec,
    cast,
)

from sqlalchemy import select, BinaryExpression, delete, Select, update, CursorResult
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import SQLCoreOperations
from sqlalchemy.sql.roles import ColumnsClauseRole

from src.modules.db.models import BaseModel, User

__all__ = ("UserRepository",)
ModelT = TypeVar("ModelT", bound=BaseModel)
logger = logging.getLogger(__name__)
P = ParamSpec("P")
RT = TypeVar("RT")
type FilterT = int | str | list[int] | None


class VendorsFilter(TypedDict):
    """Simple structure to filter users by specific params"""

    ids: list[int] | None
    slug: str | None


class ActiveVendorsStat(TypedDict):
    active: int
    inactive: int


class BaseRepository(Generic[ModelT]):
    """Base repository interface."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session: AsyncSession = session

    async def get(self, instance_id: int) -> ModelT:
        """Selects instance by provided ID"""
        instance: ModelT | None = await self.first(instance_id)
        if not instance:
            raise NoResultFound

        return instance

    async def first(self, instance_id: int) -> ModelT | None:
        """Selects instance by provided ID"""
        statement = select(self.model).filter_by(id=instance_id)
        result = await self.session.execute(statement)
        row: Sequence[ModelT] | None = result.fetchone()
        if not row:
            return None

        return row[0]

    async def all(self, **filters: FilterT) -> list[ModelT]:
        """Selects instances from DB"""
        statement = self._prepare_statement(filters=filters)
        result = await self.session.execute(statement)
        return [row[0] for row in result.fetchall()]

    async def create(self, value: dict[str, Any]) -> ModelT:
        """Creates new instance"""
        logger.debug("[DB] Creating [%s]: %s", self.model.__name__, value)
        instance = self.model(**value)
        self.session.add(instance)
        return instance

    async def get_or_create(self, id_: int, value: dict[str, Any]) -> ModelT:
        """Tries to find an instance by ID and create if it wasn't found"""
        instance = await self.first(id_)
        if instance is None:
            await self.create(value | {"id": id_})
            instance = await self.get(id_)

        return instance

    async def update(self, instance: ModelT, **value: dict[str, Any]) -> None:
        """Just updates the instance with provided update_value."""
        for key, value in value.items():
            setattr(instance, key, value)

        self.session.add(instance)

    async def delete(self, instance: ModelT) -> None:
        """Remove the instance from the DB."""
        await self.session.delete(instance)

    async def delete_by_ids(self, removing_ids: Sequence[int]) -> None:
        """Remove the instances from the DB."""
        statement = delete(self.model).filter(self.model.id.in_(removing_ids))
        await self.session.execute(statement)

    async def update_by_ids(self, updating_ids: Sequence[int], value: dict[str, Any]) -> None:
        """Update the instances by their IDs"""
        logger.info("[DB] Updating %i instances: %r", len(updating_ids), updating_ids)
        statement = update(self.model).filter(self.model.id.in_(updating_ids))
        result: CursorResult[Any] = cast(
            CursorResult[Any], await self.session.execute(statement, value)
        )
        await self.session.flush()
        logger.info("[DB] Updated %i instances", result.rowcount)

    def _prepare_statement(
        self,
        filters: dict[str, FilterT],
        entities: list[ColumnsClauseRole | SQLCoreOperations[Any]] | None = None,
    ) -> Select[tuple[ModelT]]:
        filters_stmts: list[BinaryExpression[bool]] = []
        if (ids := filters.pop("ids", None)) and isinstance(ids, list):
            filters_stmts.append(self.model.id.in_(ids))

        statement = select(*entities) if entities is not None else select(self.model)
        statement = statement.filter_by(**filters)
        if filters_stmts:
            statement = statement.filter(*filters_stmts)

        return statement


class UserRepository(BaseRepository[User]):
    """User's repository."""

    model = User

    async def get_by_username(self, username: str) -> User | None:
        """Get user by username"""

        logger.debug("[DB] Getting user by username: %s", username)
        users = await self.all(username=username)
        if not users:
            return None

        return users[0]
