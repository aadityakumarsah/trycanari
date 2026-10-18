"""Managed assurance engagements: private scope, lifecycle, and readiness evidence."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import AssuranceEngagement, AssuranceInvoice, Bounty, Sponsor, TargetConfig
from agentbounty.services import billing
from agentbounty.services import bounty as bounty_service
from agentbounty.services.crypto import Cipher
from agentbounty.services.errors import ServiceError

WORKFLOW = "assurance.workflow"
FEARED_FAILURE = "assurance.feared_failure"
IDENTITY_CONTROLS = "assurance.identity_controls"
HUMAN_APPROVAL = "assurance.human_approval"
OPERATOR_NOTES = "assurance.operator_notes"
LIMITATIONS = "assurance.limitations"

ACTIVE = {"requested", "scoping", "testing", "remediation", "retest"}


@dataclass(frozen=True)
class Scope:
    workflow: str
    feared_failure: str
    identity_controls: str
    human_approval: str


@dataclass(frozen=True)
class Readiness:
    agent_configured: bool
    boundaries_defined: bool
    attack_surface_declared: bool
    consequence_documented: bool
    identity_documented: bool
    approval_documented: bool

    @property
    def ready(self) -> bool:
        return all(
            (
                self.agent_configured,
                self.boundaries_defined,
                self.attack_surface_declared,
                self.consequence_documented,
                self.identity_documented,
                self.approval_documented,
            )
        )


def _encrypt(cipher: Cipher, value: str, purpose: str) -> bytes:
    return cipher.encrypt(value.strip().encode(), purpose=purpose)


def _decrypt(cipher: Cipher, value: bytes, purpose: str) -> str:
    return cipher.decrypt(value, purpose=purpose).decode()


def optional_text(cipher: Cipher, value: bytes | None, purpose: str) -> str:
    return _decrypt(cipher, value, purpose) if value is not None else ""


def scope_of(cipher: Cipher, engagement: AssuranceEngagement) -> Scope:
    return Scope(
        workflow=_decrypt(cipher, engagement.workflow_enc, WORKFLOW),
        feared_failure=_decrypt(cipher, engagement.feared_failure_enc, FEARED_FAILURE),
        identity_controls=_decrypt(cipher, engagement.identity_controls_enc, IDENTITY_CONTROLS),
        human_approval=_decrypt(cipher, engagement.human_approval_enc, HUMAN_APPROVAL),
    )


async def for_bounty(
    session: AsyncSession, bounty_id: uuid.UUID, *, lock: bool = False
) -> AssuranceEngagement | None:
    query = select(AssuranceEngagement).where(AssuranceEngagement.bounty_id == bounty_id)
    if lock:
        query = query.with_for_update()
    return await session.scalar(query)


async def save(
    session: AsyncSession,
    cipher: Cipher,
    bounty: Bounty,
    *,
    service: str,
    stage: str,
    deployment: str,
    untrusted_inputs: list[str],
    sensitive_data: list[str],
    consequential_actions: list[str],
    workflow: str,
    feared_failure: str,
    identity_controls: str,
    human_approval: str,
) -> AssuranceEngagement:
    engagement = await for_bounty(session, bounty.id, lock=True)
    if engagement is not None and engagement.status in {
        "testing",
        "remediation",
        "retest",
        "complete",
        "cancelled",
    }:
        raise ServiceError(409, "the assessment scope is locked while evidence is being produced")
    values: dict[str, Any] = {
        "service": service,
        "stage": stage,
        "deployment": deployment,
        "untrusted_inputs_json": list(dict.fromkeys(untrusted_inputs)),
        "sensitive_data_json": list(dict.fromkeys(sensitive_data)),
        "consequential_actions_json": list(dict.fromkeys(consequential_actions)),
        "workflow_enc": _encrypt(cipher, workflow, WORKFLOW),
        "feared_failure_enc": _encrypt(cipher, feared_failure, FEARED_FAILURE),
        "identity_controls_enc": _encrypt(cipher, identity_controls, IDENTITY_CONTROLS),
        "human_approval_enc": _encrypt(cipher, human_approval, HUMAN_APPROVAL),
        "updated_at": datetime.now(UTC),
    }
    if engagement is None:
        engagement = AssuranceEngagement(bounty_id=bounty.id, **values)
        session.add(engagement)
    else:
        for name, value in values.items():
            setattr(engagement, name, value)
    await session.flush()
    return engagement


async def readiness(
    session: AsyncSession, cipher: Cipher, bounty: Bounty, engagement: AssuranceEngagement
) -> Readiness:
    config = await bounty_service.latest_config(session, bounty.id)
    canaries = await bounty_service.canary_rows(session, config.id) if config is not None else []
    scope = scope_of(cipher, engagement)
    return Readiness(
        agent_configured=config is not None,
        boundaries_defined=bool(canaries),
        attack_surface_declared=bool(engagement.untrusted_inputs_json),
        consequence_documented=bool(scope.feared_failure.strip()),
        identity_documented=bool(scope.identity_controls.strip()),
        approval_documented=bool(scope.human_approval.strip()),
    )


async def require_current_config(session: AsyncSession, bounty: Bounty) -> TargetConfig:
    config = await bounty_service.latest_config(session, bounty.id)
    if config is None:
        raise ServiceError(409, "configure the agent before starting assessment testing")
    return config


async def submit(session: AsyncSession, cipher: Cipher, bounty: Bounty) -> AssuranceEngagement:
    engagement = await for_bounty(session, bounty.id, lock=True)
    if engagement is None:
        raise ServiceError(409, "save the assessment scope before requesting the sprint")
    if engagement.status not in {"draft", "requested"}:
        raise ServiceError(409, "this assessment can no longer be requested")
    engagement.status = "requested"
    engagement.requested_at = engagement.requested_at or datetime.now(UTC)
    engagement.updated_at = datetime.now(UTC)
    # A request may be submitted before setup is complete: hands-on scoping is part of
    # the managed service. Readiness is returned so the missing work remains explicit.
    await readiness(session, cipher, bounty, engagement)
    await session.flush()
    return engagement


def apply_admin_update(
    cipher: Cipher,
    engagement: AssuranceEngagement,
    *,
    status: str | None,
    quoted_price_cents: int | None,
    operator_notes: str | None,
    limitations: str | None,
) -> None:
    now = datetime.now(UTC)
    if status is not None:
        if engagement.status in {"complete", "cancelled"} and status != engagement.status:
            raise ServiceError(409, "a completed or cancelled assessment cannot be reopened")
        engagement.status = status
        if status in ACTIVE and engagement.started_at is None:
            engagement.started_at = now
        if status == "complete":
            engagement.completed_at = now
        elif engagement.completed_at is not None:
            engagement.completed_at = None
    if quoted_price_cents is not None:
        engagement.quoted_price_cents = quoted_price_cents
    if operator_notes is not None:
        engagement.operator_notes_enc = (
            _encrypt(cipher, operator_notes, OPERATOR_NOTES) if operator_notes.strip() else None
        )
    if limitations is not None:
        engagement.limitations_enc = (
            _encrypt(cipher, limitations, LIMITATIONS) if limitations.strip() else None
        )
    engagement.updated_at = now


async def live_invoice(session: AsyncSession, engagement_id: uuid.UUID) -> AssuranceInvoice | None:
    """The invoice that is still owed or settled, if there is one. A voided invoice is
    history: it does not stop a new one being issued."""
    return await session.scalar(
        select(AssuranceInvoice)
        .where(
            AssuranceInvoice.engagement_id == engagement_id,
            AssuranceInvoice.state.in_(("open", "paid")),
        )
        .limit(1)
    )


async def issue_invoice(
    session: AsyncSession,
    biller: billing.BillingBackend | None,
    bounty: Bounty,
    engagement: AssuranceEngagement,
    sponsor: Sponsor,
    currency: str,
) -> AssuranceInvoice:
    """Bill the sponsor for this assessment, once.

    The price is what an operator quoted: billing a zero is a mistake, not a free
    assessment, so it is refused rather than sent.

    The provider is called before the row exists, so an issuance that fails partway could
    leave a payable invoice at the provider that no row of ours points at. The issuance id
    below is what closes that: it counts the invoices this engagement already has, so a
    retry of a failed issuance carries the same id and the provider replays its first
    answer instead of making a second payable invoice. Issuing again after a void sees one
    more row, so that is a new id and genuinely a new invoice.
    """
    if biller is None:
        raise ServiceError(503, "this deployment issues no invoices")
    if engagement.quoted_price_cents <= 0:
        raise ServiceError(409, "quote a price before invoicing the assessment")
    if (existing := await live_invoice(session, engagement.id)) is not None:
        if existing.state == "paid":
            raise ServiceError(409, "this assessment is already paid for")
        raise ServiceError(409, "this assessment already has an open invoice")
    # Committed rows only, so a retry that wrote nothing counts the same way twice.
    already = await session.scalar(
        select(func.count())
        .select_from(AssuranceInvoice)
        .where(AssuranceInvoice.engagement_id == engagement.id)
    )
    try:
        made = await biller.create_invoice(
            email=sponsor.email,
            amount_cents=engagement.quoted_price_cents,
            currency=currency,
            description=f"Managed agent assessment: {bounty.title}",
            reference=str(engagement.id),
            issuance=f"{engagement.id}:{int(already or 0)}",
        )
    except billing.BillingError as exc:
        # 502 for a provider that may yet work, 400 for one that refused outright.
        raise ServiceError(
            400 if exc.permanent else 502, f"the invoice was not created: {exc}"
        ) from exc
    invoice = AssuranceInvoice(
        engagement_id=engagement.id,
        sponsor_id=sponsor.id,
        provider=biller.provider,
        provider_invoice_id=made.provider_id,
        amount_cents=made.amount_cents,
        currency=made.currency,
        state="open",
        hosted_url=made.hosted_url,
    )
    session.add(invoice)
    await session.flush()
    return invoice


async def settle_invoice(
    session: AsyncSession, provider: str, provider_invoice_id: str, *, state: str
) -> AssuranceInvoice | None:
    """Record what the provider says became of an invoice.

    Returns None for an id we never issued, so a webhook for someone else's invoice is
    ignored rather than treated as an error. Settling twice changes nothing: a provider
    may deliver the same event more than once.
    """
    invoice = await session.scalar(
        select(AssuranceInvoice)
        .where(
            AssuranceInvoice.provider == provider,
            AssuranceInvoice.provider_invoice_id == provider_invoice_id,
        )
        .with_for_update()
    )
    if invoice is None or invoice.state == state:
        return invoice
    if invoice.state == "paid":
        # Money already arrived. Nothing a later event says undoes that on its own.
        return invoice
    invoice.state = state
    invoice.paid_at = datetime.now(UTC) if state == "paid" else None
    await session.flush()
    return invoice


async def adopt_invoice(
    session: AsyncSession,
    provider: str,
    provider_invoice_id: str,
    *,
    engagement_id: uuid.UUID,
    amount_cents: int,
    currency: str,
    hosted_url: str | None,
    state: str,
) -> AssuranceInvoice | None:
    """Record an invoice the provider holds that no row of ours points at.

    Issuing writes its row after the provider call, and an idempotency key is only good
    for as long as the provider keeps it, so an invoice can exist at the provider with
    nothing here pointing at it. The engagement travels in the invoice's metadata, so
    when money arrives for one of these it can be taken up rather than ignored.

    Only into an empty slot. If the engagement already has a live invoice then two are
    payable at once, which is a real discrepancy and a person's to settle: voiding one
    automatically would be deciding what somebody paid for.
    """
    engagement = await session.get(AssuranceEngagement, engagement_id)
    if engagement is None:
        return None
    bounty = await session.get(Bounty, engagement.bounty_id)
    if bounty is None:
        return None
    if await live_invoice(session, engagement_id) is not None:
        return None
    invoice = AssuranceInvoice(
        engagement_id=engagement_id,
        sponsor_id=bounty.sponsor_id,
        provider=provider,
        provider_invoice_id=provider_invoice_id,
        amount_cents=amount_cents,
        currency=currency,
        state=state,
        hosted_url=hosted_url,
        paid_at=datetime.now(UTC) if state == "paid" else None,
    )
    session.add(invoice)
    await session.flush()
    return invoice
