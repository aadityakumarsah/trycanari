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

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import Attempt, AttemptState, Finding, ReferralCode, Sponsor
from agentbounty.services.errors import ServiceError
from agentbounty.services.harness import SlotFill
from agentbounty.settings import Settings

WINDOW = timedelta(hours=24)
# What an attack costs, by how much text it carries: (up to this many characters, credits).
# Longer attacks cost more to replay, five times over.
COSTS: tuple[tuple[int, int], ...] = ((2_000, 1), (8_000, 2), (40_000, 4))

# An ambassador may hold this many unused codes at a time, so a stolen ambassador
# account cannot mint invitations without end.
MAX_UNUSED_PER_AMBASSADOR = 25
# The two kinds of referral code.
HUNTER = "hunter"
SPONSOR = "sponsor"
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
    # Extra tokens the first attack on a bounty costs, on top of its length price.
    new_bounty_premium: int = 0

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
        new_bounty_premium=settings.new_bounty_premium,
    )


async def attacked_before(
    session: AsyncSession, account_id: uuid.UUID, bounty_id: uuid.UUID
) -> bool:
    """Whether the account has already attacked this bounty, so a new one costs the
    premium and a repeat does not. A replay the platform could not run is not engagement."""
    seen = await session.scalar(
        select(Attempt.id)
        .where(
            Attempt.account_id == account_id,
            Attempt.bounty_id == bounty_id,
            Attempt.state != AttemptState.ERROR,
        )
        .limit(1)
    )
    return seen is not None


async def quote(
    session: AsyncSession,
    account_id: uuid.UUID,
    bounty_id: uuid.UUID,
    fills: Sequence[SlotFill],
    settings: Settings,
) -> int:
    """What an attack would cost this account: its length price, plus the new-bounty
    premium when the account has not attacked this bounty before."""
    base = cost_of(fills)
    if await attacked_before(session, account_id, bounty_id):
        return base
    return base + settings.new_bounty_premium


async def charge(
    session: AsyncSession,
    account_id: uuid.UUID,
    bounty_id: uuid.UUID,
    fills: Sequence[SlotFill],
    settings: Settings,
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
    if not can_hunt(account, settings):
        raise ServiceError(403, "enter a referral code to take part in bounties")
    cost = await quote(session, account_id, bounty_id, fills, settings)
    left = (await allowance(session, account, settings)).balance
    if cost > left:
        raise ServiceError(
            402,
            f"this attack costs {cost} tokens and you have {left}."
            " Your allowance refills as attempts from the last 24 hours age out",
        )
    return cost


# --- referral codes ---------------------------------------------------------------------
#
# A code lets exactly one account take part in bounties. It is used when an account
# holds it, and the unique constraint on that column is what makes a second use
# impossible, whatever two requests do at the same moment.


def can_hunt(account: Sponsor, settings: Settings) -> bool:
    """Whether the account may attack: always, unless the platform is invite-only and it
    has neither redeemed a code nor been let in by an admin."""
    return (
        not settings.invite_only
        or account.referral_code is not None
        or account.sponsor_code is not None
        or account.hunting_granted
    )


def can_sponsor(account: Sponsor, settings: Settings) -> bool:
    """Whether the account may list an agent: always, unless the platform is invite-only
    and it has neither redeemed a sponsor code nor been let in by an admin."""
    return (
        not settings.invite_only
        or account.sponsor_code is not None
        or account.sponsoring_granted
        or is_admin(account, settings)
    )


def is_owner(account: Sponsor, settings: Settings) -> bool:
    """Whether the account is an owner: named in the settings, and proven to hold that
    address by signing in with Google. Only an owner decides who else is an admin."""
    return account.google_sub is not None and account.email in settings.owners


def is_admin(account: Sponsor, settings: Settings) -> bool:
    """Whether the account runs the platform: an owner, or made an admin by one."""
    return account.is_admin or is_owner(account, settings)


def new_code() -> str:
    return "-".join("".join(secrets.choice(_ALPHABET) for _ in range(4)) for _ in range(2))


def normalise(code: str) -> str:
    return code.strip().upper()


def _holder() -> Any:
    """The email of the account that used a code, or NULL while it is unused."""
    return (
        select(Sponsor.email)
        .where(_held_by_account())
        .correlate(ReferralCode)
        .limit(1)
        .scalar_subquery()
    )


def _held_by_account() -> Any:
    """An account holds this code, in whichever column its kind goes."""
    return or_(
        Sponsor.referral_code == ReferralCode.code, Sponsor.sponsor_code == ReferralCode.code
    )


async def _unused(session: AsyncSession, code: str | None) -> ReferralCode:
    if not code or not code.strip():
        raise ServiceError(422, "enter a referral code")
    found = await session.scalar(
        select(ReferralCode).where(ReferralCode.code == normalise(code)).with_for_update()
    )
    if found is None:
        raise ServiceError(403, "that referral code is not valid")
    taken = await session.scalar(
        select(Sponsor.id).where(
            or_(Sponsor.referral_code == found.code, Sponsor.sponsor_code == found.code)
        )
    )
    if taken:
        raise ServiceError(403, "that referral code has already been used")
    return found


async def redeem(session: AsyncSession, code: str | None) -> str:
    """Check a referral code exists and is unused, and hold it until this transaction
    ends so that no two people take it. Returns the code as stored."""
    return (await _unused(session, code)).code


async def settle(session: AsyncSession, account: Sponsor) -> None:
    """Put the code a new account joined with where its kind belongs: a sponsor code is
    held as one. An account is made holding its code as a plain referral code."""
    if account.referral_code is None:
        return
    kind = await session.scalar(
        select(ReferralCode.kind).where(ReferralCode.code == account.referral_code)
    )
    if kind == SPONSOR:
        account.sponsor_code, account.referral_code = account.referral_code, None
        await session.flush()


async def redeem_for(session: AsyncSession, account: Sponsor, code: str | None) -> None:
    """Use up a referral code for an account: a hunter code to take part in bounties, a
    sponsor code to list agents as well. Someone who hunts already may still redeem a
    sponsor code."""
    found = await _unused(session, code)
    if found.kind == SPONSOR:
        if account.sponsor_code is not None or account.sponsoring_granted:
            raise ServiceError(409, "you can already list agents")
        account.sponsor_code = found.code
    else:
        if (
            account.referral_code is not None
            or account.sponsor_code is not None
            or account.hunting_granted
        ):
            raise ServiceError(409, "you already have access to the bounties")
        account.referral_code = found.code
    await session.flush()


async def create_codes(
    session: AsyncSession,
    owner: Sponsor | None,
    count: int = 1,
    code: str | None = None,
    *,
    unlimited: bool = False,
    kind: str = HUNTER,
) -> list[ReferralCode]:
    """Make single-use codes. An ambassador may hold only so many unused ones at a time,
    and makes hunter codes only; an admin, and the operator (no owner), may make as many
    as they ask for, of either kind."""
    if kind not in (HUNTER, SPONSOR):
        raise ServiceError(422, "a code is for a hunter or for a sponsor")
    if count < 1:
        raise ServiceError(422, "make at least one code")
    if code is not None and count != 1:
        raise ServiceError(422, "a code of your own choosing can only be made one at a time")
    if owner is not None and not unlimited:
        if not owner.is_ambassador:
            raise ServiceError(403, "only ambassadors can make referral codes")
        if kind == SPONSOR:
            raise ServiceError(403, "only an admin can make sponsor codes")
        # Counted with the ambassador's row held, so two requests cannot both fit under it.
        await session.execute(select(Sponsor.id).where(Sponsor.id == owner.id).with_for_update())
        unused = await session.scalar(
            select(func.count())
            .select_from(ReferralCode)
            .where(ReferralCode.owner_id == owner.id, _holder().is_(None))
        )
        if int(unused or 0) + count > MAX_UNUSED_PER_AMBASSADOR:
            raise ServiceError(
                409,
                f"you can hold {MAX_UNUSED_PER_AMBASSADOR} unused codes at a time;"
                f" you have {int(unused or 0)}. Share those first",
            )
    if code is not None:
        value = normalise(code)
        if await session.scalar(select(ReferralCode.id).where(ReferralCode.code == value)):
            raise ServiceError(409, "that code already exists")
        values = [value]
    else:
        values = list({new_code() for _ in range(count)})
        # Astronomically unlikely to collide; top up rather than assume.
        while len(values) < count:
            values = list({*values, new_code()})
    made = [
        ReferralCode(code=value, owner_id=owner.id if owner else None, kind=kind)
        for value in values
    ]
    session.add_all(made)
    await session.flush()
    return made


@dataclass(frozen=True)
class Invite:
    """What a referral code offers, as shown to whoever follows its link."""

    code: str
    available: bool
    invited_by: str | None
    kind: str = HUNTER


async def invite(session: AsyncSession, code: str) -> Invite | None:
    row = (
        await session.execute(
            select(ReferralCode, _holder(), Sponsor.name)
            .outerjoin(Sponsor, Sponsor.id == ReferralCode.owner_id)
            .where(ReferralCode.code == normalise(code))
        )
    ).first()
    if row is None:
        return None
    found, holder, owner_name = row
    return Invite(found.code, holder is None, owner_name, found.kind)


@dataclass(frozen=True)
class CodeLine:
    code: ReferralCode
    used_by: str | None
    owner_email: str | None


async def all_codes(
    session: AsyncSession, *, limit: int, offset: int
) -> tuple[list[CodeLine], int]:
    """A page of every code, newest first, with who used it and who made it, and the total."""
    total = await session.scalar(select(func.count()).select_from(ReferralCode))
    rows = await session.execute(
        select(ReferralCode, _holder(), Sponsor.email)
        .outerjoin(Sponsor, Sponsor.id == ReferralCode.owner_id)
        .order_by(ReferralCode.created_at.desc(), ReferralCode.id)
        .limit(limit)
        .offset(offset)
    )
    return [CodeLine(code, holder, owner) for code, holder, owner in rows], int(total or 0)


async def codes_of(session: AsyncSession, owner: Sponsor) -> list[CodeLine]:
    """An ambassador's codes, newest first, each with whether it has been used."""
    rows = await session.execute(
        select(ReferralCode, _holder())
        .where(ReferralCode.owner_id == owner.id)
        .order_by(ReferralCode.created_at.desc(), ReferralCode.id)
        .limit(200)
    )
    return [CodeLine(code, holder, owner.email) for code, holder in rows]
