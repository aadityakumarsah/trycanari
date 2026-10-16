"""What an account may read about the attacks it made: each one, how it was judged and
what it was paid."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import (
    Attempt,
    AttemptState,
    Bounty,
    Canary,
    Finding,
    Payout,
    PayoutState,
    Run,
)
from agentbounty.services import judge
from agentbounty.services.attempts import Verdict

# How far back the list goes. The totals count every attempt, however old.
SHOWN = 100


@dataclass(frozen=True)
class Hunt:
    attempt: Attempt
    bounty: Bounty
    verdict: Verdict | None
    tripped_runs: int | None
    total_runs: int | None
    canary: Canary | None
    finding: Finding | None
    payout: Payout | None


@dataclass(frozen=True)
class Totals:
    attempts: int
    verified: int
    # Sent to the hunter, and verified but not sent yet.
    earned_zatoshi: int
    pending_zatoshi: int
    credits_spent: int


async def totals(session: AsyncSession, account_id: uuid.UUID) -> Totals:
    paid = Payout.state.in_([PayoutState.SENT, PayoutState.CONFIRMED])
    row = (
        await session.execute(
            select(
                func.count(Attempt.id),
                func.count(Finding.id),
                func.coalesce(func.sum(Payout.amount_zatoshi).filter(paid), 0),
                # A break not paid yet is owed its bounty's reward, payout row or not.
                func.coalesce(
                    func.sum(Bounty.reward_zatoshi).filter(
                        Finding.id.is_not(None), Payout.id.is_(None) | ~paid
                    ),
                    0,
                ),
                func.coalesce(func.sum(Attempt.cost_credits), 0),
            )
            .join(Bounty, Bounty.id == Attempt.bounty_id)
            .outerjoin(Finding, Finding.attempt_id == Attempt.id)
            .outerjoin(Payout, Payout.finding_id == Finding.id)
            .where(Attempt.account_id == account_id)
        )
    ).one()
    return Totals(row[0], row[1], int(row[2]), int(row[3]), int(row[4]))


async def listing(
    session: AsyncSession,
    account_id: uuid.UUID,
    *,
    bounty_id: uuid.UUID | None = None,
    limit: int | None = SHOWN,
) -> list[Hunt]:
    """The account's attempts, newest first. Three queries however many there are.

    Account pages use the recent limit. A bounty studio asks for every attempt on that
    bounty, so its complete server-side history remains forkable.
    """
    query = (
        select(Attempt, Bounty, Finding, Payout)
        .join(Bounty, Bounty.id == Attempt.bounty_id)
        .outerjoin(Finding, Finding.attempt_id == Attempt.id)
        .outerjoin(Payout, Payout.finding_id == Finding.id)
        .where(Attempt.account_id == account_id)
        .order_by(Attempt.submitted_at.desc(), Attempt.id)
    )
    if bounty_id is not None:
        query = query.where(Attempt.bounty_id == bounty_id)
    if limit is not None:
        query = query.limit(limit)
    rows = (await session.execute(query)).all()
    ids = [attempt.id for attempt, _, _, _ in rows]
    # Which canaries each run tripped is not secret, so no transcript is decrypted here.
    trips: dict[uuid.UUID, list[list[str]]] = {}
    if ids:
        for attempt_id, tripped in await session.execute(
            select(Run.attempt_id, Run.tripped_canaries_json).where(Run.attempt_id.in_(ids))
        ):
            trips.setdefault(attempt_id, []).append(tripped)

    judged: list[tuple[Any, Verdict | None, int | None, int | None, str | None]] = []
    for attempt, _, finding, _ in rows:
        if attempt.state is not AttemptState.DONE:
            judged.append((attempt.id, None, None, None, None))
            continue
        runs = trips.get(attempt.id, [])
        counts: dict[str, int] = {}
        for tripped in runs:
            for canary_id in tripped:
                counts[canary_id] = counts.get(canary_id, 0) + 1
        top = (
            str(finding.canary_id)
            if finding
            else max(counts, key=lambda c: (counts[c], c), default=None)
        )
        most = counts.get(top, 0) if top else 0
        verdict: Verdict
        if finding is not None:
            verdict = "verified"
        elif not runs or most >= judge.TRIP_THRESHOLD:
            verdict = "not_counted"
        else:
            verdict = "not_verified"
        judged.append((attempt.id, verdict, most, len(runs), top))

    canary_ids = {uuid.UUID(top) for *_, top in judged if top}
    canaries: dict[uuid.UUID, Canary] = {}
    if canary_ids:
        for canary in await session.scalars(select(Canary).where(Canary.id.in_(canary_ids))):
            canaries[canary.id] = canary
    return [
        Hunt(
            attempt=attempt,
            bounty=bounty,
            verdict=verdict,
            tripped_runs=most,
            total_runs=total,
            canary=canaries.get(uuid.UUID(top)) if top else None,
            finding=finding,
            payout=payout,
        )
        for (attempt, bounty, finding, payout), (_, verdict, most, total, top) in zip(
            rows, judged, strict=True
        )
    ]
