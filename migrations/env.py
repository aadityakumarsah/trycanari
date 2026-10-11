"""Alembic environment: runs migrations over the async engine.

The URL comes from DATABASE_URL, unless the caller set "sqlalchemy.url" on the
config (the tests do, to migrate a scratch database).
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from agentbounty.db import engine_options
from agentbounty.models import Base
from agentbounty.settings import load_settings

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def database_url() -> str:
    """Where to migrate: the URL the caller set, else DIRECT_URL, else DATABASE_URL.

    Migrations want a real session (they take locks and run DDL in one
    transaction), so a direct or session-mode connection is preferred over a
    transaction-mode pooler.
    """
    override = config.get_main_option("sqlalchemy.url")
    if override:
        return override
    settings = load_settings()
    return settings.direct_url or settings.database_url


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of connecting."""
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    url, connect_args = engine_options(database_url())
    engine = create_async_engine(url, poolclass=NullPool, connect_args=connect_args)
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
