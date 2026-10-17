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
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
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
    # Accounts are listed newest first.
    __table_args__ = (
        Index("ix_sponsors_created_at", "created_at"),
        CheckConstraint(
            "credit_limit IS NULL OR credit_limit >= 0", name="credit_limit_not_negative"
        ),
    )

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
    # A picture they uploaded themselves, which is shown in preference to Google's.
    uploaded_picture_url: Mapped[str | None] = mapped_column(Text)
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
    # The referral code the account has redeemed, which is what lets it take part in
    # bounties. Several accounts may hold one code, when it was made for several.
    referral_code: Mapped[str | None] = mapped_column(Text, index=True)
    # Let in by an admin, without a code.
    hunting_granted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # A sponsor code the account has redeemed, which lets it list agents and attack them.
    # Kept apart from referral_code so that a hunter invited to sponsor later holds both.
    sponsor_code: Mapped[str | None] = mapped_column(Text, index=True)
    # Let list agents by an admin, without a code.
    sponsoring_granted: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Made only to try the platform out. Shown to no one: not on the leaderboard, and not
    # in the admin's list of people. Set in the database, never through the API.
    is_demo: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # The platform's own hunter (services/house.py). There is one, it is made by the
    # worker that runs it, and a break it finds is never paid for.
    is_house: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # When an admin banned the account. A banned account can neither attack nor list agents.
    banned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Tokens the account may spend in any 24 hours, when an admin has set that by hand.
    # Unset, its tier decides (services/credits.py).
    credit_limit: Mapped[int | None] = mapped_column(Integer)
    # When an admin last wiped what the account had spent. Nothing before it counts.
    credits_reset_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The memo a deposit carries to pay for this account's plan, once it has asked for one.
    plan_code: Mapped[str | None] = mapped_column(Text, unique=True)
    # The plan runs until then (services/plans.py). Unset, or past: no plan.
    plan_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_now()


class ReferralCode(Base):
    """A code that lets an account take part in bounties: one account, unless it was made
    for more. How often it has been used is not stored here: it is used once for each
    account that holds it."""

    __tablename__ = "referral_codes"
    __table_args__ = (
        Index("ix_referral_codes_created_at", "created_at"),
        CheckConstraint("kind IN ('hunter', 'sponsor')", name="kind_known"),
        CheckConstraint("max_uses > 0", name="max_uses_positive"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    code: Mapped[str] = mapped_column(Text, unique=True)
    # What it lets its account do: "hunter" to attack, "sponsor" to list agents and attack.
    kind: Mapped[str] = mapped_column(Text, default="hunter", server_default="hunter")
    # How many accounts it admits before it is used up.
    max_uses: Mapped[int] = mapped_column(Integer, default=1, server_default=text("1"))
    # The ambassador who made it. A code made by the operator has no owner.
    owner_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sponsors.id"), index=True)
    created_at: Mapped[datetime] = created_now()


class AdminAction(Base):
    """Something an admin did. Append-only, like the ledger: the database refuses edits."""

    __tablename__ = "admin_actions"
    __table_args__ = (Index("ix_admin_actions_created_at", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    admin_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sponsors.id"), index=True)
    action: Mapped[str] = mapped_column(Text)
    target_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sponsors.id"))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_now()


class PilotApplication(Base):
    """A company asking to become a design partner, kept private for the operator."""

    __tablename__ = "pilot_applications"
    __table_args__ = (
        CheckConstraint("stage IN ('prototype', 'beta', 'production')", name="stage_known"),
        CheckConstraint(
            "status IN ('new', 'contacted', 'qualified', 'pilot', 'paid', 'closed')",
            name="status_known",
        ),
        Index("ix_pilot_applications_created_at", "created_at"),
        Index("ix_pilot_applications_status", "status"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    work_email: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)
    company: Mapped[str] = mapped_column(Text)
    company_website: Mapped[str | None] = mapped_column(Text)
    role: Mapped[str | None] = mapped_column(Text)
    stage: Mapped[str] = mapped_column(Text)
    use_case: Mapped[str] = mapped_column(Text)
    goals_json: Mapped[list[str]] = mapped_column(JSONB)
    source: Mapped[str] = mapped_column(Text, default="direct", server_default="direct")
    status: Mapped[str] = mapped_column(Text, default="new", server_default="new")
    notes: Mapped[str] = mapped_column(Text, default="", server_default="")
    created_at: Mapped[datetime] = created_now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )


class AssuranceEngagement(Base):
    """A private, managed assessment of one sponsor-owned agent.

    The categorical scope is useful for reporting and stays queryable. Free-form
    threat-model details and operator notes are encrypted because they can reveal
    production architecture, controls, and failure modes.
    """

    __tablename__ = "assurance_engagements"
    __table_args__ = (
        UniqueConstraint("bounty_id"),
        CheckConstraint(
            "status IN ('draft', 'requested', 'scoping', 'testing', 'remediation',"
            " 'retest', 'complete', 'cancelled')",
            name="status_known",
        ),
        CheckConstraint("service IN ('sprint', 'continuous')", name="service_known"),
        CheckConstraint("stage IN ('prototype', 'beta', 'production')", name="stage_known"),
        CheckConstraint(
            "deployment IN ('managed', 'private_cloud', 'on_prem')",
            name="deployment_known",
        ),
        CheckConstraint("quoted_price_cents >= 0", name="quoted_price_not_negative"),
        Index("ix_assurance_engagements_status", "status"),
        Index("ix_assurance_engagements_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bounty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bounties.id"), index=True)
    service: Mapped[str] = mapped_column(Text, default="sprint", server_default="sprint")
    status: Mapped[str] = mapped_column(Text, default="draft", server_default="draft")
    stage: Mapped[str] = mapped_column(Text)
    deployment: Mapped[str] = mapped_column(Text, default="managed", server_default="managed")
    untrusted_inputs_json: Mapped[list[str]] = mapped_column(JSONB)
    sensitive_data_json: Mapped[list[str]] = mapped_column(JSONB)
    consequential_actions_json: Mapped[list[str]] = mapped_column(JSONB)
    workflow_enc: Mapped[bytes] = mapped_column(LargeBinary)
    feared_failure_enc: Mapped[bytes] = mapped_column(LargeBinary)
    identity_controls_enc: Mapped[bytes] = mapped_column(LargeBinary)
    human_approval_enc: Mapped[bytes] = mapped_column(LargeBinary)
    operator_notes_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    limitations_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    quoted_price_cents: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    tested_config_hash: Mapped[str | None] = mapped_column(String(64))
    tested_config_version: Mapped[int | None] = mapped_column(Integer)
    requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_now()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), onupdate=text("now()")
    )


class PlatformSetting(Base):
    """A setting the owner changes while the service runs. The value is ciphertext."""

    __tablename__ = "platform_settings"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    value_enc: Mapped[bytes] = mapped_column(LargeBinary)
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sponsors.id"))
    updated_at: Mapped[datetime] = created_now()


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
    # Set when the sponsor deletes a draft or a closed bounty. It is then shown to no one;
    # the row stays because money records point at it.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # The sponsor lets the attacks that broke this agent be replayed against other
    # sponsors' agents, as part of the shared library. Off unless they turn it on.
    shares_breaks: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Scanned again every day while it is live, not only when its config changes.
    watch: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    created_at: Mapped[datetime] = created_now()


class TargetConfig(Base):
    __tablename__ = "target_configs"
    __table_args__ = (
        UniqueConstraint("bounty_id", "version"),
        CheckConstraint(
            "(endpoint_base IS NULL) = (endpoint_key_enc IS NULL)", name="endpoint_has_key"
        ),
    )

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
    # The sponsor's frame around each entry point, by slot: the document's name, the tool
    # that carries a planted result, the URL of the page, how many turns may be scripted.
    # none_as_null: a config that declares no scaffold stores SQL NULL, not {}.
    scaffold_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    # What the agent is asked to do when the hunter does not write the user's message.
    # Shown to hunters: it is the job their planted text has to derail.
    task: Mapped[str | None] = mapped_column(Text)
    # The sponsor's own model endpoint, when replays go there and not to the platform's
    # provider, and the key for it as ciphertext.
    endpoint_base: Mapped[str | None] = mapped_column(Text)
    endpoint_key_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = created_now()


class Canary(Base):
    __tablename__ = "canaries"
    __table_args__ = (
        CheckConstraint(
            "(type IN ('secret_leak', 'system_prompt_leak', 'phishing_link_injection',"
            " 'malicious_code_injection') AND secret_enc IS NOT NULL AND rule_json IS NULL)"
            " OR (type IN ('forbidden_tool', 'unauthorized_action', 'policy_violation')"
            " AND rule_json IS NOT NULL AND secret_enc IS NULL)"
            " OR (type = 'data_exfiltration' AND secret_enc IS NOT NULL"
            " AND rule_json IS NOT NULL)",
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
    # What every attack asks: what has this account, this address, this bounty done lately?
    __table_args__ = (
        Index("ix_attempts_account_submitted", "account_id", "submitted_at"),
        Index("ix_attempts_bounty_submitted", "bounty_id", "submitted_at"),
        Index("ix_attempts_address_submitted", "payout_address", "submitted_at"),
        Index("ix_attempts_submitted_at", "submitted_at"),
    )

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
    # When a worker last claimed the attempt. For a queued attempt delayed by provider
    # throttling, a future value is the earliest time a worker may claim it again.
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    tries: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # The account that made the attempt, when it was made by one, and what it cost in
    # credits. An account's allowance is what it has not spent lately (services/credits.py).
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sponsors.id"), index=True)
    cost_credits: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # Replays finished so far in the current try, for the hunter watching. Decides nothing.
    runs_done: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))


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


class SavedAttack(Base):
    """A verified break kept as both a regression case and a corpus probe.

    It deliberately stores only the attack and canary kind. Canary definitions, rules,
    planted values and transcripts remain in their encrypted/source records.
    """

    __tablename__ = "saved_attacks"

    id: Mapped[uuid.UUID] = uuid_pk()
    finding_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("findings.id"), unique=True, index=True
    )
    bounty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bounties.id"), index=True)
    broken_config_hash: Mapped[str] = mapped_column(Text, index=True)
    canary_type: Mapped[CanaryType] = mapped_column(pg_enum(CanaryType, "canary_type"), index=True)
    payload_json: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_now()


class Deposit(Base):
    __tablename__ = "deposits"
    __table_args__ = (CheckConstraint("amount_zatoshi > 0", name="amount_positive"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    # Null until the memo has been matched to a bounty.
    bounty_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("bounties.id"), index=True)
    txid: Mapped[str] = mapped_column(Text, unique=True)
    amount_zatoshi: Mapped[int] = mapped_column(BigInteger)
    memo_code: Mapped[str | None] = mapped_column(Text)
    # Set when the memo was an account's plan code: the deposit pays for a plan, not a bounty.
    account_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sponsors.id"))
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


class HouseHunt(Base):
    """The house hunter's go at one config of a bounty: the attacker agent, a few tries."""

    __tablename__ = "house_hunts"
    __table_args__ = (
        # A config is hunted once. A patch makes a new config, which is hunted afresh.
        UniqueConstraint("bounty_id", "config_hash"),
        CheckConstraint("state IN ('running', 'done')", name="state_known"),
        CheckConstraint(
            "outcome IS NULL OR outcome IN ('held', 'broke', 'stopped')", name="outcome_known"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bounty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bounties.id"), index=True)
    config_hash: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(
        Text, default="running", server_default="running", index=True
    )
    # How it ended: the agent held, it broke, or the hunt could not go on (and why).
    outcome: Mapped[str | None] = mapped_column(Text)
    detail: Mapped[str | None] = mapped_column(Text)
    # Attacks the model was asked for, whether or not each could be submitted.
    tries: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    # When a worker last showed it was still on it. One that has gone quiet is taken over.
    claimed_at: Mapped[datetime] = created_now()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_now()


class Scan(Base):
    """A run of the baseline playbook, the bounty's own paid breaks and, where the sponsor
    may draw on it, the shared library, against one config of a bounty."""

    __tablename__ = "scans"
    __table_args__ = (
        UniqueConstraint("bounty_id", "config_hash"),
        CheckConstraint("state IN ('queued', 'running', 'done', 'error')", name="state_known"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bounty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bounties.id"), index=True)
    config_hash: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, default="queued", server_default="queued", index=True)
    # What each probe did and what the agent said, encrypted: it may hold the agent's secrets.
    results_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # When it was last run to the end. A scan may be run again; this is the latest.
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Whether it replays the shared library too. Decided when it is asked for.
    with_library: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    # Set when the sponsor explicitly asks for every corpus attack of one deterministic kind.
    corpus_type: Mapped[CanaryType | None] = mapped_column(pg_enum(CanaryType, "canary_type"))
    created_at: Mapped[datetime] = created_now()


class ContinuousRun(Base):
    """A corpus replay against one immutable agent version.

    The result is encrypted because even a safe summary can be derived from a
    sponsor's private configuration.  The row is also the usage record for the
    account's monthly continuous-testing plan; deposits remain the payment
    record, just as they do for the existing Watch plan.
    """

    __tablename__ = "continuous_runs"
    __table_args__ = (
        CheckConstraint("state IN ('queued', 'running', 'done', 'error')", name="state_known"),
        CheckConstraint(
            "trigger IN ('version', 'ci', 'schedule', 'model_update')", name="trigger_known"
        ),
        Index("ix_continuous_runs_bounty_finished", "bounty_id", "finished_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    bounty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bounties.id"), index=True)
    config_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("target_configs.id"), index=True)
    config_hash: Mapped[str] = mapped_column(Text, index=True)
    trigger: Mapped[str] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, default="queued", server_default="queued", index=True)
    # Ciphertext of per-probe verdicts. It deliberately does not duplicate attack payloads.
    results_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_now()


class ControlProject(Base):
    """An isolated behavior-control workspace owned by one trycanari account."""

    __tablename__ = "control_projects"
    __table_args__ = (
        CheckConstraint("adapter_kind IN ('text', 'activation')", name="adapter_kind_known"),
        Index("ix_control_projects_sponsor_created", "sponsor_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    sponsor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sponsors.id"), index=True)
    name: Mapped[str] = mapped_column(Text)
    model: Mapped[str] = mapped_column(Text)
    adapter_kind: Mapped[str] = mapped_column(Text, default="text", server_default="text")
    # Granular operations this workspace is allowed to execute.
    operations_json: Mapped[list[str]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_now()


class ControlRun(Base):
    """One encrypted control-workflow result; raw inputs are not columns."""

    __tablename__ = "control_runs"
    __table_args__ = (
        CheckConstraint("overall IN ('pass', 'fail', 'unknown')", name="overall_known"),
        Index("ix_control_runs_project_created", "project_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("control_projects.id"), index=True)
    input_digest: Mapped[str] = mapped_column(String(64))
    response_digest: Mapped[str] = mapped_column(String(64))
    overall: Mapped[str] = mapped_column(Text)
    result_enc: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = created_now()


class ControlEvent(Base):
    """Append-only audit event for a behavior-control run."""

    __tablename__ = "control_events"
    __table_args__ = (Index("ix_control_events_run_created", "run_id", "created_at"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("control_runs.id"), index=True)
    action: Mapped[str] = mapped_column(Text)
    detail_json: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = created_now()


class Notification(Base):
    """Something an account is told: shown under the site's bell, and sent as an email.

    Written in the transaction that makes the event true, so it exists exactly when the
    event does. It is also the email outbox: `email_state` says whether it has gone out.
    """

    __tablename__ = "notifications"
    __table_args__ = (
        # An account is told of one event once, however often the step that tells it runs.
        UniqueConstraint("account_id", "kind", "finding_id"),
        CheckConstraint(
            "kind IN ('bounty_broken', 'break_verified', 'payout_held', 'payout_sent',"
            " 'reward_paid')",
            name="kind_known",
        ),
        CheckConstraint(
            "email_state IN ('pending', 'sending', 'sent', 'failed', 'skipped')",
            name="email_state_known",
        ),
        Index("ix_notifications_account_created", "account_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sponsors.id"))
    kind: Mapped[str] = mapped_column(Text)
    bounty_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("bounties.id"))
    finding_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("findings.id"))
    # The other party, when the account is told who: the hunter, for a sponsor.
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sponsors.id"))
    amount_zatoshi: Mapped[int | None] = mapped_column(BigInteger)
    txid: Mapped[str | None] = mapped_column(Text)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # pending until the mail process takes it; "sending" is committed before the provider
    # is called, so a crash in between never sends the same email twice.
    email_state: Mapped[str] = mapped_column(
        Text, default="pending", server_default="pending", index=True
    )
    email_tries: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
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
