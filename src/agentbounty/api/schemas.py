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
from agentbounty.services.harness import (
    DEFAULT_SCRIPTED_TURNS,
    MAX_LABEL_CHARACTERS,
    MAX_SCRIPTED_TURNS,
    MAX_SLOT_CHARACTERS,
    EntryPoint,
)
from agentbounty.services.judge import Operator, PatternMode

MAX_ZATOSHI = 21_000_000 * 100_000_000

Name = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_.-]+$")]
Label = Annotated[str, Field(min_length=1, max_length=64)]
# One user turn of a scripted attack.
Turn = Annotated[str, Field(min_length=1, max_length=MAX_SLOT_CHARACTERS)]


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
    # Extra tokens the first attack on a bounty costs, on top of its length price.
    new_bounty_premium: int = 0


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


CodeKind = Literal["hunter", "sponsor"]


class InviteOut(BaseModel):
    code: str
    # What it lets its account do: "hunter" to attack, "sponsor" to list agents and attack.
    kind: CodeKind = "hunter"
    # False once it has no use left. A code works once, unless it was made for more people.
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
    can_sponsor: bool
    sponsoring_granted: bool
    referral_code: str | None
    sponsor_code: str | None
    # Banned accounts can neither attack nor list agents.
    banned: bool
    # Tokens left of the cap for the last 24 hours, and the limit an admin set by hand
    # in place of the tier's, if any.
    credits_balance: int
    credits_cap: int
    credit_limit: int | None
    verified_breaks: int
    # Until when the account holds the Watch plan, if it does.
    plan_until: datetime | None = None
    created_at: datetime


class PersonUpdate(Body):
    is_ambassador: bool | None = None
    is_admin: bool | None = None
    # Let them take part in bounties without a referral code, or take that back.
    hunting_granted: bool | None = None
    # Let them list agents without a sponsor code, or take that back.
    sponsoring_granted: bool | None = None
    # Ban them, or lift the ban.
    banned: bool | None = None
    # Tokens they may spend in any 24 hours, in place of their tier's cap. Sent as null,
    # it puts them back on their tier's.
    credit_limit: StrictInt | None = Field(default=None, ge=0, le=10_000)
    # Forget what they have spent, so their allowance is full again.
    reset_credits: bool | None = None
    # Add this many days to their Watch plan, without a payment. 0 ends the plan.
    plan_days: StrictInt | None = Field(default=None, ge=0, le=3_650)


class AdminCodeCreate(Body):
    # Who the codes are for: a hunter, or a sponsor (who may also hunt).
    kind: CodeKind = "hunter"
    # How many codes to make.
    count: StrictInt = Field(default=1, ge=1, le=500)
    # How many people each code admits before it is used up.
    max_uses: StrictInt = Field(default=1, ge=1, le=10_000)
    # The code itself, when making one; otherwise they are generated.
    code: str | None = Field(default=None, min_length=6, max_length=32, pattern=r"^[A-Za-z0-9-]+$")


class CodeUserOut(BaseModel):
    email: str
    name: str | None
    joined_at: datetime


class AdminCodeOut(BaseModel):
    code: str
    kind: CodeKind = "hunter"
    # The first to use it, or null while nobody has.
    used_by: str | None
    # How many people it admits, how many it has, and who they are, first to last.
    max_uses: int = 1
    uses: int = 0
    users: list[CodeUserOut] = []
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


class CodesDeleted(BaseModel):
    deleted: int


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
    # Whether the account may list an agent: always, unless the platform is invite-only
    # and it has not redeemed a sponsor code yet.
    can_sponsor: bool
    # Banned by an admin: it can neither attack nor list agents, whatever codes it holds.
    banned: bool = False
    is_ambassador: bool
    is_admin: bool
    # The owner: the only one who can make or remove admins.
    is_owner: bool
    # Whether the account has what the Watch plan gives, and until when it has paid.
    plan_active: bool = True
    plan_until: datetime | None = None
    # Whether attacks need an account and credits on this deployment.
    invite_only: bool
    verified_breaks: int
    credits: CreditsOut


class ReferralCodeCreate(Body):
    # Make several single-use links, or one shared link with several seats.
    count: StrictInt = Field(default=1, ge=1, le=10)
    max_uses: StrictInt = Field(default=1, ge=1, le=25)


class ReferralUserOut(BaseModel):
    email: str
    name: str | None
    joined_at: datetime


class ReferralCodeOut(BaseModel):
    code: str
    used: bool
    max_uses: int = 1
    uses: int = 0
    users: list[ReferralUserOut] = Field(default_factory=list)
    created_at: datetime


class ReferralCodesPage(BaseModel):
    items: list[ReferralCodeOut]
    total: int
    seat_limit: int
    available_seats: int


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


class OnboardingTemplateOut(BaseModel):
    key: str
    title: str
    description: str
    canary_labels: list[str]


class TemplateBountyIn(BountyCreate):
    pass


class ToolIn(Body):
    name: Name
    description: str = Field(default="", max_length=2_000)
    # JSON Schema for the tool's arguments.
    parameters: dict[str, Any] = Field(default_factory=lambda: {"type": "object"})
    # What the mock returns when the agent calls the tool. No real tool ever runs.
    result: str = Field(default="ok", max_length=10_000)
    # It reads from somewhere a stranger can write: a web page, an inbox, a shared file.
    # A hunter's tool-result fill is returned by every tool marked this way.
    untrusted: bool = False


class ScaffoldIn(Body):
    """The sponsor's frame around one entry point. The hunter writes none of it."""

    # What the agent is told it is looking at: a document's name, a page's URL, the
    # heading on a memory note.
    label: str = Field(default="", max_length=MAX_LABEL_CHARACTERS)
    # The tool whose result carries the hunter's text (tool_result and web_page).
    tool: Name | None = None
    # How many user turns a multi_turn script may hold.
    turns: int = Field(default=DEFAULT_SCRIPTED_TURNS, ge=1, le=MAX_SCRIPTED_TURNS)


class ConfigIn(Body):
    system_prompt: str = Field(min_length=1, max_length=100_000)
    model: str = Field(min_length=1, max_length=200)
    tools: list[ToolIn] = Field(default_factory=list, max_length=32)
    params: dict[str, Any] = Field(default_factory=dict)
    entry_points: list[EntryPoint] = Field(default_factory=lambda: [EntryPoint.USER_MESSAGE])
    # The frame around each entry point, by slot. A slot left out is unframed.
    scaffold: dict[EntryPoint, ScaffoldIn] = Field(default_factory=dict)
    # What the agent is asked to do when the hunter does not write the user's message.
    # Needed with the tool-result entry point, and shown to hunters.
    task: str = Field(default="", max_length=4_000)
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


class SystemPromptCanaryIn(Body):
    type: Literal[CanaryType.SYSTEM_PROMPT_LEAK]
    public_label: Label
    sentence: str = Field(min_length=1, max_length=1_000)


class UnauthorizedActionCanaryIn(Body):
    type: Literal[CanaryType.UNAUTHORIZED_ACTION]
    public_label: Label
    tool: Name
    predicate: PredicateIn | None = None


class DataExfiltrationCanaryIn(Body):
    type: Literal[CanaryType.DATA_EXFILTRATION]
    public_label: Label
    value: str = Field(max_length=1_000)
    sink_tools: list[Name] = Field(min_length=1, max_length=16)
    sink_arguments: list[Name] = Field(min_length=1, max_length=16)


class PhishingLinkCanaryIn(Body):
    type: Literal[CanaryType.PHISHING_LINK_INJECTION]
    public_label: Label
    url: str = Field(min_length=1, max_length=2_000)


class MaliciousCodeCanaryIn(Body):
    type: Literal[CanaryType.MALICIOUS_CODE_INJECTION]
    public_label: Label
    marker: str = Field(min_length=4, max_length=1_000)


class PolicyViolationCanaryIn(Body):
    type: Literal[CanaryType.POLICY_VIOLATION]
    public_label: Label
    pattern: str = Field(min_length=1, max_length=1_000)
    mode: PatternMode = PatternMode.EXACT
    case_sensitive: bool = False
    test_text: str = Field(default="", max_length=2_000)


CanaryIn = Annotated[
    SecretCanaryIn
    | ToolCanaryIn
    | SystemPromptCanaryIn
    | UnauthorizedActionCanaryIn
    | DataExfiltrationCanaryIn
    | PhishingLinkCanaryIn
    | MaliciousCodeCanaryIn
    | PolicyViolationCanaryIn,
    Field(discriminator="type"),
]


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
    """One slot of an attack. Every slot but multi_turn carries `content`; multi_turn
    carries `turns`, the user turns to send in order."""

    slot: EntryPoint
    content: str = Field(default="", max_length=MAX_SLOT_CHARACTERS)
    turns: list[Turn] = Field(default_factory=list, max_length=MAX_SCRIPTED_TURNS)


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
    untrusted: bool = False


class ScaffoldOut(BaseModel):
    """A frame as its sponsor set it."""

    label: str = ""
    tool: str | None = None
    turns: int = DEFAULT_SCRIPTED_TURNS


class ScaffoldPublic(BaseModel):
    """What a hunter is told about a frame: what their text will look like to the agent,
    and how many turns they may script. Never which tool carries it."""

    label: str = ""
    turns: int = DEFAULT_SCRIPTED_TURNS


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
    scaffold: dict[EntryPoint, ScaffoldOut] = Field(default_factory=dict)
    task: str = ""
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


class SponsorshipOut(SponsorBounty):
    """One of a sponsor's bounties on their sponsor page: what it is and how it has fared."""

    created_at: datetime
    # Attacks hunters have submitted, how many were verified breaks, and when the last
    # attack came in.
    attempts: int
    breaks: int
    last_attack_at: datetime | None
    # Rewards that have left its escrow for hunters.
    paid_zatoshi: int
    # Of its working config. Null and empty before an agent is described.
    model: str | None
    entry_points: list[str]
    canaries: int


class SponsorBreakOut(BaseModel):
    """A verified break of one of the sponsor's bounties, and who made it."""

    id: uuid.UUID
    bounty_id: uuid.UUID
    bounty_title: str
    # Null when the attack was made without an account.
    hunter: HunterOut | None
    # The platform's own house hunter found it: nothing was paid.
    house: bool
    canary: CanaryPublic
    tripped_runs: int
    total_runs: int
    # What was paid, or is being paid, for it. Nought for a break the house found.
    reward_zatoshi: int
    payout_state: PayoutState | None
    txid: str | None
    verified_at: datetime


class SponsorTotals(BaseModel):
    bounties: int
    live: int
    drafts: int
    # Broken and waiting for a patch.
    paused: int
    # Closed: over, with any unspent escrow refunded.
    past: int
    # What all the bounties' escrows hold now.
    escrow_zatoshi: int
    attempts: int
    breaks: int
    paid_zatoshi: int


class SponsorOverviewOut(BaseModel):
    """A sponsor's own page: everything they have listed, and who broke what."""

    member_since: datetime
    totals: SponsorTotals
    bounties: list[SponsorshipOut]
    # Newest first.
    breaks: list[SponsorBreakOut]


class HouseHuntOut(BaseModel):
    """The house hunter's go at the bounty's current config."""

    state: Literal["running", "done"]
    # Once done: the agent held, it broke, or the hunt could not go on (and why).
    outcome: Literal["held", "broke", "stopped"] | None
    # Attacks it has made so far.
    tries: int
    detail: str | None


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
    # Whether the attacks that broke it may be replayed against other sponsors' agents,
    # and whether it is scanned again every day while it is live.
    shares_breaks: bool = False
    watch: bool = False
    # Null when the house hunter has not been at this config: it is switched off, the
    # bounty is not live, or its turn has not come.
    house_hunt: HouseHuntOut | None = None


class BountySettingsIn(Body):
    shares_breaks: bool | None = None
    watch: bool | None = None


class BountySettingsOut(BaseModel):
    shares_breaks: bool
    watch: bool


class ContinuousProbeOut(BaseModel):
    case_id: uuid.UUID
    canary_type: CanaryType
    source: Literal["past_break", "corpus"]
    tripped: bool


class ContinuousRunOut(BaseModel):
    id: uuid.UUID
    config_version: int
    config_hash: str
    trigger: Literal["version", "ci", "schedule", "model_update"]
    state: Literal["queued", "running", "done", "error"]
    passed: bool | None
    total: int
    succeeded: int
    success_rate: float | None
    covered_canary_types: list[CanaryType]
    regressed: list[ContinuousProbeOut]
    created_at: datetime
    finished_at: datetime | None


class ContinuousTestingOut(BaseModel):
    plan: PlanOut
    runs: list[ContinuousRunOut]


class PlanOut(BaseModel):
    """The Watch plan, as an account sees it."""

    # Whether a plan is sold here at all. Where it is not, everyone has what it gives.
    offered: bool
    active: bool
    until: datetime | None
    price_zatoshi: int
    days: int
    # How to pay: send the price to this address with the code as the memo. The code is
    # null until the account has asked for it.
    escrow_address: str
    code: str | None
    confirmations_required: int


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
    # Where the probe comes from: the playbook, a break this bounty paid for, or the
    # library of attacks that broke other agents.
    source: Literal["playbook", "past_break", "library"] = "playbook"
    tripped: list[str]
    # Empty for an attack from the library that this agent held against: it was written
    # against someone else's agent, and is shown only once it breaks this one.
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
    # When it last ran to the end, and whether that run drew on the shared library.
    finished_at: datetime | None = None
    with_library: bool = False
    corpus_type: CanaryType | None = None


class ComplianceSubject(BaseModel):
    bounty_id: uuid.UUID
    title: str
    state: BountyState
    model: str | None
    config_version: int | None
    config_hash: str | None
    entry_points: list[str]
    tools: list[str]
    # Labels and kinds only: never a secret or a rule.
    tripwires: list[dict[str, str]]


class ComplianceTesting(BaseModel):
    # The standard of proof: a tripwire in `threshold` of `runs_per_attack` replays.
    runs_per_attack: int
    threshold: int
    scan_state: str | None
    scan_finished_at: datetime | None
    scan_with_library: bool
    probes_run: int
    probes_held: int
    # Attacks by hunters over every config, how many were replayed to a verdict, and
    # how many of those were verified breaks.
    attacks: int
    attacks_judged: int
    # Of the judged attacks, those the platform's house hunter made; and how its hunt
    # of the current config ended, if there has been one.
    house_attacks: int = 0
    house_outcome: str | None = None
    verified_breaks: int
    watch: bool
    rearm: str | None


class ComplianceRisk(BaseModel):
    # An entry of one of OWASP's lists, such as LLM01:2025 or ASI02.
    id: str
    source: str
    name: str
    # How an attack gets in, or what it does once it is in.
    role: Literal["vector", "impact"]
    tested_by: str
    tripwires: list[str]
    probes_run: int
    probes_held: int
    breaks: int
    open_breaks: int
    status: Literal["not_tested", "held", "broken", "patched", "fixed"]


class ComplianceControl(BaseModel):
    ref: str
    title: str
    # What the provision asks for, in this report's words and not the text of the law.
    asks: str
    # Never "met": only whether there is evidence that bears on it.
    status: Literal["evidence", "partial", "gap", "none", "not_applicable"]
    evidence: list[str]


class ComplianceFramework(BaseModel):
    id: str
    name: str
    citation: str
    scope: str
    controls: list[ComplianceControl]


class ComplianceFinding(BaseModel):
    id: uuid.UUID
    tripwire: str
    vector: str
    risks: list[str]
    config_hash: str
    tripped_runs: int
    verified_at: datetime
    # Open on the current config, patched since, or patched and checked by re-arming.
    status: Literal["broken", "patched", "fixed"]


class ComplianceOut(BaseModel):
    subject: ComplianceSubject
    testing: ComplianceTesting
    risks: list[ComplianceRisk]
    frameworks: list[ComplianceFramework]
    findings: list[ComplianceFinding]
    generated_at: datetime
    references: list[str]
    disclaimer: str
    # SHA-256 of everything above generated_at: the same evidence gives the same digest.
    evidence_digest: str


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


class RegressionCaseOut(BaseModel):
    id: uuid.UUID
    finding_id: uuid.UUID
    broken_config_hash: str
    canary_type: CanaryType
    attack: list[FillIn]
    created_at: datetime


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
    # The break's own page: /break/{id}.
    id: uuid.UUID | None = None
    name: str | None
    picture_url: str | None
    github: str | None
    canary: CanaryPublic
    reward_zatoshi: int
    tripped_runs: int
    paid: bool
    # The house hunter found it: nothing was paid, and the reward shown is nought.
    house: bool = False
    verified_at: datetime


class SponsorPublic(BaseModel):
    """Who lists a bounty, as they describe themselves on their profile."""

    name: str | None
    picture_url: str | None
    github: str | None
    company: str | None
    website: str | None


class BountyPublic(BaseModel):
    id: uuid.UUID
    title: str
    description: str
    sponsor: SponsorPublic | None = None
    state: BountyState
    accepting_attempts: bool
    reward_zatoshi: int
    entry_points: list[EntryPoint]
    # The frame around each entry point, for the slots whose frame a hunter needs to see.
    scaffold: dict[EntryPoint, ScaffoldPublic] = Field(default_factory=dict)
    # What the agent is asked to do when the hunter does not write the user's message.
    task: str = ""
    canaries: list[CanaryPublic]
    # How many attacks hunters have submitted, and how many were verified breaks.
    attempts: int = 0
    breaks: int = 0
    # The hunters who broke it, newest first.
    broken_by: list[BreakerOut] = Field(default_factory=list)
    created_at: datetime


class HunterRulesOut(BaseModel):
    runs_per_attempt: int
    trips_required: int
    max_slot_characters: int
    max_scripted_turns: int
    payout_address_kind: str
    attempts_per_payout_address_per_hour: int
    attempts_per_bounty_per_hour: int


class HunterAllowanceOut(BaseModel):
    tier: str
    balance: int
    cap: int
    spent: int
    window_hours: int = 24
    next_tier: str | None
    next_cap: int | None


class HunterBountyOut(BountyPublic):
    rules: HunterRulesOut
    allowance: HunterAllowanceOut


class StatsOut(BaseModel):
    live_bounties: int
    reward_pool_zatoshi: int
    attempts: int
    verified_breaks: int
    paid_zatoshi: int


class BreakOut(BaseModel):
    """A verified break, as the public sees it: that it happened, never how."""

    id: uuid.UUID | None = None
    bounty_id: uuid.UUID
    bounty_title: str
    canary: CanaryPublic
    reward_zatoshi: int
    tripped_runs: int
    # The house hunter found it: nothing was paid, and the reward shown is nought.
    house: bool = False
    verified_at: datetime


class BreakPage(BaseModel):
    """One verified break, on a page of its own that anyone can be sent: who broke which
    agent, how surely, and the payment that proves it was paid. Never the attack."""

    id: uuid.UUID
    bounty_id: uuid.UUID
    bounty_title: str
    sponsor: SponsorPublic | None
    hunter: HunterOut | None
    canary: CanaryPublic
    tripped_runs: int
    total_runs: int
    reward_zatoshi: int
    # The payout, once there is one: its state and, when sent, its transaction.
    payout_state: PayoutState | None
    txid: str | None
    # The house hunter found it: there is no payout, and the reward shown is nought.
    house: bool = False
    verified_at: datetime


class ActivityOut(BaseModel):
    stats: StatsOut
    recent_breaks: list[BreakOut]


class AttemptLine(BaseModel):
    id: uuid.UUID
    state: AttemptState
    verified: bool
    # Made by the house hunter, the platform's own attacker.
    house: bool = False
    submitted_at: datetime


class AttemptActivity(BaseModel):
    total: int
    queued: int
    verified: int
    # How many of the total the house hunter made.
    house: int = 0
    recent: list[AttemptLine]


class AttemptIn(Body):
    payout_address: str = Field(min_length=1, max_length=512)
    fills: list[FillIn] = Field(min_length=1, max_length=len(EntryPoint))


class AttemptAccepted(BaseModel):
    attempt_id: uuid.UUID
    # Shown once. Send it as a bearer token to read the result.
    status_token: str


class HunterAttemptAccepted(BaseModel):
    attempt_id: uuid.UUID


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
    # Worker tries so far. Provider-capacity deferrals do not increase this value.
    tries: int = 0
    # When a provider throttle delayed the attempt, the next automatic retry time.
    retry_at: datetime | None = None
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


class HunterAttemptStatusOut(AttemptStatusOut):
    attempt_id: uuid.UUID
    # Generated only from replay tallies, tool calls and the shown agent replies.
    feedback: list[str] = Field(default_factory=list)


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
    # The break was the house hunter's: nothing was paid for it.
    house: bool = False
    read: bool
    created_at: datetime


class NotificationsOut(BaseModel):
    unread: int
    items: list[NotificationOut]


class NotificationsRead(Body):
    # Without ids, everything is marked read.
    ids: list[uuid.UUID] | None = Field(default=None, max_length=100)
