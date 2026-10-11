"""The tables.

Money is BIGINT zatoshi (1 ZEC = 100_000_000 zatoshi). No table stores a
balance: a bounty's balance is the sum of its ledger entries.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    LargeBinary,
    Numeric,
    Text,
    UniqueConstraint,
    Uuid,
    false,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from agentbounty.models.base import Base, created_now, uuid_pk
from agentbounty.models.enums import (
    AttemptState,
    BountyState,
    CanaryType,
    DepositState,
    FindingState,
    LedgerKind,
    PaymentAction,
    PayoutState,
    RearmState,
    pg_enum,
)


class Sponsor(Base):
    __tablename__ = "sponsors"

    id: Mapped[uuid.UUID] = uuid_pk()
    email: Mapped[str] = mapped_column(Text, unique=True)
    # Unknown until the sponsor funds a bounty.
    refund_address: Mapped[str | None] = mapped_column(Text)
    # SHA-256 of the sponsor's API key. The key itself is shown once and never stored.
    api_key_hash: Mapped[str | None] = mapped_column(Text, unique=True)
    # Google's identifier for the account, for a sponsor who signs in with Google.
    google_sub: Mapped[str | None] = mapped_column(Text, unique=True)
    # What to call them and the picture to show, when known. Google supplies both.
    name: Mapped[str | None] = mapped_column(Text)
    picture_url: Mapped[str | None] = mapped_column(Text)
    # What they say about themselves. All of it is theirs to give or leave out.
    github: Mapped[str | None] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    location: Mapped[str | None] = mapped_column(Text)
    website: Mapped[str | None] = mapped_column(Text)
    bio: Mapped[str | None] = mapped_column(Text)
    # Runs the platform: may make ambassadors, other admins and referral codes of any size.
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # May hand out referral codes.
    is_ambassador: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # The referral code the account has redeemed, which is what lets it take part in bounties.
    referral_code: Mapped[str | None] = mapped_column(Text, index=True)
    created_at: Mapped[datetime] = created_now()


class ReferralCode(Base):
    """A code that admits a fixed number of new accounts."""

    __tablename__ = "referral_codes"
    __table_args__ = (CheckConstraint("max_uses > 0", name="max_uses_positive"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(Text, unique=True)
    # The ambassador who made it. A code made by the operator has no owner.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sponsors.id"), index=True)
    max_uses: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_now()


class Bounty(Base):
    __tablename__ = "bounties"
    __table_args__ = (
        CheckConstraint("reward_zatoshi > 0", name="reward_positive"),
        CheckConstraint("fee_rate >= 0 AND fee_rate <= 1", name="fee_rate_fraction"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    sponsor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sponsors.id"), index=True)
    # Shown on the public listing. Written by the sponsor, so always escaped on output.
    title: Mapped[str] = mapped_column(
        Text, default="Untitled agent", server_default="Untitled agent"
    )
    description: Mapped[str] = mapped_column(Text, default="", server_default="")
    state: Mapped[BountyState] = mapped_column(
        pg_enum(BountyState, "bounty_state"), default=BountyState.DRAFT, server_default="draft"
    )
    reward_zatoshi: Mapped[int] = mapped_column(BigInteger)
    # A fraction of the reward: 0.1 is 10%.
    fee_rate: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    # The memo a sponsor puts on a deposit so it can be matched to this bounty.
    deposit_code: Mapped[str] = mapped_column(Text, unique=True)
    # bounties and target_configs refer to each other, so this key is added after both exist.
    live_config_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("target_configs.id", use_alter=True)
    )
    # Set when the sponsor asks to close. The bounty takes no new attempts from then on
    # and becomes closed once the attempts already in flight have been judged and paid.
    close_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_now()


class TargetConfig(Base):
    __tablename__ = "target_configs"
    __table_args__ = (UniqueConstraint("bounty_id", "version"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    bounty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bounties.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    config_hash: Mapped[str] = mapped_column(Text, index=True)
    # Ciphertext. The plaintext never reaches the database.
    system_prompt_enc: Mapped[bytes] = mapped_column(LargeBinary)
    tools_enc: Mapped[bytes] = mapped_column(LargeBinary)
    model: Mapped[str] = mapped_column(Text)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    entry_points_json: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_now()


class Canary(Base):
    __tablename__ = "canaries"
    __table_args__ = (
        CheckConstraint(
            "(type = 'secret_leak' AND secret_enc IS NOT NULL AND rule_json IS NULL)"
            " OR (type = 'forbidden_tool' AND rule_json IS NOT NULL AND secret_enc IS NULL)",
            name="definition_matches_type",
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    config_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("target_configs.id"), index=True)
    type: Mapped[CanaryType] = mapped_column(pg_enum(CanaryType, "canary_type"))
    secret_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    # none_as_null: a missing rule is SQL NULL, not the JSON value null.
    rule_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    # Shown to hunters. The secret and the rule never are.
    public_label: Mapped[str] = mapped_column(Text)


class Attempt(Base):
    __tablename__ = "attempts"

    id: Mapped[uuid.UUID] = uuid_pk()
    bounty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bounties.id"), index=True)
    # The config the attempt was submitted against; a verdict binds to this hash.
    config_hash: Mapped[str] = mapped_column(Text)
    payload_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    payout_address: Mapped[str] = mapped_column(Text)
    state: Mapped[AttemptState] = mapped_column(
        pg_enum(AttemptState, "attempt_state"),
        default=AttemptState.QUEUED,
        server_default="queued",
        index=True,
    )
    # SHA-256 of the token the hunter reads the result with.
    status_token: Mapped[str] = mapped_column(Text, unique=True)
    submitted_at: Mapped[datetime] = created_now()
    # When a worker last claimed the attempt, and how many times one has.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tries: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # The account that made the attempt, when it was made by one, and what it cost in
    # credits. An account's allowance is what it has not spent lately (services/credits.py).
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sponsors.id"), index=True)
    cost_credits: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (UniqueConstraint("attempt_id", "run_index"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("attempts.id"), index=True)
    run_index: Mapped[int] = mapped_column(Integer)
    # Ciphertext. A transcript of a successful leak contains the secret, and evidence can
    # quote it, so neither is stored in the clear.
    transcript_enc: Mapped[bytes] = mapped_column(LargeBinary)
    tripped_canaries_json: Mapped[list[str]] = mapped_column(JSONB)
    evidence_enc: Mapped[bytes] = mapped_column(LargeBinary)
    token_usage: Mapped[int] = mapped_column(Integer)


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (
        # An attempt is judged once, so it yields at most one finding.
        UniqueConstraint("attempt_id"),
        # One finding per hole: a config can be broken, and paid for, only once.
        UniqueConstraint("bounty_id", "config_hash"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    attempt_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("attempts.id"))
    bounty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bounties.id"))
    config_hash: Mapped[str] = mapped_column(Text)
    canary_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("canaries.id"), index=True)
    tripped_runs: Mapped[int] = mapped_column(Integer)
    state: Mapped[FindingState] = mapped_column(
        pg_enum(FindingState, "finding_state"),
        default=FindingState.VERIFIED,
        server_default="verified",
    )
    verified_at: Mapped[datetime] = created_now()


class Deposit(Base):
    __tablename__ = "deposits"
    __table_args__ = (CheckConstraint("amount_zatoshi > 0", name="amount_positive"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    # Null until the memo has been matched to a bounty.
    bounty_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bounties.id"), index=True)
    txid: Mapped[str] = mapped_column(Text, unique=True)
    amount_zatoshi: Mapped[int] = mapped_column(BigInteger)
    memo_code: Mapped[str | None] = mapped_column(Text)
    confirmations: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    state: Mapped[DepositState] = mapped_column(
        pg_enum(DepositState, "deposit_state"), default=DepositState.SEEN, server_default="seen"
    )
    seen_at: Mapped[datetime] = created_now()


class Payout(Base):
    __tablename__ = "payouts"
    __table_args__ = (
        # One payout per finding. This constraint, not application code, is the guarantee.
        UniqueConstraint("finding_id"),
        CheckConstraint("amount_zatoshi > 0", name="amount_positive"),
        CheckConstraint("fee_zatoshi >= 0", name="fee_not_negative"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("findings.id"))
    amount_zatoshi: Mapped[int] = mapped_column(BigInteger)
    fee_zatoshi: Mapped[int] = mapped_column(BigInteger)
    to_address: Mapped[str] = mapped_column(Text)
    txid: Mapped[str | None] = mapped_column(Text)
    state: Mapped[PayoutState] = mapped_column(
        pg_enum(PayoutState, "payout_state"), default=PayoutState.PENDING, server_default="pending"
    )
    created_at: Mapped[datetime] = created_now()


class Refund(Base):
    """The unspent escrow going back to the sponsor when a bounty closes."""

    __tablename__ = "refunds"
    __table_args__ = (
        # One refund per bounty, guaranteed here and not by application code.
        UniqueConstraint("bounty_id"),
        CheckConstraint("amount_zatoshi > 0", name="amount_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bounty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bounties.id"))
    amount_zatoshi: Mapped[int] = mapped_column(BigInteger)
    to_address: Mapped[str] = mapped_column(Text)
    txid: Mapped[str | None] = mapped_column(Text)
    state: Mapped[PayoutState] = mapped_column(
        pg_enum(PayoutState, "payout_state"), default=PayoutState.PENDING, server_default="pending"
    )
    created_at: Mapped[datetime] = created_now()


class RearmCheck(Base):
    """A replay of every paid attack against a patched config, before the bounty reopens."""

    __tablename__ = "rearm_checks"
    # One check per config: a config that still breaks must be patched, not checked again.
    __table_args__ = (UniqueConstraint("bounty_id", "config_hash"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    bounty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bounties.id"), index=True)
    config_hash: Mapped[str] = mapped_column(Text)
    state: Mapped[RearmState] = mapped_column(
        pg_enum(RearmState, "rearm_state"),
        default=RearmState.QUEUED,
        server_default="queued",
        index=True,
    )
    # The most runs any paid attack tripped a canary in, out of the runs per attempt.
    tripped_runs: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_now()


class PaymentAudit(Base):
    """Append-only: one row for every payment action, written with the action itself."""

    __tablename__ = "payment_audit"

    id: Mapped[uuid.UUID] = uuid_pk()
    action: Mapped[PaymentAction] = mapped_column(pg_enum(PaymentAction, "payment_action"))
    bounty_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bounties.id"), index=True)
    # The deposit, payout or refund the action is about.
    ref_id: Mapped[uuid.UUID] = mapped_column(Uuid, index=True)
    amount_zatoshi: Mapped[int | None] = mapped_column(BigInteger)
    txid: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_now()


class LedgerEntry(Base):
    """Append-only: a trigger created in the first migration rejects UPDATE and DELETE."""

    __tablename__ = "ledger_entries"
    __table_args__ = (
        # Deposits add to a bounty's balance; everything else takes from it.
        CheckConstraint(
            "(kind = 'deposit' AND amount_zatoshi > 0)"
            " OR (kind <> 'deposit' AND amount_zatoshi < 0)",
            name="sign_matches_kind",
        ),
        # The same deposit or payout cannot be entered twice.
        UniqueConstraint("kind", "ref_id"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bounty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bounties.id"), index=True)
    kind: Mapped[LedgerKind] = mapped_column(pg_enum(LedgerKind, "ledger_kind"))
    amount_zatoshi: Mapped[int] = mapped_column(BigInteger)
    # The deposit or payout this entry records.
    ref_id: Mapped[uuid.UUID] = mapped_column(Uuid)
    created_at: Mapped[datetime] = created_now()
