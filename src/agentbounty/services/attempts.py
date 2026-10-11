"""Attempts: submission, the queue, verdicts and what a hunter may read back.

The attempts table is the queue. Nothing here calls the model; the worker
does that between claim_next and record_result.
"""

from __future__ import annotations

import secrets
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from sqlalchemy import and_, exists, func, or_, select, tuple_, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from agentbounty.models import (
    Attempt,
    AttemptState,
    Bounty,
    BountyState,
    Canary,
    Finding,
    Payout,
    Run,
)
from agentbounty.models import TargetConfig as TargetConfigRow
from agentbounty.services import judge
from agentbounty.services.bounty import LoadedCanary, LoadedTarget, hash_token, lock_bounty
from agentbounty.services.crypto import Cipher
from agentbounty.services.errors import ServiceError
from agentbounty.services.harness import (
    AttemptError,
    EntryPoint,
    RunResult,
    SlotFill,
    validate_fills,
)
from agentbounty.services.wallet import Network, validate_address

Verdict = Literal["verified", "not_verified", "not_counted"]


# --- submission -------------------------------------------------------------------------


@dataclass(frozen=True)
class SubmissionLimits:
    """Caps on what the queue will accept. None switches a cap off."""

    # Attempts in a rolling hour.
    per_address_per_hour: int | None = None
    per_bounty_per_hour: int | None = None
    # Model tokens a bounty's replays may use in a rolling 24 hours.
    bounty_daily_tokens: int | None = None


async def _enforce_limits(
    session: AsyncSession, bounty: Bounty, payout_address: str, limits: SubmissionLimits
) -> None:
    """Raise 429 if accepting one more attempt would break a cap.

    Counted in the database, so the caps hold across every API process.
    """
    hour_ago = datetime.now(UTC) - timedelta(hours=1)

    async def attempts_since(*where: Any) -> int:
        counted = await session.scalar(
            select(func.count()).select_from(Attempt).where(Attempt.submitted_at > hour_ago, *where)
        )
        return int(counted or 0)

    if limits.per_address_per_hour is not None and (
        await attempts_since(Attempt.payout_address == payout_address)
        >= limits.per_address_per_hour
    ):
        raise ServiceError(429, "too many attempts for this payout address; try again later")
    if limits.per_bounty_per_hour is not None and (
        await attempts_since(Attempt.bounty_id == bounty.id) >= limits.per_bounty_per_hour
    ):
        raise ServiceError(429, "this bounty has had its attempts for the hour; try again later")
    if limits.bounty_daily_tokens is not None:
        day_ago = datetime.now(UTC) - timedelta(days=1)
        spent = await session.scalar(
            select(func.coalesce(func.sum(Run.token_usage), 0))
            .join(Attempt, Attempt.id == Run.attempt_id)
            .where(Attempt.bounty_id == bounty.id, Attempt.submitted_at > day_ago)
        )
        if int(spent or 0) >= limits.bounty_daily_tokens:
            raise ServiceError(429, "this bounty's daily run budget is spent; try again later")


async def submit_attempt(
    session: AsyncSession,
    bounty_id: uuid.UUID,
    fills: Sequence[SlotFill],
    payout_address: str,
    network: Network,
    limits: SubmissionLimits | None = None,
) -> tuple[Attempt, str]:
    """Queue an attempt against the bounty's live config. Returns it with its status token."""
    # A shared lock: submissions do not block each other, but none can slip past a pause.
    bounty = await session.scalar(
        select(Bounty).where(Bounty.id == bounty_id).with_for_update(read=True)
    )
    if bounty is None or bounty.state is BountyState.DRAFT:
        raise ServiceError(404, "bounty not found")
    if bounty.state is not BountyState.LIVE or bounty.live_config_id is None:
        raise ServiceError(409, f"the bounty is {bounty.state} and is not accepting attempts")
    if bounty.close_requested_at is not None:
        raise ServiceError(409, "the bounty is closing and is not accepting attempts")
    # A reward can only ever be sent to a shielded address, so refuse anything else now.
    if not validate_address(payout_address, network=network):
        raise ServiceError(422, f"payout_address is not a shielded {network} address")
    config = await session.get_one(TargetConfigRow, bounty.live_config_id)
    try:
        validate_fills([EntryPoint(slot) for slot in config.entry_points_json], fills)
    except AttemptError as exc:
        raise ServiceError(422, str(exc)) from exc
    await _enforce_limits(session, bounty, payout_address, limits or SubmissionLimits())
    token = secrets.token_urlsafe(32)
    attempt = Attempt(
        bounty_id=bounty.id,
        # Bound here, for good: the attempt counts only against this exact config.
        config_hash=config.config_hash,
        payload_json=[{"slot": fill.slot.value, "content": fill.content} for fill in fills],
        payout_address=payout_address,
        status_token=hash_token(token),
    )
    session.add(attempt)
    await session.flush()
    return attempt, token


# --- queue ------------------------------------------------------------------------------


@dataclass(frozen=True)
class Claim:
    """A worker's hold on an attempt. `started_at` identifies this particular claim."""

    attempt_id: uuid.UUID
    started_at: datetime


async def claim_next(session: AsyncSession) -> Claim | None:
    """Mark the oldest runnable attempt as running and return a claim on it.

    Attempts of one bounty run strictly in arrival order, one at a time, so the
    first verified break is the first one that was submitted. SKIP LOCKED lets
    several workers claim attempts of different bounties without waiting.
    """
    other = aliased(Attempt)
    ahead = exists().where(
        other.bounty_id == Attempt.bounty_id,
        or_(
            other.state == AttemptState.RUNNING,
            and_(
                other.state == AttemptState.QUEUED,
                tuple_(other.submitted_at, other.id) < tuple_(Attempt.submitted_at, Attempt.id),
            ),
        ),
    )
    attempt = await session.scalar(
        select(Attempt)
        .where(Attempt.state == AttemptState.QUEUED, ~ahead)
        .order_by(Attempt.submitted_at, Attempt.id)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    if attempt is None:
        return None
    attempt.state = AttemptState.RUNNING
    attempt.started_at = datetime.now(UTC)
    attempt.tries += 1
    await session.flush()
    return Claim(attempt.id, attempt.started_at)


async def _held(session: AsyncSession, claim: Claim) -> Attempt | None:
    """The claimed attempt, locked, or None if the claim has been lost.

    A claim is lost when the attempt was taken for dead and handed to another
    worker. Whatever this worker computed must then be thrown away.
    """
    attempt = await session.scalar(
        select(Attempt).where(Attempt.id == claim.attempt_id).with_for_update()
    )
    if attempt is None or attempt.state is not AttemptState.RUNNING:
        return None
    return attempt if attempt.started_at == claim.started_at else None


async def release(session: AsyncSession, claim: Claim, *, max_tries: int) -> None:
    """Give up a claim after a failed replay: back to the queue, or error once out of tries."""
    attempt = await _held(session, claim)
    if attempt is not None:
        attempt.state = AttemptState.QUEUED if attempt.tries < max_tries else AttemptState.ERROR


async def requeue_stale(session: AsyncSession, *, older_than: timedelta, max_tries: int) -> int:
    """Recover attempts whose worker died mid-replay. Returns how many were touched.

    Nothing was written for them, since a replay is recorded in one
    transaction at the end, so they simply run again from the start.
    """
    cutoff = datetime.now(UTC) - older_than
    stale = and_(Attempt.state == AttemptState.RUNNING, Attempt.started_at < cutoff)
    retried = await session.execute(
        update(Attempt).where(stale, Attempt.tries < max_tries).values(state=AttemptState.QUEUED)
    )
    failed = await session.execute(
        update(Attempt).where(stale, Attempt.tries >= max_tries).values(state=AttemptState.ERROR)
    )
    return int(retried.rowcount) + int(failed.rowcount)  # type: ignore[attr-defined]


# --- judging and recording ----------------------------------------------------------------


@dataclass(frozen=True)
class Judgement:
    """The judge's view of one attempt: per-run trips, and the canary that was broken, if any."""

    tripped_by_run: tuple[tuple[uuid.UUID, ...], ...]
    evidence_by_run: tuple[tuple[dict[str, Any], ...], ...]
    broken_canary: uuid.UUID | None
    tripped_runs: int


def _detect(canary: LoadedCanary, transcript: judge.Transcript) -> judge.Detection:
    definition = canary.definition
    if definition.secret is not None:
        return judge.detect_secret_leak(transcript, definition.secret)
    assert definition.rule is not None  # noqa: S101 - a definition has a secret or a rule
    return judge.detect_forbidden_tool(transcript, definition.rule)


def judge_runs(target: LoadedTarget, results: Sequence[RunResult]) -> Judgement:
    """Run every canary's detector over every run and decide.

    If more than one canary is verified, the one that tripped in the most runs
    is the finding; ties go to the first in the config's canary order.
    """
    tripped: list[list[uuid.UUID]] = [[] for _ in results]
    evidence: list[list[dict[str, Any]]] = [[] for _ in results]
    broken: uuid.UUID | None = None
    most = 0
    for canary in target.canaries:
        detections = [_detect(canary, result.transcript) for result in results]
        for index, detection in enumerate(detections):
            if detection.tripped:
                tripped[index].append(canary.id)
                evidence[index].append(
                    {
                        "canary_id": str(canary.id),
                        "message_index": detection.message_index,
                        "location": detection.location,
                        "representation": detection.representation,
                        "arguments": detection.arguments,
                    }
                )
        verdict = judge.decide(detections)
        if verdict.verified and verdict.tripped_runs > most:
            broken, most = canary.id, verdict.tripped_runs
    return Judgement(
        tuple(tuple(ids) for ids in tripped),
        tuple(tuple(items) for items in evidence),
        broken,
        most,
    )


def _transcript_json(result: RunResult) -> list[dict[str, Any]]:
    return [
        {
            "role": message.role,
            "content": message.content,
            "tool_calls": [
                {"name": call.name, "arguments": dict(call.arguments)}
                for call in message.tool_calls
            ],
        }
        for message in result.transcript
    ]


async def _counts(session: AsyncSession, bounty: Bounty, attempt: Attempt) -> bool:
    """An attempt counts only while its bounty is live on the config it was submitted against."""
    if bounty.state is not BountyState.LIVE or bounty.live_config_id is None:
        return False
    live_hash = await session.scalar(
        select(TargetConfigRow.config_hash).where(TargetConfigRow.id == bounty.live_config_id)
    )
    return live_hash == attempt.config_hash


async def still_counts(session: AsyncSession, claim: Claim) -> bool:
    """Whether replaying the claimed attempt could still lead to a finding."""
    attempt = await session.get_one(Attempt, claim.attempt_id)
    bounty = await session.get_one(Bounty, attempt.bounty_id)
    return await _counts(session, bounty, attempt)


TRANSCRIPT = "runs.transcript"
EVIDENCE = "runs.evidence"


def read_transcript(cipher: Cipher, run: Run) -> list[dict[str, Any]]:
    """A run's transcript, decrypted. Empty for a run stored before transcripts were encrypted."""
    if not run.transcript_enc:
        return []
    transcript: list[dict[str, Any]] = cipher.decrypt_json(run.transcript_enc, purpose=TRANSCRIPT)
    return transcript


def read_evidence(cipher: Cipher, run: Run) -> list[dict[str, Any]]:
    if not run.evidence_enc:
        return []
    evidence: list[dict[str, Any]] = cipher.decrypt_json(run.evidence_enc, purpose=EVIDENCE)
    return evidence


async def record_result(
    session: AsyncSession,
    cipher: Cipher,
    claim: Claim,
    results: Sequence[RunResult] = (),
    judgement: Judgement | None = None,
) -> Finding | None:
    """Write the runs and the verdict in one transaction, so a partial replay never exists.

    Called with no results for an attempt that was not replayed because it could
    no longer count. A verified break on a live bounty becomes a finding and
    pauses the bounty; the bounty row is locked, so two workers cannot both
    record a first finding.
    """
    attempt = await _held(session, claim)
    if attempt is None:
        return None
    bounty = await lock_bounty(session, attempt.bounty_id)
    assert bounty is not None  # noqa: S101 - attempts.bounty_id is a foreign key
    for result in results:
        assert judgement is not None  # noqa: S101 - results always come with a judgement
        session.add(
            Run(
                attempt_id=attempt.id,
                run_index=result.run_index,
                transcript_enc=cipher.encrypt_json(_transcript_json(result), purpose=TRANSCRIPT),
                tripped_canaries_json=[str(i) for i in judgement.tripped_by_run[result.run_index]],
                evidence_enc=cipher.encrypt_json(
                    list(judgement.evidence_by_run[result.run_index]), purpose=EVIDENCE
                ),
                token_usage=result.total_tokens,
            )
        )
    attempt.state = AttemptState.DONE
    finding = None
    if judgement and judgement.broken_canary and await _counts(session, bounty, attempt):
        finding = Finding(
            attempt_id=attempt.id,
            bounty_id=bounty.id,
            config_hash=attempt.config_hash,
            canary_id=judgement.broken_canary,
            tripped_runs=judgement.tripped_runs,
        )
        session.add(finding)
        # One payout per hole: no further attempt is accepted or counted until the sponsor
        # activates a patched config.
        bounty.state = BountyState.PAUSED
    await session.flush()
    return finding


# --- status -----------------------------------------------------------------------------


@dataclass(frozen=True)
class AttemptStatus:
    state: AttemptState
    verdict: Verdict | None = None
    tripped_runs: int | None = None
    total_runs: int | None = None
    canary: Canary | None = None
    # The assistant messages of one run: the one that tripped the canary, if any did.
    replies: tuple[dict[str, Any], ...] = ()
    # The payout for a verified attempt, once the payment process has created it.
    payout: Payout | None = None


async def attempt_status(
    session: AsyncSession, cipher: Cipher, attempt_id: uuid.UUID, token: str
) -> AttemptStatus:
    """What the holder of the status token may see. Raises 404 for a wrong token too."""
    attempt = await session.get(Attempt, attempt_id)
    if attempt is None or not secrets.compare_digest(attempt.status_token, hash_token(token)):
        raise ServiceError(404, "attempt not found")
    if attempt.state is not AttemptState.DONE:
        return AttemptStatus(attempt.state)

    runs = list(
        await session.scalars(
            select(Run).where(Run.attempt_id == attempt.id).order_by(Run.run_index)
        )
    )
    finding = await session.scalar(select(Finding).where(Finding.attempt_id == attempt.id))
    counts: dict[str, int] = {}
    for run in runs:
        for canary_id in run.tripped_canaries_json:
            counts[canary_id] = counts.get(canary_id, 0) + 1
    top = (
        str(finding.canary_id)
        if finding
        else max(counts, key=lambda c: (counts[c], c), default=None)
    )
    tripped = counts.get(top, 0) if top else 0

    verdict: Verdict
    if finding is not None:
        verdict = "verified"
    elif not runs or tripped >= judge.TRIP_THRESHOLD:
        # It was not replayed, or it worked but the bounty had moved on: paused by an
        # earlier break, or live on a different config.
        verdict = "not_counted"
    else:
        verdict = "not_verified"

    shown = next(
        (run for run in runs if top in run.tripped_canaries_json), runs[0] if runs else None
    )
    transcript = read_transcript(cipher, shown) if shown else []
    replies = tuple(message for message in transcript if message["role"] == "assistant")
    return AttemptStatus(
        state=attempt.state,
        verdict=verdict,
        tripped_runs=tripped,
        total_runs=len(runs),
        canary=await session.get(Canary, uuid.UUID(top)) if top else None,
        replies=replies,
        payout=None
        if finding is None
        else await session.scalar(select(Payout).where(Payout.finding_id == finding.id)),
    )
