"""The payment service, on FakeWallet: funding, payouts, caps, crashes, closing and refunds."""

from __future__ import annotations

import ast
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select

import agentbounty
from agentbounty.models import (
    Bounty,
    BountyState,
    Deposit,
    DepositState,
    Finding,
    FindingState,
    LedgerEntry,
    LedgerKind,
    Payout,
    PayoutState,
    Refund,
)
from agentbounty.services.ledger import bounty_balance
from agentbounty.services.payment import SpendingCaps
from agentbounty.services.wallet import FakeWallet, SendResult
from agentbounty.workers import payment as payment_worker

from .platform import DEMO_CONFIG, FEE, HUNTER, REFUND, REWARD, STRONG, WEAK, Platform

ESCROWED = REWARD + FEE


async def ledger(platform: Platform, bounty_id: str | None = None) -> list[tuple[str, int]]:
    """Ledger entries as (kind, amount), in the order they were made."""
    query = select(LedgerEntry).order_by(LedgerEntry.created_at, LedgerEntry.kind)
    if bounty_id:
        query = query.where(LedgerEntry.bounty_id == uuid.UUID(bounty_id))
    async with platform.sessions() as session:
        return [(entry.kind.value, entry.amount_zatoshi) for entry in await session.scalars(query)]


async def balance(platform: Platform, bounty_id: str) -> int:
    async with platform.sessions() as session:
        return await bounty_balance(session, uuid.UUID(bounty_id))


async def rows(platform: Platform, model: type[Any]) -> list[Any]:
    async with platform.sessions() as session:
        return list(await session.scalars(select(model)))


async def bounty_state(platform: Platform, bounty_id: str) -> BountyState:
    async with platform.sessions() as session:
        return (await session.get_one(Bounty, uuid.UUID(bounty_id))).state


async def verified(platform: Platform, bounty_id: str) -> dict[str, str]:
    """Submit a winning attempt and have the worker judge it. Nothing is paid yet."""
    attempt = await platform.submit(bounty_id, STRONG)
    await platform.work()
    assert (await platform.status(attempt))["verdict"] == "verified"
    return attempt


# --- funding ------------------------------------------------------------------------------


async def test_a_confirmed_deposit_with_the_right_memo_takes_a_bounty_live(
    platform: Platform,
) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.draft()
    funding = await sponsor.fund(bounty_id)
    assert funding == {
        "state": "awaiting_deposit",
        "escrow_address": platform.wallet.address,
        "deposit_code": funding["deposit_code"],
        "amount_due_zatoshi": ESCROWED,
        "confirmations_required": 3,
    }
    assert funding["deposit_code"].startswith("dep-")
    assert (await platform.client.get("/v1/bounties")).json() == []
    # Asking again does not change the code a payment may already be carrying.
    assert await sponsor.fund(bounty_id) == funding

    # Seen, but not yet confirmed: nothing is credited.
    platform.wallet.simulate_deposit(ESCROWED, funding["deposit_code"], confirmations=1)
    await platform.payments.tick()
    (deposit,) = await rows(platform, Deposit)
    assert (deposit.state, deposit.confirmations, str(deposit.bounty_id)) == (
        DepositState.MATCHED,
        1,
        bounty_id,
    )
    assert await ledger(platform) == []
    assert await bounty_state(platform, bounty_id) is BountyState.AWAITING_DEPOSIT

    platform.wallet.mine(2)
    await platform.payments.tick()
    assert await bounty_state(platform, bounty_id) is BountyState.LIVE
    assert await ledger(platform, bounty_id) == [("deposit", ESCROWED)]
    (deposit,) = await rows(platform, Deposit)
    assert (deposit.state, deposit.confirmations, deposit.amount_zatoshi) == (
        DepositState.CREDITED,
        3,
        ESCROWED,
    )
    (listed,) = (await platform.client.get("/v1/bounties")).json()
    assert (listed["id"], listed["accepting_attempts"]) == (bounty_id, True)
    assert (await sponsor.fund(bounty_id, expect=409))["detail"] == "the bounty is already live"


async def test_a_deposit_is_credited_once_however_often_it_is_seen(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.funded(platform)
    for _ in range(3):
        await platform.payments.tick()
    # A restarted payment process reads the whole history again.
    restarted = platform.payment_service()
    await restarted.tick()
    platform.wallet.mine(50)
    await restarted.tick()
    await platform.payment_service().tick()

    assert await ledger(platform, bounty_id) == [("deposit", ESCROWED)]
    assert len(await rows(platform, Deposit)) == 1
    assert await balance(platform, bounty_id) == ESCROWED


async def test_a_bounty_goes_live_only_when_fully_funded(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.draft()
    funding = await sponsor.fund(bounty_id)
    platform.wallet.simulate_deposit(REWARD, funding["deposit_code"], confirmations=5)
    await platform.payments.tick()
    assert await bounty_state(platform, bounty_id) is BountyState.AWAITING_DEPOSIT
    assert (await sponsor.fund(bounty_id))["amount_due_zatoshi"] == FEE

    # The rest, and a little too much, in a second payment with the same code.
    platform.wallet.simulate_deposit(FEE + 500, funding["deposit_code"], confirmations=5)
    await platform.payments.tick()
    assert await bounty_state(platform, bounty_id) is BountyState.LIVE
    assert await ledger(platform, bounty_id) == [("deposit", REWARD), ("deposit", FEE + 500)]


async def test_deposits_that_match_nothing_are_recorded_and_never_credited(
    platform: Platform,
) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.draft()
    funding = await sponsor.fund(bounty_id)
    platform.wallet.simulate_deposit(700, "dep-0000000000000000", confirmations=9)
    platform.wallet.simulate_deposit(800, "", confirmations=9)
    platform.wallet.simulate_deposit(900, funding["deposit_code"].upper(), confirmations=9)
    for _ in range(2):
        await platform.payments.tick()

    deposits = sorted(await rows(platform, Deposit), key=lambda d: d.amount_zatoshi)
    assert [(d.state, d.bounty_id, d.memo_code) for d in deposits] == [
        (DepositState.UNMATCHED, None, "dep-0000000000000000"),
        (DepositState.UNMATCHED, None, None),
        (DepositState.UNMATCHED, None, funding["deposit_code"].upper()),
    ]
    assert await ledger(platform) == []
    assert await bounty_state(platform, bounty_id) is BountyState.AWAITING_DEPOSIT


async def test_funding_runs_the_listing_check_and_validates_the_refund_address(
    platform: Platform,
) -> None:
    sponsor = await platform.sponsor()
    empty = (await sponsor.call("POST", "/v1/bounties", 201, json={"reward_zatoshi": REWARD}))["id"]
    assert "upload a config" in (await sponsor.fund(empty, expect=409))["detail"]

    bounty_id = await sponsor.draft()
    path = f"/v1/bounties/{bounty_id}/fund"
    for address in ("t1Hsc1LR8yKnbbe3twRp88p6vFfC5t7DLbs", "not-an-address"):
        refused = await sponsor.call("POST", path, 422, json={"refund_address": address})
        assert "not a shielded testnet address" in refused["detail"]
    assert await bounty_state(platform, bounty_id) is BountyState.DRAFT
    await (await platform.sponsor("other@example.com")).call(
        "POST", path, 404, json={"refund_address": REFUND}
    )

    # Once it is waiting for its deposit, the config that was checked can no longer change.
    await sponsor.fund(bounty_id)
    await sponsor.call("PUT", f"/v1/bounties/{bounty_id}/config", 409, json=DEMO_CONFIG)


async def test_a_reward_can_only_be_claimed_at_a_shielded_address(platform: Platform) -> None:
    bounty_id = await (await platform.sponsor()).funded(platform)
    for address in ("t1Hsc1LR8yKnbbe3twRp88p6vFfC5t7DLbs", HUNTER[:-1] + "8", "hunter"):
        refused = await platform.submit(bounty_id, WEAK, payout_address=address, expect=422)
        assert "not a shielded testnet address" in str(refused)


# --- payouts ------------------------------------------------------------------------------


async def test_a_verified_finding_is_paid_exactly_once(platform: Platform) -> None:
    bounty_id = await (await platform.sponsor()).funded(platform)
    attempt = await verified(platform, bounty_id)
    assert (await platform.status(attempt))["payout"] is None
    (finding,) = await rows(platform, Finding)

    await platform.payments.tick()
    (sent,) = platform.wallet.sent
    assert (sent.to_address, sent.amount_zatoshi, sent.memo_text) == (
        HUNTER,
        REWARD,
        f"finding {finding.id}",
    )
    assert await ledger(platform, bounty_id) == [
        ("deposit", ESCROWED),
        ("payout", -REWARD),
        ("fee", -FEE),
    ]
    assert await balance(platform, bounty_id) == 0

    (payout,) = await rows(platform, Payout)
    assert (payout.finding_id, payout.state, payout.txid) == (
        finding.id,
        PayoutState.SENT,
        sent.txid,
    )
    assert (payout.amount_zatoshi, payout.fee_zatoshi, payout.to_address) == (REWARD, FEE, HUNTER)
    (finding,) = await rows(platform, Finding)
    assert finding.state is FindingState.PAID
    assert (await platform.status(attempt))["payout"] == {
        "state": "sent",
        "amount_zatoshi": REWARD,
        "txid": sent.txid,
    }

    # The payout worker runs again, and again after a restart: nothing more is sent.
    await platform.payments.tick()
    await platform.payments.pay_findings()
    await platform.payment_service().tick()
    assert len(platform.wallet.sent) == 1
    assert len(await rows(platform, Payout)) == 1
    assert len(await ledger(platform, bounty_id)) == 3
    # What is left in the wallet is the platform's fee.
    assert await platform.wallet.get_balance() == FEE


async def test_a_crash_after_the_payout_row_is_created_does_not_lose_or_double_the_payment(
    platform: Platform,
) -> None:
    bounty_id = await (await platform.sponsor()).funded(platform)
    await verified(platform, bounty_id)
    (finding,) = await rows(platform, Finding)

    # The process dies between step one and step three.
    await platform.payments._create_payout(finding.id)
    (payout,) = await rows(platform, Payout)
    assert (payout.state, payout.txid) == (PayoutState.PENDING, None)
    assert platform.wallet.sent == []

    # On restart the finding is skipped (it has a payout row) and the pending row is sent.
    restarted = platform.payment_service()
    await restarted._create_payout(finding.id)
    assert len(await rows(platform, Payout)) == 1
    await restarted.tick()
    await restarted.tick()
    assert len(platform.wallet.sent) == 1
    assert await balance(platform, bounty_id) == 0


class DiesAfterSending(FakeWallet):
    """A wallet whose process is killed just after the payment has gone out."""

    async def send_payment(
        self, to_address: str, amount_zatoshi: int, memo_text: str
    ) -> SendResult:
        await super().send_payment(to_address, amount_zatoshi, memo_text)
        raise KeyboardInterrupt("the payment process was killed")


async def test_a_crash_between_sending_and_recording_never_sends_again(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    wallet = DiesAfterSending()
    crashing = platform.payment_service(wallet)
    bounty_id = await sponsor.draft()
    funding = await sponsor.fund(bounty_id)
    wallet.simulate_deposit(ESCROWED, funding["deposit_code"], confirmations=3)
    await crashing.poll_deposits()
    await verified(platform, bounty_id)

    with pytest.raises(KeyboardInterrupt):
        await crashing.pay_findings()
    assert len(wallet.sent) == 1

    # The money left, but nothing recorded it. The row says so, and stays that way.
    (payout,) = await rows(platform, Payout)
    assert (payout.state, payout.txid) == (PayoutState.SENDING, None)
    restarted = platform.payment_service(wallet)
    for _ in range(3):
        await restarted.pay_findings()
    assert len(wallet.sent) == 1
    (payout,) = await rows(platform, Payout)
    assert payout.state is PayoutState.SENDING
    (finding,) = await rows(platform, Finding)
    assert finding.state is FindingState.VERIFIED
    # And the bounty cannot be closed and refunded around an unsettled payout.
    await sponsor.call("POST", f"/v1/bounties/{bounty_id}/close", json={})
    await restarted.close_bounties()
    assert await bounty_state(platform, bounty_id) is not BountyState.CLOSED
    assert await rows(platform, Refund) == []


async def test_a_send_that_clearly_failed_is_retried(platform: Platform) -> None:
    bounty_id = await (await platform.sponsor()).funded(platform)
    await verified(platform, bounty_id)
    platform.wallet.fail_next_send("lightwalletd unreachable")
    await platform.payments.tick()

    (payout,) = await rows(platform, Payout)
    assert (payout.state, payout.txid) == (PayoutState.PENDING, None)
    assert platform.wallet.sent == []
    assert await ledger(platform, bounty_id) == [("deposit", ESCROWED)]

    await platform.payments.tick()
    assert len(platform.wallet.sent) == 1
    assert (await rows(platform, Payout))[0].state is PayoutState.SENT
    assert await balance(platform, bounty_id) == 0


async def test_a_send_that_may_have_gone_out_is_not_retried(platform: Platform) -> None:
    bounty_id = await (await platform.sponsor()).funded(platform)
    await verified(platform, bounty_id)
    platform.wallet.fail_next_send("broadcast timed out", ambiguous=True)
    for _ in range(3):
        await platform.payments.tick()
    await platform.payment_service().tick()

    (payout,) = await rows(platform, Payout)
    assert (payout.state, payout.txid) == (PayoutState.SENDING, None)
    assert platform.wallet.sent == []
    assert await ledger(platform, bounty_id) == [("deposit", ESCROWED)]


# --- caps and balance ---------------------------------------------------------------------


async def assert_nothing_was_paid(platform: Platform, bounty_id: str, deposits: int = 1) -> None:
    assert platform.wallet.sent == []
    assert await rows(platform, Payout) == []
    assert len(await ledger(platform, bounty_id)) == deposits
    (finding,) = [f for f in await rows(platform, Finding) if str(f.bounty_id) == bounty_id]
    assert finding.state is FindingState.VERIFIED


async def test_a_payout_over_the_per_payment_cap_is_rejected_whole(platform: Platform) -> None:
    bounty_id = await (await platform.sponsor()).funded(platform)
    await verified(platform, bounty_id)
    capped = platform.payment_service(caps=SpendingCaps(per_payout=REWARD - 1))
    for _ in range(2):
        await capped.tick()
    await assert_nothing_was_paid(platform, bounty_id)
    assert await balance(platform, bounty_id) == ESCROWED

    # Nothing was left behind, so once the cap allows it the payout simply happens.
    await platform.payment_service(caps=SpendingCaps(per_payout=REWARD)).tick()
    assert [payment.amount_zatoshi for payment in platform.wallet.sent] == [REWARD]


async def test_the_daily_cap_holds_back_the_payout_that_would_cross_it(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    first, second = await sponsor.funded(platform), await sponsor.funded(platform)
    await verified(platform, first)
    await verified(platform, second)

    capped = platform.payment_service(caps=SpendingCaps(daily=REWARD + REWARD // 2))
    for _ in range(2):
        await capped.tick()
    assert [payment.amount_zatoshi for payment in platform.wallet.sent] == [REWARD]
    assert len(await rows(platform, Payout)) == 1
    states = sorted(finding.state.value for finding in await rows(platform, Finding))
    assert states == ["paid", "verified"]
    assert sorted([await balance(platform, first), await balance(platform, second)]) == [
        0,
        ESCROWED,
    ]

    await platform.payment_service(caps=SpendingCaps(daily=2 * REWARD)).tick()
    assert len(platform.wallet.sent) == 2


async def test_a_payout_the_bounty_cannot_cover_is_rejected_whole(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    # Live without a deposit, which is possible outside production.
    unfunded = await sponsor.live()
    await verified(platform, unfunded)
    # The wallet itself has money, from another sponsor's deposit: it must not be used.
    other = await sponsor.funded(platform)
    for _ in range(2):
        await platform.payments.tick()

    await assert_nothing_was_paid(platform, unfunded, deposits=0)
    assert await balance(platform, other) == ESCROWED
    assert await platform.wallet.get_balance() == ESCROWED


# --- closing and refunds ------------------------------------------------------------------


async def test_closing_refunds_the_remaining_balance_exactly_once(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.funded(platform)
    closing = await sponsor.call("POST", f"/v1/bounties/{bounty_id}/close", json={})
    assert closing == {"state": "live", "closing": True}

    # From the request on, the bounty takes no attempts and is off the public list.
    assert (await platform.client.get("/v1/bounties")).json() == []
    shown = (await platform.client.get(f"/v1/bounties/{bounty_id}")).json()
    assert shown["accepting_attempts"] is False
    assert "closing" in str(await platform.submit(bounty_id, STRONG, expect=409))
    # Nothing moved yet: the API process cannot pay.
    assert platform.wallet.sent == []

    await platform.payments.tick()
    assert await bounty_state(platform, bounty_id) is BountyState.CLOSED
    (sent,) = platform.wallet.sent
    assert (sent.to_address, sent.amount_zatoshi, sent.memo_text) == (
        REFUND,
        ESCROWED,
        f"refund {bounty_id}",
    )
    assert await ledger(platform, bounty_id) == [("deposit", ESCROWED), ("refund", -ESCROWED)]
    assert await balance(platform, bounty_id) == 0
    (refund,) = await rows(platform, Refund)
    assert (refund.state, refund.txid, refund.amount_zatoshi) == (
        PayoutState.SENT,
        sent.txid,
        ESCROWED,
    )

    for service in (platform.payments, platform.payment_service()):
        await service.tick()
        await service.close_bounties()
        await service.send_refunds()
    assert len(platform.wallet.sent) == 1
    assert len(await rows(platform, Refund)) == 1
    assert len(await ledger(platform, bounty_id)) == 2
    again = await sponsor.call("POST", f"/v1/bounties/{bounty_id}/close", 409, json={})
    assert again["detail"] == "the bounty is already closed"
    await sponsor.fund(bounty_id, expect=409)


async def test_closing_waits_for_attempts_in_flight_and_pays_a_break_among_them(
    platform: Platform,
) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.funded(platform)
    attempt = await platform.submit(bounty_id, STRONG)
    await sponsor.call("POST", f"/v1/bounties/{bounty_id}/close", json={})

    # The attempt was queued before the request: closing cannot dodge it.
    await platform.payments.tick()
    assert await bounty_state(platform, bounty_id) is BountyState.LIVE
    assert platform.wallet.sent == []

    await platform.work()
    assert (await platform.status(attempt))["verdict"] == "verified"
    await platform.payments.tick()
    await platform.payments.tick()
    assert await bounty_state(platform, bounty_id) is BountyState.CLOSED
    # The hunter is paid. Nothing but the fee is left, so there is nothing to refund.
    assert [(p.to_address, p.amount_zatoshi) for p in platform.wallet.sent] == [(HUNTER, REWARD)]
    assert await rows(platform, Refund) == []
    assert await balance(platform, bounty_id) == 0


async def test_closing_after_a_payout_refunds_only_what_is_left(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.draft()
    funding = await sponsor.fund(bounty_id)
    platform.wallet.simulate_deposit(ESCROWED + 12_345, funding["deposit_code"], confirmations=3)
    await platform.payments.tick()
    await verified(platform, bounty_id)
    await platform.payments.tick()

    other_address = {"refund_address": HUNTER}
    await sponsor.call("POST", f"/v1/bounties/{bounty_id}/close", json=other_address)
    await platform.payments.tick()
    assert [(p.to_address, p.amount_zatoshi) for p in platform.wallet.sent] == [
        (HUNTER, REWARD),
        (HUNTER, 12_345),
    ]
    assert await ledger(platform, bounty_id) == [
        ("deposit", ESCROWED + 12_345),
        ("payout", -REWARD),
        ("fee", -FEE),
        ("refund", -12_345),
    ]
    assert await balance(platform, bounty_id) == 0


async def test_closing_a_bounty_that_holds_nothing_sends_nothing(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.draft()
    await sponsor.call("POST", f"/v1/bounties/{bounty_id}/close", json={})
    await platform.payments.tick()
    assert await bounty_state(platform, bounty_id) is BountyState.CLOSED
    assert platform.wallet.sent == []
    assert await rows(platform, Refund) == []
    assert await ledger(platform, bounty_id) == []

    refused = await sponsor.call(
        "POST", f"/v1/bounties/{await sponsor.draft()}/close", 422, json={"refund_address": "t1bad"}
    )
    assert "not a shielded testnet address" in refused["detail"]


async def test_a_refund_follows_the_same_send_rules_as_a_payout(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.funded(platform)
    await sponsor.call("POST", f"/v1/bounties/{bounty_id}/close", json={})

    platform.wallet.fail_next_send("lightwalletd unreachable")
    await platform.payments.tick()
    (refund,) = await rows(platform, Refund)
    assert (refund.state, refund.txid) == (PayoutState.PENDING, None)
    # The ledger was debited with the refund row, so the balance is already zero.
    assert await balance(platform, bounty_id) == 0

    platform.wallet.fail_next_send("broadcast timed out", ambiguous=True)
    for _ in range(3):
        await platform.payments.tick()
    (refund,) = await rows(platform, Refund)
    assert (refund.state, refund.txid) == (PayoutState.SENDING, None)
    assert platform.wallet.sent == []
    assert len(await rows(platform, Refund)) == 1


async def test_a_deposit_that_arrives_after_closing_is_not_credited(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.draft()
    funding = await sponsor.fund(bounty_id)
    platform.wallet.simulate_deposit(ESCROWED, funding["deposit_code"], confirmations=1)
    await platform.payments.tick()
    await sponsor.call("POST", f"/v1/bounties/{bounty_id}/close", json={})
    platform.wallet.mine(5)
    await platform.payments.tick()

    (deposit,) = await rows(platform, Deposit)
    assert deposit.state is DepositState.UNMATCHED
    assert await ledger(platform, bounty_id) == []
    assert await bounty_state(platform, bounty_id) is BountyState.CLOSED
    assert platform.wallet.sent == []


# --- the ledger agrees with the wallet ----------------------------------------------------


async def test_the_ledger_and_the_wallet_agree_after_a_full_cycle(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    paid, refunded, open_ = [await sponsor.funded(platform) for _ in range(3)]
    await verified(platform, paid)
    await sponsor.call("POST", f"/v1/bounties/{refunded}/close", json={})
    for _ in range(2):
        await platform.payments.tick()

    async with platform.sessions() as session:
        totals = dict(
            (
                await session.execute(
                    select(LedgerEntry.kind, func.sum(LedgerEntry.amount_zatoshi)).group_by(
                        LedgerEntry.kind
                    )
                )
            ).all()
        )
    assert {kind.value: int(total) for kind, total in totals.items()} == {
        "deposit": 3 * ESCROWED,
        "payout": -REWARD,
        "fee": -FEE,
        "refund": -ESCROWED,
    }
    assert [await balance(platform, b) for b in (paid, refunded, open_)] == [0, 0, ESCROWED]
    # The wallet holds the open bounty's escrow and the fee earned on the payout.
    assert await platform.wallet.get_balance() == ESCROWED + FEE
    assert totals[LedgerKind.FEE] == -FEE


# --- the payment process ------------------------------------------------------------------


async def test_with_the_fake_wallet_awaited_deposits_arrive_by_themselves(
    platform: Platform,
) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.draft()
    await sponsor.fund(bounty_id)
    settings = platform.app.state.settings
    service, wallet = payment_worker.make_service(settings, platform.sessions)
    assert wallet is not None

    assert await payment_worker.autofund(platform.sessions, wallet, 3) == 1
    await service.tick()
    assert await bounty_state(platform, bounty_id) is BountyState.LIVE
    # Not again for a bounty that is funded, or whose deposit is already on its way.
    assert await payment_worker.autofund(platform.sessions, wallet, 3) == 0
    assert await ledger(platform, bounty_id) == [("deposit", ESCROWED)]


def test_only_the_payment_code_can_reach_a_wallet() -> None:
    """The API and the replay worker may check an address. They may not hold a wallet."""
    package = Path(agentbounty.__file__).parent
    holders = {"services/wallet.py", "services/payment.py", "workers/payment.py"}
    wallet_names = {"WalletBackend", "FakeWallet", "ZcashWallet", "make_wallet", "PaymentService"}
    for path in package.rglob("*.py"):
        relative = path.relative_to(package).as_posix()
        if relative in holders:
            continue
        imported = {
            alias.name
            for node in ast.walk(ast.parse(path.read_text()))
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert not imported & wallet_names, f"{relative} imports {imported & wallet_names}"
