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
from collections.abc import AsyncIterator, Sequence
from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentbounty.db import create_engine, create_session_factory
from agentbounty.logs import configure_logging
from agentbounty.models import Attempt
from agentbounty.models import TargetConfig as TargetConfigRow
from agentbounty.services import (
    attempts,
    continuous,
    endpoint,
    house,
    plans,
    playbook,
    provider,
    rearm,
    regression,
    scan,
)
from agentbounty.services.bounty import LoadedTarget, load_target
from agentbounty.services.crypto import Cipher
from agentbounty.services.harness import (
    DEFAULT_LIMITS,
    AttemptError,
    SandboxLimits,
    SlotFill,
    fills_from_json,
    fills_to_json,
    run_attempt,
)
from agentbounty.services.judge import RUNS_PER_ATTEMPT, TRIP_THRESHOLD
from agentbounty.services.model import (
    ChatMessage,
    FakeModel,
    ModelClient,
    ModelParams,
    ModelRateLimitError,
    ModelResponse,
    RealModel,
    ToolSpec,
)
from agentbounty.settings import Settings, load_settings

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]


@contextlib.asynccontextmanager
async def model_for(
    target: LoadedTarget, platform: ModelClient, own: endpoint.Connector | None
) -> AsyncIterator[ModelClient]:
    """The model a config is replayed on: the sponsor's own endpoint if it names one,
    and otherwise the platform's provider."""
    if target.config.endpoint is None:
        yield platform
        return
    if own is None or target.endpoint_key is None:
        raise RuntimeError("this config names its own endpoint, which cannot be opened here")
    model = own(target.config.endpoint, target.endpoint_key)
    try:
        yield model
    finally:
        await model.aclose()


class PlatformModel:
    """The platform's model, which the owner can change while the worker runs.

    It answers with the provider set in the admin page when there is one, and with the
    one the worker started on otherwise. `refresh` looks for a change.
    """

    def __init__(self, default: ModelClient) -> None:
        self._default = default
        self._chosen: RealModel | None = None
        self._signature: tuple[str, str] | None = None

    @property
    def provider(self) -> str:
        return self._signature[0] if self._signature else "the server's settings"

    async def refresh(
        self, sessions: SessionFactory, cipher: Cipher, connect: endpoint.Connector
    ) -> bool:
        """Follow what the owner has set. True when the model in use changed."""
        async with sessions() as session:
            chosen = await provider.stored(session, cipher)
        signature = (chosen.base, chosen.key) if chosen else None
        if signature == self._signature:
            return False
        previous, self._chosen = self._chosen, connect(*signature) if signature else None
        self._signature = signature
        if previous is not None:
            await previous.aclose()
        return True

    async def complete(
        self, messages: Sequence[ChatMessage], tools: Sequence[ToolSpec], params: ModelParams
    ) -> ModelResponse:
        return await (self._chosen or self._default).complete(messages, tools, params)

    async def aclose(self) -> None:
        if self._chosen is not None:
            await self._chosen.aclose()
        if isinstance(self._default, RealModel):
            await self._default.aclose()


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
        fills = fills_from_json(attempt.payload_json)
        return await load_target(session, cipher, config), fills


async def process(
    sessions: SessionFactory,
    cipher: Cipher,
    model: ModelClient,
    claim: attempts.Claim,
    *,
    max_tries: int,
    limits: SandboxLimits = DEFAULT_LIMITS,
    own: endpoint.Connector | None = None,
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

    async def progress(runs_done: int) -> None:
        # A short transaction of its own, between model calls. Nothing depends on it.
        async with sessions.begin() as session:
            await attempts.note_progress(session, claim, runs_done)

    try:
        async with model_for(target, model, own) as chosen:
            results = await run_attempt(
                target.config,
                fills,
                chosen,
                runs=RUNS_PER_ATTEMPT,
                limits=limits,
                on_run=progress,
            )
    except ModelRateLimitError as exc:
        logger.warning(
            "provider rate-limited attempt %s; retrying in %.1f seconds",
            claim.attempt_id,
            exc.retry_after,
        )
        async with sessions.begin() as session:
            await attempts.defer_for_capacity(
                session, claim, retry_after=timedelta(seconds=exc.retry_after)
            )
        return
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
    own: endpoint.Connector | None = None,
) -> bool:
    """Do one unit of work: an attempt, or else a re-arm check. False when there is none."""
    async with sessions.begin() as session:
        claim = await attempts.claim_next(session)
    if claim is not None:
        await process(sessions, cipher, model, claim, max_tries=max_tries, limits=limits, own=own)
        return True
    async with sessions.begin() as session:
        check = await rearm.claim_next(session)
    if check is not None:
        await process_rearm(sessions, cipher, model, check, limits=limits, own=own)
        return True
    async with sessions.begin() as session:
        asked = await scan.claim_next(session)
    if asked is not None:
        await process_scan(sessions, cipher, model, asked, limits=limits, own=own)
        return True
    async with sessions.begin() as session:
        continuous_claim = await continuous.claim_next(session)
    if continuous_claim is None:
        return False
    await process_continuous(sessions, cipher, model, continuous_claim, limits=limits, own=own)
    return True


async def process_continuous(
    sessions: SessionFactory,
    cipher: Cipher,
    model: ModelClient,
    claim: continuous.Claim,
    *,
    limits: SandboxLimits = DEFAULT_LIMITS,
    own: endpoint.Connector | None = None,
) -> None:
    """Run every durable attack against an immutable agent version."""
    async with sessions() as session:
        row = await session.get(TargetConfigRow, claim.config_id)
        target = (
            await load_target(session, cipher, row)
            if row and row.config_hash == claim.config_hash
            else None
        )
    if target is None:
        async with sessions.begin() as session:
            await continuous.fail(session, claim)
        return
    try:
        async with model_for(target, model, own) as chosen, sessions() as session:
            results = await continuous.execute(
                session, cipher, claim, target, chosen, limits=limits
            )
    except ModelRateLimitError as exc:
        logger.warning(
            "provider deferred continuous replay %s for %.1f seconds",
            claim.run_id,
            exc.retry_after,
        )
        async with sessions.begin() as session:
            await continuous.defer_for_capacity(
                session, claim, retry_after=timedelta(seconds=exc.retry_after)
            )
        return
    except Exception:
        logger.exception("continuous replay for bounty %s failed", claim.bounty_id)
        async with sessions.begin() as session:
            await continuous.fail(session, claim)
        return
    async with sessions.begin() as session:
        await continuous.record(session, cipher, claim, results)


async def process_scan(
    sessions: SessionFactory,
    cipher: Cipher,
    model: ModelClient,
    claim: scan.ScanClaim,
    *,
    limits: SandboxLimits = DEFAULT_LIMITS,
    own: endpoint.Connector | None = None,
) -> None:
    """Run everything a scan replays against a config (the playbook, the bounty's paid
    breaks, the shared library), one replay a probe, and record what each probe did."""
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
        probes = await scan.probes(session, claim, target) if target else []
    if target is None:
        # The config was edited after the scan was asked for: there is nothing to scan.
        async with sessions.begin() as session:
            await scan.fail(session, claim)
        return

    results: list[dict[str, Any]] = []
    try:
        async with model_for(target, model, own) as chosen:
            for probe in probes:
                (ran,) = await run_attempt(
                    target.config, list(probe.fills), chosen, runs=1, limits=limits
                )
                tripped = [
                    canary.definition.public_label for canary in attempts.tripped_in(target, ran)
                ]
                said = next(
                    (
                        m.content
                        for m in reversed(ran.transcript)
                        if m.role == "assistant" and m.content
                    ),
                    "",
                )
                results.append(
                    {
                        "id": probe.id,
                        "name": probe.name,
                        "technique": probe.technique,
                        "aimed_at": probe.aimed_at,
                        "source": probe.source,
                        "tripped": tripped,
                        # An attack from the library was written against someone else's
                        # agent. It is shown only once it has broken this one.
                        "attack": fills_to_json(probe.fills)
                        if tripped or probe.source != playbook.LIBRARY
                        else [],
                        "reply": said[:600],
                        "tool_calls": [
                            {"name": call.name, "arguments": dict(call.arguments)}
                            for call in ran.tool_calls
                        ],
                    }
                )
    except ModelRateLimitError as exc:
        logger.warning("provider deferred scan %s for %.1f seconds", claim.scan_id, exc.retry_after)
        async with sessions.begin() as session:
            await scan.defer_for_capacity(
                session, claim, retry_after=timedelta(seconds=exc.retry_after)
            )
        return
    except Exception:
        logger.exception("baseline scan for bounty %s failed", claim.bounty_id)
        async with sessions.begin() as session:
            await scan.fail(session, claim)
        return
    async with sessions.begin() as session:
        await scan.record(session, cipher, claim, results)
    logger.info(
        "baseline scan done",
        extra={
            "bounty_id": str(claim.bounty_id),
            "probes": len(results),
            "tripped": sum(1 for result in results if result["tripped"]),
        },
    )


async def process_rearm(
    sessions: SessionFactory,
    cipher: Cipher,
    model: ModelClient,
    claim: rearm.RearmClaim,
    *,
    limits: SandboxLimits = DEFAULT_LIMITS,
    own: endpoint.Connector | None = None,
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
        cases = await rearm.saved_attacks(session, claim.bounty_id)
    if target is None:
        # The config was edited after the check was asked for: it proves nothing now.
        async with sessions.begin() as session:
            await rearm.record(session, claim, passed=False, tripped_runs=0)
        return

    worst = 0
    try:
        async with model_for(target, model, own) as chosen:
            for case in cases:
                try:
                    replayed = await regression.replay(case, target, chosen, limits=limits)
                except AttemptError:
                    # The exact regression no longer fits this config's declared surface.
                    continue
                judgement = replayed.judgement
                worst = max(worst, _most_trips(judgement))
                if judgement.broken_canary is not None:
                    break
    except ModelRateLimitError as exc:
        logger.warning(
            "provider deferred re-arm check %s for %.1f seconds",
            claim.check_id,
            exc.retry_after,
        )
        async with sessions.begin() as session:
            await rearm.defer_for_capacity(
                session, claim, retry_after=timedelta(seconds=exc.retry_after)
            )
        return
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
    own: endpoint.Connector | None = None,
) -> int:
    """Process attempts until none is runnable. Returns how many were processed."""
    processed = 0
    while await work_once(sessions, cipher, model, max_tries=max_tries, limits=limits, own=own):
        processed += 1
    return processed


async def run_house(
    settings: Settings,
    sessions: SessionFactory,
    cipher: Cipher,
    model: ModelClient,
    stop: asyncio.Event,
) -> None:
    """The house hunter (services/house.py), beside the replays that judge its attacks.

    It writes its attacks with the platform's model, and where that is the fake one, with
    a script that knows none of the fake agent's directives."""
    thinker = house.ScriptedThinker() if settings.model_backend == "fake" else model
    logger.info("house hunter started: %d tries a config", settings.house_attempts)

    async def wait() -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)

    while not stop.is_set():
        try:
            if await house.hunt_once(sessions, cipher, thinker, settings, wait=wait):
                continue
        except Exception:
            logger.exception("house hunter iteration failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=settings.house_poll_seconds)


async def run(settings: Settings, stop: asyncio.Event) -> None:
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    cipher = Cipher(settings.secret_encryption_key.get_secret_value())
    own = endpoint.connector(settings)
    model = PlatformModel(make_model(settings))
    stale_after = timedelta(seconds=settings.attempt_timeout_seconds)
    logger.info("replay worker started (model backend: %s)", settings.model_backend)
    hunting = (
        asyncio.create_task(run_house(settings, sessions, cipher, model, stop))
        if settings.house_hunter
        else None
    )
    try:
        while not stop.is_set():
            try:
                # Cheap, and it is what lets the owner change provider without a restart.
                if await model.refresh(sessions, cipher, own):
                    logger.info("model provider is now %s", model.provider)
                async with sessions.begin() as session:
                    await attempts.requeue_stale(
                        session, older_than=stale_after, max_tries=settings.attempt_max_tries
                    )
                    await rearm.requeue_stale(session, older_than=stale_after)
                    await scan.requeue_stale(session, older_than=stale_after)
                    await continuous.requeue_stale(session, older_than=stale_after)
                    await scan.requeue_watched(session, plans_offered=plans.offered(settings))
                    await continuous.requeue_scheduled(
                        session, plans_offered=plans.offered(settings)
                    )
                if await work_once(
                    sessions, cipher, model, max_tries=settings.attempt_max_tries, own=own
                ):
                    continue
            except Exception:
                logger.exception("worker iteration failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_seconds)
    finally:
        if hunting is not None:
            # It stops by itself once `stop` is set; it is not left holding the model.
            hunting.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await hunting
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
