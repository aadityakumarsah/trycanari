"""Key-authenticated API for autonomous bounty hunters."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_

from agentbounty.api.deps import BearerToken, CipherDep, SettingsDep, Transaction
from agentbounty.api.public import public_views, status_out
from agentbounty.api.ratelimit import limit_by_identity
from agentbounty.api.schemas import (
    AttemptIn,
    BountyPublic,
    HunterAllowanceOut,
    HunterAttemptAccepted,
    HunterAttemptStatusOut,
    HunterBountyOut,
    HunterRulesOut,
)
from agentbounty.models import Bounty, BountyState, Sponsor
from agentbounty.services import attempts, credits, judge
from agentbounty.services import bounty as bounty_service
from agentbounty.services.attempts import SubmissionLimits
from agentbounty.services.harness import MAX_SCRIPTED_TURNS, MAX_SLOT_CHARACTERS, SlotFill
from agentbounty.services.wallet import address_kind, address_rule


async def api_hunter(session: Transaction, token: BearerToken) -> Sponsor:
    sponsor = await bounty_service.sponsor_for_key(session, token)
    if sponsor is None:
        raise HTTPException(401, "unknown API key", headers={"WWW-Authenticate": "Bearer"})
    return sponsor


HunterDep = Annotated[Sponsor, Depends(api_hunter)]


def limit_by_api_key(request: Request, sponsor: HunterDep) -> None:
    limit_by_identity(request, f"api-key:{sponsor.id}")


router = APIRouter(
    prefix="/v1/hunter",
    tags=["agent hunter"],
    dependencies=[Depends(limit_by_api_key)],
)


async def _with_rules(
    listed: list[BountyPublic],
    session: Transaction,
    sponsor: HunterDep,
    settings: SettingsDep,
) -> list[HunterBountyOut]:
    allowed = await credits.allowance(session, sponsor, settings)
    allowance = HunterAllowanceOut(
        tier=allowed.tier,
        balance=allowed.balance,
        cap=allowed.cap,
        spent=allowed.spent,
        next_tier=allowed.next_tier,
        next_cap=allowed.next_cap,
    )
    rules = HunterRulesOut(
        runs_per_attempt=judge.RUNS_PER_ATTEMPT,
        trips_required=judge.TRIP_THRESHOLD,
        max_slot_characters=MAX_SLOT_CHARACTERS,
        max_scripted_turns=MAX_SCRIPTED_TURNS,
        payout_address_kind=address_kind(settings),
        attempts_per_payout_address_per_hour=settings.attempts_per_address_per_hour,
        attempts_per_bounty_per_hour=settings.attempts_per_bounty_per_hour,
    )
    return [
        HunterBountyOut(**bounty.model_dump(), rules=rules, allowance=allowance)
        for bounty in listed
    ]


@router.get("/bounties")
async def list_bounties(
    session: Transaction, sponsor: HunterDep, settings: SettingsDep
) -> list[HunterBountyOut]:
    """List live bounties, their attack surfaces, canary labels, rules and reward."""
    live = and_(Bounty.state == BountyState.LIVE, Bounty.close_requested_at.is_(None))
    return await _with_rules(await public_views(session, live), session, sponsor, settings)


@router.get("/bounties/{bounty_id}")
async def get_bounty(
    bounty_id: uuid.UUID,
    session: Transaction,
    sponsor: HunterDep,
    settings: SettingsDep,
) -> HunterBountyOut:
    """Read one public bounty brief. Draft bounties remain private."""
    listed = await public_views(
        session, and_(Bounty.id == bounty_id, Bounty.live_config_id.is_not(None))
    )
    if not listed:
        raise HTTPException(404, "bounty not found")
    return (await _with_rules(listed, session, sponsor, settings))[0]


@router.post("/bounties/{bounty_id}/attempts", status_code=202)
async def submit_attempt(
    bounty_id: uuid.UUID,
    body: AttemptIn,
    session: Transaction,
    sponsor: HunterDep,
    settings: SettingsDep,
) -> HunterAttemptAccepted:
    """Queue slot fills. The API key owns the attempt and is used to poll it."""
    fills = [SlotFill(fill.slot, fill.content, tuple(fill.turns)) for fill in body.fills]

    async def charge() -> int:
        return await credits.charge(session, sponsor.id, bounty_id, fills, settings)

    attempt, _ = await attempts.submit_attempt(
        session,
        bounty_id,
        fills,
        body.payout_address,
        address_rule(settings),
        SubmissionLimits(
            per_address_per_hour=settings.attempts_per_address_per_hour,
            per_bounty_per_hour=settings.attempts_per_bounty_per_hour,
            bounty_daily_tokens=settings.bounty_daily_token_budget,
            platform_daily_tokens=settings.platform_daily_token_budget,
        ),
        account_id=sponsor.id,
        charge=charge,
    )
    return HunterAttemptAccepted(attempt_id=attempt.id)


@router.get("/attempts/{attempt_id}")
async def get_result(
    attempt_id: uuid.UUID,
    session: Transaction,
    sponsor: HunterDep,
    cipher: CipherDep,
) -> HunterAttemptStatusOut:
    """Poll an owned attempt, including verdict, factual feedback and one run's replies."""
    status = await attempts.own_attempt_status(session, cipher, attempt_id, sponsor.id)
    public = status_out(status)
    return HunterAttemptStatusOut(
        **public.model_dump(),
        attempt_id=attempt_id,
        feedback=list(attempts.why_not_verified(status)),
    )
