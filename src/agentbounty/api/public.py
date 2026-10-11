"""Public endpoints: what a hunter, human or agent, needs. No account required."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.api.deps import BearerToken, Transaction
from agentbounty.api.schemas import (
    AttemptAccepted,
    AttemptIn,
    AttemptStatusOut,
    BountyPublic,
    CanaryPublic,
    ReplyOut,
    ToolCallOut,
)
from agentbounty.models import Bounty, BountyState
from agentbounty.models import TargetConfig as TargetConfigRow
from agentbounty.services import attempts as attempt_service
from agentbounty.services.bounty import canary_rows
from agentbounty.services.harness import EntryPoint, SlotFill

router = APIRouter(prefix="/v1", tags=["public"])


async def _public(session: AsyncSession, bounty: Bounty) -> BountyPublic:
    """A bounty as hunters see it: labels and entry points, never a secret, rule or prompt."""
    config = (
        await session.get(TargetConfigRow, bounty.live_config_id) if bounty.live_config_id else None
    )
    canaries = await canary_rows(session, config.id) if config else []
    return BountyPublic(
        id=bounty.id,
        state=bounty.state,
        accepting_attempts=bounty.state is BountyState.LIVE,
        reward_zatoshi=bounty.reward_zatoshi,
        entry_points=[EntryPoint(slot) for slot in config.entry_points_json] if config else [],
        canaries=[CanaryPublic(type=c.type, public_label=c.public_label) for c in canaries],
    )


@router.get("/bounties")
async def list_bounties(session: Transaction) -> list[BountyPublic]:
    """Bounties that are live right now, oldest first."""
    live = await session.scalars(
        select(Bounty)
        .where(Bounty.state == BountyState.LIVE)
        .order_by(Bounty.created_at, Bounty.id)
    )
    return [await _public(session, bounty) for bounty in live]


@router.get("/bounties/{bounty_id}")
async def get_bounty(bounty_id: uuid.UUID, session: Transaction) -> BountyPublic:
    """One bounty that is or has been live. A draft does not exist as far as the public goes."""
    bounty = await session.get(Bounty, bounty_id)
    if bounty is None or bounty.live_config_id is None:
        raise HTTPException(404, "bounty not found")
    return await _public(session, bounty)


@router.post("/bounties/{bounty_id}/attempts", status_code=202)
async def submit_attempt(
    bounty_id: uuid.UUID, body: AttemptIn, session: Transaction
) -> AttemptAccepted:
    fills = [SlotFill(fill.slot, fill.content) for fill in body.fills]
    attempt, token = await attempt_service.submit_attempt(
        session, bounty_id, fills, body.payout_address
    )
    return AttemptAccepted(attempt_id=attempt.id, status_token=token)


@router.get("/attempts/{attempt_id}")
async def get_attempt(
    attempt_id: uuid.UUID, session: Transaction, token: BearerToken
) -> AttemptStatusOut:
    status = await attempt_service.attempt_status(session, attempt_id, token)
    return AttemptStatusOut(
        state=status.state,
        verdict=status.verdict,
        tripped_runs=status.tripped_runs,
        total_runs=status.total_runs,
        canary=None
        if status.canary is None
        else CanaryPublic(type=status.canary.type, public_label=status.canary.public_label),
        replies=[
            ReplyOut(
                text=reply["content"],
                tool_calls=[ToolCallOut(**call) for call in reply["tool_calls"]],
            )
            for reply in status.replies
        ],
    )
