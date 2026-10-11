from __future__ import annotations

import uuid
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import (
    Attempt,
    Bounty,
    BountyState,
    Canary,
    CanaryType,
    Deposit,
    Finding,
    LedgerEntry,
    LedgerKind,
    Payout,
    PayoutState,
    Sponsor,
    TargetConfig,
)
from agentbounty.services.ledger import append_entry, bounty_balance

HUNTER_ADDRESS = "utest1hunter"
# 2**53 + 1: the smallest positive integer a 64-bit float cannot represent.
BEYOND_FLOAT = 9_007_199_254_740_993


async def make_bounty(session: AsyncSession, reward_zatoshi: int = 1_000_000) -> Bounty:
    sponsor = Sponsor(email=f"{uuid.uuid4()}@example.com")
    session.add(sponsor)
    await session.flush()
    bounty = Bounty(
        sponsor_id=sponsor.id,
        reward_zatoshi=reward_zatoshi,
        fee_rate=Decimal("0.1"),
        deposit_code=f"dep-{uuid.uuid4().hex}",
    )
    session.add(bounty)
    await session.flush()
    return bounty


async def make_finding(session: AsyncSession, bounty: Bounty) -> Finding:
    config = TargetConfig(
        bounty_id=bounty.id,
        version=1,
        config_hash="hash-1",
        system_prompt_enc=b"ciphertext",
        tools_enc=b"ciphertext",
        model="test-model",
        params_json={"max_tokens": 16},
        entry_points_json=["user_message"],
    )
    session.add(config)
    await session.flush()
    canary = Canary(
        config_id=config.id,
        type=CanaryType.FORBIDDEN_TOOL,
        rule_json={"tool": "transfer_funds"},
        public_label="transfer_funds",
    )
    attempt = Attempt(
        bounty_id=bounty.id,
        config_hash=config.config_hash,
        payload_json=[{"slot": "user_message", "content": "attack"}],
        payout_address=HUNTER_ADDRESS,
        status_token=uuid.uuid4().hex,
    )
    session.add_all([canary, attempt])
    await session.flush()
    finding = Finding(attempt_id=attempt.id, canary_id=canary.id, tripped_runs=3)
    session.add(finding)
    await session.flush()
    return finding


def payout_for(finding: Finding, amount_zatoshi: int = 1_000_000) -> Payout:
    return Payout(
        finding_id=finding.id,
        amount_zatoshi=amount_zatoshi,
        fee_zatoshi=amount_zatoshi // 10,
        to_address=HUNTER_ADDRESS,
    )


async def test_balance_is_the_sum_of_ledger_entries(session: AsyncSession) -> None:
    bounty = await make_bounty(session)
    other = await make_bounty(session)
    assert await bounty_balance(session, bounty.id) == 0

    await append_entry(
        session,
        bounty_id=bounty.id,
        kind=LedgerKind.DEPOSIT,
        amount_zatoshi=1_100_000,
        ref_id=uuid.uuid4(),
    )
    payout_id = uuid.uuid4()
    await append_entry(
        session,
        bounty_id=bounty.id,
        kind=LedgerKind.PAYOUT,
        amount_zatoshi=-1_000_000,
        ref_id=payout_id,
    )
    await append_entry(
        session, bounty_id=other.id, kind=LedgerKind.DEPOSIT, amount_zatoshi=7, ref_id=uuid.uuid4()
    )

    assert await bounty_balance(session, bounty.id) == 1_100_000 - 1_000_000
    # A fee is a separate entry against the same payout.
    await append_entry(
        session, bounty_id=bounty.id, kind=LedgerKind.FEE, amount_zatoshi=-100_000, ref_id=payout_id
    )
    assert await bounty_balance(session, bounty.id) == 0
    assert await bounty_balance(session, other.id) == 7


async def test_a_finding_can_have_only_one_payout(session: AsyncSession) -> None:
    finding = await make_finding(session, await make_bounty(session))
    session.add(payout_for(finding))
    await session.flush()

    session.add(payout_for(finding))
    with pytest.raises(IntegrityError, match="uq_payouts_finding_id"):
        await session.flush()


async def test_amounts_are_exact_integers(session: AsyncSession) -> None:
    bounty = await make_bounty(session, reward_zatoshi=BEYOND_FLOAT)
    deposit = Deposit(bounty_id=bounty.id, txid=uuid.uuid4().hex, amount_zatoshi=BEYOND_FLOAT)
    session.add(deposit)
    await session.flush()
    await append_entry(
        session,
        bounty_id=bounty.id,
        kind=LedgerKind.DEPOSIT,
        amount_zatoshi=BEYOND_FLOAT,
        ref_id=deposit.id,
    )
    await append_entry(
        session, bounty_id=bounty.id, kind=LedgerKind.REFUND, amount_zatoshi=-1, ref_id=bounty.id
    )
    session.expunge_all()

    reward = await session.scalar(select(Bounty.reward_zatoshi).where(Bounty.id == bounty.id))
    stored = await session.scalar(select(Deposit.amount_zatoshi).where(Deposit.id == deposit.id))
    balance = await bounty_balance(session, bounty.id)
    for value, expected in ((reward, BEYOND_FLOAT), (stored, BEYOND_FLOAT), (balance, 2**53)):
        assert type(value) is int
        assert value == expected


@pytest.mark.parametrize("amount", [1.0, True, "5", Decimal(5)])
async def test_ledger_refuses_amounts_that_are_not_integers(
    session: AsyncSession, amount: object
) -> None:
    bounty = await make_bounty(session)
    with pytest.raises(TypeError, match="must be an int"):
        await append_entry(
            session,
            bounty_id=bounty.id,
            kind=LedgerKind.DEPOSIT,
            amount_zatoshi=amount,  # type: ignore[arg-type]
            ref_id=uuid.uuid4(),
        )


async def test_ledger_is_append_only(session: AsyncSession) -> None:
    bounty = await make_bounty(session)
    await append_entry(
        session, bounty_id=bounty.id, kind=LedgerKind.DEPOSIT, amount_zatoshi=5, ref_id=uuid.uuid4()
    )
    for statement in (
        "UPDATE ledger_entries SET amount_zatoshi = 500",
        "DELETE FROM ledger_entries",
        "TRUNCATE ledger_entries",
    ):
        with pytest.raises(DBAPIError, match="append-only"):
            async with session.begin_nested():
                await session.execute(text(statement))
    assert await bounty_balance(session, bounty.id) == 5


async def test_ledger_rejects_wrong_signs_and_duplicate_entries(session: AsyncSession) -> None:
    bounty = await make_bounty(session)
    ref_id = uuid.uuid4()
    for kind, amount in ((LedgerKind.DEPOSIT, -5), (LedgerKind.PAYOUT, 5), (LedgerKind.FEE, 0)):
        with pytest.raises(IntegrityError, match="sign_matches_kind"):
            async with session.begin_nested():
                await append_entry(
                    session, bounty_id=bounty.id, kind=kind, amount_zatoshi=amount, ref_id=ref_id
                )

    await append_entry(
        session, bounty_id=bounty.id, kind=LedgerKind.DEPOSIT, amount_zatoshi=5, ref_id=ref_id
    )
    # Crediting the same deposit again must fail rather than double the balance.
    with pytest.raises(IntegrityError, match="uq_ledger_entries_kind_ref_id"):
        async with session.begin_nested():
            await append_entry(
                session,
                bounty_id=bounty.id,
                kind=LedgerKind.DEPOSIT,
                amount_zatoshi=5,
                ref_id=ref_id,
            )
    assert await bounty_balance(session, bounty.id) == 5


async def test_defaults_enums_and_timestamps(session: AsyncSession) -> None:
    bounty = await make_bounty(session)
    finding = await make_finding(session, bounty)
    payout = payout_for(finding)
    session.add(payout)
    await session.flush()
    session.expunge_all()

    loaded = await session.get_one(Bounty, bounty.id)
    assert loaded.state is BountyState.DRAFT
    assert loaded.fee_rate == Decimal("0.1")
    assert isinstance(loaded.id, uuid.UUID)
    assert loaded.created_at.tzinfo is not None
    assert (await session.get_one(Payout, payout.id)).state is PayoutState.PENDING

    column_type = await session.scalar(
        text(
            "SELECT udt_name FROM information_schema.columns"
            " WHERE table_name = 'bounties' AND column_name = 'state'"
        )
    )
    assert column_type == "bounty_state"
    with pytest.raises(DBAPIError, match="invalid input value for enum"):
        async with session.begin_nested():
            await session.execute(text("UPDATE bounties SET state = 'archived'"))


async def test_a_canary_definition_must_match_its_type(session: AsyncSession) -> None:
    finding = await make_finding(session, await make_bounty(session))
    config_id = (await session.get_one(Canary, finding.canary_id)).config_id
    session.add(
        Canary(
            config_id=config_id,
            type=CanaryType.SECRET_LEAK,
            secret_enc=b"ciphertext",
            public_label="signing_reference",
        )
    )
    await session.flush()

    for broken in (
        Canary(config_id=config_id, type=CanaryType.SECRET_LEAK, public_label="no secret"),
        Canary(config_id=config_id, type=CanaryType.FORBIDDEN_TOOL, public_label="no rule"),
    ):
        savepoint = await session.begin_nested()
        session.add(broken)
        with pytest.raises(IntegrityError, match="definition_matches_type"):
            await session.flush()
        await savepoint.rollback()


async def test_ledger_entry_rows_carry_what_was_appended(session: AsyncSession) -> None:
    bounty = await make_bounty(session)
    ref_id = uuid.uuid4()
    entry = await append_entry(
        session, bounty_id=bounty.id, kind=LedgerKind.DEPOSIT, amount_zatoshi=42, ref_id=ref_id
    )
    session.expunge_all()
    loaded = await session.get_one(LedgerEntry, entry.id)
    assert (loaded.kind, loaded.amount_zatoshi, loaded.ref_id) == (LedgerKind.DEPOSIT, 42, ref_id)
    assert loaded.created_at.tzinfo is not None
