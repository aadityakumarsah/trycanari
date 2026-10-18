"""Sponsor endpoints: authenticated with the API key issued at sign-up."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import false, func, select

from agentbounty.api.deps import CipherDep, OwnBounty, SettingsDep, SponsorDep, Transaction
from agentbounty.api.public import status_out
from agentbounty.api.ratelimit import limit_by_client
from agentbounty.api.schemas import (
    AccountOut,
    AccountUpdate,
    ActivateOut,
    ActorOut,
    ApiKeyOut,
    AttemptActivity,
    AttemptLine,
    AttemptStatusOut,
    BountyCreate,
    BountyCreated,
    BountySettingsIn,
    BountySettingsOut,
    CanaryDetail,
    CanaryIn,
    CanaryOut,
    CanaryPublic,
    CloseIn,
    CloseOut,
    ComplianceOut,
    ConfigDetail,
    ConfigIn,
    ConfigOut,
    ContinuousProbeOut,
    ContinuousRunOut,
    ContinuousTestingOut,
    CostStep,
    CreditsOut,
    DataExfiltrationCanaryIn,
    EvidenceOut,
    FillIn,
    FindingOut,
    FundIn,
    FundOut,
    HouseHuntOut,
    HunterOut,
    HuntOut,
    HuntPayoutOut,
    HuntsOut,
    HuntTotals,
    LedgerEntryOut,
    LedgerOut,
    MaliciousCodeCanaryIn,
    MessageOut,
    ModelsOut,
    ModelTestIn,
    ModelTestOut,
    NotificationOut,
    NotificationsOut,
    NotificationsRead,
    OnboardingTemplateOut,
    PayoutOut,
    PhishingLinkCanaryIn,
    PlanOut,
    PolicyViolationCanaryIn,
    ProbeOut,
    RearmOut,
    ReferralCodeCreate,
    ReferralCodeOut,
    ReferralCodesPage,
    ReferralRedeem,
    ReferralUserOut,
    RegressionCaseOut,
    RunOut,
    ScaffoldOut,
    ScanOut,
    SecretCanaryIn,
    SponsorBounty,
    SponsorBountyDetail,
    SponsorBreakOut,
    SponsorCreate,
    SponsorCreated,
    SponsorOverviewOut,
    SponsorshipOut,
    SponsorTotals,
    StateOut,
    SystemPromptCanaryIn,
    TemplateBountyIn,
    ToolOut,
    UnauthorizedActionCanaryIn,
)
from agentbounty.api.session import COOKIE, seal_sponsor, set_session_cookie
from agentbounty.models import (
    Attempt,
    AttemptState,
    BountyState,
    Canary,
    CanaryType,
    Finding,
    FindingState,
    SavedAttack,
    Scan,
    Sponsor,
)
from agentbounty.models import TargetConfig as TargetConfigRow
from agentbounty.money import format_amount
from agentbounty.services import attempts as attempt_service
from agentbounty.services import bounty as bounty_service
from agentbounty.services import (
    compliance,
    continuous,
    credits,
    endpoint,
    house,
    hunts,
    notify,
    onboarding,
    pictures,
    plans,
    playbook,
    provider,
    rearm,
    reports,
    scan,
)
from agentbounty.services.bounty import CanaryDefinition
from agentbounty.services.crypto import Cipher
from agentbounty.services.errors import ServiceError
from agentbounty.services.harness import MockTool, Scaffold, TargetConfig
from agentbounty.services.judge import (
    RUNS_PER_ATTEMPT,
    ArgumentPredicate,
    DataExfiltration,
    MaliciousCodeInjection,
    PhishingLinkInjection,
    PolicyViolation,
    SystemPromptLeak,
    ToolRule,
)
from agentbounty.services.model import RealModel
from agentbounty.services.wallet import address_rule
from agentbounty.settings import Settings

router = APIRouter(prefix="/v1", tags=["sponsor"])

# How long the platform provider's list of models is kept before it is asked for again.
MODELS_KEPT_SECONDS = 600


@router.post("/sponsors", status_code=201, dependencies=[Depends(limit_by_client)])
async def create_sponsor(
    body: SponsorCreate, session: Transaction, settings: SettingsDep
) -> SponsorCreated:
    """Create an account. Anyone may. A referral code given here is redeemed straight away;
    without one the account can do everything but attack, on an invite-only platform."""
    given = body.referral_code and body.referral_code.strip()
    code = await credits.redeem(session, given) if settings.invite_only and given else None
    sponsor, api_key = await bounty_service.create_sponsor(session, body.email, code)
    await credits.settle(session, sponsor)
    return SponsorCreated(sponsor_id=sponsor.id, api_key=api_key)


@router.get("/account")
async def account(session: Transaction, sponsor: SponsorDep, settings: SettingsDep) -> AccountOut:
    """Who is signed in, and what they may still spend on attacks."""
    allowed = await credits.allowance(session, sponsor, settings)
    return AccountOut(
        email=sponsor.email,
        name=sponsor.name,
        picture_url=sponsor.uploaded_picture_url or sponsor.picture_url,
        picture_uploaded=sponsor.uploaded_picture_url is not None,
        can_upload_picture=settings.cloudinary_url is not None,
        google_linked=sponsor.google_sub is not None,
        github=sponsor.github,
        company=sponsor.company,
        role=sponsor.role,
        location=sponsor.location,
        website=sponsor.website,
        bio=sponsor.bio,
        can_hunt=credits.can_hunt(sponsor, settings),
        can_sponsor=credits.can_sponsor(sponsor, settings),
        banned=credits.is_banned(sponsor),
        is_ambassador=sponsor.is_ambassador,
        is_admin=credits.is_admin(sponsor, settings),
        is_owner=credits.is_owner(sponsor, settings),
        plan_active=plans.active(sponsor, settings),
        plan_until=sponsor.plan_until,
        invite_only=settings.invite_only,
        verified_breaks=allowed.breaks,
        credits=CreditsOut(
            balance=allowed.balance,
            cap=allowed.cap,
            window_hours=int(credits.WINDOW.total_seconds() // 3600),
            tier=allowed.tier,
            next_tier=allowed.next_tier,
            next_cap=allowed.next_cap,
            costs=[CostStep(up_to_characters=size, credits=cost) for size, cost in credits.COSTS],
            new_bounty_premium=allowed.new_bounty_premium,
        ),
    )


def _plan_out(request: Request, sponsor: Sponsor, settings: Settings) -> PlanOut:
    return PlanOut(
        offered=plans.offered(settings),
        active=plans.active(sponsor, settings),
        until=sponsor.plan_until,
        price_zatoshi=settings.plan_price_zatoshi,
        days=settings.plan_days,
        escrow_address=request.app.state.escrow_address,
        code=sponsor.plan_code,
        confirmations_required=settings.deposit_confirmations,
    )


@router.get("/account/plan")
async def read_plan(request: Request, sponsor: SponsorDep, settings: SettingsDep) -> PlanOut:
    """The Watch plan: whether one is sold here, whether the account holds it, what it
    costs and how to pay."""
    return _plan_out(request, sponsor, settings)


@router.post("/account/plan", dependencies=[Depends(limit_by_client)])
async def start_plan(
    request: Request, session: Transaction, sponsor: SponsorDep, settings: SettingsDep
) -> PlanOut:
    """Ask to pay for the plan: the answer carries the code to put in the deposit's memo.
    The plan starts, or runs on, when the deposit has confirmed."""
    if not plans.offered(settings):
        raise ServiceError(409, "there is no plan to buy here: everything it gives is included")
    await plans.code_for(session, sponsor)
    return _plan_out(request, sponsor, settings)


@router.patch("/account")
async def update_account(
    body: AccountUpdate, session: Transaction, sponsor: SponsorDep, settings: SettingsDep
) -> AccountOut:
    """Change the profile. Only the fields that are sent change; an empty one is cleared."""
    for field in body.model_fields_set:
        value = getattr(body, field)
        if field == "name" and not value:
            raise ServiceError(422, "a name cannot be blank")
        setattr(sponsor, field, value or None)
    await session.flush()
    return await account(session, sponsor, settings)


def _cloudinary(settings: Settings) -> pictures.Cloudinary:
    if settings.cloudinary_url is None:
        raise ServiceError(404, "uploading a picture is not available here")
    return pictures.Cloudinary.from_url(settings.cloudinary_url.get_secret_value())


@router.put("/account/picture", dependencies=[Depends(limit_by_client)])
async def upload_picture(
    request: Request, session: Transaction, sponsor: SponsorDep, settings: SettingsDep
) -> AccountOut:
    """Set the profile picture. The request body is the image itself: PNG, JPEG or WebP,
    up to 4 MB. It replaces the picture the account had, which is not kept."""
    store = _cloudinary(settings)
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > pictures.MAX_BYTES:
        raise ServiceError(413, "that picture is larger than 4 MB")
    # Read with a ceiling, whatever the length header said.
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > pictures.MAX_BYTES:
            raise ServiceError(413, "that picture is larger than 4 MB")
    kind = pictures.image_type(bytes(data))
    if kind is None:
        raise ServiceError(415, "upload a PNG, JPEG or WebP image")
    sponsor.uploaded_picture_url = await store.upload(
        sponsor.id, bytes(data), kind, request.app.state.cloudinary_transport
    )
    await session.flush()
    return await account(session, sponsor, settings)


@router.delete("/account/picture", dependencies=[Depends(limit_by_client)])
async def remove_picture(
    request: Request, session: Transaction, sponsor: SponsorDep, settings: SettingsDep
) -> AccountOut:
    """Remove the uploaded picture, here and where it was stored."""
    if sponsor.uploaded_picture_url is not None:
        await _cloudinary(settings).delete(sponsor.id, request.app.state.cloudinary_transport)
        sponsor.uploaded_picture_url = None
        await session.flush()
    return await account(session, sponsor, settings)


@router.post("/account/referral", dependencies=[Depends(limit_by_client)])
async def redeem_referral(
    body: ReferralRedeem, session: Transaction, sponsor: SponsorDep, settings: SettingsDep
) -> AccountOut:
    """Redeem a referral code: a hunter code lets the account take part in bounties, a
    sponsor code lets it list agents as well."""
    await credits.redeem_for(session, sponsor, body.code)
    return await account(session, sponsor, settings)


@router.post("/account/api-key", dependencies=[Depends(limit_by_client)])
async def new_api_key(
    request: Request, response: Response, session: Transaction, sponsor: SponsorDep
) -> ApiKeyOut:
    """Make a new API key for scripts and agents. The old one stops working at once.

    An account that signed in with Google has no key until it asks for one here.
    """
    api_key = bounty_service.new_api_key(sponsor)
    await session.flush()
    if request.cookies.get(COOKIE):
        # A browser signed in with the old key would be signed out by this. Its session
        # is re-issued to name the account instead, which no key change can break.
        set_session_cookie(request, response, seal_sponsor(request.app.state.cipher, sponsor.id))
    return ApiKeyOut(api_key=api_key)


@router.get("/account/attempts")
async def my_attempts(session: Transaction, sponsor: SponsorDep) -> HuntsOut:
    """Every attack this account made, newest first: what was sent, how it was judged and
    what it was paid, with totals over all of them."""
    counted = await hunts.totals(session, sponsor.id)
    return HuntsOut(
        totals=HuntTotals(
            attempts=counted.attempts,
            verified=counted.verified,
            earned_zatoshi=counted.earned_zatoshi,
            pending_zatoshi=counted.pending_zatoshi,
            credits_spent=counted.credits_spent,
        ),
        items=[
            HuntOut(
                id=hunt.attempt.id,
                bounty_id=hunt.bounty.id,
                bounty_title=hunt.bounty.title,
                bounty_state=hunt.bounty.state,
                reward_zatoshi=hunt.bounty.reward_zatoshi,
                state=hunt.attempt.state,
                verdict=hunt.verdict,
                tripped_runs=hunt.tripped_runs,
                total_runs=hunt.total_runs,
                canary=None
                if hunt.canary is None
                else CanaryPublic(type=hunt.canary.type, public_label=hunt.canary.public_label),
                attack=[FillIn(**fill) for fill in hunt.attempt.payload_json],
                payout_address=hunt.attempt.payout_address,
                cost_credits=hunt.attempt.cost_credits,
                submitted_at=hunt.attempt.submitted_at,
                verified_at=hunt.finding.verified_at if hunt.finding else None,
                payout=None
                if hunt.payout is None
                else HuntPayoutOut(
                    state=hunt.payout.state,
                    amount_zatoshi=hunt.payout.amount_zatoshi,
                    txid=hunt.payout.txid,
                    to_address=hunt.payout.to_address,
                    created_at=hunt.payout.created_at,
                ),
            )
            for hunt in await hunts.listing(session, sponsor.id)
        ],
    )


@router.get("/account/attempts/{attempt_id}")
async def my_attempt(
    attempt_id: uuid.UUID, session: Transaction, sponsor: SponsorDep, cipher: CipherDep
) -> AttemptStatusOut:
    """One of this account's attempts in full, with what the agent said and did. The same
    answer its status token reads, for an account that no longer holds the token."""
    return status_out(
        await attempt_service.own_attempt_status(session, cipher, attempt_id, sponsor.id)
    )


async def _notifications(session: Transaction, sponsor: Sponsor) -> NotificationsOut:
    shown, unread = await notify.listing(session, sponsor.id)
    return NotificationsOut(
        unread=unread,
        items=[
            NotificationOut(
                id=line.notification.id,
                kind=line.notification.kind,  # type: ignore[arg-type]
                bounty_id=line.notification.bounty_id,
                bounty_title=line.bounty_title,
                actor=None
                if line.actor is None
                else ActorOut(
                    name=line.actor.name,
                    picture_url=line.actor.uploaded_picture_url or line.actor.picture_url,
                    github=line.actor.github,
                ),
                amount_zatoshi=line.notification.amount_zatoshi,
                txid=line.notification.txid,
                house=line.actor is not None and line.actor.is_house,
                tripped_count=line.notification.tripped_count,
                read=line.notification.read_at is not None,
                created_at=line.notification.created_at,
            )
            for line in shown
        ],
    )


@router.get("/notifications")
async def notifications(session: Transaction, sponsor: SponsorDep) -> NotificationsOut:
    """What this account has been told: breaks of its bounties, and what became of the
    breaks it made. Newest first, with how many it has not read."""
    return await _notifications(session, sponsor)


@router.post("/notifications/read")
async def read_notifications(
    body: NotificationsRead, session: Transaction, sponsor: SponsorDep
) -> NotificationsOut:
    """Mark notifications read: the ones named, or all of them."""
    await notify.mark_read(session, sponsor.id, body.ids)
    return await _notifications(session, sponsor)


@router.get("/referral-codes")
async def referral_codes(session: Transaction, sponsor: SponsorDep) -> list[ReferralCodeOut]:
    """The referral codes this account has made, and which have been used."""
    return [
        ReferralCodeOut(
            code=line.code.code,
            used=len(line.people) >= line.code.max_uses,
            max_uses=line.code.max_uses,
            uses=len(line.people),
            users=[
                ReferralUserOut(email=user.email, name=user.name, joined_at=user.joined_at)
                for user in line.people
            ],
            created_at=line.code.created_at,
        )
        for line in await credits.codes_of(session, sponsor)
    ]


@router.get("/referral-codes/page")
async def referral_codes_page(
    session: Transaction,
    sponsor: SponsorDep,
    limit: int = Query(default=8, ge=1, le=25),
    offset: int = Query(default=0, ge=0, le=1_000_000),
) -> ReferralCodesPage:
    """One small page of this account's links, for incremental dashboard loading."""
    lines = await credits.codes_of(session, sponsor, limit=limit, offset=offset)
    total, open_seats = await credits.code_stats(session, sponsor)
    return ReferralCodesPage(
        items=[
            ReferralCodeOut(
                code=line.code.code,
                used=len(line.people) >= line.code.max_uses,
                max_uses=line.code.max_uses,
                uses=len(line.people),
                users=[
                    ReferralUserOut(email=user.email, name=user.name, joined_at=user.joined_at)
                    for user in line.people
                ],
                created_at=line.code.created_at,
            )
            for line in lines
        ],
        total=total,
        seat_limit=credits.MAX_UNUSED_PER_AMBASSADOR,
        available_seats=max(0, credits.MAX_UNUSED_PER_AMBASSADOR - open_seats),
    )


@router.post("/referral-codes", status_code=201, dependencies=[Depends(limit_by_client)])
async def create_referral_codes(
    body: ReferralCodeCreate, session: Transaction, sponsor: SponsorDep
) -> list[ReferralCodeOut]:
    """Make individual invite links or one shared link with several bounded seats."""
    made = await credits.create_codes(session, sponsor, body.count, max_uses=body.max_uses)
    for code in made:
        await session.refresh(code)
    return [
        ReferralCodeOut(
            code=code.code,
            used=False,
            max_uses=code.max_uses,
            uses=0,
            users=[],
            created_at=code.created_at,
        )
        for code in made
    ]


@router.post("/bounties", status_code=201)
async def create_bounty(
    body: BountyCreate, session: Transaction, sponsor: SponsorDep, settings: SettingsDep
) -> BountyCreated:
    if not credits.can_sponsor(sponsor, settings):
        raise ServiceError(403, "listing an agent needs a sponsor invite: enter a sponsor code")
    currency = settings.currency
    if body.reward_zatoshi < currency.minimum:
        least = format_amount(currency.minimum, currency)
        raise ServiceError(422, f"the reward must be at least {least} {currency.symbol}")
    bounty = await bounty_service.create_bounty(
        session, sponsor, body.reward_zatoshi, settings.fee_rate, body.title, body.description
    )
    return BountyCreated(id=bounty.id, state=bounty.state)


@router.get("/onboarding/templates")
async def onboarding_templates() -> list[OnboardingTemplateOut]:
    """Small, editable starter agents with deterministic canaries already attached."""
    return [
        OnboardingTemplateOut(
            key=item.key,
            title=item.title,
            description=item.description,
            canary_labels=[canary.public_label for canary in item.canaries],
        )
        for item in onboarding.starters()
    ]


@router.post("/bounties/templates/{key}", status_code=201)
async def create_from_template(
    key: str,
    body: TemplateBountyIn,
    session: Transaction,
    sponsor: SponsorDep,
    cipher: CipherDep,
    settings: SettingsDep,
) -> BountyCreated:
    """Create an editable draft and queue its free baseline before it is funded."""
    if not credits.can_sponsor(sponsor, settings):
        raise ServiceError(403, "listing an agent needs a sponsor invite: enter a sponsor code")
    template = onboarding.starter(key)
    if template is None:
        raise ServiceError(404, "that starter template does not exist")
    if body.reward_zatoshi < settings.currency.minimum:
        minimum = format_amount(settings.currency.minimum, settings.currency)
        raise ServiceError(422, f"the reward must be at least {minimum} {settings.currency.symbol}")
    bounty = await bounty_service.create_bounty(
        session,
        sponsor,
        body.reward_zatoshi,
        settings.fee_rate,
        body.title or template.title,
        body.description or template.description,
    )
    await bounty_service.put_config(session, cipher, bounty, template.config)
    for canary in template.canaries:
        await bounty_service.add_canary(session, cipher, bounty, canary)
    await scan.request(session, bounty, with_library=False)
    return BountyCreated(id=bounty.id, state=bounty.state)


async def _endpoint(base: str | None, settings: Settings) -> str | None:
    """A sponsor's endpoint URL in its stored form, refused if it may not be used."""
    if base is None:
        return None
    allow_private = settings.allow_private_model_endpoints
    try:
        normal = endpoint.normalize(base, allow_private=allow_private)
        await endpoint.check_reachable_host(normal, allow_private=allow_private)
    except endpoint.EndpointError as exc:
        raise ServiceError(422, str(exc)) from exc
    return normal


def platform_model(request: Request, using: provider.Provider) -> RealModel:
    """A client for the platform's provider. One the owner set in the admin page is a
    URL typed by a person, so it is opened the guarded way."""
    if using.source == "admin":
        opened: RealModel = request.app.state.model_connector(using.base, using.key)
        return opened
    client = httpx.AsyncClient(transport=request.app.state.model_transport, timeout=30)
    return RealModel(using.base, using.key, client=client)


@router.get("/models")
async def platform_models(
    request: Request, session: Transaction, cipher: CipherDep, settings: SettingsDep, _: SponsorDep
) -> ModelsOut:
    """The models the platform's own provider offers, for the quick-start choice."""
    using = await provider.current(session, cipher, settings)
    if using is None:
        return ModelsOut(provider=None, models=[])
    kept = request.app.state.models_listed
    # Kept per provider address: a change of provider must not show the old one's list.
    if kept is None or kept[0] < time.monotonic() or kept[1] != using.base:
        model = platform_model(request, using)
        try:
            names = await model.models()
        finally:
            await model.aclose()
        # A list that could not be read is asked for again soon; a good one is kept longer.
        kept = (time.monotonic() + (MODELS_KEPT_SECONDS if names else 30), using.base, names)
        request.app.state.models_listed = kept
    return ModelsOut(provider=urlsplit(using.base).hostname, models=kept[2])


@router.post("/bounties/{bounty_id}/model-test", dependencies=[Depends(limit_by_client)])
async def test_model(
    body: ModelTestIn,
    request: Request,
    session: Transaction,
    bounty: OwnBounty,
    cipher: CipherDep,
    settings: SettingsDep,
) -> ModelTestOut:
    """Send one short message to the model, to show that the URL, key and name work before
    a config is saved. Without an endpoint it asks the platform's provider."""
    base = await _endpoint(body.endpoint_base, settings)
    if base is None:
        using = await provider.current(session, cipher, settings)
        if using is None:
            return ModelTestOut(
                ok=True,
                detail="This deployment runs a built-in test model, which answers to any name.",
                milliseconds=0,
                models=[],
            )
        model = platform_model(request, using)
    else:
        own_key = body.endpoint_key.get_secret_value() if body.endpoint_key else None
        if own_key is None:
            # The saved key is used only for the endpoint it was saved for.
            row = await bounty_service.latest_config(session, bounty.id)
            if row is None or row.endpoint_base != base or row.endpoint_key_enc is None:
                raise ServiceError(422, "enter the API key for your model endpoint")
            own_key = cipher.decrypt(
                row.endpoint_key_enc, purpose=bounty_service.ENDPOINT_KEY
            ).decode()
        model = request.app.state.model_connector(base, own_key)
    try:
        result = await endpoint.test(model, body.model)
        names = await model.models() if base is not None and result.ok else []
    finally:
        await model.aclose()
    return ModelTestOut(
        ok=result.ok, detail=result.detail, milliseconds=result.milliseconds, models=names
    )


@router.put("/bounties/{bounty_id}/config")
async def put_config(
    body: ConfigIn,
    session: Transaction,
    bounty: OwnBounty,
    cipher: CipherDep,
    settings: SettingsDep,
) -> ConfigOut:
    base = await _endpoint(body.endpoint_base, settings)
    try:
        config = TargetConfig(
            system_prompt=body.system_prompt,
            model=body.model,
            tools=tuple(
                MockTool(tool.name, tool.description, tool.parameters, tool.result, tool.untrusted)
                for tool in body.tools
            ),
            params=body.params,
            entry_points=tuple(body.entry_points),
            endpoint=base,
            task=body.task.strip(),
            scaffold={
                slot: Scaffold(label=frame.label.strip(), tool=frame.tool, turns=frame.turns)
                for slot, frame in body.scaffold.items()
            },
        )
    except ValueError as exc:
        raise ServiceError(422, str(exc)) from exc
    key = body.endpoint_key.get_secret_value() if body.endpoint_key else None
    row = await bounty_service.put_config(session, cipher, bounty, config, key)
    # A target config is an immutable agent version. Every new version is immediately
    # tested against the durable corpus when the sponsor has the monthly plan.
    if plans.active(
        await session.get_one(Sponsor, bounty.sponsor_id), settings
    ) and await continuous.has_cases(session, bounty.id):
        await continuous.request(session, bounty, row, trigger="version")
    return ConfigOut(version=row.version, config_hash=row.config_hash)


@router.post("/bounties/{bounty_id}/canaries", status_code=201)
async def add_canary(
    body: CanaryIn, session: Transaction, bounty: OwnBounty, cipher: CipherDep
) -> CanaryOut:
    try:
        if isinstance(body, SecretCanaryIn):
            definition = CanaryDefinition(
                CanaryType.SECRET_LEAK, body.public_label, secret=body.secret
            )
        elif isinstance(body, SystemPromptCanaryIn):
            value = SystemPromptLeak(body.sentence).sentence
            definition = CanaryDefinition(
                CanaryType.SYSTEM_PROMPT_LEAK, body.public_label, secret=value
            )
        elif isinstance(body, DataExfiltrationCanaryIn):
            parameters = DataExfiltration(
                body.value, tuple(body.sink_tools), tuple(body.sink_arguments)
            )
            definition = CanaryDefinition(
                CanaryType.DATA_EXFILTRATION,
                body.public_label,
                secret=body.value,
                exfiltration=parameters,
            )
        elif isinstance(body, PhishingLinkCanaryIn):
            value = PhishingLinkInjection(body.url).url
            definition = CanaryDefinition(
                CanaryType.PHISHING_LINK_INJECTION, body.public_label, secret=value
            )
        elif isinstance(body, MaliciousCodeCanaryIn):
            value = MaliciousCodeInjection(body.marker).marker
            definition = CanaryDefinition(
                CanaryType.MALICIOUS_CODE_INJECTION, body.public_label, secret=value
            )
        elif isinstance(body, PolicyViolationCanaryIn):
            definition = CanaryDefinition(
                CanaryType.POLICY_VIOLATION,
                body.public_label,
                policy=PolicyViolation(
                    body.pattern, body.mode, body.case_sensitive, body.test_text
                ),
            )
        else:
            predicate = (
                None
                if body.predicate is None
                else ArgumentPredicate(
                    body.predicate.argument, body.predicate.operator, body.predicate.value
                )
            )
            definition = CanaryDefinition(
                CanaryType.UNAUTHORIZED_ACTION
                if isinstance(body, UnauthorizedActionCanaryIn)
                else CanaryType.FORBIDDEN_TOOL,
                body.public_label,
                rule=ToolRule(body.tool, predicate),
            )
    except ValueError as exc:
        raise ServiceError(422, str(exc)) from exc
    canary, config_hash = await bounty_service.add_canary(session, cipher, bounty, definition)
    return CanaryOut(id=canary.id, config_hash=config_hash)


@router.post("/bounties/{bounty_id}/activate")
async def activate(
    session: Transaction, bounty: OwnBounty, cipher: CipherDep, settings: SettingsDep
) -> ActivateOut:
    """Run the listing check, pin the config and go live."""
    row = await bounty_service.activate(
        session, cipher, bounty, allow_unfunded=settings.allow_unfunded_activation
    )
    return ActivateOut(state=bounty.state, config_hash=row.config_hash)


@router.post("/bounties/{bounty_id}/fund")
async def fund(
    body: FundIn,
    request: Request,
    session: Transaction,
    bounty: OwnBounty,
    cipher: CipherDep,
    settings: SettingsDep,
) -> FundOut:
    """Start funding. The bounty goes live when the deposit has confirmed.

    This process never touches the wallet: it hands out the escrow address it
    was configured with, and the payment process watches for the deposit.
    """
    due = await bounty_service.fund(
        session, cipher, bounty, body.refund_address, address_rule(settings)
    )
    return FundOut(
        state=bounty.state,
        escrow_address=request.app.state.escrow_address,
        deposit_code=bounty.deposit_code,
        amount_due_zatoshi=due,
        confirmations_required=settings.deposit_confirmations,
    )


@router.post("/bounties/{bounty_id}/close")
async def close(
    body: CloseIn, session: Transaction, bounty: OwnBounty, settings: SettingsDep
) -> CloseOut:
    """Stop taking attempts and ask for the unspent escrow back.

    The payment process closes the bounty and sends the refund once attempts
    already in flight have been judged and any break among them paid.
    """
    await bounty_service.request_close(session, bounty, body.refund_address, address_rule(settings))
    return CloseOut(state=bounty.state, closing=bounty.state is not BountyState.CLOSED)


@router.delete("/bounties/{bounty_id}", status_code=204)
async def delete_bounty(session: Transaction, bounty: OwnBounty) -> Response:
    """Delete a draft that holds no money, or a closed bounty. It leaves the dashboard
    and every public page; its ledger, findings and payouts are kept."""
    await bounty_service.delete(session, bounty)
    return Response(status_code=204)


@router.post("/bounties/{bounty_id}/rearm", status_code=202)
async def request_rearm(session: Transaction, bounty: OwnBounty) -> RearmOut:
    """Replay the paid attack against the patched config.

    The replay worker does the replaying; this only queues it. The bounty can be
    funded and activated on the config once the check has passed.
    """
    check = await rearm.request(session, bounty)
    return RearmOut(
        state=check.state, config_hash=check.config_hash, tripped_runs=check.tripped_runs
    )


@router.post("/bounties/{bounty_id}/pause")
async def pause(session: Transaction, bounty: OwnBounty) -> StateOut:
    """Stop taking attempts. Resume with `activate`."""
    await bounty_service.pause(session, bounty)
    return StateOut(state=bounty.state)


def _summary(summary: reports.BountySummary) -> dict[str, Any]:
    bounty = summary.bounty
    return {
        "id": bounty.id,
        "title": bounty.title,
        "description": bounty.description,
        "state": bounty.state,
        "closing": bounty.close_requested_at is not None and bounty.state is not BountyState.CLOSED,
        "reward_zatoshi": bounty.reward_zatoshi,
        "balance_zatoshi": summary.balance_zatoshi,
        "required_zatoshi": summary.required_zatoshi,
    }


@router.get("/sponsor/bounties")
async def my_bounties(session: Transaction, sponsor: SponsorDep) -> list[SponsorBounty]:
    return [
        SponsorBounty(**_summary(summary))
        for summary in await reports.sponsor_bounties(session, sponsor)
    ]


@router.get("/sponsor/overview")
async def sponsor_overview(session: Transaction, sponsor: SponsorDep) -> SponsorOverviewOut:
    """The sponsor's own page: every bounty they have listed with its attacks, breaks and
    payouts, and every verified break with the hunter who made it."""
    overview = await reports.sponsor_overview(session, sponsor)
    listed = [
        SponsorshipOut(
            **_summary(item.summary),
            created_at=item.summary.bounty.created_at,
            attempts=item.attempts,
            breaks=item.breaks,
            last_attack_at=item.last_attack_at,
            paid_zatoshi=item.paid_zatoshi,
            model=item.model,
            entry_points=list(item.entry_points),
            canaries=item.canaries,
        )
        for item in overview.sponsorships
    ]

    def count(state: BountyState) -> int:
        return sum(1 for item in listed if item.state is state)

    return SponsorOverviewOut(
        member_since=sponsor.created_at,
        totals=SponsorTotals(
            bounties=len(listed),
            live=count(BountyState.LIVE),
            drafts=count(BountyState.DRAFT) + count(BountyState.AWAITING_DEPOSIT),
            paused=count(BountyState.PAUSED),
            past=count(BountyState.CLOSED),
            escrow_zatoshi=sum(item.balance_zatoshi for item in listed),
            attempts=sum(item.attempts for item in listed),
            breaks=sum(item.breaks for item in listed),
            paid_zatoshi=sum(item.paid_zatoshi for item in listed),
        ),
        bounties=listed,
        breaks=[
            SponsorBreakOut(
                id=found.finding.id,
                bounty_id=found.bounty.id,
                bounty_title=found.bounty.title,
                hunter=None
                if found.hunter is None
                else HunterOut(
                    name=found.hunter.name,
                    picture_url=found.hunter.uploaded_picture_url or found.hunter.picture_url,
                    github=found.hunter.github,
                    company=found.hunter.company,
                    role=found.hunter.role,
                    website=found.hunter.website,
                ),
                house=found.finding.state is FindingState.WAIVED,
                canary=CanaryPublic(type=found.canary.type, public_label=found.canary.public_label),
                tripped_runs=found.finding.tripped_runs,
                total_runs=RUNS_PER_ATTEMPT,
                reward_zatoshi=found.payout.amount_zatoshi if found.payout else 0,
                payout_state=found.payout.state if found.payout else None,
                txid=found.payout.txid if found.payout else None,
                verified_at=found.finding.verified_at,
            )
            for found in overview.breaks
        ],
    )


@router.get("/sponsor/bounties/{bounty_id}")
async def my_bounty(
    request: Request,
    session: Transaction,
    bounty: OwnBounty,
    cipher: CipherDep,
    settings: SettingsDep,
) -> SponsorBountyDetail:
    """Everything about one bounty, including the decrypted config and canaries, for its owner."""
    detail = await reports.bounty_detail(session, cipher, bounty)
    target = detail.target
    check = (
        await rearm.check_for(session, bounty.id, detail.config_hash)
        if detail.config_hash is not None
        else None
    )
    hunt = await house.latest(session, bounty, detail.config_hash)
    config = None
    if target is not None and detail.config_version is not None and detail.config_hash is not None:
        config = ConfigDetail(
            version=detail.config_version,
            config_hash=detail.config_hash,
            live=detail.config_is_live,
            system_prompt=target.config.system_prompt,
            model=target.config.model,
            tools=[
                ToolOut(
                    name=tool.name,
                    description=tool.description,
                    parameters=dict(tool.parameters),
                    result=tool.result,
                    untrusted=tool.untrusted,
                )
                for tool in target.config.tools
            ],
            params=dict(target.config.params),
            entry_points=sorted(target.config.entry_points),
            scaffold={
                slot: ScaffoldOut(label=frame.label, tool=frame.tool, turns=frame.turns)
                for slot, frame in target.config.scaffold.items()
            },
            task=target.config.task,
            endpoint_base=target.config.endpoint,
            endpoint_key_set=target.endpoint_key is not None,
        )
    return SponsorBountyDetail(
        **_summary(detail.summary),
        deposit_code=bounty.deposit_code,
        escrow_address=request.app.state.escrow_address,
        confirmations_required=settings.deposit_confirmations,
        refund_address=detail.refund_address,
        rearm=None
        if check is None
        else RearmOut(
            state=check.state, config_hash=check.config_hash, tripped_runs=check.tripped_runs
        ),
        config=config,
        canaries=[
            CanaryDetail(
                id=canary.id,
                type=canary.definition.type,
                public_label=canary.definition.public_label,
                secret=canary.definition.secret,
                rule=bounty_service.structured_parameters(canary.definition),
            )
            for canary in (target.canaries if target else ())
        ],
        shares_breaks=bounty.shares_breaks,
        watch=bounty.watch,
        house_hunt=None
        if hunt is None
        else HouseHuntOut(
            state=hunt.state,  # type: ignore[arg-type]
            outcome=hunt.outcome,  # type: ignore[arg-type]
            tries=hunt.tries,
            detail=hunt.detail,
        ),
    )


@router.patch("/bounties/{bounty_id}/settings")
async def bounty_settings(
    body: BountySettingsIn,
    session: Transaction,
    bounty: OwnBounty,
    sponsor: SponsorDep,
    settings: SettingsDep,
) -> BountySettingsOut:
    """Share this bounty's breaks with the library, or stop; have it scanned every day,
    or stop. Sharing is the sponsor's choice and is off until they make it."""
    if body.shares_breaks is not None:
        bounty.shares_breaks = body.shares_breaks
    if body.watch is not None and body.watch != bounty.watch:
        if body.watch:
            plans.require(sponsor, settings, "a scan every day")
        bounty.watch = body.watch
    await session.flush()
    return BountySettingsOut(shares_breaks=bounty.shares_breaks, watch=bounty.watch)


@router.get("/bounties/{bounty_id}/findings")
async def findings(session: Transaction, bounty: OwnBounty, cipher: CipherDep) -> list[FindingOut]:
    """Every verified break of the bounty, with the attack, all transcripts and the evidence."""

    def public(canary: Canary | None) -> CanaryPublic | None:
        if canary is None:
            return None
        return CanaryPublic(type=canary.type, public_label=canary.public_label)

    found = await reports.findings_report(session, cipher, bounty)
    # Who made each winning attack, for those made by an account. Their profile is theirs
    # to fill in, and what they put there is what the sponsor is shown.
    hunters: dict[Any, HunterOut] = {}
    attempt_ids = [report.finding.attempt_id for report in found]
    if attempt_ids:
        for attempt_id, who in await session.execute(
            select(Attempt.id, Sponsor)
            .join(Sponsor, Sponsor.id == Attempt.account_id)
            .where(Attempt.id.in_(attempt_ids))
        ):
            hunters[attempt_id] = HunterOut(
                name=who.name,
                picture_url=who.uploaded_picture_url or who.picture_url,
                github=who.github,
                company=who.company,
                role=who.role,
                website=who.website,
            )
    out = []
    for report in found:
        payout = report.payout
        out.append(
            FindingOut(
                id=report.finding.id,
                state=report.finding.state.value,
                canary=CanaryPublic(
                    type=report.canary.type, public_label=report.canary.public_label
                ),
                tripped_runs=report.finding.tripped_runs,
                config_hash=report.finding.config_hash,
                verified_at=report.finding.verified_at,
                attack=[FillIn(**fill) for fill in report.attack],
                payout=None
                if payout is None
                else PayoutOut(
                    state=payout.state, amount_zatoshi=payout.amount_zatoshi, txid=payout.txid
                ),
                runs=[
                    RunOut(
                        run_index=run.run_index,
                        transcript=[MessageOut(**message) for message in run.transcript],
                        evidence=[
                            EvidenceOut(
                                canary=public(item.canary),
                                message_index=item.message_index,
                                location=item.location,
                                representation=item.representation,
                                matched_text=item.matched_text,
                            )
                            for item in run.evidence
                        ],
                    )
                    for run in report.runs
                ],
                hunter=hunters.get(report.finding.attempt_id),
            )
        )
    return out


@router.get("/bounties/{bounty_id}/regressions")
async def regression_cases(session: Transaction, bounty: OwnBounty) -> list[RegressionCaseOut]:
    """Exact verified attacks retained as replayable cases for this agent."""
    cases = await session.scalars(
        select(SavedAttack)
        .where(SavedAttack.bounty_id == bounty.id)
        .order_by(SavedAttack.created_at, SavedAttack.id)
    )
    return [
        RegressionCaseOut(
            id=case.id,
            finding_id=case.finding_id,
            broken_config_hash=case.broken_config_hash,
            canary_type=case.canary_type,
            attack=[FillIn(**fill) for fill in case.payload_json],
            created_at=case.created_at,
        )
        for case in cases
    ]


async def _continuous_out(session: Transaction, cipher: Cipher, run: Any) -> ContinuousRunOut:
    row = await session.get(TargetConfigRow, run.config_id)
    results = continuous.results_of(cipher, run)
    regressions = (
        await continuous.regression_cases(session, run, cipher) if run.state == "done" else []
    )
    succeeded = sum(1 for result in results if result.get("tripped"))
    return ContinuousRunOut(
        id=run.id,
        config_version=row.version if row is not None else 0,
        config_hash=run.config_hash,
        trigger=run.trigger,
        state=run.state,
        passed=continuous.passed(results) if run.state == "done" else None,
        total=len(results),
        succeeded=succeeded,
        success_rate=(succeeded / len(results))
        if results
        else (0.0 if run.state == "done" else None),
        failures=succeeded,
        attack_success_rate=(succeeded / len(results))
        if results
        else (0.0 if run.state == "done" else None),
        covered_canary_types=sorted(
            {CanaryType(result["canary_type"]) for result in results}, key=lambda item: item.value
        ),
        regressed=[ContinuousProbeOut(**item) for item in regressions],
        created_at=run.created_at,
        finished_at=run.finished_at,
    )


@router.get("/bounties/{bounty_id}/continuous")
async def continuous_testing(
    request: Request,
    session: Transaction,
    bounty: OwnBounty,
    sponsor: SponsorDep,
    cipher: CipherDep,
    settings: SettingsDep,
) -> ContinuousTestingOut:
    """Version-by-version corpus outcomes. Results contain labels and verdicts, never attacks."""
    runs = await continuous.history(session, bounty.id)
    return ContinuousTestingOut(
        plan=_plan_out(request, sponsor, settings),
        runs=[await _continuous_out(session, cipher, run) for run in runs],
    )


@router.post("/bounties/{bounty_id}/continuous/gate", status_code=202)
async def continuous_gate(
    session: Transaction,
    bounty: OwnBounty,
    sponsor: SponsorDep,
    settings: SettingsDep,
    cipher: CipherDep,
) -> ContinuousRunOut:
    """Queue the CI regression gate for the newest version; poll the returned run at GET.

    A completed run passes only when no saved attack succeeds. This deliberately exposes
    only case identifiers and canary kinds, not corpus payloads.
    """
    plans.require(sponsor, settings, "continuous testing")
    row = await bounty_service.latest_config(session, bounty.id)
    if row is None:
        raise ServiceError(409, "describe the agent before running the regression gate")
    run = await continuous.request(session, bounty, row, trigger="ci")
    return await _continuous_out(session, cipher, run)


def _scan_out(cipher: Cipher, found: Scan | None, config_hash: str | None) -> ScanOut:
    results = scan.results_of(cipher, found) if found is not None else []
    return ScanOut(
        state=found.state if found is not None else None,  # type: ignore[arg-type]
        config_hash=config_hash,
        probes=[ProbeOut(**result) for result in results],
        held=sum(1 for result in results if not result["tripped"]),
        total=len(results),
        risks=playbook.RISK,
        requested_at=found.created_at if found is not None else None,
        finished_at=found.finished_at if found is not None else None,
        with_library=found.with_library if found is not None else False,
        corpus_type=found.corpus_type if found is not None else None,
    )


@router.get("/bounties/{bounty_id}/scan")
async def read_scan(session: Transaction, bounty: OwnBounty, cipher: CipherDep) -> ScanOut:
    """The baseline scan of the bounty's current config: the playbook's probes and what
    each one did. Editing the config or its canaries makes a new config, with no scan yet."""
    found, config_hash = await scan.latest(session, bounty)
    return _scan_out(cipher, found, config_hash)


@router.post("/bounties/{bounty_id}/scan", status_code=202, dependencies=[Depends(limit_by_client)])
async def start_scan(
    session: Transaction,
    bounty: OwnBounty,
    cipher: CipherDep,
    sponsor: SponsorDep,
    settings: SettingsDep,
    again: bool = False,
    corpus_type: CanaryType | None = None,
) -> ScanOut:
    """Scan the current config: the baseline playbook's well-known attacks, each aimed at
    one of the agent's own canaries, then every attack this bounty has paid for and,
    with the Watch plan, the shared library. Each is replayed once. There is one scan
    per config; `again` runs a finished one afresh, which the plan also covers."""
    if again and corpus_type is None:
        plans.require(sponsor, settings, "scanning an unchanged agent again")
    # Where no plan is sold everyone may draw on the library; where one is, its holders.
    found = await scan.request(
        session,
        bounty,
        again=again or corpus_type is not None,
        with_library=plans.active(sponsor, settings) or corpus_type is not None,
        corpus_type=corpus_type,
    )
    await session.refresh(found)
    return _scan_out(cipher, found, found.config_hash)


@router.get("/bounties/{bounty_id}/compliance")
async def compliance_report(
    session: Transaction, bounty: OwnBounty, cipher: CipherDep
) -> ComplianceOut:
    """What the bounty's testing shows, set against OWASP's lists of risks for LLM and
    agentic applications, the EU AI Act and ISO/IEC 42001: for each, which evidence bears
    on it and whether that evidence is there. It holds labels and counts, and never a
    prompt, a secret, a rule or an attack. It says where evidence stands, never that a
    requirement is met."""
    evidence = await reports.compliance_evidence(session, cipher, bounty)
    return ComplianceOut(**compliance.report(evidence, datetime.now(UTC)))


@router.get("/bounties/{bounty_id}/attempts")
async def attempts(session: Transaction, bounty: OwnBounty) -> AttemptActivity:
    """How much the bounty is being attacked: totals and the latest attempts.

    Only a verified break hands the sponsor an attack's content (see findings).
    """
    verified = Finding.id.is_not(None)
    # The house hunter's attacks are in the same queue, and are told apart.
    by_house = func.coalesce(Sponsor.is_house, false())
    total, queued, broke, housed = (
        await session.execute(
            select(
                func.count(Attempt.id),
                func.count(Attempt.id).filter(
                    Attempt.state.in_([AttemptState.QUEUED, AttemptState.RUNNING])
                ),
                func.count(Finding.id),
                func.count(Attempt.id).filter(by_house),
            )
            .outerjoin(Finding, Finding.attempt_id == Attempt.id)
            .outerjoin(Sponsor, Sponsor.id == Attempt.account_id)
            .where(Attempt.bounty_id == bounty.id)
        )
    ).one()
    recent = await session.execute(
        select(Attempt.id, Attempt.state, verified, by_house, Attempt.submitted_at)
        .outerjoin(Finding, Finding.attempt_id == Attempt.id)
        .outerjoin(Sponsor, Sponsor.id == Attempt.account_id)
        .where(Attempt.bounty_id == bounty.id)
        .order_by(Attempt.submitted_at.desc(), Attempt.id)
        .limit(50)
    )
    return AttemptActivity(
        total=total,
        queued=queued,
        verified=broke,
        house=housed,
        recent=[
            AttemptLine(
                id=id_, state=state, verified=bool(is_break), house=bool(housed), submitted_at=at
            )
            for id_, state, is_break, housed, at in recent
        ],
    )


@router.get("/bounties/{bounty_id}/ledger")
async def ledger(session: Transaction, bounty: OwnBounty) -> LedgerOut:
    """Deposits, payouts, fees and refunds, oldest first, and the balance they sum to."""
    lines, balance = await reports.ledger_report(session, bounty)
    return LedgerOut(
        balance_zatoshi=balance,
        entries=[
            LedgerEntryOut(
                kind=line.kind,
                amount_zatoshi=line.amount_zatoshi,
                txid=line.txid,
                created_at=line.created_at,
            )
            for line in lines
        ],
    )
