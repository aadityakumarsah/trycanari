"""Managed agent-assurance scope for sponsors and the private delivery queue."""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from agentbounty.api.admin import Limit, Offset, record
from agentbounty.api.deps import AdminDep, CipherDep, OwnBounty, Transaction
from agentbounty.api.ratelimit import limit_by_client
from agentbounty.api.schemas import (
    AdminAssuranceOut,
    AdminAssurancePage,
    AdminAssuranceUpdate,
    AssuranceIn,
    AssuranceOut,
    AssuranceReadiness,
    AssuranceStateOut,
)
from agentbounty.models import AssuranceEngagement, Bounty, Sponsor
from agentbounty.services import assurance
from agentbounty.services.crypto import Cipher

sponsor = APIRouter(prefix="/v1", tags=["assurance"])
admin = APIRouter(
    prefix="/v1/admin/assessments",
    tags=["admin"],
    include_in_schema=False,
    dependencies=[Depends(limit_by_client)],
)


async def _out(
    session: Transaction,
    cipher: Cipher,
    bounty: Bounty,
    engagement: AssuranceEngagement,
) -> AssuranceOut:
    scope = assurance.scope_of(cipher, engagement)
    ready = await assurance.readiness(session, cipher, bounty, engagement)
    return AssuranceOut(
        id=engagement.id,
        bounty_id=bounty.id,
        service=engagement.service,  # type: ignore[arg-type]
        status=engagement.status,  # type: ignore[arg-type]
        stage=engagement.stage,  # type: ignore[arg-type]
        deployment=engagement.deployment,  # type: ignore[arg-type]
        untrusted_inputs=engagement.untrusted_inputs_json,  # type: ignore[arg-type]
        sensitive_data=engagement.sensitive_data_json,  # type: ignore[arg-type]
        consequential_actions=engagement.consequential_actions_json,  # type: ignore[arg-type]
        workflow=scope.workflow,
        feared_failure=scope.feared_failure,
        identity_controls=scope.identity_controls,
        human_approval=scope.human_approval,
        limitations=assurance.optional_text(
            cipher, engagement.limitations_enc, assurance.LIMITATIONS
        ),
        quoted_price_cents=engagement.quoted_price_cents,
        tested_config_hash=engagement.tested_config_hash,
        tested_config_version=engagement.tested_config_version,
        readiness=AssuranceReadiness(**ready.__dict__, ready=ready.ready),
        requested_at=engagement.requested_at,
        started_at=engagement.started_at,
        completed_at=engagement.completed_at,
        created_at=engagement.created_at,
        updated_at=engagement.updated_at,
    )


@sponsor.get("/bounties/{bounty_id}/assurance")
async def read_assurance(
    session: Transaction, bounty: OwnBounty, cipher: CipherDep
) -> AssuranceStateOut:
    """Read the private assessment scope and its delivery status."""
    engagement = await assurance.for_bounty(session, bounty.id)
    return AssuranceStateOut(
        engagement=await _out(session, cipher, bounty, engagement)
        if engagement is not None
        else None
    )


@sponsor.put("/bounties/{bounty_id}/assurance")
async def save_assurance(
    body: AssuranceIn,
    session: Transaction,
    bounty: OwnBounty,
    cipher: CipherDep,
) -> AssuranceOut:
    """Save the threat model and delivery requirements; all free text is encrypted."""
    engagement = await assurance.save(
        session,
        cipher,
        bounty,
        service=body.service,
        stage=body.stage,
        deployment=body.deployment,
        untrusted_inputs=list(body.untrusted_inputs),
        sensitive_data=list(body.sensitive_data),
        consequential_actions=list(body.consequential_actions),
        workflow=body.workflow,
        feared_failure=body.feared_failure,
        identity_controls=body.identity_controls,
        human_approval=body.human_approval,
    )
    await session.refresh(engagement)
    return await _out(session, cipher, bounty, engagement)


@sponsor.post("/bounties/{bounty_id}/assurance/request")
async def request_assurance(
    session: Transaction, bounty: OwnBounty, cipher: CipherDep
) -> AssuranceOut:
    """Hand a saved scope to the managed-service delivery queue."""
    engagement = await assurance.submit(session, cipher, bounty)
    await session.refresh(engagement)
    return await _out(session, cipher, bounty, engagement)


async def _admin_out(
    session: Transaction,
    cipher: Cipher,
    engagement: AssuranceEngagement,
    bounty: Bounty,
    account: Sponsor,
) -> AdminAssuranceOut:
    shared = await _out(session, cipher, bounty, engagement)
    return AdminAssuranceOut(
        **shared.model_dump(),
        sponsor_email=account.email,
        bounty_title=bounty.title,
        operator_notes=assurance.optional_text(
            cipher, engagement.operator_notes_enc, assurance.OPERATOR_NOTES
        ),
    )


@admin.get("")
async def assessments(
    session: Transaction,
    cipher: CipherDep,
    _: AdminDep,
    status: Annotated[str | None, Query(max_length=30)] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AdminAssurancePage:
    matching: list[Any] = []
    if status:
        matching.append(AssuranceEngagement.status == status)
    total = await session.scalar(
        select(func.count()).select_from(AssuranceEngagement).where(*matching)
    )
    rows = await session.execute(
        select(AssuranceEngagement, Bounty, Sponsor)
        .join(Bounty, Bounty.id == AssuranceEngagement.bounty_id)
        .join(Sponsor, Sponsor.id == Bounty.sponsor_id)
        .where(*matching)
        .order_by(AssuranceEngagement.created_at.desc(), AssuranceEngagement.id)
        .limit(limit)
        .offset(offset)
    )
    return AdminAssurancePage(
        items=[
            await _admin_out(session, cipher, engagement, bounty, account)
            for engagement, bounty, account in rows
        ],
        total=int(total or 0),
    )


@admin.patch("/{engagement_id}")
async def update_assessment(
    engagement_id: uuid.UUID,
    body: AdminAssuranceUpdate,
    session: Transaction,
    cipher: CipherDep,
    operator: AdminDep,
) -> AdminAssuranceOut:
    row = await session.execute(
        select(AssuranceEngagement, Bounty, Sponsor)
        .join(Bounty, Bounty.id == AssuranceEngagement.bounty_id)
        .join(Sponsor, Sponsor.id == Bounty.sponsor_id)
        .where(AssuranceEngagement.id == engagement_id)
        .with_for_update()
    )
    found = row.one_or_none()
    if found is None:
        raise HTTPException(404, "assessment not found")
    engagement, bounty, account = found
    if body.status in {"testing", "retest"} and body.status != engagement.status:
        config = await assurance.require_current_config(session, bounty)
        engagement.tested_config_hash = config.config_hash
        engagement.tested_config_version = config.version
    assurance.apply_admin_update(
        cipher,
        engagement,
        status=body.status,
        quoted_price_cents=body.quoted_price_cents,
        operator_notes=body.operator_notes,
        limitations=body.limitations,
    )
    record(
        session,
        operator,
        "assurance_updated",
        account,
        f"{bounty.title}: {engagement.status}",
    )
    await session.flush()
    return await _admin_out(session, cipher, engagement, bounty, account)
