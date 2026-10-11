"""The re-arm gate: a broken bounty reopens only on a config the paid attack no longer breaks.

A sponsor who has paid for a break uploads a patched config and asks for a
re-arm check. The replay worker replays every paid attack of the bounty against
that config. Only if none of them is a verified break again may the bounty be
funded and activated on it. The paid attack is the regression test.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import (
    Attempt,
    Bounty,
    BountyState,
    Finding,
    RearmCheck,
    RearmState,
)
from agentbounty.models import TargetConfig as TargetConfigRow
from agentbounty.services.bounty import latest_config
from agentbounty.services.errors import ServiceError


async def has_findings(session: AsyncSession, bounty_id: uuid.UUID) -> bool:
    found = await session.scalar(select(Finding.id).where(Finding.bounty_id == bounty_id).limit(1))
    return found is not None


async def check_for(
    session: AsyncSession, bounty_id: uuid.UUID, config_hash: str
) -> RearmCheck | None:
    return await session.scalar(
        select(RearmCheck).where(
            RearmCheck.bounty_id == bounty_id, RearmCheck.config_hash == config_hash
        )
    )


async def require_passed(session: AsyncSession, bounty: Bounty, row: TargetConfigRow) -> None:
    """Raise 409 unless this config may go live: never broken, or re-armed since."""
    if not await has_findings(session, bounty.id):
        return
    check = await check_for(session, bounty.id, row.config_hash)
    if check is None:
        raise ServiceError(
            409,
            "this bounty has been broken; replay the paid attack against the new config"
            f" first (POST /v1/bounties/{bounty.id}/rearm)",
        )
    if check.state is RearmState.FAILED:
        raise ServiceError(409, "the paid attack still breaks this config; patch it again")
    if check.state is not RearmState.PASSED:
        raise ServiceError(409, "the paid attack is still being replayed against this config")


async def request(session: AsyncSession, bounty: Bounty) -> RearmCheck:
    """Queue a replay of the bounty's paid attacks against its working config.

    Asking again for the same config returns the check that already exists:
    a config is checked once, and a config that failed has to be changed.
    """
    if bounty.state is not BountyState.PAUSED or bounty.close_requested_at is not None:
        raise ServiceError(409, "only a paused bounty can be re-armed")
    if not await has_findings(session, bounty.id):
        raise ServiceError(409, "this bounty has not been broken; there is nothing to replay")
    row = await latest_config(session, bounty.id)
    if row is None:
        raise ServiceError(409, "upload a config first")
    broken = await session.scalar(
        select(Finding.id).where(
            Finding.bounty_id == bounty.id, Finding.config_hash == row.config_hash
        )
    )
    if broken:
        raise ServiceError(409, "this config has been broken; upload a patched config first")
    check = await check_for(session, bounty.id, row.config_hash)
    if check is None:
        check = RearmCheck(bounty_id=bounty.id, config_hash=row.config_hash)
        session.add(check)
        await session.flush()
    return check


# --- the worker's side --------------------------------------------------------------------


@dataclass(frozen=True)
class RearmClaim:
    check_id: uuid.UUID
    bounty_id: uuid.UUID
    config_hash: str
    started_at: datetime


async def claim_next(session: AsyncSession) -> RearmClaim | None:
    check = await session.scalar(
        select(RearmCheck)
        .where(RearmCheck.state == RearmState.QUEUED)
        .order_by(RearmCheck.created_at, RearmCheck.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if check is None:
        return None
    check.state = RearmState.RUNNING
    check.started_at = datetime.now(UTC)
    await session.flush()
    return RearmClaim(check.id, check.bounty_id, check.config_hash, check.started_at)


async def paid_attacks(session: AsyncSession, bounty_id: uuid.UUID) -> list[list[dict[str, str]]]:
    """The fills of every attempt that broke the bounty, oldest first."""
    payloads = await session.scalars(
        select(Attempt.payload_json)
        .join(Finding, Finding.attempt_id == Attempt.id)
        .where(Finding.bounty_id == bounty_id)
        .order_by(Finding.verified_at, Finding.id)
    )
    return [list(payload) for payload in payloads]


async def record(
    session: AsyncSession, claim: RearmClaim, *, passed: bool, tripped_runs: int
) -> None:
    """Write the outcome, unless the claim was lost to a worker that replaced this one."""
    check = await session.scalar(
        select(RearmCheck).where(RearmCheck.id == claim.check_id).with_for_update()
    )
    if check is None or check.state is not RearmState.RUNNING:
        return
    if check.started_at != claim.started_at:
        return
    check.state = RearmState.PASSED if passed else RearmState.FAILED
    check.tripped_runs = tripped_runs


async def release(session: AsyncSession, claim: RearmClaim) -> None:
    """Put a check back in the queue after a replay that could not be completed."""
    await session.execute(
        update(RearmCheck)
        .where(
            RearmCheck.id == claim.check_id,
            RearmCheck.state == RearmState.RUNNING,
            RearmCheck.started_at == claim.started_at,
        )
        .values(state=RearmState.QUEUED)
    )


async def requeue_stale(session: AsyncSession, *, older_than: timedelta) -> None:
    """Recover checks whose worker died mid-replay."""
    cutoff = datetime.now(UTC) - older_than
    await session.execute(
        update(RearmCheck)
        .where(RearmCheck.state == RearmState.RUNNING, RearmCheck.started_at < cutoff)
        .values(state=RearmState.QUEUED)
    )
