"""DB-specific module that provides specific operations on the database."""

import logging
from datetime import UTC, datetime
from typing import (
    Callable,
    Generic,
    TypeVar,
    Any,
    TypedDict,
    Sequence,
    ParamSpec,
    cast,
    Literal,
    NamedTuple,
)

from sqlalchemy import (
    select,
    BinaryExpression,
    delete,
    Select,
    update,
    CursorResult,
    func,
    or_,
    Row,
    and_,
    ColumnElement,
)
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import SQLCoreOperations
from sqlalchemy.sql.operators import isnot
from sqlalchemy.sql.roles import ColumnsClauseRole

from src.modules.db.models import BaseModel, User, UserSession, File
from src.modules.db.models.podcasts import Episode, Podcast, Cookie

__all__ = ("UserRepository", "UserSessionRepository")

from src.schemas import PodcastStatistics

ModelT = TypeVar("ModelT", bound=BaseModel)
logger = logging.getLogger(__name__)
P = ParamSpec("P")
RT = TypeVar("RT")
type FilterT = int | str | list[int] | None
type UpdateT = int | str | datetime | None
type CreateT = int | str | datetime | list[dict] | None


class VendorsFilter(TypedDict):
    """Simple structure to filter users by specific params"""

    ids: list[int] | None
    slug: str | None


class ActiveVendorsStat(TypedDict):
    active: int
    inactive: int


class EpisodesStatData(NamedTuple):
    total_count: int = 0
    total_duration: int = 0
    total_file_size: int = 0
    last_created_at: datetime | None = None
    last_published_at: datetime | None = None


class BaseRepository(Generic[ModelT]):
    """Base repository interface."""

    model: type[ModelT]

    def __init__(self, session: AsyncSession) -> None:
        self.session: AsyncSession = session

    async def get(self, instance_id: int, **filters: FilterT) -> ModelT:
        """Selects instance by provided ID"""
        # TODO: research and encapsulate checking on owner
        instance: ModelT | None = await self.first(**(filters | {"id": instance_id}))
        if not instance:
            raise NoResultFound

        return instance

    async def first(self, **filters: FilterT) -> ModelT | None:
        """Selects instance by provided ID"""
        statement = self._prepare_statement(filters=filters)
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

    async def all_paginated(
        self, offset: int = 0, limit: int = 10, sort_by: str = "-created_at", **filters: FilterT
    ) -> tuple[list[ModelT], int]:
        """Get paginated objects with optional filters.

        Args:
            offset: Number of items to skip
            limit: Maximum number of items to return
            **filters: Optional filters to apply

        Returns:
            Tuple of (objects list, total count)
        """
        logger.debug("[DB] Getting paginated %s (offset=%i, limit=%i)", self.model, offset, limit)

        # Prepare base statement for count
        count_filters = filters.copy()
        count_filters_stmts: list[BinaryExpression[bool]] = []
        if (ids := count_filters.pop("ids", None)) and isinstance(ids, list):
            count_filters_stmts.append(self.model.id.in_(ids))

        # Get paginated releases
        statement = self._prepare_statement(filters=filters)
        oder_by = self._sort_criteria(sort_by)
        objects = await self.session.scalars(
            statement.order_by(oder_by).offset(offset).limit(limit)
        )
        total: int = await self.get_total_count(**filters)
        instances: list[ModelT] = list(objects.all())
        logger.debug("[DB] Found %i instances, total: %i", len(instances), total)
        return instances, total

    async def create(self, **value: CreateT) -> ModelT:
        """Creates new instance"""
        logger.debug("[DB] Creating [%s]: %s", self.model.__name__, value)
        instance = self.model(**value)
        self.session.add(instance)
        return instance

    async def get_or_create(self, id_: int, value: dict[str, Any]) -> ModelT:
        """Tries to find an instance by ID and create if it wasn't found"""
        instance = await self.first(id_)
        if instance is None:
            await self.create(**(value | {"id": id_}))
            instance = await self.get(id_)

        return instance

    async def update(self, instance: ModelT, **value: UpdateT) -> None:
        """Just updates the instance with provided update_value."""
        for key, field_value in value.items():
            setattr(instance, key, field_value)

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

    async def update_by_filters(self, filters: dict[str, FilterT], value: dict[str, Any]) -> None:
        """Update the instances by some filters"""
        logger.info("[DB] Updating instances by filter: %s", filters)

        statement = update(self.model).filter_by(**filters)
        result: CursorResult[Any] = cast(
            CursorResult[Any], await self.session.execute(statement, value)
        )
        await self.session.flush()
        logger.info("[DB] Updated %i instances", result.rowcount)

    async def get_total_count(self, **filters: FilterT) -> int:
        """Get total count of instances by filters."""
        logger.debug("[DB] Getting total count of %s: %s", self.model.__name__, filters)
        statement = self._prepare_statement(filters=filters, entities=[func.count(self.model.id)])
        return await self.session.scalar(statement) or 0

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

    def _filter_criteria(self, filter_kwargs) -> ColumnElement[bool]:
        filters: list[BinaryExpression[bool]] = []
        for filter_name, filter_value in filter_kwargs.items():
            field_name, _, criteria = filter_name.partition("__")
            field = getattr(self.model, field_name)
            if criteria in ("eq", ""):
                filters.append(field == filter_value)
            elif criteria == "gt":
                filters.append(field > filter_value)
            elif criteria == "lt":
                filters.append(field < filter_value)
            elif criteria == "is":
                filters.append(field.is_(filter_value))
            elif criteria == "in":
                filters.append(field.in_(filter_value))
            elif criteria == "inarr":
                filters.append(field.contains([filter_value]))
            elif criteria == "icontains":
                filters.append(field.ilike(f"%{filter_value}%"))
            elif criteria == "ne":
                filters.append(field != filter_value)
            else:
                raise NotImplementedError(f"Unexpected criteria: {criteria}")

        return and_(True, *filters)

    def _sort_criteria(self, sort_by: str) -> Callable[[], BinaryExpression[bool]]:
        field = getattr(self.model, sort_by)
        return field.desc() if sort_by.startswith("-") else field.asc()


class UserRepository(BaseRepository[User]):
    """User's repository."""

    model = User

    async def get_by_email(self, email: str) -> User | None:
        """Get user by email (login)."""
        logger.debug("[DB] Getting user by email: %s", email)
        users = await self.all(email=email)
        if not users:
            return None
        return users[0]


class UserSessionRepository(BaseRepository[UserSession]):
    """Browser session rows (public_id in cookie)."""

    model = UserSession

    async def get_active_with_user(self, public_id: str) -> tuple[UserSession, User] | None:
        """Return session and user if cookie id is valid and not expired."""
        now = datetime.now(UTC)
        stmt = (
            select(UserSession, User)
            .join(User, UserSession.user_id == User.id)
            .where(
                UserSession.public_id == public_id,
                UserSession.is_active.is_(True),
                UserSession.expired_at > now,
            )
            .limit(1)
        )
        result = await self.session.execute(stmt)
        row = result.first()
        if not row:
            return None
        return row[0], row[1]

    async def deactivate_by_public_id(self, public_id: str) -> None:
        """Invalidate session on logout (idempotent)."""
        user_session = await self.first(public_id=public_id)
        if user_session is None:
            return

        await self.update(user_session, is_active=False)


class PodcastRepository(BaseRepository[Podcast]):
    """Podcast's repository."""

    model = Podcast

    async def all_with_aggregations(
        self,
        offset: int = 0,
        limit: int = 10,
        sort_by: str = "id",
        **filters: FilterT,
    ) -> tuple[list[Podcast], int]:
        """Get podcasts with aggregated episode statistics (count, duration, size, dates).

        Adds dynamic attributes to Podcast objects:
        - episodes_count: int
        - duration: int (total seconds)
        - total_file_size: int (total bytes)
        - last_publication_date: datetime | None
        - last_download_date: datetime | None
        """
        logger.debug("[DB] Getting podcasts with aggregations: %s", filters)
        filters_dict = dict(filters)

        # Build aggregation query with LEFT JOINs to include podcasts without episodes
        statement = (
            select(
                Podcast,
                func.count(Episode.id).label("episodes_count"),
                func.coalesce(func.sum(Episode.length), 0).label("total_duration"),
                func.coalesce(func.sum(File.size), 0).label("total_size"),
                func.max(Episode.published_at).label("last_published_at"),
                func.max(Episode.created_at).label("last_created_at"),
            )
            .outerjoin(Episode, Podcast.id == Episode.podcast_id)
            .outerjoin(File, Episode.audio_id == File.id)
            .group_by(Podcast.id)
            .offset(offset)
            .limit(limit)
        )

        # Apply filters similar to _prepare_statement logic
        filters_stmts: list[BinaryExpression[bool]] = []
        if (ids := filters_dict.pop("ids", None)) and isinstance(ids, list):
            filters_stmts.append(Podcast.id.in_(ids))

        statement = statement.filter_by(**filters_dict)
        if filters_stmts:
            statement = statement.filter(*filters_stmts)

        order_by = self._sort_criteria(sort_by)
        statement = statement.order_by(order_by)

        result = await self.session.execute(statement)
        rows = result.all()
        podcasts_with_stats = []
        for row in rows:
            podcast: Podcast = row[0]
            podcast.stat = PodcastStatistics(
                episodes_count=row.episodes_count,
                total_duration=row.total_duration,
                total_size=row.total_size,
                last_published_at=row.last_published_at,
                last_created_at=row.last_created_at,
            )
            podcasts_with_stats.append(podcast)

        total = await self.get_total_count(**filters)
        logger.debug(
            "[DB] Found %i podcasts with aggregations, total: %i",
            len(podcasts_with_stats),
            total,
        )
        return podcasts_with_stats, total

    async def update_by_filters(self, filters: dict[str, FilterT], value: dict[str, Any]) -> None:
        """Update the instances by some filters"""
        logger.info("[DB] Updating instances by filter: %s", filters)
        statement = update(self.model).filter(self._filter_criteria(filters))
        result: CursorResult[Any] = cast(
            CursorResult[Any], await self.session.execute(statement, value)
        )
        await self.session.flush()
        logger.info("[DB] Updated %i instances", result.rowcount)


class EpisodeRepository(BaseRepository[Episode]):
    """Podcast's repository."""

    model = Episode

    async def all(self, **filters: FilterT) -> list[Episode]:
        """Get all episodes, but with extended filters' logic."""
        logger.debug("[DB] Getting all episodes: %s", filters)
        statement = select(self.model).outerjoin(File, Episode.audio_id == File.id)

        def process_suffix(statement: Select, field_name: str, suffix: str, value: Any) -> Select:
            field = getattr(self.model, field_name)
            match suffix:
                case "ne":
                    statement = statement.filter(field != value)
                case "gt":
                    statement = statement.filter(field > value)
                case "lt":
                    statement = statement.filter(field < value)
                case "lte":
                    statement = statement.filter(field <= value)
                case "gte":
                    statement = statement.filter(field >= value)
                case "isnot":
                    statement = statement.filter(isnot(field, value))
                case _:
                    logger.warning(
                        "[DB] Unknown filter suffix '%s' | field: '%s'", suffix, field_name
                    )
                    statement = statement

            return statement

        for filter_key, filter_value in filters.items():
            match filter_key:
                case "search":
                    statement = statement.filter(
                        or_(
                            Episode.title.ilike(f"%{filter_value}%"),
                            Episode.description.ilike(f"%{filter_value}%"),
                        )
                    )

                case "podcast_id":
                    statement = statement.filter(Episode.podcast_id == filter_value)

                case "statuses":
                    statuses_str = str(filter_value)
                    statuses: list[str] = [st.upper() for st in statuses_str.split(",")]
                    statement = statement.filter(Episode.status.in_(statuses))

                case "audio__size__gte":
                    if not isinstance(filter_value, int):
                        raise ValueError("Invalid value for 'audio__size__gte'")

                    statement = statement.filter(File.size >= int(filter_value))

                case "audio__size__lte":
                    if not isinstance(filter_value, int):
                        raise ValueError("Invalid value for 'audio__size__lte'")

                    statement = statement.filter(File.size <= int(filter_value))

                case "podcast__name":
                    statement = statement.join(Podcast, Episode.podcast_id == Podcast.id).filter(
                        Podcast.name.ilike(f"%{filter_value}%")
                    )

                case _:
                    field_name, _, suffix = filter_key.partition("__")
                    if suffix:
                        statement = process_suffix(
                            statement=statement,
                            field_name=field_name,
                            suffix=suffix,
                            value=filter_value,
                        )
                    else:
                        statement = statement.filter(getattr(Episode, field_name) == filter_value)

        result = await self.session.execute(statement.order_by(Episode.created_at.desc()))
        return [row[0] for row in result.fetchall()]

    async def update_by_filters(self, filters: dict[str, FilterT], value: dict[str, Any]) -> None:
        """Update the instances by some filters"""
        logger.info("[DB] Updating instances by filter: %s", filters)
        statement = update(self.model).filter(self._filter_criteria(filters))
        result: CursorResult[Any] = cast(
            CursorResult[Any], await self.session.execute(statement, value)
        )
        await self.session.flush()
        logger.info("[DB] Updated %i instances", result.rowcount)

    #
    # async def _prepare_filters(self, **filters: FilterT) -> list[Statement]:
    #     """Get all episodes, but with extended filters' logic."""
    #     logger.debug("[DB] Getting filters for episodes: %s", filters)
    #
    #     def process_suffix(field_name: str, suffix: str, value: Any) -> Any:
    #         field = getattr(self.model, field_name)
    #         match suffix:
    #             case "ne":
    #                 statement = statement.filter(field != value)
    #             case "gt":
    #                 statement = statement.filter(field > value)
    #             case "lt":
    #                 statement = statement.filter(field < value)
    #             case "lte":
    #                 statement = statement.filter(field <= value)
    #             case "gte":
    #                 statement = statement.filter(field >= value)
    #             case "isnot":
    #                 statement = statement.filter(isnot(field, value))
    #             case _:
    #                 logger.warning(
    #                     "[DB] Unknown filter suffix '%s' | field: '%s'", suffix, field_name
    #                 )
    #                 statement = statement
    #
    #         return statement
    #
    #     for filter_key, filter_value in filters.items():
    #         match filter_key:
    #             case "search":
    #                 statement = statement.filter(
    #                     or_(
    #                         Episode.title.ilike(f"%{filter_value}%"),
    #                         Episode.description.ilike(f"%{filter_value}%"),
    #                     )
    #                 )
    #
    #             case "podcast_id":
    #                 statement = statement.filter(Episode.podcast_id == filter_value)
    #
    #             case "statuses":
    #                 statuses_str = str(filter_value)
    #                 statuses: list[str] = [st.upper() for st in statuses_str.split(",")]
    #                 statement = statement.filter(Episode.status.in_(statuses))
    #
    #             case "audio__size__gte":
    #                 if not isinstance(filter_value, int):
    #                     raise ValueError("Invalid value for 'audio__size__gte'")
    #
    #                 statement = statement.filter(File.size >= int(filter_value))
    #
    #             case "audio__size__lte":
    #                 if not isinstance(filter_value, int):
    #                     raise ValueError("Invalid value for 'audio__size__lte'")
    #
    #                 statement = statement.filter(File.size <= int(filter_value))
    #
    #             case "podcast__name":
    #                 statement = statement.join(Podcast, Episode.podcast_id == Podcast.id).filter(
    #                     Podcast.name.ilike(f"%{filter_value}%")
    #                 )
    #
    #             case _:
    #                 field_name, _, suffix = filter_key.partition("__")
    #                 if suffix:
    #                     statement = process_suffix(
    #                         statement=statement,
    #                         field_name=field_name,
    #                         suffix=suffix,
    #                         value=filter_value,
    #                     )
    #                 else:
    #                     statement = statement.filter(getattr(Episode, field_name) == filter_value)
    #
    #     result = await self.session.execute(statement.order_by(Episode.created_at.desc()))
    #     return [row[0] for row in result.fetchall()]

    async def get_last(
        self,
        podcast_id: int,
        field: Literal["created_at", "published_at"],
    ) -> Episode | None:
        order_by = getattr(Episode, field)
        statement = self._prepare_statement(filters={"podcast_id": podcast_id}).order_by(
            order_by.desc()
        )
        result = await self.session.execute(statement)
        row: Sequence[Episode] | None = result.fetchone()
        return row[0] if row else None

    async def get_aggregated(self, podcast_id: int | None = None) -> EpisodesStatData:
        """Get aggregated stats for episodes, optionally filtered by podcast_id."""
        filters: dict[str, FilterT] = {}
        if podcast_id:
            filters["podcast_id"] = podcast_id

        statement = self._prepare_statement(
            filters=filters,
            entities=[
                func.max(Episode.created_at).label("last_created_at"),
                func.max(Episode.published_at).label("last_published_at"),
                func.count(Episode.id).label("total_count"),
                func.sum(Episode.length).label("total_duration"),
                func.sum(File.size).label("total_size"),
            ],
        ).join(File, Episode.audio_id == File.id)

        result = await self.session.execute(statement)
        row_data: Row | None = result.fetchone()
        if not row_data:
            return EpisodesStatData()

        return EpisodesStatData(
            total_count=row_data.total_count,
            total_duration=row_data.total_duration,
            total_file_size=row_data.total_size,
            last_created_at=row_data.last_created_at,
            last_published_at=row_data.last_published_at,
        )


class CookieRepository(BaseRepository[Cookie]):
    """
    Cookie Repository class
    """

    model = Cookie


class FileRepository(BaseRepository[File]):
    """
    File Repository class
    """

    model = File

    async def first_by_access_token(self, access_token: str) -> File | None:
        """Lookup media file by public URL token (/m/{token}/, /r/{token}/)."""
        statement = select(File).filter_by(access_token=access_token)
        result = await self.session.execute(statement)
        row = result.first()
        return row[0] if row else None

    async def copy(self, file_id: int, owner_id: int, available: bool = True) -> File:
        source_file: File = await self.get(file_id)
        logger.debug("Copying file: source %s | owner_id %s", source_file, owner_id)
        return await self.create(
            type=source_file.type,
            owner_id=owner_id,
            available=available,
            path=source_file.path,
            size=source_file.size,
            source_url=source_file.source_url,
        )
