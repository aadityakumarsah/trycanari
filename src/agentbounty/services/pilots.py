"""The design-partner application funnel and its real product outcomes."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agentbounty.models import (
    Attempt,
    AttemptState,
    Bounty,
    BountyState,
    Deposit,
    DepositState,
    Finding,
    PilotApplication,
    RearmCheck,
    RearmState,
    Scan,
    Sponsor,
)


async def apply(
    session: AsyncSession,
    *,
    name: str,
    work_email: str,
    company: str,
    company_website: str,
    role: str,
    stage: str,
    use_case: str,
    goals: Sequence[str],
    source: str,
) -> PilotApplication:
    """Create or refresh one company's application without resetting operator progress."""
    application = await session.scalar(
        select(PilotApplication).where(PilotApplication.work_email == work_email).with_for_update()
    )
    values = {
        "name": name,
        "company": company,
        "company_website": company_website or None,
        "role": role or None,
        "stage": stage,
        "use_case": use_case,
        "goals_json": list(dict.fromkeys(goals)),
        "source": source.strip() or "direct",
        "updated_at": datetime.now(UTC),
    }
    if application is None:
        application = PilotApplication(work_email=work_email, **values)
        session.add(application)
    else:
        for name, value in values.items():
            setattr(application, name, value)
    await session.flush()
    return application


@dataclass(frozen=True)
class Outcomes:
    account_id: uuid.UUID | None = None
    bounties: int = 0
    launched_bounties: int = 0
    scans_completed: int = 0
    verified_breaks: int = 0
    paid_plan: bool = False


@dataclass(frozen=True)
class Traction:
    tested_agents_7d: int = 0
    regressions_caught: int = 0
    median_patch_hours: float | None = None


async def outcomes(
    session: AsyncSession, applications: list[PilotApplication]
) -> dict[str, Outcomes]:
    """Tie leads to measured product activity by their normalized account email."""
    emails = [application.work_email for application in applications]
    if not emails:
        return {}
    accounts = list(await session.scalars(select(Sponsor).where(Sponsor.email.in_(emails))))
    by_email = {account.email: account for account in accounts}
    account_ids = [account.id for account in accounts]
    if not account_ids:
        return {email: Outcomes() for email in emails}

    bounty_rows = await session.execute(
        select(
            Bounty.sponsor_id,
            func.count(Bounty.id),
            func.count(Bounty.id).filter(
                Bounty.state.not_in((BountyState.DRAFT, BountyState.AWAITING_DEPOSIT))
            ),
        )
        .where(Bounty.sponsor_id.in_(account_ids), Bounty.deleted_at.is_(None))
        .group_by(Bounty.sponsor_id)
    )
    bounty_stats = {row[0]: (int(row[1]), int(row[2])) for row in bounty_rows}
    scan_rows = await session.execute(
        select(Bounty.sponsor_id, func.count(Scan.id))
        .join(Scan, Scan.bounty_id == Bounty.id)
        .where(Bounty.sponsor_id.in_(account_ids), Scan.state == "done")
        .group_by(Bounty.sponsor_id)
    )
    scan_stats = {row[0]: int(row[1]) for row in scan_rows}
    finding_rows = await session.execute(
        select(Bounty.sponsor_id, func.count(Finding.id))
        .join(Finding, Finding.bounty_id == Bounty.id)
        .where(Bounty.sponsor_id.in_(account_ids))
        .group_by(Bounty.sponsor_id)
    )
    finding_stats = {row[0]: int(row[1]) for row in finding_rows}
    paid_accounts = set(
        await session.scalars(
            select(Deposit.account_id).where(
                Deposit.account_id.in_(account_ids), Deposit.state == DepositState.CREDITED
            )
        )
    )

    measured: dict[str, Outcomes] = {}
    for email in emails:
        account = by_email.get(email)
        if account is None:
            measured[email] = Outcomes()
            continue
        bounty_count, launched_count = bounty_stats.get(account.id, (0, 0))
        measured[email] = Outcomes(
            account_id=account.id,
            bounties=bounty_count,
            launched_bounties=launched_count,
            scans_completed=scan_stats.get(account.id, 0),
            verified_breaks=finding_stats.get(account.id, 0),
            paid_plan=account.id in paid_accounts,
        )
    return measured


async def traction(session: AsyncSession, applications: list[PilotApplication]) -> Traction:
    """Measure weekly use, regressions and patch speed for applicant accounts only."""
    emails = [application.work_email for application in applications]
    if not emails:
        return Traction()
    account_ids = list(await session.scalars(select(Sponsor.id).where(Sponsor.email.in_(emails))))
    if not account_ids:
        return Traction()
    since = datetime.now(UTC) - timedelta(days=7)
    scan_bounties = set(
        await session.scalars(
            select(Bounty.id)
            .join(Scan, Scan.bounty_id == Bounty.id)
            .where(
                Bounty.sponsor_id.in_(account_ids),
                Scan.state == "done",
                Scan.finished_at >= since,
            )
        )
    )
    attempt_bounties = set(
        await session.scalars(
            select(Bounty.id)
            .join(Attempt, Attempt.bounty_id == Bounty.id)
            .where(
                Bounty.sponsor_id.in_(account_ids),
                Attempt.state == AttemptState.DONE,
                Attempt.submitted_at >= since,
            )
        )
    )
    regressions = int(
        await session.scalar(
            select(func.count(RearmCheck.id))
            .join(Bounty, Bounty.id == RearmCheck.bounty_id)
            .where(
                Bounty.sponsor_id.in_(account_ids),
                RearmCheck.state == RearmState.FAILED,
            )
        )
        or 0
    )
    findings = list(
        await session.execute(
            select(Finding.bounty_id, Finding.verified_at)
            .join(Bounty, Bounty.id == Finding.bounty_id)
            .where(Bounty.sponsor_id.in_(account_ids))
        )
    )
    passes = list(
        await session.execute(
            select(RearmCheck.bounty_id, RearmCheck.created_at)
            .join(Bounty, Bounty.id == RearmCheck.bounty_id)
            .where(
                Bounty.sponsor_id.in_(account_ids),
                RearmCheck.state == RearmState.PASSED,
            )
        )
    )
    patch_hours = [
        (min(after) - verified_at).total_seconds() / 3600
        for bounty_id, verified_at in findings
        if (
            after := [
                created_at
                for candidate_id, created_at in passes
                if candidate_id == bounty_id and created_at >= verified_at
            ]
        )
    ]
    return Traction(
        tested_agents_7d=len(scan_bounties | attempt_bounties),
        regressions_caught=regressions,
        median_patch_hours=round(float(median(patch_hours)), 1) if patch_hours else None,
    )
