"""Payment service: deposits, payouts and refunds.

This is the only code that calls the wallet. It takes no requests: it is
driven by database state alone, so nothing a hunter or sponsor sends can ask
it to pay. A payout happens because the replay worker wrote a finding.

Every step is idempotent, and each is its own transaction:

    deposits   keyed by txid; credited once (ledger unique on kind + ref_id)
    payouts    keyed by finding_id; one row per finding, sent at most once
    refunds    keyed by bounty_id; one per bounty, sent at most once

A send is bracketed by two commits. The row is marked "sending" before the
wallet is called and "sent" after. If the process dies in between, the row
stays "sending" and is never sent again automatically: nobody can know
whether the money left, so a person has to look.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from agentbounty.models import (
    Attempt,
    AttemptState,
    Bounty,
    BountyState,
    Deposit,
    DepositState,
    Finding,
    FindingState,
    LedgerKind,
    PaymentAction,
    PaymentAudit,
    Payout,
    PayoutState,
    Refund,
    Sponsor,
    TargetConfig,
)
from agentbounty.services.ledger import append_entry, bounty_balance
from agentbounty.services.wallet import IncomingPayment, WalletBackend

logger = logging.getLogger(__name__)

SessionFactory = async_sessionmaker[AsyncSession]


def fee_for(bounty: Bounty) -> int:
    """The platform fee on a payout, rounded down to a whole zatoshi."""
    fee = (Decimal(bounty.reward_zatoshi) * bounty.fee_rate).to_integral_value(ROUND_DOWN)
    return int(fee)


def required_balance(bounty: Bounty) -> int:
    """What a sponsor escrows: the reward and the fee. The fee is charged only on a payout."""
    return bounty.reward_zatoshi + fee_for(bounty)


async def go_live_if_funded(session: AsyncSession, bounty: Bounty) -> bool:
    """Pin the bounty's config and set it live once its escrow covers reward and fee.

    The config was checked when the sponsor asked to fund, and cannot have
    changed since: a bounty awaiting its deposit is not editable.
    """
    if bounty.state is not BountyState.AWAITING_DEPOSIT or bounty.close_requested_at is not None:
        return False
    if await bounty_balance(session, bounty.id) < required_balance(bounty):
        return False
    config_id = await session.scalar(
        select(TargetConfig.id)
        .where(TargetConfig.bounty_id == bounty.id)
        .order_by(TargetConfig.version.desc())
        .limit(1)
    )
    if config_id is None:
        return False
    bounty.state = BountyState.LIVE
    bounty.live_config_id = config_id
    return True


async def audit(
    session: AsyncSession,
    action: PaymentAction,
    *,
    ref_id: uuid.UUID,
    bounty_id: uuid.UUID | None = None,
    amount_zatoshi: int | None = None,
    txid: str | None = None,
    detail: str | None = None,
) -> None:
    """Record one payment action in the append-only audit log, and in the process log.

    The audit row is written in the caller's transaction, so it exists exactly
    when the action it describes does.
    """
    session.add(
        PaymentAudit(
            action=action,
            ref_id=ref_id,
            bounty_id=bounty_id,
            amount_zatoshi=amount_zatoshi,
            txid=txid,
            detail=detail,
        )
    )
    await session.flush()
    logger.info(
        "payment action: %s",
        action.value,
        extra={
            "audit": True,
            "action": action.value,
            "ref_id": str(ref_id),
            "bounty_id": str(bounty_id) if bounty_id else None,
            "amount_zatoshi": amount_zatoshi,
            "txid": txid,
            "detail": detail,
        },
    )


class _RejectedError(Exception):
    """A payout that must not be made now. Raising it rolls its transaction back."""


@dataclass(frozen=True)
class SpendingCaps:
    # Zatoshi. None means no cap.
    per_payout: int | None = None
    daily: int | None = None


class PaymentService:
    def __init__(
        self,
        sessions: SessionFactory,
        wallet: WalletBackend,
        *,
        deposit_confirmations: int = 3,
        caps: SpendingCaps | None = None,
    ) -> None:
        self._sessions = sessions
        self._wallet = wallet
        self._deposit_confirmations = deposit_confirmations
        self._caps = caps or SpendingCaps()
        # Kept in memory: after a restart every deposit is read again, which is
        # harmless because recording one is idempotent on its txid.
        self._cursor: str | None = None

    async def tick(self) -> None:
        """One pass over everything there is to do."""
        await self.poll_deposits()
        await self.pay_findings()
        await self.close_bounties()
        await self.send_refunds()

    # --- deposits -------------------------------------------------------------------------

    async def poll_deposits(self) -> None:
        page = await self._wallet.list_incoming(self._cursor)
        for payment in page.payments:
            async with self._sessions.begin() as session:
                await self._record_deposit(session, payment)
        self._cursor = page.cursor

    async def _record_deposit(self, session: AsyncSession, payment: IncomingPayment) -> None:
        # The insert does nothing for a txid already known, so two pollers cannot collide.
        await session.execute(
            insert(Deposit)
            .values(
                txid=payment.txid,
                amount_zatoshi=payment.amount_zatoshi,
                memo_code=(payment.memo or "").strip() or None,
                confirmations=payment.confirmations,
                state=DepositState.SEEN,
            )
            .on_conflict_do_nothing(index_elements=[Deposit.txid])
        )
        deposit = await session.scalar(
            select(Deposit).where(Deposit.txid == payment.txid).with_for_update()
        )
        assert deposit is not None  # noqa: S101 - inserted above, or already there
        deposit.confirmations = payment.confirmations

        if deposit.state is DepositState.SEEN:
            bounty = await self._bounty_awaiting(session, deposit.memo_code)
            if bounty is None:
                deposit.state = DepositState.UNMATCHED
                await audit(
                    session,
                    PaymentAction.DEPOSIT_UNMATCHED,
                    ref_id=deposit.id,
                    amount_zatoshi=deposit.amount_zatoshi,
                    txid=deposit.txid,
                    detail="the memo matches no open bounty; needs manual handling",
                )
            else:
                deposit.bounty_id = bounty.id
                deposit.state = DepositState.MATCHED
                # Money for a draft its sponsor deleted brings the draft back, so that they
                # can see the deposit and close the bounty for a refund.
                bounty.deleted_at = None
                await audit(
                    session,
                    PaymentAction.DEPOSIT_MATCHED,
                    ref_id=deposit.id,
                    bounty_id=bounty.id,
                    amount_zatoshi=deposit.amount_zatoshi,
                    txid=deposit.txid,
                )

        if (
            deposit.state is DepositState.MATCHED
            and deposit.confirmations >= self._deposit_confirmations
        ):
            assert deposit.bounty_id is not None  # noqa: S101 - set when it was matched
            bounty = await session.scalar(
                select(Bounty).where(Bounty.id == deposit.bounty_id).with_for_update()
            )
            assert bounty is not None  # noqa: S101 - deposits.bounty_id is a foreign key
            if _open_for_deposits(bounty):
                # The ledger's unique (kind, ref_id) makes a second credit impossible.
                await append_entry(
                    session,
                    bounty_id=bounty.id,
                    kind=LedgerKind.DEPOSIT,
                    amount_zatoshi=deposit.amount_zatoshi,
                    ref_id=deposit.id,
                )
                deposit.state = DepositState.CREDITED
                await audit(
                    session,
                    PaymentAction.DEPOSIT_CREDITED,
                    ref_id=deposit.id,
                    bounty_id=bounty.id,
                    amount_zatoshi=deposit.amount_zatoshi,
                    txid=deposit.txid,
                )
                if await go_live_if_funded(session, bounty):
                    logger.info("bounty %s is funded and live", bounty.id)
            else:
                # The bounty closed while the deposit was confirming. Crediting it now
                # would strand the money behind a refund that has already been computed.
                deposit.state = DepositState.UNMATCHED
                await audit(
                    session,
                    PaymentAction.DEPOSIT_UNMATCHED,
                    ref_id=deposit.id,
                    bounty_id=bounty.id,
                    amount_zatoshi=deposit.amount_zatoshi,
                    txid=deposit.txid,
                    detail="arrived after the bounty closed; needs manual handling",
                )

    @staticmethod
    async def _bounty_awaiting(session: AsyncSession, code: str | None) -> Bounty | None:
        if code is None:
            return None
        bounty = await session.scalar(select(Bounty).where(Bounty.deposit_code == code))
        return bounty if bounty is not None and _open_for_deposits(bounty) else None

    # --- payouts --------------------------------------------------------------------------

    async def pay_findings(self) -> None:
        async with self._sessions() as session:
            unpaid = (
                await session.scalars(
                    select(Finding.id)
                    .outerjoin(Payout, Payout.finding_id == Finding.id)
                    .where(Finding.state == FindingState.VERIFIED, Payout.id.is_(None))
                    .order_by(Finding.verified_at, Finding.id)
                )
            ).all()
        for finding_id in unpaid:
            await self._create_payout(finding_id)

        async with self._sessions() as session:
            pending = (
                await session.scalars(
                    select(Payout.id)
                    .where(Payout.state == PayoutState.PENDING, Payout.txid.is_(None))
                    .order_by(Payout.created_at, Payout.id)
                )
            ).all()
        for payout_id in pending:
            await self._send_payout(payout_id)

    async def _create_payout(self, finding_id: uuid.UUID) -> None:
        """Steps one and two: the payout row, then the checks, in one transaction.

        If a check fails the transaction is rolled back, so a rejected payout
        leaves nothing behind and is considered afresh on the next pass.
        """
        try:
            async with self._sessions.begin() as session:
                finding = await session.scalar(
                    select(Finding)
                    .where(Finding.id == finding_id, Finding.state == FindingState.VERIFIED)
                    .with_for_update(skip_locked=True)
                )
                if finding is None:
                    return
                bounty = await session.scalar(
                    select(Bounty).where(Bounty.id == finding.bounty_id).with_for_update()
                )
                attempt = await session.get_one(Attempt, finding.attempt_id)
                assert bounty is not None  # noqa: S101 - findings.bounty_id is a foreign key
                amount, fee = bounty.reward_zatoshi, fee_for(bounty)
                # The unique constraint on finding_id is what makes a retry safe: a second
                # row for this finding cannot be inserted, here or by another process.
                payout = Payout(
                    finding_id=finding.id,
                    amount_zatoshi=amount,
                    fee_zatoshi=fee,
                    to_address=attempt.payout_address,
                )
                session.add(payout)
                await session.flush()
                await self._check(session, bounty, amount, fee)
                await audit(
                    session,
                    PaymentAction.PAYOUT_CREATED,
                    ref_id=payout.id,
                    bounty_id=bounty.id,
                    amount_zatoshi=amount,
                    detail=f"finding {finding.id}",
                )
        except _RejectedError as rejected:
            logger.error("payout for finding %s rejected: %s", finding_id, rejected)
        except IntegrityError:
            logger.info("finding %s already has a payout", finding_id)

    async def _check(self, session: AsyncSession, bounty: Bounty, amount: int, fee: int) -> None:
        balance = await bounty_balance(session, bounty.id)
        if amount + fee > balance:
            raise _RejectedError(
                f"reward and fee come to {amount + fee} zatoshi but bounty {bounty.id}"
                f" holds {balance}"
            )
        if self._caps.per_payout is not None and amount > self._caps.per_payout:
            raise _RejectedError(
                f"{amount} zatoshi is over the per-payment cap of {self._caps.per_payout}"
            )
        if self._caps.daily is not None:
            # Counts the row just inserted. Failed payouts did not spend anything.
            since = datetime.now(UTC) - timedelta(days=1)
            spent = await session.scalar(
                select(func.coalesce(func.sum(Payout.amount_zatoshi), 0)).where(
                    Payout.created_at > since, Payout.state != PayoutState.FAILED
                )
            )
            if int(spent or 0) > self._caps.daily:
                raise _RejectedError(
                    f"this payout would bring the last 24 hours to {int(spent or 0)} zatoshi,"
                    f" over the daily cap of {self._caps.daily}"
                )

    async def _send_payout(self, payout_id: uuid.UUID) -> None:
        """Steps three and four. Only a payout that is pending with no txid is ever sent."""
        async with self._sessions.begin() as session:
            payout = await session.scalar(
                select(Payout)
                .where(
                    Payout.id == payout_id,
                    Payout.state == PayoutState.PENDING,
                    Payout.txid.is_(None),
                )
                .with_for_update(skip_locked=True)
            )
            if payout is None:
                return
            payout.state = PayoutState.SENDING
            to_address, amount, finding_id = (
                payout.to_address,
                payout.amount_zatoshi,
                payout.finding_id,
            )
        # "sending" is committed. From here until the result is recorded, a crash leaves
        # the row where it is, and it will not be sent again.
        result = await self._wallet.send_payment(to_address, amount, f"finding {finding_id}")

        if result.txid is None:
            # Nobody can know whether an ambiguous send left the wallet, so the row stays
            # "sending" for a person to resolve. A clear refusal goes back to "pending".
            action = (
                PaymentAction.PAYOUT_UNRESOLVED if result.ambiguous else PaymentAction.PAYOUT_RETRY
            )
            async with self._sessions.begin() as session:
                payout = await session.get_one(Payout, payout_id, with_for_update=True)
                finding = await session.get_one(Finding, payout.finding_id)
                if not result.ambiguous:
                    payout.state = PayoutState.PENDING
                await audit(
                    session,
                    action,
                    ref_id=payout.id,
                    bounty_id=finding.bounty_id,
                    amount_zatoshi=amount,
                    detail=result.error,
                )
            return

        async with self._sessions.begin() as session:
            payout = await session.get_one(Payout, payout_id, with_for_update=True)
            finding = await session.get_one(Finding, payout.finding_id, with_for_update=True)
            payout.txid = result.txid
            payout.state = PayoutState.SENT
            await append_entry(
                session,
                bounty_id=finding.bounty_id,
                kind=LedgerKind.PAYOUT,
                amount_zatoshi=-payout.amount_zatoshi,
                ref_id=payout.id,
            )
            if payout.fee_zatoshi:
                await append_entry(
                    session,
                    bounty_id=finding.bounty_id,
                    kind=LedgerKind.FEE,
                    amount_zatoshi=-payout.fee_zatoshi,
                    ref_id=payout.id,
                )
            finding.state = FindingState.PAID
            await audit(
                session,
                PaymentAction.PAYOUT_SENT,
                ref_id=payout.id,
                bounty_id=finding.bounty_id,
                amount_zatoshi=payout.amount_zatoshi,
                txid=result.txid,
            )

    # --- closing and refunds --------------------------------------------------------------

    async def close_bounties(self) -> None:
        async with self._sessions() as session:
            closing = (
                await session.scalars(
                    select(Bounty.id).where(
                        Bounty.close_requested_at.is_not(None), Bounty.state != BountyState.CLOSED
                    )
                )
            ).all()
        for bounty_id in closing:
            async with self._sessions.begin() as session:
                await self._close(session, bounty_id)

    async def _close(self, session: AsyncSession, bounty_id: uuid.UUID) -> None:
        bounty = await session.scalar(
            select(Bounty)
            .where(Bounty.id == bounty_id, Bounty.state != BountyState.CLOSED)
            .with_for_update(skip_locked=True)
        )
        if bounty is None:
            return
        # Wait for attempts in flight: one of them may still earn the reward.
        in_flight = await session.scalar(
            select(Attempt.id)
            .where(
                Attempt.bounty_id == bounty.id,
                Attempt.state.in_((AttemptState.QUEUED, AttemptState.RUNNING)),
            )
            .limit(1)
        )
        # And for every finding to be paid: until then the ledger does not yet show
        # what the bounty owes, and the remaining balance would be overstated.
        unpaid = await session.scalar(
            select(Finding.id)
            .where(Finding.bounty_id == bounty.id, Finding.state == FindingState.VERIFIED)
            .limit(1)
        )
        if in_flight or unpaid:
            return
        remaining = await bounty_balance(session, bounty.id)
        if remaining > 0:
            sponsor = await session.get_one(Sponsor, bounty.sponsor_id)
            if not sponsor.refund_address:
                logger.error("bounty %s cannot close: its sponsor has no refund address", bounty.id)
                return
            refund = Refund(
                bounty_id=bounty.id, amount_zatoshi=remaining, to_address=sponsor.refund_address
            )
            session.add(refund)
            await session.flush()
            # Entered with the refund row, so the balance is zero from now on and a second
            # pass has nothing left to refund.
            await append_entry(
                session,
                bounty_id=bounty.id,
                kind=LedgerKind.REFUND,
                amount_zatoshi=-remaining,
                ref_id=refund.id,
            )
            await audit(
                session,
                PaymentAction.REFUND_CREATED,
                ref_id=refund.id,
                bounty_id=bounty.id,
                amount_zatoshi=remaining,
            )
        bounty.state = BountyState.CLOSED
        logger.info("bounty %s closed; %d zatoshi to refund", bounty.id, max(remaining, 0))

    async def send_refunds(self) -> None:
        async with self._sessions() as session:
            pending = (
                await session.scalars(
                    select(Refund.id)
                    .where(Refund.state == PayoutState.PENDING, Refund.txid.is_(None))
                    .order_by(Refund.created_at, Refund.id)
                )
            ).all()
        for refund_id in pending:
            await self._send_refund(refund_id)

    async def _send_refund(self, refund_id: uuid.UUID) -> None:
        async with self._sessions.begin() as session:
            refund = await session.scalar(
                select(Refund)
                .where(
                    Refund.id == refund_id,
                    Refund.state == PayoutState.PENDING,
                    Refund.txid.is_(None),
                )
                .with_for_update(skip_locked=True)
            )
            if refund is None:
                return
            refund.state = PayoutState.SENDING
            to_address, amount, bounty_id = (
                refund.to_address,
                refund.amount_zatoshi,
                refund.bounty_id,
            )
        result = await self._wallet.send_payment(to_address, amount, f"refund {bounty_id}")

        async with self._sessions.begin() as session:
            refund = await session.get_one(Refund, refund_id, with_for_update=True)
            if result.txid is not None:
                refund.txid = result.txid
                refund.state = PayoutState.SENT
                action = PaymentAction.REFUND_SENT
            elif result.ambiguous:
                # Stays "sending": it may have gone out, so it is not sent again.
                action = PaymentAction.REFUND_UNRESOLVED
            else:
                refund.state = PayoutState.PENDING
                action = PaymentAction.REFUND_RETRY
            await audit(
                session,
                action,
                ref_id=refund.id,
                bounty_id=bounty_id,
                amount_zatoshi=amount,
                txid=result.txid,
                detail=result.error,
            )


def _open_for_deposits(bounty: Bounty) -> bool:
    return bounty.state is not BountyState.CLOSED and bounty.close_requested_at is None
