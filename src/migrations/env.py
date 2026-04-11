import asyncio
import logging
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from alembic import context
from alembic.script import ScriptDirectory

from src.settings.db import get_db_settings
from src.modules.db.models import BaseModel

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config
logger = logging.getLogger("alembic.env")


# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

db_settings = get_db_settings()

target_metadata = BaseModel.metadata
config.set_main_option("sqlalchemy.url", db_settings.database_dsn)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """In this scenario we need to create an Engine
    and associate a connection with the context.

    """

    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""

    asyncio.run(run_async_migrations())


def process_revision_directives(context, revision, directives) -> None:  # type: ignore
    """
    Allows to form correct revision id
    like:
        0001_initial.py
        0002_second.py
        ...
    """
    # extract Migration
    migration_script = directives[0]
    if migration_script.upgrade_ops.is_empty():
        directives[:] = []
        logger.info("No changes in schema detected.")

    # extract current head revision
    head_revision = ScriptDirectory.from_config(context.config).get_current_head()

    if head_revision is None:
        # edge case with first migration
        new_rev_id = 1
    else:
        # default branch with incrementation
        last_rev_id = int(head_revision.lstrip("0"))
        new_rev_id = last_rev_id + 1

    # fill zeros up to 4 digits: 1 -> 0001
    migration_script.rev_id = "{0:04}".format(new_rev_id)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
