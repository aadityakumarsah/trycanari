"""The ledger: the only record of money, and the only source of a bounty's balance.

Entries are appended and never changed. Nothing stores a balance; it is always
the sum of a bounty's entries.
"""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import LedgerEntry, LedgerKind


async def append_entry(
    session: AsyncSession,
    *,
    bounty_id: uuid.UUID,
    kind: LedgerKind,
    amount_zatoshi: int,
    ref_id: uuid.UUID,
) -> LedgerEntry:
    """Append one entry inside the caller's transaction.

    The entry is flushed, so constraint violations surface here, but not
    committed: it stands or falls with whatever else the caller is doing.
    Deposits are positive; payouts, fees and refunds are negative.
    """
    # bool is a subclass of int, and a float would be silently truncated by the driver.
    if type(amount_zatoshi) is not int:
        raise TypeError(f"amount_zatoshi must be an int, not {type(amount_zatoshi).__name__}")
    entry = LedgerEntry(
        bounty_id=bounty_id, kind=kind, amount_zatoshi=amount_zatoshi, ref_id=ref_id
    )
    session.add(entry)
    await session.flush()
    return entry


async def bounty_balance(session: AsyncSession, bounty_id: uuid.UUID) -> int:
    """The bounty's balance in zatoshi: the sum of its ledger entries."""
    total = await session.scalar(
        select(func.coalesce(func.sum(LedgerEntry.amount_zatoshi), 0)).where(
            LedgerEntry.bounty_id == bounty_id
        )
    )
    # SUM over BIGINT is NUMERIC in PostgreSQL; the value is integral, so this is exact.
    return int(total or 0)
