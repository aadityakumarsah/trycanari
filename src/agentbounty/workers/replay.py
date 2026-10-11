"""Replay worker: takes attempts off the queue, replays them, records verdicts.

It needs the database, the encryption key and the model API. It has no wallet
and no way to pay anyone: a payout can only follow from a finding it writes.

Run it with:  python -m agentbounty.workers.replay
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentbounty.db import create_engine, create_session_factory
from agentbounty.logs import configure_logging
from agentbounty.models import Attempt
from agentbounty.models import TargetConfig as TargetConfigRow
from agentbounty.services import attempts, rearm
from agentbounty.services.bounty import LoadedTarget, load_target
from agentbounty.services.crypto import Cipher
from agentbounty.services.harness import (
    DEFAULT_LIMITS,
    EntryPoint,
    SandboxLimits,
    SlotFill,
    run_attempt,
)
from agentbounty.services.judge import RUNS_PER_ATTEMPT, TRIP_THRESHOLD
from agentbounty.services.model import FakeModel, ModelClient, RealModel
from agentbounty.settings import Settings, load_settings

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]


def make_model(settings: Settings) -> ModelClient:
    if settings.model_backend == "fake":
        return FakeModel()
    if settings.model_api_base is None or settings.model_api_key is None:
        raise ValueError("MODEL_BACKEND=real needs MODEL_API_BASE and MODEL_API_KEY")
    return RealModel(settings.model_api_base, settings.model_api_key.get_secret_value())


async def _load(
    sessions: SessionFactory, cipher: Cipher, claim: attempts.Claim
) -> tuple[LoadedTarget, list[SlotFill]] | None:
    """The config the attempt is bound to and its fills, or None if it can no longer count."""
    async with sessions() as session:
        if not await attempts.still_counts(session, claim):
            return None
        attempt = await session.get_one(Attempt, claim.attempt_id)
        config = await session.scalar(
            select(TargetConfigRow)
            .where(
                TargetConfigRow.bounty_id == attempt.bounty_id,
                TargetConfigRow.config_hash == attempt.config_hash,
            )
            .order_by(TargetConfigRow.version.desc())
            .limit(1)
        )
        if config is None:
            return None
        fills = [SlotFill(EntryPoint(f["slot"]), f["content"]) for f in attempt.payload_json]
        return await load_target(session, cipher, config), fills


async def process(
    sessions: SessionFactory,
    cipher: Cipher,
    model: ModelClient,
    claim: attempts.Claim,
    *,
    max_tries: int,
    limits: SandboxLimits = DEFAULT_LIMITS,
) -> None:
    """Replay one claimed attempt and record the outcome.

    No transaction is open while the model is being called. Everything the
    replay produced is written at the end in a single transaction; if this
    process dies before that, nothing of the replay exists and the attempt is
    retried from the start.
    """
    loaded = await _load(sessions, cipher, claim)
    if loaded is None:
        async with sessions.begin() as session:
            await attempts.record_result(session, cipher, claim)
        return
    target, fills = loaded
    try:
        results = await run_attempt(
            target.config, fills, model, runs=RUNS_PER_ATTEMPT, limits=limits
        )
    except Exception:
        logger.exception("replay of attempt %s failed", claim.attempt_id)
        async with sessions.begin() as session:
            await attempts.release(session, claim, max_tries=max_tries)
        return
    judgement = attempts.judge_runs(target, results)
    async with sessions.begin() as session:
        finding = await attempts.record_result(session, cipher, claim, results, judgement)
    if finding is not None:
        logger.info("attempt %s verified: finding %s", claim.attempt_id, finding.id)


async def work_once(
    sessions: SessionFactory,
    cipher: Cipher,
    model: ModelClient,
    *,
    max_tries: int,
    limits: SandboxLimits = DEFAULT_LIMITS,
) -> bool:
    """Do one unit of work: an attempt, or else a re-arm check. False when there is none."""
    async with sessions.begin() as session:
        claim = await attempts.claim_next(session)
    if claim is not None:
        await process(sessions, cipher, model, claim, max_tries=max_tries, limits=limits)
        return True
    async with sessions.begin() as session:
        check = await rearm.claim_next(session)
    if check is None:
        return False
    await process_rearm(sessions, cipher, model, check, limits=limits)
    return True


async def process_rearm(
    sessions: SessionFactory,
    cipher: Cipher,
    model: ModelClient,
    claim: rearm.RearmClaim,
    *,
    limits: SandboxLimits = DEFAULT_LIMITS,
) -> None:
    """Replay every paid attack of a bounty against its patched config.

    The check passes only if none of them is a verified break again. An attack
    that no longer fits the config's entry points cannot be made at all, which
    counts as not breaking it.
    """
    async with sessions() as session:
        config = await session.scalar(
            select(TargetConfigRow)
            .where(
                TargetConfigRow.bounty_id == claim.bounty_id,
                TargetConfigRow.config_hash == claim.config_hash,
            )
            .order_by(TargetConfigRow.version.desc())
            .limit(1)
        )
        target = await load_target(session, cipher, config) if config else None
        attacks = await rearm.paid_attacks(session, claim.bounty_id)
    if target is None:
        # The config was edited after the check was asked for: it proves nothing now.
        async with sessions.begin() as session:
            await rearm.record(session, claim, passed=False, tripped_runs=0)
        return

    worst = 0
    try:
        for payload in attacks:
            fills = [
                SlotFill(EntryPoint(fill["slot"]), fill["content"])
                for fill in payload
                if fill["slot"] in target.config.entry_points
            ]
            if not fills:
                continue
            results = await run_attempt(
                target.config, fills, model, runs=RUNS_PER_ATTEMPT, limits=limits
            )
            judgement = attempts.judge_runs(target, results)
            worst = max(worst, _most_trips(judgement))
            if judgement.broken_canary is not None:
                break
    except Exception:
        logger.exception("re-arm replay for bounty %s failed", claim.bounty_id)
        async with sessions.begin() as session:
            await rearm.release(session, claim)
        return
    passed = worst < TRIP_THRESHOLD
    async with sessions.begin() as session:
        await rearm.record(session, claim, passed=passed, tripped_runs=worst)
    logger.info(
        "re-arm check for bounty %s %s",
        claim.bounty_id,
        "passed" if passed else "failed",
        extra={"bounty_id": str(claim.bounty_id), "tripped_runs": worst},
    )


def _most_trips(judgement: attempts.Judgement) -> int:
    """The most runs any single canary tripped in."""
    counts: dict[object, int] = {}
    for tripped in judgement.tripped_by_run:
        for canary_id in tripped:
            counts[canary_id] = counts.get(canary_id, 0) + 1
    return max(counts.values(), default=0)


async def drain(
    sessions: SessionFactory,
    cipher: Cipher,
    model: ModelClient,
    *,
    max_tries: int = 3,
    limits: SandboxLimits = DEFAULT_LIMITS,
) -> int:
    """Process attempts until none is runnable. Returns how many were processed."""
    processed = 0
    while await work_once(sessions, cipher, model, max_tries=max_tries, limits=limits):
        processed += 1
    return processed


async def run(settings: Settings, stop: asyncio.Event) -> None:
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    cipher = Cipher(settings.secret_encryption_key.get_secret_value())
    model = make_model(settings)
    stale_after = timedelta(seconds=settings.attempt_timeout_seconds)
    logger.info("replay worker started (model backend: %s)", settings.model_backend)
    try:
        while not stop.is_set():
            try:
                async with sessions.begin() as session:
                    await attempts.requeue_stale(
                        session, older_than=stale_after, max_tries=settings.attempt_max_tries
                    )
                    await rearm.requeue_stale(session, older_than=stale_after)
                if await work_once(sessions, cipher, model, max_tries=settings.attempt_max_tries):
                    continue
            except Exception:
                logger.exception("worker iteration failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
    finally:
        if isinstance(model, RealModel):
            await model.aclose()
        await engine.dispose()


def main() -> None:
    configure_logging()
    settings = load_settings()

    async def serve() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(signum, stop.set)
        await run(settings, stop)

    asyncio.run(serve())


if __name__ == "__main__":
    main()
