"""Baseline scans: the playbook run against a sponsor's config, once per config.

A scan answers "would the obvious attacks already work?" before a bounty is
funded. Each probe is replayed once, which is cheap and enough to show a
weakness; it is not the five-replay standard a paid break is held to, and the
results say so.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import Bounty, Scan
from agentbounty.services import bounty as bounty_service
from agentbounty.services.crypto import Cipher
from agentbounty.services.errors import ServiceError

RESULTS = "scan.results"


async def latest(session: AsyncSession, bounty: Bounty) -> tuple[Scan | None, str | None]:
    """The scan of the bounty's current config, if one was asked for, and that config's hash."""
    row = await bounty_service.latest_config(session, bounty.id)
    if row is None:
        return None, None
    found = await session.scalar(
        select(Scan).where(Scan.bounty_id == bounty.id, Scan.config_hash == row.config_hash)
    )
    return found, row.config_hash


async def request(session: AsyncSession, bounty: Bounty) -> Scan:
    """Queue a scan of the bounty's current config. Asking again returns the same one."""
    row = await bounty_service.latest_config(session, bounty.id)
    if row is None:
        raise ServiceError(409, "describe the agent before scanning it")
    if not await bounty_service.canary_rows(session, row.id):
        raise ServiceError(409, "add a canary first: a scan needs something to trip")
    found = await session.scalar(
        select(Scan).where(Scan.bounty_id == bounty.id, Scan.config_hash == row.config_hash)
    )
    if found is not None:
        if found.state == "error":
            # The platform failed it last time, so it may be tried again.
            found.state = "queued"
            found.started_at = None
            await session.flush()
        return found
    made = Scan(bounty_id=bounty.id, config_hash=row.config_hash)
    session.add(made)
    await session.flush()
    return made


@dataclass(frozen=True)
class ScanClaim:
    scan_id: uuid.UUID
    bounty_id: uuid.UUID
    config_hash: str


async def claim_next(session: AsyncSession) -> ScanClaim | None:
    """Take the oldest queued scan, so that only one worker runs it."""
    found = await session.scalar(
        select(Scan)
        .where(Scan.state == "queued")
        .order_by(Scan.created_at, Scan.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if found is None:
        return None
    found.state = "running"
    found.started_at = datetime.now(UTC)
    return ScanClaim(found.id, found.bounty_id, found.config_hash)


async def record(
    session: AsyncSession, cipher: Cipher, claim: ScanClaim, results: list[dict[str, Any]]
) -> None:
    await session.execute(
        update(Scan)
        .where(Scan.id == claim.scan_id)
        .values(state="done", results_enc=cipher.encrypt_json(results, purpose=RESULTS))
    )


async def fail(session: AsyncSession, claim: ScanClaim) -> None:
    await session.execute(update(Scan).where(Scan.id == claim.scan_id).values(state="error"))


async def requeue_stale(session: AsyncSession, *, older_than: timedelta) -> None:
    """A scan whose worker died is put back, to be run again from the start."""
    await session.execute(
        update(Scan)
        .where(Scan.state == "running", Scan.started_at < datetime.now(UTC) - older_than)
        .values(state="queued", started_at=None)
    )


def results_of(cipher: Cipher, found: Scan) -> list[dict[str, Any]]:
    if found.results_enc is None:
        return []
    stored = cipher.decrypt_json(found.results_enc, purpose=RESULTS)
    return stored if isinstance(stored, list) else []
