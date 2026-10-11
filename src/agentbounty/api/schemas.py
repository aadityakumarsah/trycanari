"""Request and response bodies."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from agentbounty.models import AttemptState, BountyState, CanaryType, PayoutState, RearmState
from agentbounty.services.harness import MAX_SLOT_CHARACTERS, EntryPoint
from agentbounty.services.judge import Operator

MAX_ZATOSHI = 21_000_000 * 100_000_000

Name = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")]
Label = Annotated[str, Field(min_length=1, max_length=64)]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- sponsors ---------------------------------------------------------------------------


class SponsorCreate(Body):
    email: str = Field(min_length=3, max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class SponsorCreated(BaseModel):
    sponsor_id: uuid.UUID
    # Shown once. Only its hash is stored.
    api_key: str


class BountyCreate(Body):
    reward_zatoshi: StrictInt = Field(gt=0, le=MAX_ZATOSHI)
    # Both are public.
    title: str = Field(default="Untitled agent", min_length=1, max_length=120)
    description: str = Field(default="", max_length=2_000)


class BountyCreated(BaseModel):
    id: uuid.UUID
    state: BountyState


class ToolIn(Body):
    name: Name
    description: str = Field(default="", max_length=2_000)
    # JSON Schema for the tool's arguments.
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    # What the mock returns when the agent calls the tool. No real tool ever runs.
    result: str = Field(default="ok", max_length=10_000)


class ConfigIn(Body):
    system_prompt: str = Field(min_length=1, max_length=100_000)
    model: str = Field(min_length=1, max_length=200)
    tools: list[ToolIn] = Field(default_factory=list, max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)
    entry_points: list[EntryPoint] = Field(default_factory=lambda: [EntryPoint.USER_MESSAGE])


class ConfigOut(BaseModel):
    version: int
    config_hash: str


class PredicateIn(Body):
    argument: str = Field(min_length=1, max_length=64)
    operator: Operator
    value: Any


class SecretCanaryIn(Body):
    type: Literal[CanaryType.SECRET_LEAK]
    public_label: Label
    secret: str = Field(max_length=1_000)


class ToolCanaryIn(Body):
    type: Literal[CanaryType.FORBIDDEN_TOOL]
    public_label: Label
    tool: Name
    # Without a predicate, any call to the tool trips the canary.
    predicate: PredicateIn | None = None


CanaryIn = Annotated[SecretCanaryIn | ToolCanaryIn, Field(discriminator="type")]


class CanaryOut(BaseModel):
    id: uuid.UUID
    # Adding a canary changes the config's hash.
    config_hash: str


class ActivateOut(BaseModel):
    state: BountyState
    config_hash: str


class FundIn(Body):
    # Where the unspent escrow goes when the bounty closes.
    refund_address: str = Field(min_length=1, max_length=512)


class FundOut(BaseModel):
    state: BountyState
    # Send the amount due to this address with the deposit code as the memo.
    escrow_address: str
    deposit_code: str
    amount_due_zatoshi: int
    # Confirmations the deposit needs before the bounty goes live.
    confirmations_required: int


class CloseIn(Body):
    refund_address: str | None = Field(default=None, min_length=1, max_length=512)


class CloseOut(BaseModel):
    state: BountyState
    # True until attempts in flight have finished and the refund has been entered.
    closing: bool


class CanaryPublic(BaseModel):
    type: CanaryType
    public_label: str


class FillIn(Body):
    slot: EntryPoint
    content: str = Field(min_length=1, max_length=MAX_SLOT_CHARACTERS)


class ToolCallOut(BaseModel):
    name: str
    arguments: dict[str, Any]


class PayoutOut(BaseModel):
    state: PayoutState
    amount_zatoshi: int
    txid: str | None


class StateOut(BaseModel):
    state: BountyState


class SponsorBounty(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    state: BountyState
    # Close has been requested and is waiting for attempts in flight.
    closing: bool
    reward_zatoshi: int
    balance_zatoshi: int
    # Reward plus fee: what the escrow must hold for the bounty to go live.
    required_zatoshi: int


class ToolOut(BaseModel):
    name: str
    description: str
    parameters: dict[str, Any]
    result: str


class ConfigDetail(BaseModel):
    version: int
    config_hash: str
    # Whether this version is the one pinned live, or a newer one not yet activated.
    live: bool
    system_prompt: str
    model: str
    tools: list[ToolOut]
    params: dict[str, Any]
    entry_points: list[EntryPoint]


class CanaryDetail(BaseModel):
    id: uuid.UUID
    type: CanaryType
    public_label: str
    secret: str | None
    rule: dict[str, Any] | None


class RearmOut(BaseModel):
    # queued or running while the paid attack is being replayed; then passed or failed.
    state: RearmState
    config_hash: str
    # The most runs the paid attack tripped a canary in. Three or more is a failure.
    tripped_runs: int


class SponsorBountyDetail(SponsorBounty):
    # Where and how to deposit: the escrow address, with the code as the memo.
    escrow_address: str
    deposit_code: str
    confirmations_required: int
    refund_address: str | None
    # The re-arm check of the working config, if one has been asked for.
    rearm: RearmOut | None
    config: ConfigDetail | None
    canaries: list[CanaryDetail]


class MessageOut(BaseModel):
    role: str
    content: str
    tool_calls: list[ToolCallOut]


class EvidenceOut(BaseModel):
    canary: CanaryPublic | None
    # Index into the run's transcript of the message that tripped the canary.
    message_index: int
    location: str
    representation: str | None
    matched_text: str


class RunOut(BaseModel):
    run_index: int
    transcript: list[MessageOut]
    evidence: list[EvidenceOut]


class FindingOut(BaseModel):
    id: uuid.UUID
    state: str
    canary: CanaryPublic
    tripped_runs: int
    config_hash: str
    verified_at: datetime
    # The attack as submitted. Replaying it is the regression test for a patch.
    attack: list[FillIn]
    payout: PayoutOut | None
    runs: list[RunOut]


class LedgerEntryOut(BaseModel):
    kind: str
    amount_zatoshi: int
    txid: str | None
    created_at: datetime


class LedgerOut(BaseModel):
    balance_zatoshi: int
    entries: list[LedgerEntryOut]


# --- public -----------------------------------------------------------------------------


class BountyPublic(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    state: BountyState
    accepting_attempts: bool
    reward_zatoshi: int
    entry_points: list[EntryPoint]
    canaries: list[CanaryPublic]


class AttemptIn(Body):
    payout_address: str = Field(min_length=1, max_length=512)
    fills: list[FillIn] = Field(min_length=1, max_length=len(EntryPoint))


class AttemptAccepted(BaseModel):
    attempt_id: uuid.UUID
    # Shown once. Send it as a bearer token to read the result.
    status_token: str


class ReplyOut(BaseModel):
    text: str
    tool_calls: list[ToolCallOut]


class AttemptStatusOut(BaseModel):
    state: AttemptState
    # Set once the attempt is done. "not_counted": the bounty was no longer live on the
    # config the attempt was submitted against.
    verdict: Literal["verified", "not_verified", "not_counted"] | None = None
    tripped_runs: int | None = None
    total_runs: int | None = None
    canary: CanaryPublic | None = None
    # What the agent said and did in one run.
    replies: list[ReplyOut] = Field(default_factory=list)
    # Set for a verified attempt once its payout has been created.
    payout: PayoutOut | None = None
