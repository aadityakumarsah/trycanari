from __future__ import annotations

import asyncio

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from agentbounty.models import Base

from .conftest import alembic_config, scratch_database

TABLES = {
    "referral_codes",
    "sponsors",
    "bounties",
    "target_configs",
    "canaries",
    "attempts",
    "runs",
    "findings",
    "deposits",
    "payouts",
    "ledger_entries",
    "refunds",
    "rearm_checks",
    "payment_audit",
}
ENUM_TYPES = {
    "bounty_state",
    "canary_type",
    "attempt_state",
    "finding_state",
    "deposit_state",
    "payout_state",
    "ledger_kind",
    "rearm_state",
    "payment_action",
}


async def _names(url: str, query: str) -> set[str]:
    engine = create_async_engine(url, poolclass=NullPool)
    async with engine.connect() as connection:
        names = set((await connection.scalars(text(query))).all())
    await engine.dispose()
    return names


PUBLIC_TABLES = "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
PUBLIC_ENUMS = "SELECT typname FROM pg_type WHERE typtype = 'e'"


def test_migration_applies_to_a_clean_database_and_reverses() -> None:
    with scratch_database() as url:
        config = alembic_config(url)
        command.upgrade(config, "head")
        assert asyncio.run(_names(url, PUBLIC_TABLES)) == TABLES | {"alembic_version"}
        assert asyncio.run(_names(url, PUBLIC_ENUMS)) == ENUM_TYPES

        command.downgrade(config, "base")
        assert asyncio.run(_names(url, PUBLIC_TABLES)) == {"alembic_version"}
        assert asyncio.run(_names(url, PUBLIC_ENUMS)) == set()

        command.upgrade(config, "head")
        assert asyncio.run(_names(url, PUBLIC_TABLES)) == TABLES | {"alembic_version"}


async def test_every_table_has_row_level_security(session: AsyncSession) -> None:
    rows = await session.execute(
        text(
            "SELECT c.relname, c.relrowsecurity FROM pg_class c"
            " JOIN pg_namespace n ON n.oid = c.relnamespace"
            " WHERE n.nspname = 'public' AND c.relkind = 'r'"
        )
    )
    secured = {name: enabled for name, enabled in rows}
    assert set(secured) == TABLES | {"alembic_version"}
    assert all(secured.values()), [name for name, enabled in secured.items() if not enabled]
    # No policies: nothing but the owner, which the service connects as, gets in.
    assert (
        await session.scalar(text("SELECT count(*) FROM pg_policies WHERE schemaname = 'public'"))
        == 0
    )


async def test_models_and_migrations_describe_the_same_schema(session: AsyncSession) -> None:
    def diff(connection: Connection) -> list[object]:
        return list(compare_metadata(MigrationContext.configure(connection), Base.metadata))

    connection = await session.connection()
    assert await connection.run_sync(diff) == []
