"""Scans: known attacks run against a sponsor's config.

A scan answers "would what is already known to work, work?". It replays the
baseline playbook, every attack this bounty has paid for and, where the
sponsor may draw on it, the shared library: attacks that broke other
sponsors' agents. Each probe is replayed
once, which is cheap and enough to show a weakness; it is not the five-replay
standard a paid break is held to, and the results say so.

There is one scan per config. It may be run again, which replaces what it
found, and a watched bounty's is run again every day.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import (
    Attempt,
    Bounty,
    BountyState,
    Canary,
    CanaryType,
    Finding,
    SavedAttack,
    Scan,
    Sponsor,
)
from agentbounty.models import TargetConfig as TargetConfigRow
from agentbounty.services import bounty as bounty_service
from agentbounty.services import playbook
from agentbounty.services.bounty import LoadedTarget
from agentbounty.services.crypto import Cipher
from agentbounty.services.errors import ServiceError

RESULTS = "scan.results"
# How many of the library's attacks one scan replays: the newest. It bounds what a scan
# costs however large the library grows.
LIBRARY_PER_SCAN = 20
# A watched bounty is scanned again once its last scan is this old.
WATCH_EVERY = timedelta(hours=24)


async def latest(session: AsyncSession, bounty: Bounty) -> tuple[Scan | None, str | None]:
    """The scan of the bounty's current config, if one was asked for, and that config's hash."""
    row = await bounty_service.latest_config(session, bounty.id)
    if row is None:
        return None, None
    found = await session.scalar(
        select(Scan).where(Scan.bounty_id == bounty.id, Scan.config_hash == row.config_hash)
    )
    return found, row.config_hash


async def request(
    session: AsyncSession,
    bounty: Bounty,
    *,
    again: bool = False,
    with_library: bool = False,
    corpus_type: CanaryType | None = None,
) -> Scan:
    """Queue a scan of the bounty's current config. Asking again returns the same one,
    unless `again` asks for a finished scan to be run afresh."""
    row = await bounty_service.latest_config(session, bounty.id)
    if row is None:
        raise ServiceError(409, "describe the agent before scanning it")
    if not await bounty_service.canary_rows(session, row.id):
        raise ServiceError(409, "add a canary first: a scan needs something to trip")
    found = await session.scalar(
        select(Scan).where(Scan.bounty_id == bounty.id, Scan.config_hash == row.config_hash)
    )
    if found is not None:
        # The platform failed it last time, so it may be tried again.
        if found.state == "error" or (again and found.state == "done"):
            found.state = "queued"
            found.started_at = None
            found.with_library = with_library
            found.corpus_type = corpus_type
            await session.flush()
        return found
    made = Scan(
        bounty_id=bounty.id,
        config_hash=row.config_hash,
        with_library=with_library,
        corpus_type=corpus_type,
    )
    session.add(made)
    await session.flush()
    return made


@dataclass(frozen=True)
class ScanClaim:
    scan_id: uuid.UUID
    bounty_id: uuid.UUID
    config_hash: str
    with_library: bool = False
    corpus_type: CanaryType | None = None


async def claim_next(session: AsyncSession) -> ScanClaim | None:
    """Take the oldest queued scan, so that only one worker runs it."""
    found = await session.scalar(
        select(Scan)
        .where(
            Scan.state == "queued",
            or_(Scan.started_at.is_(None), Scan.started_at <= datetime.now(UTC)),
        )
        .order_by(Scan.created_at, Scan.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if found is None:
        return None
    found.state = "running"
    found.started_at = datetime.now(UTC)
    return ScanClaim(
        found.id, found.bounty_id, found.config_hash, found.with_library, found.corpus_type
    )


async def probes(
    session: AsyncSession, claim: ScanClaim, target: LoadedTarget
) -> list[playbook.Probe]:
    """Everything a scan replays: the playbook, then every attack the bounty has paid
    for, then the newest of the shared library."""
    entry_points = target.config.entry_points
    built = playbook.probes(target)
    if not target.canaries:
        return built
    paid = await session.execute(
        select(Finding.id, Attempt.payload_json, Canary.public_label)
        .join(Attempt, Attempt.id == Finding.attempt_id)
        .join(Canary, Canary.id == Finding.canary_id)
        .where(Finding.bounty_id == claim.bounty_id)
        .order_by(Finding.verified_at, Finding.id)
    )
    for finding_id, payload, label in paid:
        fills = playbook.refit(payload, entry_points)
        if fills:
            built.append(
                playbook.Probe(
                    f"break:{finding_id}",
                    "An attack you paid for",
                    "Past break",
                    fills,
                    label,
                    playbook.PAST_BREAK,
                )
            )
    if claim.with_library:
        query = (
            select(SavedAttack)
            .join(Bounty, Bounty.id == SavedAttack.bounty_id)
            .where(SavedAttack.bounty_id != claim.bounty_id)
        )
        if claim.corpus_type is None:
            query = query.where(Bounty.shares_breaks.is_(True))
        else:
            query = query.where(SavedAttack.canary_type == claim.corpus_type)
        shared = await session.scalars(
            query.order_by(SavedAttack.created_at.desc(), SavedAttack.id).limit(LIBRARY_PER_SCAN)
        )
        for saved in shared:
            fills = playbook.refit(saved.payload_json, entry_points)
            if fills:
                built.append(
                    playbook.Probe(
                        f"library:{saved.id}",
                        "An attack that broke another agent",
                        "Shared library",
                        fills,
                        "any canary",
                        playbook.LIBRARY,
                    )
                )
    return built


async def record(
    session: AsyncSession, cipher: Cipher, claim: ScanClaim, results: list[dict[str, Any]]
) -> None:
    await session.execute(
        update(Scan)
        .where(Scan.id == claim.scan_id)
        .values(
            state="done",
            results_enc=cipher.encrypt_json(results, purpose=RESULTS),
            finished_at=datetime.now(UTC),
        )
    )


async def fail(session: AsyncSession, claim: ScanClaim) -> None:
    await session.execute(update(Scan).where(Scan.id == claim.scan_id).values(state="error"))


async def defer_for_capacity(
    session: AsyncSession, claim: ScanClaim, *, retry_after: timedelta
) -> None:
    await session.execute(
        update(Scan)
        .where(Scan.id == claim.scan_id, Scan.state == "running")
        .values(state="queued", started_at=datetime.now(UTC) + retry_after)
    )


async def requeue_stale(session: AsyncSession, *, older_than: timedelta) -> None:
    """A scan whose worker died is put back, to be run again from the start."""
    await session.execute(
        update(Scan)
        .where(Scan.state == "running", Scan.started_at < datetime.now(UTC) - older_than)
        .values(state="queued", started_at=None)
    )


async def requeue_watched(session: AsyncSession, *, plans_offered: bool) -> int:
    """Queue again the scan of every watched, live bounty whose last one is a day old.
    Where a plan is sold, only for sponsors who hold one. Returns how many were queued."""
    now = datetime.now(UTC)
    # A watched bounty that has never been scanned as it now is gets its first scan.
    unscanned = (
        select(Bounty.id, TargetConfigRow.config_hash)
        .join(Sponsor, Sponsor.id == Bounty.sponsor_id)
        .join(TargetConfigRow, TargetConfigRow.id == Bounty.live_config_id)
        .outerjoin(
            Scan,
            (Scan.bounty_id == Bounty.id) & (Scan.config_hash == TargetConfigRow.config_hash),
        )
        .where(
            Bounty.watch.is_(True),
            Bounty.state == BountyState.LIVE,
            Bounty.deleted_at.is_(None),
            Scan.id.is_(None),
        )
    )
    if plans_offered:
        unscanned = unscanned.where(Sponsor.plan_until > now)
    first = (await session.execute(unscanned)).all()
    for bounty_id, config_hash in first:
        session.add(Scan(bounty_id=bounty_id, config_hash=config_hash, with_library=True))
    await session.flush()
    due = (
        select(Scan.id)
        .join(Bounty, Bounty.id == Scan.bounty_id)
        .join(Sponsor, Sponsor.id == Bounty.sponsor_id)
        .join(TargetConfigRow, TargetConfigRow.id == Bounty.live_config_id)
        .where(
            Bounty.watch.is_(True),
            Bounty.state == BountyState.LIVE,
            Bounty.deleted_at.is_(None),
            Scan.config_hash == TargetConfigRow.config_hash,
            Scan.state == "done",
            or_(Scan.finished_at.is_(None), Scan.finished_at < now - WATCH_EVERY),
        )
    )
    if plans_offered:
        due = due.where(Sponsor.plan_until > now)
    queued = await session.execute(
        update(Scan)
        .where(Scan.id.in_(due))
        .values(state="queued", started_at=None, with_library=True)
        .returning(Scan.id)
    )
    return len(queued.all()) + len(first)


def results_of(cipher: Cipher, found: Scan) -> list[dict[str, Any]]:
    if found.results_enc is None:
        return []
    stored = cipher.decrypt_json(found.results_enc, purpose=RESULTS)
    return stored if isinstance(stored, list) else []
