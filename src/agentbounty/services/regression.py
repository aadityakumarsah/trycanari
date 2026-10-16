"""Durable verified attacks and deterministic replays against an agent config."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import CanaryType, SavedAttack
from agentbounty.services import attempts
from agentbounty.services.bounty import LoadedTarget
from agentbounty.services.harness import (
    DEFAULT_LIMITS,
    RunResult,
    SandboxLimits,
    fills_from_json,
    run_attempt,
)
from agentbounty.services.judge import RUNS_PER_ATTEMPT
from agentbounty.services.model import ModelClient


@dataclass(frozen=True)
class Replay:
    case: SavedAttack
    runs: tuple[RunResult, ...]
    judgement: attempts.Judgement

    @property
    def verified(self) -> bool:
        return self.judgement.broken_canary is not None


async def cases_for_bounty(session: AsyncSession, bounty_id: uuid.UUID) -> list[SavedAttack]:
    return list(
        await session.scalars(
            select(SavedAttack)
            .where(SavedAttack.bounty_id == bounty_id)
            .order_by(SavedAttack.created_at, SavedAttack.id)
        )
    )


async def corpus(
    session: AsyncSession,
    canary_type: CanaryType | None = None,
    *,
    excluding_bounty: uuid.UUID | None = None,
    limit: int | None = None,
) -> list[SavedAttack]:
    query = select(SavedAttack)
    if canary_type is not None:
        query = query.where(SavedAttack.canary_type == canary_type)
    if excluding_bounty is not None:
        query = query.where(SavedAttack.bounty_id != excluding_bounty)
    query = query.order_by(SavedAttack.created_at.desc(), SavedAttack.id)
    if limit is not None:
        query = query.limit(limit)
    return list(await session.scalars(query))


async def replay(
    case: SavedAttack,
    target: LoadedTarget,
    model: ModelClient,
    *,
    limits: SandboxLimits = DEFAULT_LIMITS,
) -> Replay:
    """Replay the exact stored fills. No refitting or model judgement is involved."""
    runs = await run_attempt(
        target.config,
        fills_from_json(case.payload_json),
        model,
        runs=RUNS_PER_ATTEMPT,
        limits=limits,
    )
    return Replay(case, tuple(runs), attempts.judge_runs(target, runs))
