"""Sponsor endpoints: authenticated with the API key issued at sign-up."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request

from agentbounty.api.deps import CipherDep, OwnBounty, SettingsDep, SponsorDep, Transaction
from agentbounty.api.ratelimit import limit_by_client
from agentbounty.api.schemas import (
    ActivateOut,
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
    RunOut,
    SecretCanaryIn,
    SponsorBounty,
    SponsorBountyDetail,
    SponsorCreate,
    SponsorCreated,
    StateOut,
    ToolOut,
)
from agentbounty.models import BountyState, Canary, CanaryType
from agentbounty.services import bounty as bounty_service
from agentbounty.services import rearm, reports
from agentbounty.services.bounty import CanaryDefinition
from agentbounty.services.errors import ServiceError
from agentbounty.services.harness import MockTool, TargetConfig
from agentbounty.services.judge import ArgumentPredicate, ToolRule

router = APIRouter(prefix="/v1", tags=["sponsor"])


@router.post("/sponsors", status_code=201, dependencies=[Depends(limit_by_client)])
async def create_sponsor(body: SponsorCreate, session: Transaction) -> SponsorCreated:
    sponsor, api_key = await bounty_service.create_sponsor(session, body.email)
    return SponsorCreated(sponsor_id=sponsor.id, api_key=api_key)


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
