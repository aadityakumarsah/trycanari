"""Sponsor endpoints: authenticated with the API key issued at sign-up."""

from __future__ import annotations

from typing import Any

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
    LedgerEntryOut,
    LedgerOut,
    MessageOut,
    PayoutOut,
    RearmOut,
    ReferralCodeCreate,
    ReferralCodeOut,
    ReferralRedeem,
    RunOut,
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
)
from agentbounty.services import bounty as bounty_service
from agentbounty.services import credits, rearm, reports
from agentbounty.services.bounty import CanaryDefinition
from agentbounty.services.errors import ServiceError
from agentbounty.services.harness import MockTool, TargetConfig
from agentbounty.services.judge import ArgumentPredicate, ToolRule

router = APIRouter(prefix="/v1", tags=["sponsor"])


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
        picture_url=sponsor.picture_url,
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
    bounty = await bounty_service.create_bounty(
        session, sponsor, body.reward_zatoshi, settings.fee_rate, body.title, body.description
    )
    return BountyCreated(id=bounty.id, state=bounty.state)


@router.put("/bounties/{bounty_id}/config")
async def put_config(
    body: ConfigIn, session: Transaction, bounty: OwnBounty, cipher: CipherDep
) -> ConfigOut:
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
        )
    except ValueError as exc:
        raise ServiceError(422, str(exc)) from exc
    row = await bounty_service.put_config(session, cipher, bounty, config)
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
        session, cipher, bounty, body.refund_address, settings.zcash_network
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
    await bounty_service.request_close(session, bounty, body.refund_address, settings.zcash_network)
    return CloseOut(state=bounty.state, closing=bounty.state is not BountyState.CLOSED)


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

    out = []
    for report in await reports.findings_report(session, cipher, bounty):
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
            )
        )
    return out


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
