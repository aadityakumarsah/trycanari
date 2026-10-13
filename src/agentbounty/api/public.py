"""Public endpoints: what a hunter, human or agent, needs. No account required."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from agentbounty.api.deps import BearerToken, CipherDep, MaybeSponsor, SettingsDep, Transaction
from agentbounty.api.ratelimit import limit_by_client
from agentbounty.api.schemas import (
    ActivityOut,
    AttemptAccepted,
    AttemptIn,
    AttemptStatusOut,
    BountyPublic,
    BreakerOut,
    BreakOut,
    BreakPage,
    CanaryPublic,
    CanaryTallyOut,
    HunterOut,
    InviteOut,
    LeaderOut,
    PayoutOut,
    ReplyOut,
    RunSummaryOut,
    SponsorPublic,
    StatsOut,
    ToolCallOut,
)
from agentbounty.models import (
    Attempt,
    Bounty,
    BountyState,
    Canary,
    Finding,
    Payout,
    PayoutState,
    Sponsor,
)
from agentbounty.models import TargetConfig as TargetConfigRow
from agentbounty.services import attempts as attempt_service
from agentbounty.services import credits, judge
from agentbounty.services.attempts import SubmissionLimits
from agentbounty.services.harness import EntryPoint, SlotFill
from agentbounty.services.wallet import address_rule

router = APIRouter(prefix="/v1", tags=["public"], dependencies=[Depends(limit_by_client)])

# How many finished bounties the public list of them goes back.
COMPLETED_SHOWN = 60

RECENT_BREAKS = 8
LEADERS = 10
_MAX_CACHED = 2_000


async def cached[T](request: Request, key: str, load: Callable[[], Awaitable[T]]) -> T:
    """An answer that is the same for everyone, kept in memory for a few seconds.

    Nothing private goes through here, and nothing that decides money: submitting an
    attempt checks the bounty's state in the database, whatever a cached page said.
    """
    ttl: float = request.app.state.settings.public_cache_seconds
    cache: dict[str, tuple[float, Any]] = request.app.state.public_cache
    now = time.monotonic()
    hit = cache.get(key)
    if hit is not None and hit[0] > now:
        value: T = hit[1]
        return value
    value = await load()
    if ttl > 0:
        if len(cache) > _MAX_CACHED:
            cache.clear()
        cache[key] = (now + ttl, value)
    return value


def _count_of(column: Any) -> Any:
    return select(func.count()).where(column == Bounty.id).correlate(Bounty).scalar_subquery()


async def public_views(
    session: AsyncSession, where: Any, *, order: tuple[Any, ...] | None = None, limit: int = 500
) -> list[BountyPublic]:
    """Bounties as hunters see them: labels and entry points, never a secret, rule or prompt.

    Four queries at most, however many bounties there are.
    """
    rows = (
        await session.execute(
            select(Bounty, _count_of(Attempt.bounty_id), _count_of(Finding.bounty_id), Sponsor)
            .join(Sponsor, Sponsor.id == Bounty.sponsor_id)
            .where(where, Bounty.deleted_at.is_(None))
            .order_by(*(order or (Bounty.created_at, Bounty.id)))
            .limit(limit)
        )
    ).all()
    config_ids = [bounty.live_config_id for bounty, *_ in rows if bounty.live_config_id]
    entry_points: dict[uuid.UUID, list[str]] = {}
    tasks: dict[uuid.UUID, str] = {}
    canaries: dict[uuid.UUID, list[CanaryPublic]] = {}
    if config_ids:
        for config_id, slots, task in await session.execute(
            select(
                TargetConfigRow.id, TargetConfigRow.entry_points_json, TargetConfigRow.task
            ).where(TargetConfigRow.id.in_(config_ids))
        ):
            entry_points[config_id] = slots
            tasks[config_id] = task or ""
        for config_id, kind, label in await session.execute(
            select(Canary.config_id, Canary.type, Canary.public_label)
            .where(Canary.config_id.in_(config_ids))
            .order_by(Canary.public_label, Canary.id)
        ):
            canaries.setdefault(config_id, []).append(CanaryPublic(type=kind, public_label=label))
    broken_by: dict[uuid.UUID, list[BreakerOut]] = {}
    broken = [bounty.id for bounty, _, breaks, _ in rows if breaks]
    if broken:
        # Who a hunter is, is what they chose to put on their profile, as on the
        # leaderboard. The attack itself stays the sponsor's.
        for finding, who, kind, label, amount, state in await session.execute(
            select(
                Finding,
                Sponsor,
                Canary.type,
                Canary.public_label,
                func.coalesce(Payout.amount_zatoshi, Bounty.reward_zatoshi),
                Payout.state,
            )
            .join(Bounty, Bounty.id == Finding.bounty_id)
            .join(Canary, Canary.id == Finding.canary_id)
            .join(Attempt, Attempt.id == Finding.attempt_id)
            .outerjoin(Sponsor, Sponsor.id == Attempt.account_id)
            .outerjoin(Payout, Payout.finding_id == Finding.id)
            .where(Finding.bounty_id.in_(broken))
            .order_by(Finding.verified_at.desc(), Finding.id)
        ):
            broken_by.setdefault(finding.bounty_id, []).append(
                BreakerOut(
                    id=finding.id,
                    name=who.name if who else None,
                    picture_url=(who.uploaded_picture_url or who.picture_url) if who else None,
                    github=who.github if who else None,
                    canary=CanaryPublic(type=kind, public_label=label),
                    reward_zatoshi=int(amount),
                    tripped_runs=finding.tripped_runs,
                    paid=state in (PayoutState.SENT, PayoutState.CONFIRMED),
                    verified_at=finding.verified_at,
                )
            )
    return [
        BountyPublic(
            id=bounty.id,
            title=bounty.title,
            description=bounty.description,
            # Who lists it is what they chose to put on their profile, as for hunters.
            sponsor=SponsorPublic(
                name=owner.name,
                picture_url=owner.uploaded_picture_url or owner.picture_url,
                github=owner.github,
                company=owner.company,
                website=owner.website,
            ),
            state=bounty.state,
            accepting_attempts=bounty.state is BountyState.LIVE
            and bounty.close_requested_at is None,
            reward_zatoshi=bounty.reward_zatoshi,
            entry_points=[
                EntryPoint(slot)
                for slot in entry_points.get(bounty.live_config_id or bounty.id, [])
            ],
            task=tasks.get(bounty.live_config_id or bounty.id, ""),
            canaries=canaries.get(bounty.live_config_id or bounty.id, []),
            attempts=attempts,
            breaks=breaks,
            broken_by=broken_by.get(bounty.id, []),
            created_at=bounty.created_at,
        )
        for bounty, attempts, breaks, owner in rows
    ]


def status_out(status: attempt_service.AttemptStatus) -> AttemptStatusOut:
    """An attempt's status as its hunter reads it."""

    def public(canary: Canary) -> CanaryPublic:
        return CanaryPublic(type=canary.type, public_label=canary.public_label)

    return AttemptStatusOut(
        state=status.state,
        verdict=status.verdict,
        tripped_runs=status.tripped_runs,
        total_runs=status.total_runs,
        canary=None if status.canary is None else public(status.canary),
        replies=[
            ReplyOut(
                text=reply["content"],
                tool_calls=[ToolCallOut(**call) for call in reply["tool_calls"]],
            )
            for reply in status.replies
        ],
        payout=None
        if status.payout is None
        else PayoutOut(
            state=status.payout.state,
            amount_zatoshi=status.payout.amount_zatoshi,
            txid=status.payout.txid,
        ),
        queue_ahead=status.queue_ahead,
        runs_done=len(status.runs) if status.runs else status.runs_done,
        runs_total=judge.RUNS_PER_ATTEMPT,
        threshold=judge.TRIP_THRESHOLD,
        runs=[
            RunSummaryOut(
                run_index=run.run_index,
                tripped=list(run.tripped),
                replies=run.replies,
                tool_calls=list(run.tool_calls),
                tokens=run.tokens,
            )
            for run in status.runs
        ],
        canaries=[
            CanaryTallyOut(canary=public(canary), tripped_runs=count)
            for canary, count in status.canaries
        ],
    )


@router.get("/bounties")
async def list_bounties(
    request: Request, session: Transaction, show: Literal["live", "completed"] = "live"
) -> list[BountyPublic]:
    """Bounties that are live right now, oldest first. With `show=completed`: the ones
    hunters attacked that are now over, newest first: closed, or broken and not live again."""
    if show == "completed":
        broken = select(Finding.id).where(Finding.bounty_id == Bounty.id).exists()
        attacked = select(Attempt.id).where(Attempt.bounty_id == Bounty.id).exists()
        over = and_(
            Bounty.live_config_id.is_not(None),
            attacked,
            or_(
                Bounty.state == BountyState.CLOSED,
                and_(Bounty.state == BountyState.PAUSED, broken),
            ),
        )
        return await cached(
            request,
            "bounties:completed",
            lambda: public_views(
                session, over, order=(Bounty.created_at.desc(), Bounty.id), limit=COMPLETED_SHOWN
            ),
        )
    live = and_(Bounty.state == BountyState.LIVE, Bounty.close_requested_at.is_(None))
    return await cached(request, "bounties", lambda: public_views(session, live))


@router.get("/activity")
async def activity(request: Request, session: Transaction) -> ActivityOut:
    """What is happening across the platform: totals, and the latest verified breaks.

    A break is listed as a fact (which bounty, which canary, how much it paid). The
    attack that did it belongs to the sponsor and is never shown here.
    """

    async def load() -> ActivityOut:
        live = and_(Bounty.state == BountyState.LIVE, Bounty.close_requested_at.is_(None))
        sent = Payout.state.in_([PayoutState.SENT, PayoutState.CONFIRMED])
        totals = (
            await session.execute(
                select(
                    select(func.count()).select_from(Bounty).where(live).scalar_subquery(),
                    select(func.coalesce(func.sum(Bounty.reward_zatoshi), 0))
                    .where(live)
                    .scalar_subquery(),
                    select(func.count()).select_from(Attempt).scalar_subquery(),
                    select(func.count()).select_from(Finding).scalar_subquery(),
                    select(func.coalesce(func.sum(Payout.amount_zatoshi), 0))
                    .where(sent)
                    .scalar_subquery(),
                )
            )
        ).one()
        breaks = await session.execute(
            select(
                Finding.id,
                Finding.bounty_id,
                Bounty.title,
                Canary.type,
                Canary.public_label,
                func.coalesce(Payout.amount_zatoshi, Bounty.reward_zatoshi),
                Finding.tripped_runs,
                Finding.verified_at,
            )
            .join(Bounty, Bounty.id == Finding.bounty_id)
            .join(Canary, Canary.id == Finding.canary_id)
            .outerjoin(Payout, Payout.finding_id == Finding.id)
            # A break is listed with a link to its bounty, so not one of a deleted bounty.
            .where(Bounty.deleted_at.is_(None))
            .order_by(Finding.verified_at.desc(), Finding.id)
            .limit(RECENT_BREAKS)
        )
        return ActivityOut(
            stats=StatsOut(
                live_bounties=totals[0],
                reward_pool_zatoshi=int(totals[1]),
                attempts=totals[2],
                verified_breaks=totals[3],
                paid_zatoshi=int(totals[4]),
            ),
            recent_breaks=[
                BreakOut(
                    id=finding_id,
                    bounty_id=bounty_id,
                    bounty_title=title,
                    canary=CanaryPublic(type=kind, public_label=label),
                    reward_zatoshi=int(amount),
                    tripped_runs=tripped,
                    verified_at=at,
                )
                for finding_id, bounty_id, title, kind, label, amount, tripped, at in breaks
            ],
        )

    return await cached(request, "activity", load)


@router.get("/breaks/{finding_id}")
async def break_page(finding_id: uuid.UUID, request: Request, session: Transaction) -> BreakPage:
    """One verified break, for a page that can be sent to anyone: who broke which agent,
    in how many of the replays, and the payment. The attack stays the sponsor's."""

    async def load() -> BreakPage:
        hunter, owner = aliased(Sponsor), aliased(Sponsor)
        row = (
            await session.execute(
                select(Finding, Bounty, Canary, Payout, hunter, owner)
                .join(Bounty, Bounty.id == Finding.bounty_id)
                .join(Canary, Canary.id == Finding.canary_id)
                .join(Attempt, Attempt.id == Finding.attempt_id)
                .join(owner, owner.id == Bounty.sponsor_id)
                .outerjoin(hunter, hunter.id == Attempt.account_id)
                .outerjoin(Payout, Payout.finding_id == Finding.id)
                .where(Finding.id == finding_id, Bounty.deleted_at.is_(None))
            )
        ).first()
        if row is None:
            raise HTTPException(404, "that break does not exist")
        finding, bounty, canary, payout, who, sponsor = row
        return BreakPage(
            id=finding.id,
            bounty_id=bounty.id,
            bounty_title=bounty.title,
            sponsor=SponsorPublic(
                name=sponsor.name,
                picture_url=sponsor.uploaded_picture_url or sponsor.picture_url,
                github=sponsor.github,
                company=sponsor.company,
                website=sponsor.website,
            ),
            # Who a hunter is, is what they chose to put on their profile. A demo
            # account is nobody.
            hunter=None
            if who is None or who.is_demo
            else HunterOut(
                name=who.name,
                picture_url=who.uploaded_picture_url or who.picture_url,
                github=who.github,
                company=who.company,
                role=who.role,
                website=who.website,
            ),
            canary=CanaryPublic(type=canary.type, public_label=canary.public_label),
            tripped_runs=finding.tripped_runs,
            total_runs=judge.RUNS_PER_ATTEMPT,
            reward_zatoshi=int(payout.amount_zatoshi if payout else bounty.reward_zatoshi),
            payout_state=payout.state if payout else None,
            txid=payout.txid if payout else None,
            verified_at=finding.verified_at,
        )

    return await cached(request, f"break:{finding_id}", load)


@router.get("/leaderboard")
async def leaderboard(request: Request, session: Transaction) -> list[LeaderOut]:
    """The hunters with the most verified breaks. Only breaks made from an account count,
    and a hunter appears under what they chose to put on their profile."""

    async def load() -> list[LeaderOut]:
        breaks = func.count(Finding.id)
        earned = func.coalesce(
            func.sum(Payout.amount_zatoshi).filter(
                Payout.state.in_([PayoutState.SENT, PayoutState.CONFIRMED])
            ),
            0,
        )
        rows = await session.execute(
            select(Sponsor, breaks, earned, func.max(Finding.verified_at))
            .join(Attempt, Attempt.account_id == Sponsor.id)
            .join(Finding, Finding.attempt_id == Attempt.id)
            .outerjoin(Payout, Payout.finding_id == Finding.id)
            .where(Sponsor.is_demo.is_(False))
            .group_by(Sponsor.id)
            .order_by(breaks.desc(), earned.desc(), func.max(Finding.verified_at))
            .limit(LEADERS)
        )
        return [
            LeaderOut(
                name=who.name,
                picture_url=who.uploaded_picture_url or who.picture_url,
                github=who.github,
                company=who.company,
                breaks=count,
                earned_zatoshi=int(total),
                last_break_at=last,
            )
            for who, count, total, last in rows
        ]

    return await cached(request, "leaderboard", load)


@router.get("/invites/{code}")
async def invite(code: str, session: Transaction) -> InviteOut:
    """What a referral code offers: who it is from and whether it has a use left."""
    found = await credits.invite(session, code[:40])
    if found is None:
        raise HTTPException(404, "that invite does not exist")
    return InviteOut(
        code=found.code,
        kind=found.kind,  # type: ignore[arg-type]
        available=found.available,
        invited_by=found.invited_by,
    )


@router.get("/bounties/{bounty_id}")
async def get_bounty(bounty_id: uuid.UUID, request: Request, session: Transaction) -> BountyPublic:
    """One bounty that is or has been live. A draft does not exist as far as the public goes."""

    async def load() -> BountyPublic:
        found = await public_views(
            session, and_(Bounty.id == bounty_id, Bounty.live_config_id.is_not(None))
        )
        if not found:
            raise HTTPException(404, "bounty not found")
        return found[0]

    return await cached(request, f"bounty:{bounty_id}", load)


@router.post("/bounties/{bounty_id}/attempts", status_code=202)
async def submit_attempt(
    bounty_id: uuid.UUID,
    body: AttemptIn,
    session: Transaction,
    settings: SettingsDep,
    account: MaybeSponsor,
) -> AttemptAccepted:
    """Submit an attack. In invite-only mode it must come from an account, and it spends
    credits from that account's allowance."""
    fills = [SlotFill(fill.slot, fill.content) for fill in body.fills]
    if settings.invite_only and account is None:
        raise HTTPException(
            401,
            "sign in to attack: this platform is invite-only",
            headers={"WWW-Authenticate": "Bearer"},
        )
    account_id = account.id if account else None

    async def charge() -> int:
        assert account_id is not None  # noqa: S101 - set whenever charging is on
        return await credits.charge(session, account_id, bounty_id, fills, settings)

    attempt, token = await attempt_service.submit_attempt(
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
        account_id=account_id,
        charge=charge if settings.invite_only else None,
    )
    return AttemptAccepted(attempt_id=attempt.id, status_token=token)


@router.get("/attempts/{attempt_id}")
async def get_attempt(
    attempt_id: uuid.UUID, session: Transaction, token: BearerToken, cipher: CipherDep
) -> AttemptStatusOut:
    return status_out(await attempt_service.attempt_status(session, cipher, attempt_id, token))
