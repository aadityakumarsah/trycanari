"""The Watch plan: what a sponsor pays for by the month, on top of the fee on a reward.

A bounty costs nothing until it is broken. The plan is for the testing that goes on
whether or not anyone breaks it: scanning an agent again without changing it, a scan of
every watched bounty each day, and the shared library of attacks that broke other
sponsors' agents.

Where no price is set there is no plan to buy and everyone has what it gives. An account
pays by sending the price to the escrow address with its plan code as the memo; the
payment process sees the deposit and moves the account's date on. An admin can move
the date by hand.
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import Sponsor
from agentbounty.services.errors import ServiceError
from agentbounty.settings import Settings


def offered(settings: Settings) -> bool:
    """Whether there is a plan to buy here at all."""
    return settings.plan_price_zatoshi > 0


def active(account: Sponsor, settings: Settings) -> bool:
    """Whether the account has what the plan gives: always, where no plan is sold."""
    if not offered(settings):
        return True
    return account.plan_until is not None and account.plan_until > datetime.now(UTC)


def require(account: Sponsor, settings: Settings, what: str) -> None:
    if not active(account, settings):
        raise ServiceError(402, f"{what} is part of the Watch plan")


async def code_for(session: AsyncSession, account: Sponsor) -> str:
    """The memo that marks a deposit as payment for this account's plan."""
    if account.plan_code is None:
        account.plan_code = "plan-" + secrets.token_hex(8)
        await session.flush()
    return account.plan_code


def extend(account: Sponsor, days: int) -> datetime:
    """Add days to the account's plan, from today or from where it now ends."""
    now = datetime.now(UTC)
    start = account.plan_until if account.plan_until and account.plan_until > now else now
    account.plan_until = start + timedelta(days=days)
    return account.plan_until


def end(account: Sponsor) -> None:
    account.plan_until = None
