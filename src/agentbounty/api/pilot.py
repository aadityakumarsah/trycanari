"""Public design-partner applications and the private operator funnel."""

from __future__ import annotations

import secrets
import uuid
from datetime import UTC, datetime
from typing import Annotated, cast

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select

from agentbounty.api.admin import Limit, Offset, record
from agentbounty.api.deps import AdminDep, Transaction
from agentbounty.api.ratelimit import limit_by_client
from agentbounty.api.schemas import (
    AdminPilotOut,
    AdminPilotsPage,
    AdminPilotUpdate,
    PilotApplicationAccepted,
    PilotApplicationIn,
    PilotFunnelOut,
    PilotGoal,
    PilotStage,
    PilotStatus,
    PilotTractionOut,
)
from agentbounty.models import PilotApplication
from agentbounty.services import pilots

public = APIRouter(tags=["pilot"])
admin = APIRouter(
    prefix="/v1/admin/pilots",
    tags=["admin"],
    include_in_schema=False,
    dependencies=[Depends(limit_by_client)],
)


@public.post(
    "/v1/pilot/applications",
    status_code=201,
    dependencies=[Depends(limit_by_client)],
)
async def apply(body: PilotApplicationIn, session: Transaction) -> PilotApplicationAccepted:
    """Ask to join the design-partner pilot. Applications and notes are never public."""
    if body.fax:
        return PilotApplicationAccepted(
            id=uuid.UUID(bytes=secrets.token_bytes(16), version=4),
            message="Application received. We will reply by email if the pilot is a fit.",
        )
    application = await pilots.apply(
        session,
        name=body.name,
        work_email=body.work_email,
        company=body.company,
        company_website=body.company_website,
        role=body.role,
        stage=body.stage,
        use_case=body.use_case,
        goals=body.goals,
        source=body.source,
    )
    return PilotApplicationAccepted(
        id=application.id,
        message="Application received. We will reply by email if the pilot is a fit.",
    )


def _out(application: PilotApplication, outcome: pilots.Outcomes) -> AdminPilotOut:
    return AdminPilotOut(
        id=application.id,
        name=application.name,
        work_email=application.work_email,
        company=application.company,
        company_website=application.company_website,
        role=application.role,
        stage=cast(PilotStage, application.stage),
        use_case=application.use_case,
        goals=cast(list[PilotGoal], application.goals_json),
        source=application.source,
        status=cast(PilotStatus, application.status),
        notes=application.notes,
        created_at=application.created_at,
        updated_at=application.updated_at,
        account_id=outcome.account_id,
        bounties=outcome.bounties,
        launched_bounties=outcome.launched_bounties,
        scans_completed=outcome.scans_completed,
        verified_breaks=outcome.verified_breaks,
        paid_plan=outcome.paid_plan,
    )


@admin.get("")
async def listing(
    session: Transaction,
    _: AdminDep,
    status: Annotated[PilotStatus | None, Query()] = None,
    limit: Limit = 50,
    offset: Offset = 0,
) -> AdminPilotsPage:
    """Applications plus measured movement from signup through paid plan."""
    where = () if status is None else (PilotApplication.status == status,)
    total = int(
        await session.scalar(select(func.count()).select_from(PilotApplication).where(*where)) or 0
    )
    applications = list(
        await session.scalars(
            select(PilotApplication)
            .where(*where)
            .order_by(PilotApplication.created_at.desc(), PilotApplication.id)
            .limit(limit)
            .offset(offset)
        )
    )
    all_applications = list(await session.scalars(select(PilotApplication)))
    all_outcomes = await pilots.outcomes(session, all_applications)
    page_outcomes = {
        email: all_outcomes[email] for email in {item.work_email for item in applications}
    }
    status_counts: dict[str, int] = {}
    for application in all_applications:
        status_counts[application.status] = status_counts.get(application.status, 0) + 1
    measured = list(all_outcomes.values())
    measured_traction = await pilots.traction(session, all_applications)
    active_pilots = sum(application.status in {"pilot", "paid"} for application in all_applications)
    paid = sum(item.paid_plan for item in measured)
    return AdminPilotsPage(
        items=[_out(item, page_outcomes[item.work_email]) for item in applications],
        total=total,
        funnel=PilotFunnelOut(
            applications=len(all_applications),
            signed_up=sum(item.account_id is not None for item in measured),
            listed_agent=sum(item.bounties > 0 for item in measured),
            ran_scan=sum(item.scans_completed > 0 for item in measured),
            launched_bounty=sum(item.launched_bounties > 0 for item in measured),
            paid=paid,
            status_counts=status_counts,
        ),
        traction=PilotTractionOut(
            tested_agents_7d=measured_traction.tested_agents_7d,
            regressions_caught=measured_traction.regressions_caught,
            median_patch_hours=measured_traction.median_patch_hours,
            active_pilots=active_pilots,
            paid_conversion_percent=round(paid / active_pilots * 100, 1) if active_pilots else 0,
        ),
    )


@admin.patch("/{application_id}")
async def update(
    application_id: uuid.UUID,
    body: AdminPilotUpdate,
    session: Transaction,
    operator: AdminDep,
) -> AdminPilotOut:
    application = await session.scalar(
        select(PilotApplication).where(PilotApplication.id == application_id).with_for_update()
    )
    if application is None:
        raise HTTPException(404, "no such pilot application")
    if body.status is not None:
        application.status = body.status
    if body.notes is not None:
        application.notes = body.notes.strip()
    application.updated_at = datetime.now(UTC)
    record(session, operator, "pilot_updated", None, f"{application.company}: {application.status}")
    await session.flush()
    outcome = (await pilots.outcomes(session, [application]))[application.work_email]
    return _out(application, outcome)
