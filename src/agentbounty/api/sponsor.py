"""Sponsor endpoints: authenticated with the API key issued at sign-up."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import func, select

from agentbounty.api.deps import CipherDep, OwnBounty, SettingsDep, SponsorDep, Transaction
from agentbounty.api.ratelimit import limit_by_client
from agentbounty.api.schemas import (
    AccountOut,
    AccountUpdate,
    ActivateOut,
    ApiKeyOut,
    AttemptActivity,
    AttemptLine,
    BountyCreate,
    BountyCreated,
    CanaryDetail,
    CanaryIn,
    CanaryOut,
    CanaryPublic,
    CloseIn,
    CloseOut,
    ConfigDetail,
    ConfigIn,
    ConfigOut,
    CostStep,
    CreditsOut,
    EvidenceOut,
    FillIn,
    FindingOut,
    FundIn,
    FundOut,
    HunterOut,
    LedgerEntryOut,
    LedgerOut,
    MessageOut,
    ModelsOut,
    ModelTestIn,
    ModelTestOut,
    PayoutOut,
    ProbeOut,
    RearmOut,
    ReferralCodeCreate,
    ReferralCodeOut,
    ReferralRedeem,
    RunOut,
    ScanOut,
    SecretCanaryIn,
    SponsorBounty,
    SponsorBountyDetail,
    SponsorCreate,
    SponsorCreated,
    StateOut,
    ToolOut,
)
from agentbounty.api.session import COOKIE, seal_sponsor, set_session_cookie
from agentbounty.models import (
    Attempt,
    AttemptState,
    BountyState,
    Canary,
    CanaryType,
    Finding,
    Scan,
    Sponsor,
)
from agentbounty.money import format_amount
from agentbounty.services import bounty as bounty_service
from agentbounty.services import (
    credits,
    endpoint,
    pictures,
    playbook,
    provider,
    rearm,
    reports,
    scan,
)
from agentbounty.services.bounty import CanaryDefinition
from agentbounty.services.crypto import Cipher
from agentbounty.services.errors import ServiceError
from agentbounty.services.harness import MockTool, TargetConfig
from agentbounty.services.judge import ArgumentPredicate, ToolRule
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
        is_ambassador=sponsor.is_ambassador,
        is_admin=credits.is_admin(sponsor, settings),
        is_owner=credits.is_owner(sponsor, settings),
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
        ),
    )


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
    """Redeem a referral code, which lets the account take part in bounties."""
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


@router.get("/referral-codes")
async def referral_codes(session: Transaction, sponsor: SponsorDep) -> list[ReferralCodeOut]:
    """The referral codes this account has made, and which have been used."""
    return [
        ReferralCodeOut(
            code=line.code.code, used=line.used_by is not None, created_at=line.code.created_at
        )
        for line in await credits.codes_of(session, sponsor)
    ]


@router.post("/referral-codes", status_code=201, dependencies=[Depends(limit_by_client)])
async def create_referral_codes(
    body: ReferralCodeCreate, session: Transaction, sponsor: SponsorDep
) -> list[ReferralCodeOut]:
    """Make single-use referral codes. Only ambassadors can."""
    made = await credits.create_codes(session, sponsor, body.count)
    for code in made:
        await session.refresh(code)
    return [
        ReferralCodeOut(code=code.code, used=False, created_at=code.created_at) for code in made
    ]


@router.post("/bounties", status_code=201)
async def create_bounty(
    body: BountyCreate, session: Transaction, sponsor: SponsorDep, settings: SettingsDep
) -> BountyCreated:
    currency = settings.currency
    if body.reward_zatoshi < currency.minimum:
        least = format_amount(currency.minimum, currency)
        raise ServiceError(422, f"the reward must be at least {least} {currency.symbol}")
    bounty = await bounty_service.create_bounty(
        session, sponsor, body.reward_zatoshi, settings.fee_rate, body.title, body.description
    )
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
                MockTool(tool.name, tool.description, tool.parameters, tool.result)
                for tool in body.tools
            ),
            params=body.params,
            entry_points=tuple(body.entry_points),
            endpoint=base,
        )
    except ValueError as exc:
        raise ServiceError(422, str(exc)) from exc
    key = body.endpoint_key.get_secret_value() if body.endpoint_key else None
    row = await bounty_service.put_config(session, cipher, bounty, config, key)
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
        else:
            predicate = (
                None
                if body.predicate is None
                else ArgumentPredicate(
                    body.predicate.argument, body.predicate.operator, body.predicate.value
                )
            )
            definition = CanaryDefinition(
                CanaryType.FORBIDDEN_TOOL, body.public_label, rule=ToolRule(body.tool, predicate)
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
                )
                for tool in target.config.tools
            ],
            params=dict(target.config.params),
            entry_points=sorted(target.config.entry_points),
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
                rule=None
                if canary.definition.rule is None
                else bounty_service.rule_to_json(canary.definition.rule),
            )
            for canary in (target.canaries if target else ())
        ],
    )


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
    )


@router.get("/bounties/{bounty_id}/scan")
async def read_scan(session: Transaction, bounty: OwnBounty, cipher: CipherDep) -> ScanOut:
    """The baseline scan of the bounty's current config: the playbook's probes and what
    each one did. Editing the config or its canaries makes a new config, with no scan yet."""
    found, config_hash = await scan.latest(session, bounty)
    return _scan_out(cipher, found, config_hash)


@router.post("/bounties/{bounty_id}/scan", status_code=202, dependencies=[Depends(limit_by_client)])
async def start_scan(session: Transaction, bounty: OwnBounty, cipher: CipherDep) -> ScanOut:
    """Run the baseline playbook against the current config: six well-known attacks, each
    aimed at one of the agent's own canaries and replayed once. One scan per config."""
    found = await scan.request(session, bounty)
    await session.refresh(found)
    return _scan_out(cipher, found, found.config_hash)


@router.get("/bounties/{bounty_id}/attempts")
async def attempts(session: Transaction, bounty: OwnBounty) -> AttemptActivity:
    """How much the bounty is being attacked: totals and the latest attempts.

    Only a verified break hands the sponsor an attack's content (see findings).
    """
    verified = Finding.id.is_not(None)
    total, queued, broke = (
        await session.execute(
            select(
                func.count(Attempt.id),
                func.count(Attempt.id).filter(
                    Attempt.state.in_([AttemptState.QUEUED, AttemptState.RUNNING])
                ),
                func.count(Finding.id),
            )
            .outerjoin(Finding, Finding.attempt_id == Attempt.id)
            .where(Attempt.bounty_id == bounty.id)
        )
    ).one()
    recent = await session.execute(
        select(Attempt.id, Attempt.state, verified, Attempt.submitted_at)
        .outerjoin(Finding, Finding.attempt_id == Attempt.id)
        .where(Attempt.bounty_id == bounty.id)
        .order_by(Attempt.submitted_at.desc(), Attempt.id)
        .limit(50)
    )
    return AttemptActivity(
        total=total,
        queued=queued,
        verified=broke,
        recent=[
            AttemptLine(id=id_, state=state, verified=bool(is_break), submitted_at=at)
            for id_, state, is_break, at in recent
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
