"""Payment process: matches deposits, pays verified findings, refunds closed bounties.

The only process that holds a wallet. It serves no requests and reads no
hunter input; everything it does follows from rows other processes wrote.

Run it with:  python -m agentbounty.workers.payment
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentbounty.db import create_engine, create_session_factory
from agentbounty.logs import configure_logging
from agentbounty.models import Bounty, BountyState, Deposit
from agentbounty.services.ledger import bounty_balance
from agentbounty.services.payment import PaymentService, SpendingCaps, required_balance
from agentbounty.services.wallet import FakeWallet, make_wallet
from agentbounty.settings import Settings, load_settings

logger = logging.getLogger(__name__)


def make_service(
    settings: Settings, sessions: async_sessionmaker[AsyncSession]
) -> tuple[PaymentService, FakeWallet | None]:
    """The payment service on the configured wallet, and that wallet if it is the fake one."""
    wallet = make_wallet(settings)
    service = PaymentService(
        sessions,
        wallet,
        deposit_confirmations=settings.deposit_confirmations,
        caps=SpendingCaps(settings.payout_max_zatoshi, settings.payout_daily_max_zatoshi),
        plan_price=settings.plan_price_zatoshi,
        plan_days=settings.plan_days,
    )
    return service, wallet if isinstance(wallet, FakeWallet) else None


async def autofund(
    sessions: async_sessionmaker[AsyncSession], wallet: FakeWallet, confirmations: int
) -> int:
    """Fake wallet only: make the deposit each waiting bounty is owed arrive.

    There is no chain behind the fake wallet for a sponsor to pay on, so
    without this a local stack could never fund a bounty. Returns how many
    deposits were simulated.
    """
    simulated = 0
    async with sessions() as session:
        waiting = await session.scalars(
            select(Bounty).where(
                Bounty.state == BountyState.AWAITING_DEPOSIT, Bounty.close_requested_at.is_(None)
            )
        )
        for bounty in waiting:
            due = required_balance(bounty) - await bounty_balance(session, bounty.id)
            on_its_way = await session.scalar(
                select(Deposit.id).where(Deposit.memo_code == bounty.deposit_code).limit(1)
            )
            if due > 0 and on_its_way is None:
                wallet.simulate_deposit(due, bounty.deposit_code, confirmations=confirmations)
                simulated += 1
    return simulated


async def run(settings: Settings, stop: asyncio.Event) -> None:
    engine = create_engine(settings.database_url)
    sessions = create_session_factory(engine)
    service, fake = make_service(settings, sessions)
    logger.info("payment process started (wallet backend: %s)", settings.wallet_backend)
    try:
        while not stop.is_set():
            try:
                if fake is not None and settings.fake_wallet_autofund:
                    await autofund(sessions, fake, settings.deposit_confirmations)
                await service.tick()
            except Exception:
                logger.exception("payment pass failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.payment_poll_seconds)
    finally:
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
