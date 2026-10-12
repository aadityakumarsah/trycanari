"""Request and response bodies."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    SecretStr,
    StrictInt,
    field_validator,
)

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
    # Needed in invite-only mode.
    referral_code: str | None = Field(default=None, max_length=40)


class CostStep(BaseModel):
    up_to_characters: int
    credits: int


class CreditsOut(BaseModel):
    balance: int
    cap: int
    window_hours: int
    tier: str
    next_tier: str | None
    next_cap: int | None
    costs: list[CostStep]


def _tidy(value: object) -> object:
    """Whitespace runs become one space, and the ends are trimmed."""
    return " ".join(value.split()) if isinstance(value, str) else value


def _github(value: object) -> object:
    """A GitHub username, from whatever form it was pasted in: a profile URL or an @handle."""
    if not isinstance(value, str):
        return value
    name = value.strip().removesuffix("/")
    for prefix in ("https://", "http://", "www.", "github.com/", "@"):
        name = name.removeprefix(prefix)
    return name


Line = Annotated[str, BeforeValidator(_tidy)]


class AccountUpdate(Body):
    """Every field is optional: only what is sent is changed. An empty string clears it."""

    name: Annotated[Line, Field(max_length=60)] | None = None
    github: (
        Annotated[
            str,
            BeforeValidator(_github),
            Field(max_length=39, pattern=r"^$|^[A-Za-z0-9](?:-?[A-Za-z0-9])*$"),
        ]
        | None
    ) = None
    company: Annotated[Line, Field(max_length=80)] | None = None
    role: Annotated[Line, Field(max_length=80)] | None = None
    location: Annotated[Line, Field(max_length=80)] | None = None
    website: (
        Annotated[str, Field(max_length=200, pattern=r"^$|^https?://[^\s/]+\.[^\s]+$")] | None
    ) = None
    bio: Annotated[str, Field(max_length=400)] | None = None

    @field_validator("website", "bio", mode="before")
    @classmethod
    def _trimmed(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value


class ReferralRedeem(Body):
    code: str = Field(min_length=1, max_length=40)


class InviteOut(BaseModel):
    code: str
    # False once someone has used it. A code works once.
    available: bool
    invited_by: str | None


class PersonOut(BaseModel):
    """An account as an admin sees it."""

    id: uuid.UUID
    email: str
    name: str | None
    picture_url: str | None
    github: str | None
    company: str | None
    is_admin: bool
    # An owner named in the settings: an admin that cannot be unmade from here.
    is_owner: bool
    is_ambassador: bool
    can_hunt: bool
    hunting_granted: bool
    referral_code: str | None
    created_at: datetime


class PersonUpdate(Body):
    is_ambassador: bool | None = None
    is_admin: bool | None = None
    # Let them take part in bounties without a referral code, or take that back.
    hunting_granted: bool | None = None


class AdminCodeCreate(Body):
    # How many single-use codes to make.
    count: StrictInt = Field(default=1, ge=1, le=500)
    # The code itself, when making one; otherwise they are generated.
    code: str | None = Field(default=None, min_length=6, max_length=32, pattern=r"^[A-Za-z0-9-]+$")


class AdminCodeOut(BaseModel):
    code: str
    # Who used it, or null while it is still good.
    used_by: str | None
    owner_email: str | None
    created_at: datetime


class AdminActionOut(BaseModel):
    admin_email: str
    action: str
    target_email: str | None
    detail: str | None
    created_at: datetime


class ActionsPage(BaseModel):
    items: list[AdminActionOut]
    total: int


class PeoplePage(BaseModel):
    items: list[PersonOut]
    total: int


class CodesPage(BaseModel):
    items: list[AdminCodeOut]
    total: int


class ApiKeyOut(BaseModel):
    # Shown once. Only its hash is stored, and the key it replaces stops working.
    api_key: str


class AccountOut(BaseModel):
    email: str
    name: str | None
    # The picture to show: one they uploaded, or failing that the one Google supplies.
    picture_url: str | None
    # Whether that picture is one they uploaded, and whether uploading is available here.
    picture_uploaded: bool
    can_upload_picture: bool
    # Whether the account signs in with Google, which is where a picture comes from.
    google_linked: bool
    github: str | None
    company: str | None
    role: str | None
    location: str | None
    website: str | None
    bio: str | None
    # Whether the account may attack: always, unless the platform is invite-only and it
    # has not redeemed a referral code yet.
    can_hunt: bool
    is_ambassador: bool
    is_admin: bool
    # The owner: the only one who can make or remove admins.
    is_owner: bool
    # Whether attacks need an account and credits on this deployment.
    invite_only: bool
    verified_breaks: int
    credits: CreditsOut


class ReferralCodeCreate(Body):
    # How many single-use codes to make.
    count: StrictInt = Field(default=1, ge=1, le=10)


class ReferralCodeOut(BaseModel):
    code: str
    used: bool
    created_at: datetime


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
    # The sponsor's own OpenAI-compatible endpoint. Without one, the agent runs on the
    # platform's provider.
    endpoint_base: str | None = Field(default=None, min_length=1, max_length=300)
    # Its key. May be left out when editing a config that already holds one for this endpoint.
    endpoint_key: SecretStr | None = Field(default=None, min_length=1, max_length=500)


class ModelTestIn(Body):
    model: str = Field(min_length=1, max_length=200)
    endpoint_base: str | None = Field(default=None, min_length=1, max_length=300)
    endpoint_key: SecretStr | None = Field(default=None, min_length=1, max_length=500)


class ModelTestOut(BaseModel):
    ok: bool
    detail: str
    milliseconds: int
    # The model names the endpoint offers, when it lists them.
    models: list[str]


class ModelsOut(BaseModel):
    # Where the platform's own models come from, as a host name.
    provider: str | None
    models: list[str]


class ProviderOut(BaseModel):
    """The platform's model provider, as the owner sees it. Never the key itself."""

    # "admin": set in the admin page. "environment": from the server's own settings.
    # "none": the built-in test model.
    source: Literal["admin", "environment", "none"]
    api_base: str | None
    key_hint: str | None
    updated_at: datetime | None = None
    # Model names open bounties run on this provider.
    models_in_use: list[str]


class ProviderIn(Body):
    api_base: str = Field(min_length=1, max_length=300)
    # May be left out to keep the key already saved for this address.
    api_key: SecretStr | None = Field(default=None, min_length=1, max_length=500)


class ProviderTestIn(ProviderIn):
    # A model to send one short message to. Without one, the provider is only asked
    # which models it offers.
    model: str | None = Field(default=None, min_length=1, max_length=200)


class ProviderTestOut(BaseModel):
    ok: bool
    detail: str
    milliseconds: int
    models: list[str]
    # Models open bounties run on that this provider does not list.
    missing_models: list[str]


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
    endpoint_base: str | None = None
    # The key itself is never sent back.
    endpoint_key_set: bool = False


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


class ProbeOut(BaseModel):
    id: str
    name: str
    technique: str
    # The canary it was aimed at, and the canaries it tripped: empty when the agent held.
    aimed_at: str
    tripped: list[str]
    attack: list[FillIn]
    reply: str
    tool_calls: list[ToolCallOut]


class ScanOut(BaseModel):
    # null when the current config has not been scanned.
    state: Literal["queued", "running", "done", "error"] | None
    config_hash: str | None
    probes: list[ProbeOut]
    # How many probes the agent held against, out of how many were run.
    held: int
    total: int
    # How the risk each kind of canary guards against is named, after OWASP's lists.
    risks: dict[str, str]
    requested_at: datetime | None


class LeaderOut(BaseModel):
    """A hunter on the leaderboard: what they chose to put on their profile, and what
    they have won."""

    name: str | None
    picture_url: str | None
    github: str | None
    company: str | None
    breaks: int
    earned_zatoshi: int
    last_break_at: datetime


class HunterOut(BaseModel):
    """Who broke the agent, as they describe themselves on their profile."""

    name: str | None
    picture_url: str | None
    github: str | None
    company: str | None
    role: str | None
    website: str | None


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
    # Null when the attack was made without an account.
    hunter: HunterOut | None = None


class LedgerEntryOut(BaseModel):
    kind: str
    amount_zatoshi: int
    txid: str | None
    created_at: datetime


class LedgerOut(BaseModel):
    balance_zatoshi: int
    entries: list[LedgerEntryOut]


# --- public -----------------------------------------------------------------------------


class BreakerOut(BaseModel):
    """Who broke a bounty, as the public sees it: what the hunter put on their profile,
    which canary fell and what it paid. Never the attack."""

    # All three are null for a hunter with no account, or who gave their profile none.
    name: str | None
    picture_url: str | None
    github: str | None
    canary: CanaryPublic
    reward_zatoshi: int
    tripped_runs: int
    paid: bool
    verified_at: datetime


class BountyPublic(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    state: BountyState
    accepting_attempts: bool
    reward_zatoshi: int
    entry_points: list[EntryPoint]
    canaries: list[CanaryPublic]
    # How many attacks hunters have submitted, and how many were verified breaks.
    attempts: int = 0
    breaks: int = 0
    # The hunters who broke it, newest first.
    broken_by: list[BreakerOut] = Field(default_factory=list)
    created_at: datetime


class StatsOut(BaseModel):
    live_bounties: int
    reward_pool_zatoshi: int
    attempts: int
    verified_breaks: int
    paid_zatoshi: int


class BreakOut(BaseModel):
    """A verified break, as the public sees it: that it happened, never how."""

    bounty_id: uuid.UUID
    bounty_title: str
    canary: CanaryPublic
    reward_zatoshi: int
    tripped_runs: int
    verified_at: datetime


class ActivityOut(BaseModel):
    stats: StatsOut
    recent_breaks: list[BreakOut]


class AttemptLine(BaseModel):
    id: uuid.UUID
    state: AttemptState
    verified: bool
    submitted_at: datetime


class AttemptActivity(BaseModel):
    total: int
    queued: int
    verified: int
    recent: list[AttemptLine]


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


class RunSummaryOut(BaseModel):
    """One replay in brief: what it tripped and what the agent did. Not its transcript."""

    run_index: int
    # Public labels of the canaries this run tripped. Empty when the agent held.
    tripped: list[str]
    # How many times the agent spoke, and the names of the tools it called, in order.
    replies: int
    tool_calls: list[str]
    # Model tokens the run used.
    tokens: int


class CanaryTallyOut(BaseModel):
    canary: CanaryPublic
    # How many of the runs tripped it. `threshold` or more is a verified break.
    tripped_runs: int


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
    # While queued: attempts on this bounty that are replayed before this one.
    queue_ahead: int | None = None
    # While running: replays finished so far, out of `runs_total`.
    runs_done: int = 0
    runs_total: int = 5
    # Runs a single canary has to trip in for the break to be verified.
    threshold: int = 3
    # Once done: every replay in brief, and how each canary of the agent fared.
    runs: list[RunSummaryOut] = Field(default_factory=list)
    canaries: list[CanaryTallyOut] = Field(default_factory=list)


# --- an account's own attempts and notifications ----------------------------------------


class HuntPayoutOut(PayoutOut):
    to_address: str
    created_at: datetime


class HuntOut(BaseModel):
    """One attack the account made: what was sent, how it was judged, what it was paid."""

    id: uuid.UUID
    bounty_id: uuid.UUID
    bounty_title: str
    bounty_state: BountyState
    # What the bounty pays for a verified break.
    reward_zatoshi: int
    state: AttemptState
    verdict: Literal["verified", "not_verified", "not_counted"] | None
    tripped_runs: int | None
    total_runs: int | None
    canary: CanaryPublic | None
    attack: list[FillIn]
    payout_address: str
    cost_credits: int
    submitted_at: datetime
    verified_at: datetime | None
    payout: HuntPayoutOut | None


class HuntTotals(BaseModel):
    attempts: int
    verified: int
    earned_zatoshi: int
    pending_zatoshi: int
    credits_spent: int


class HuntsOut(BaseModel):
    totals: HuntTotals
    # The latest attempts, newest first.
    items: list[HuntOut]


class ActorOut(BaseModel):
    name: str | None
    picture_url: str | None
    github: str | None


class NotificationOut(BaseModel):
    id: uuid.UUID
    # bounty_broken and reward_paid are for a bounty's sponsor; break_verified,
    # payout_held and payout_sent for the hunter who broke it.
    kind: Literal["bounty_broken", "break_verified", "payout_held", "payout_sent", "reward_paid"]
    bounty_id: uuid.UUID
    bounty_title: str
    # The hunter, on a sponsor's notification. Null when they attacked without an account.
    actor: ActorOut | None
    amount_zatoshi: int | None
    txid: str | None
    read: bool
    created_at: datetime


class NotificationsOut(BaseModel):
    unread: int
    items: list[NotificationOut]


class NotificationsRead(Body):
    # Without ids, everything is marked read.
    ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
