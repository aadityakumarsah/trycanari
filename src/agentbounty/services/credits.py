"""Invites and the credit allowance.

Replaying an attack costs model tokens, which the platform pays for. In
invite-only mode anyone may make an account, but an attack must come from one
that has redeemed a referral code, and each attack spends credits from that
account's allowance for the last 24 hours.

Nothing here stores a balance. What an account has left is its tier's cap
minus what its attempts of the last 24 hours cost, so the allowance refills by
itself as attempts age out, and an attempt the platform failed to replay
counts for nothing.
"""

from __future__ import annotations

import secrets
import string
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import Attempt, AttemptState, Finding, ReferralCode, Sponsor
from agentbounty.services.errors import ServiceError
from agentbounty.services.harness import SlotFill
from agentbounty.settings import Settings

WINDOW = timedelta(hours=24)
# What an attack costs, by how much text it carries: (up to this many characters, credits).
# Longer attacks cost more to replay, five times over.
COSTS: tuple[tuple[int, int], ...] = ((2_000, 1), (8_000, 2), (40_000, 4))

# What an ambassador may hand out.
MAX_CODES_PER_AMBASSADOR = 20
MAX_USES_PER_CODE = 25
# Easy to read aloud and type: no 0/O or 1/I/L.
_ALPHABET = "".join(sorted(set(string.ascii_uppercase + string.digits) - set("0O1IL")))


def cost_of(fills: Sequence[SlotFill]) -> int:
    """Credits an attack with these fills costs."""
    size = sum(len(fill.content) for fill in fills)
    for limit, credits in COSTS:
        if size <= limit:
            return credits
    return COSTS[-1][1]


@dataclass(frozen=True)
class Allowance:
    tier: str
    cap: int
    spent: int
    breaks: int
    # What the next tier allows, if there is one.
    next_tier: str | None
    next_cap: int | None

    @property
    def balance(self) -> int:
        return max(0, self.cap - self.spent)


async def allowance(session: AsyncSession, account: Sponsor, settings: Settings) -> Allowance:
    since = datetime.now(UTC) - WINDOW
    spent = await session.scalar(
        select(func.coalesce(func.sum(Attempt.cost_credits), 0)).where(
            Attempt.account_id == account.id,
            Attempt.submitted_at > since,
            # A replay the platform could not complete is on the platform.
            Attempt.state != AttemptState.ERROR,
        )
    )
    breaks = await session.scalar(
        select(func.count())
        .select_from(Finding)
        .join(Attempt, Attempt.id == Finding.attempt_id)
        .where(Attempt.account_id == account.id)
    )
    proven = bool(breaks)
    return Allowance(
        tier="Proven" if proven else "Starter",
        cap=settings.proven_credits if proven else settings.starter_credits,
        spent=int(spent or 0),
        breaks=int(breaks or 0),
        next_tier=None if proven else "Proven",
        next_cap=None if proven else settings.proven_credits,
    )


async def charge(
    session: AsyncSession, account_id: uuid.UUID, fills: Sequence[SlotFill], settings: Settings
) -> int:
    """What this attack costs the account, or 402 if it cannot afford it.

    The account's row is locked first, so two attacks sent at once cannot both
    spend the same last credit.
    """
    account = await session.scalar(
        select(Sponsor).where(Sponsor.id == account_id).with_for_update()
    )
    if account is None:
        raise ServiceError(401, "sign in to attack")
    if account.referral_code is None:
        raise ServiceError(403, "enter a referral code to take part in bounties")
    cost = cost_of(fills)
    left = (await allowance(session, account, settings)).balance
    if cost > left:
        raise ServiceError(
            402,
            f"this attack costs {cost} credits and you have {left}."
            " Your allowance refills as attempts from the last 24 hours age out",
        )
    return cost


# --- referral codes ---------------------------------------------------------------------


def new_code() -> str:
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(2))


def normalise(code: str) -> str:
    return code.strip().upper()


async def uses_of(session: AsyncSession, code: str) -> int:
    used = await session.scalar(
        select(func.count()).select_from(Sponsor).where(Sponsor.referral_code == code)
    )
    return int(used or 0)


async def redeem(session: AsyncSession, code: str | None) -> str:
    """Check a referral code can admit one more account, and hold it until this
    transaction ends so that no two people take its last use. Returns the code
    as stored."""
    if not code or not code.strip():
        raise ServiceError(422, "enter a referral code")
    found = await session.scalar(
        select(ReferralCode).where(ReferralCode.code == normalise(code)).with_for_update()
    )
    if found is None:
        raise ServiceError(403, "that referral code is not valid")
    if await uses_of(session, found.code) >= found.max_uses:
        raise ServiceError(403, "that referral code has been used up")
    return found.code


async def redeem_for(session: AsyncSession, account: Sponsor, code: str | None) -> None:
    """Let an account take part in bounties, using up one use of a referral code."""
    if account.referral_code is not None:
        raise ServiceError(409, "you have already redeemed a referral code")
    account.referral_code = await redeem(session, code)
    await session.flush()


def is_admin(account: Sponsor, settings: Settings) -> bool:
    """Whether the account runs the platform: made an admin, or named as an owner in the
    settings and proven to hold that address by signing in with Google."""
    return account.is_admin or (account.google_sub is not None and account.email in settings.owners)


async def create_code(
    session: AsyncSession,
    owner: Sponsor | None,
    max_uses: int,
    code: str | None = None,
    *,
    unlimited: bool = False,
) -> ReferralCode:
    """Make a code. An ambassador's are limited in number and size; an admin's, and the
    operator's (no owner), are not."""
    if owner is not None and not unlimited:
        if not owner.is_ambassador:
            raise ServiceError(403, "only ambassadors can make referral codes")
        if max_uses > MAX_USES_PER_CODE:
            raise ServiceError(422, f"a code can admit at most {MAX_USES_PER_CODE} people")
        made = await session.scalar(
            select(func.count()).select_from(ReferralCode).where(ReferralCode.owner_id == owner.id)
        )
        if int(made or 0) >= MAX_CODES_PER_AMBASSADOR:
            raise ServiceError(409, f"you have made {MAX_CODES_PER_AMBASSADOR} codes already")
    value = normalise(code) if code else new_code()
    if await session.scalar(select(ReferralCode.id).where(ReferralCode.code == value)):
        raise ServiceError(409, "that code already exists")
    referral = ReferralCode(code=value, owner_id=owner.id if owner else None, max_uses=max_uses)
    session.add(referral)
    await session.flush()
    return referral


def _used() -> Any:
    return (
        select(func.count())
        .where(Sponsor.referral_code == ReferralCode.code)
        .correlate(ReferralCode)
        .scalar_subquery()
    )


@dataclass(frozen=True)
class Invite:
    """What a referral code offers, as shown to whoever follows its link."""

    code: str
    remaining: int
    invited_by: str | None


async def invite(session: AsyncSession, code: str) -> Invite | None:
    row = (
        await session.execute(
            select(ReferralCode, _used(), Sponsor.name)
            .outerjoin(Sponsor, Sponsor.id == ReferralCode.owner_id)
            .where(ReferralCode.code == normalise(code))
        )
    ).first()
    if row is None:
        return None
    found, used, owner_name = row
    return Invite(found.code, max(0, found.max_uses - int(used)), owner_name)


async def all_codes(session: AsyncSession) -> list[tuple[ReferralCode, int, str | None]]:
    """Every code, newest first, with its use and its owner's email."""
    rows = await session.execute(
        select(ReferralCode, _used(), Sponsor.email)
        .outerjoin(Sponsor, Sponsor.id == ReferralCode.owner_id)
        .order_by(ReferralCode.created_at.desc(), ReferralCode.id)
        .limit(500)
    )
    return [(code, int(used), email) for code, used, email in rows]


async def codes_of(session: AsyncSession, owner: Sponsor) -> list[tuple[ReferralCode, int]]:
    """An ambassador's codes, newest first, each with how many accounts joined with it."""
    used = _used()
    rows = await session.execute(
        select(ReferralCode, used)
        .where(ReferralCode.owner_id == owner.id)
        .order_by(ReferralCode.created_at.desc(), ReferralCode.id)
    )
    return [(code, int(count)) for code, count in rows]
