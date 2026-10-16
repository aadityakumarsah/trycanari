"""Continuous regression testing for immutable target-config versions."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import Bounty, BountyState, Canary, ContinuousRun, SavedAttack, Sponsor
from agentbounty.models import TargetConfig as TargetConfigRow
from agentbounty.services import attempts, playbook, regression
from agentbounty.services.bounty import LoadedTarget
from agentbounty.services.crypto import Cipher
from agentbounty.services.errors import ServiceError
from agentbounty.services.harness import AttemptError, SandboxLimits, run_attempt
from agentbounty.services.judge import RUNS_PER_ATTEMPT
from agentbounty.services.model import ModelClient

RESULTS = "continuous.results"
EVERY = timedelta(hours=24)
Trigger = Literal["version", "ci", "schedule", "model_update"]


@dataclass(frozen=True)
class Claim:
    run_id: uuid.UUID
    bounty_id: uuid.UUID
    config_id: uuid.UUID
    config_hash: str
    trigger: Trigger


async def request(
    session: AsyncSession, bounty: Bounty, config: TargetConfigRow, *, trigger: Trigger
) -> ContinuousRun:
    """Queue one complete corpus replay. A run is never overwritten, so trends are auditable."""
    made = ContinuousRun(
        bounty_id=bounty.id,
        config_id=config.id,
        config_hash=config.config_hash,
        trigger=trigger,
    )
    session.add(made)
    await session.flush()
    return made


async def latest(session: AsyncSession, bounty_id: uuid.UUID) -> ContinuousRun | None:
    return await session.scalar(
        select(ContinuousRun)
        .where(ContinuousRun.bounty_id == bounty_id)
        .order_by(ContinuousRun.created_at.desc(), ContinuousRun.id.desc())
        .limit(1)
    )


async def history(session: AsyncSession, bounty_id: uuid.UUID) -> list[ContinuousRun]:
    return list(
        await session.scalars(
            select(ContinuousRun)
            .where(ContinuousRun.bounty_id == bounty_id)
            .order_by(ContinuousRun.created_at.desc(), ContinuousRun.id.desc())
            .limit(40)
        )
    )


async def claim_next(session: AsyncSession) -> Claim | None:
    found = await session.scalar(
        select(ContinuousRun)
        .where(
            ContinuousRun.state == "queued",
            or_(
                ContinuousRun.started_at.is_(None),
                ContinuousRun.started_at <= datetime.now(UTC),
            ),
        )
        .order_by(ContinuousRun.created_at, ContinuousRun.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if found is None:
        return None
    found.state = "running"
    found.started_at = datetime.now(UTC)
    return Claim(found.id, found.bounty_id, found.config_id, found.config_hash, found.trigger)  # type: ignore[arg-type]


async def cases(session: AsyncSession, bounty_id: uuid.UUID) -> list[SavedAttack]:
    """The bounty's paid attacks plus all known corpus cases, deduplicated by finding."""
    own = await regression.cases_for_bounty(session, bounty_id)
    library = await regression.corpus(session, excluding_bounty=bounty_id)
    by_id = {case.id: case for case in [*own, *library]}
    return list(by_id.values())


async def has_cases(session: AsyncSession, bounty_id: uuid.UUID) -> bool:
    """Whether a version replay has a boundary or durable attack to exercise."""
    boundary = await session.scalar(
        select(Canary.id)
        .join(TargetConfigRow, TargetConfigRow.id == Canary.config_id)
        .where(TargetConfigRow.bounty_id == bounty_id)
        .limit(1)
    )
    if boundary is not None:
        return True
    own = await session.scalar(
        select(SavedAttack.id).where(SavedAttack.bounty_id == bounty_id).limit(1)
    )
    if own is not None:
        return True
    shared = await session.scalar(
        select(SavedAttack.id).where(SavedAttack.bounty_id != bounty_id).limit(1)
    )
    return shared is not None


async def execute(
    session: AsyncSession,
    cipher: Cipher,
    claim: Claim,
    target: LoadedTarget,
    model: ModelClient,
    *,
    limits: SandboxLimits,
) -> list[dict[str, Any]]:
    del cipher  # Results are encrypted by record; replay itself only needs the loaded target.
    output: list[dict[str, Any]] = []
    # A new customer has no saved break yet. The deterministic baseline still gives every
    # version a real boundary regression suite from day one. Five replays keep the same
    # 3-of-5 proof standard used for verified findings.
    by_label = {
        canary.definition.public_label: canary.definition.type for canary in target.canaries
    }
    for probe in playbook.probes(target):
        runs = await run_attempt(
            target.config,
            list(probe.fills),
            model,
            runs=RUNS_PER_ATTEMPT,
            limits=limits,
        )
        judged = attempts.judge_runs(target, runs)
        output.append(
            {
                "case_id": f"baseline:{probe.id}:{probe.aimed_at}",
                "canary_type": by_label[probe.aimed_at].value,
                "source": "baseline",
                "tripped": judged.broken_canary is not None,
            }
        )
    for case in await cases(session, claim.bounty_id):
        try:
            replayed = await regression.replay(case, target, model, limits=limits)
            output.append(
                {
                    "case_id": str(case.id),
                    "canary_type": case.canary_type.value,
                    "source": "past_break" if case.bounty_id == claim.bounty_id else "corpus",
                    "tripped": replayed.verified,
                }
            )
        except AttemptError:
            # Removed entry points make a past payload impossible to deliver, not a regression.
            output.append(
                {
                    "case_id": str(case.id),
                    "canary_type": case.canary_type.value,
                    "source": "past_break" if case.bounty_id == claim.bounty_id else "corpus",
                    "tripped": False,
                }
            )
    return output


async def record(
    session: AsyncSession, cipher: Cipher, claim: Claim, results: list[dict[str, Any]]
) -> None:
    await session.execute(
        update(ContinuousRun)
        .where(ContinuousRun.id == claim.run_id)
        .values(
            state="done",
            results_enc=cipher.encrypt_json(results, purpose=RESULTS),
            finished_at=datetime.now(UTC),
        )
    )


async def fail(session: AsyncSession, claim: Claim) -> None:
    await session.execute(
        update(ContinuousRun).where(ContinuousRun.id == claim.run_id).values(state="error")
    )


async def defer_for_capacity(
    session: AsyncSession, claim: Claim, *, retry_after: timedelta
) -> None:
    await session.execute(
        update(ContinuousRun)
        .where(ContinuousRun.id == claim.run_id, ContinuousRun.state == "running")
        .values(state="queued", started_at=datetime.now(UTC) + retry_after)
    )


async def requeue_stale(session: AsyncSession, *, older_than: timedelta) -> None:
    await session.execute(
        update(ContinuousRun)
        .where(
            ContinuousRun.state == "running",
            ContinuousRun.started_at < datetime.now(UTC) - older_than,
        )
        .values(state="queued", started_at=None)
    )


async def requeue_scheduled(session: AsyncSession, *, plans_offered: bool) -> int:
    """Queue daily corpus replays for watched, live agents with an active plan."""
    now = datetime.now(UTC)
    due = (
        select(Bounty, TargetConfigRow)
        .join(TargetConfigRow, TargetConfigRow.id == Bounty.live_config_id)
        .join(Sponsor, Sponsor.id == Bounty.sponsor_id)
        .where(
            Bounty.watch.is_(True),
            Bounty.state == BountyState.LIVE,
            Bounty.deleted_at.is_(None),
        )
    )
    if plans_offered:
        due = due.where(Sponsor.plan_until > now)
    queued = 0
    for bounty, config in (await session.execute(due)).all():
        last = await session.scalar(
            select(ContinuousRun)
            .where(
                ContinuousRun.bounty_id == bounty.id,
                ContinuousRun.config_hash == config.config_hash,
            )
            .order_by(ContinuousRun.finished_at.desc().nullslast(), ContinuousRun.created_at.desc())
            .limit(1)
        )
        if last is None or last.finished_at is None or last.finished_at < now - EVERY:
            await request(session, bounty, config, trigger="schedule")
            queued += 1
    return queued


def results_of(cipher: Cipher, run: ContinuousRun | None) -> list[dict[str, Any]]:
    if run is None or run.results_enc is None:
        return []
    value = cipher.decrypt_json(run.results_enc, purpose=RESULTS)
    return value if isinstance(value, list) else []


async def regression_cases(
    session: AsyncSession, run: ContinuousRun, cipher: Cipher
) -> list[dict[str, Any]]:
    """Newly-successful attacks compared with the prior completed run for this agent."""
    current = results_of(cipher, run)
    previous = list(
        await session.scalars(
            select(ContinuousRun)
            .where(
                ContinuousRun.bounty_id == run.bounty_id,
                ContinuousRun.state == "done",
                ContinuousRun.id != run.id,
            )
            .order_by(ContinuousRun.finished_at.desc(), ContinuousRun.id.desc())
        )
    )
    # A CI rerun may follow the automatic version run. Compare to any earlier held
    # result, rather than only the immediately preceding run, so the gate still
    # names the reopened hole.
    held = {
        item.get("case_id")
        for earlier in previous
        for item in results_of(cipher, earlier)
        if not item.get("tripped")
    }
    return [item for item in current if item.get("tripped") and item.get("case_id") in held]


def passed(results: list[dict[str, Any]]) -> bool:
    return not any(bool(item.get("tripped")) for item in results)


async def require_enabled(account: Sponsor, plans_active: bool) -> None:
    if not plans_active:
        raise ServiceError(402, "continuous testing is part of the Watch plan")
