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
    "continuous_runs",
    "saved_attacks",
    "house_hunts",
    "notifications",
    "platform_settings",
    "scans",
    "admin_actions",
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
    "pilot_applications",
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


async def _run(url: str, *statements: str) -> list[tuple[object, ...]]:
    engine = create_async_engine(url, poolclass=NullPool)
    rows: list[tuple[object, ...]] = []
    async with engine.begin() as connection:
        for statement in statements:
            result = await connection.execute(text(statement))
            rows = [tuple(row) for row in result] if result.returns_rows else []
    await engine.dispose()
    return rows


def test_a_bounty_already_live_is_not_handed_to_the_house_hunter() -> None:
    """The house hunter goes at what is listed from now on. Applying its migration to a
    platform with bounties out there must leave them as they are."""
    listed = (
        "INSERT INTO sponsors (email) VALUES ('team@example.com')",
        # One bounty that is live, and one that is a draft.
        "INSERT INTO bounties (sponsor_id, reward_zatoshi, fee_rate, deposit_code, state)"
        " SELECT id, 1000000, 0.1, 'dep-live', 'live' FROM sponsors",
        "INSERT INTO bounties (sponsor_id, reward_zatoshi, fee_rate, deposit_code, state)"
        " SELECT id, 1000000, 0.1, 'dep-draft', 'draft' FROM sponsors",
        "INSERT INTO target_configs (bounty_id, version, config_hash, system_prompt_enc,"
        " tools_enc, model, params_json, entry_points_json)"
        " SELECT id, 1, 'hash-' || state::text, 'x', 'x', 'm', '{}', '[]' FROM bounties",
        "UPDATE bounties SET live_config_id = c.id FROM target_configs c"
        " WHERE c.bounty_id = bounties.id AND bounties.state = 'live'",
    )
    # A break the house found, to be taken back to a schema that has no word for it.
    broken = (
        "INSERT INTO canaries (config_id, type, public_label, rule_json)"
        " SELECT id, 'forbidden_tool', 'rule', '{\"tool\": \"t\"}' FROM target_configs"
        " WHERE config_hash = 'hash-live'",
        "INSERT INTO attempts (bounty_id, config_hash, payload_json, payout_address,"
        " status_token) SELECT id, 'hash-live', '[]', 'a', 't' FROM bounties"
        " WHERE state = 'live'",
        "INSERT INTO findings (attempt_id, bounty_id, config_hash, canary_id, tripped_runs,"
        " state) SELECT a.id, a.bounty_id, a.config_hash, c.id, 5, 'waived'"
        " FROM attempts a, canaries c",
    )
    hunted = (
        "SELECT b.deposit_code, h.config_hash, h.state, h.outcome, h.tries, h.detail"
        " FROM house_hunts h JOIN bounties b ON b.id = h.bounty_id"
    )
    with scratch_database() as url:
        config = alembic_config(url)
        command.upgrade(config, "0024")
        asyncio.run(_run(url, *listed))
        command.upgrade(config, "head")
        assert asyncio.run(_run(url, hunted)) == [
            (
                "dep-live",
                "hash-live",
                "done",
                "stopped",
                0,
                "this config was live before the house hunter was switched on",
            )
        ]
        asyncio.run(_run(url, *broken))
        command.downgrade(config, "0024")
        assert asyncio.run(_run(url, "SELECT state::text FROM findings")) == [("paid",)]
        assert asyncio.run(_names(url, PUBLIC_TABLES)) == (
            TABLES - {"house_hunts", "saved_attacks", "continuous_runs", "pilot_applications"}
        ) | {"alembic_version"}


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
