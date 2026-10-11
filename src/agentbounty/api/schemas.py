"""Request and response bodies."""

from __future__ import annotations

import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt

from agentbounty.models import AttemptState, BountyState, CanaryType, PayoutState
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


# --- public -----------------------------------------------------------------------------


class CanaryPublic(BaseModel):
    type: CanaryType
    public_label: str


class BountyPublic(BaseModel):
    id: uuid.UUID
    state: BountyState
    accepting_attempts: bool
    reward_zatoshi: int
    entry_points: list[EntryPoint]
    canaries: list[CanaryPublic]


class FillIn(Body):
    slot: EntryPoint
    content: str = Field(min_length=1, max_length=MAX_SLOT_CHARACTERS)


class AttemptIn(Body):
    payout_address: str = Field(min_length=1, max_length=512)
    fills: list[FillIn] = Field(min_length=1, max_length=len(EntryPoint))


class AttemptAccepted(BaseModel):
    attempt_id: uuid.UUID
    # Shown once. Send it as a bearer token to read the result.
    status_token: str


class ToolCallOut(BaseModel):
    name: str
    arguments: dict[str, Any]


class ReplyOut(BaseModel):
    text: str
    tool_calls: list[ToolCallOut]


class PayoutOut(BaseModel):
    state: PayoutState
    amount_zatoshi: int
    txid: str | None


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
