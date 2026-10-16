"""The private design-partner funnel and its measured product outcomes."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update

from agentbounty.models import (
    AdminAction,
    Bounty,
    BountyState,
    Deposit,
    DepositState,
    PilotApplication,
    RearmCheck,
    RearmState,
    Scan,
    Sponsor,
)

from .platform import Platform


def application(email: str = "Founder@Example.com") -> dict[str, object]:
    return {
        "name": "Ada Founder",
        "work_email": email,
        "company": "Example Agents",
        "company_website": "https://example.com",
        "role": "Founder",
        "stage": "beta",
        "use_case": "A support agent reads private customer data and can approve account refunds.",
        "goals": ["baseline", "continuous"],
        "source": "yc-outreach",
        "fax": "",
    }


async def make_admin(platform: Platform, email: str = "operator@example.com") -> dict[str, str]:
    operator = await platform.sponsor(email)
    async with platform.sessions.begin() as session:
        await session.execute(update(Sponsor).where(Sponsor.email == email).values(is_admin=True))
    return operator.headers


async def test_application_is_private_normalized_and_refreshes_without_losing_progress(
    platform: Platform,
) -> None:
    first = await platform.client.post("/v1/pilot/applications", json=application())
    assert first.status_code == 201
    assert first.json()["message"].startswith("Application received")

    async with platform.sessions.begin() as session:
        saved = await session.scalar(select(PilotApplication))
        assert saved is not None
        assert saved.work_email == "founder@example.com"
        saved.status = "qualified"
        saved.notes = "Call booked for Tuesday"

    changed = application("founder@example.com")
    changed.update({"name": "Ada Lovelace", "stage": "production", "goals": ["audit"]})
    second = await platform.client.post("/v1/pilot/applications", json=changed)
    assert second.status_code == 201
    assert second.json()["id"] == first.json()["id"]

    async with platform.sessions() as session:
        applications = list(await session.scalars(select(PilotApplication)))
    assert len(applications) == 1
    assert applications[0].name == "Ada Lovelace"
    assert applications[0].goals_json == ["audit"]
    assert applications[0].status == "qualified"
    assert applications[0].notes == "Call booked for Tuesday"

    assert (await platform.client.get("/v1/admin/pilots")).status_code == 401


async def test_honeypot_is_accepted_without_storing_a_lead(platform: Platform) -> None:
    bot = application("bot@example.com")
    bot["fax"] = "+1 555 0100"
    response = await platform.client.post("/v1/pilot/applications", json=bot)
    assert response.status_code == 201
    async with platform.sessions() as session:
        assert await session.scalar(select(PilotApplication)) is None


async def test_application_validation_rejects_non_http_sites_and_vague_use_cases(
    platform: Platform,
) -> None:
    invalid = application()
    invalid.update({"company_website": "example.com", "use_case": "AI bot"})
    response = await platform.client.post("/v1/pilot/applications", json=invalid)
    assert response.status_code == 422
    assert {item["loc"][-1] for item in response.json()["detail"]} == {
        "company_website",
        "use_case",
    }


async def test_admin_funnel_uses_real_account_scan_launch_and_payment_activity(
    platform: Platform,
) -> None:
    await platform.client.post("/v1/pilot/applications", json=application())
    admin = await make_admin(platform)
    sponsor = await platform.sponsor("founder@example.com")
    bounty_id = await sponsor.draft()

    async with platform.sessions.begin() as session:
        account = await session.scalar(
            select(Sponsor).where(Sponsor.email == "founder@example.com")
        )
        assert account is not None
        await session.execute(
            update(Bounty).where(Bounty.id == uuid.UUID(bounty_id)).values(state=BountyState.PAUSED)
        )
        session.add(
            Scan(
                bounty_id=uuid.UUID(bounty_id),
                config_hash="baseline-config",
                state="done",
                finished_at=datetime.now(UTC),
            )
        )
        session.add(
            RearmCheck(
                bounty_id=uuid.UUID(bounty_id),
                config_hash="regressed-config",
                state=RearmState.FAILED,
                tripped_runs=3,
            )
        )
        lead = await session.scalar(select(PilotApplication))
        assert lead is not None
        lead.status = "pilot"
        session.add(
            Deposit(
                txid="pilot-plan-payment",
                amount_zatoshi=1,
                account_id=account.id,
                state=DepositState.CREDITED,
            )
        )

    response = await platform.client.get("/v1/admin/pilots", headers=admin)
    assert response.status_code == 200
    page = response.json()
    assert page["funnel"] == {
        "applications": 1,
        "signed_up": 1,
        "listed_agent": 1,
        "ran_scan": 1,
        "launched_bounty": 1,
        "paid": 1,
        "status_counts": {"pilot": 1},
    }
    assert page["traction"] == {
        "tested_agents_7d": 1,
        "regressions_caught": 1,
        "median_patch_hours": None,
        "active_pilots": 1,
        "paid_conversion_percent": 100.0,
    }
    lead = page["items"][0]
    assert lead["account_id"] == str(account.id)
    assert (lead["bounties"], lead["launched_bounties"], lead["scans_completed"]) == (1, 1, 1)
    assert lead["paid_plan"] is True

    updated = await platform.client.patch(
        f"/v1/admin/pilots/{lead['id']}",
        headers=admin,
        json={"status": "pilot", "notes": "Weekly review on Friday"},
    )
    assert updated.status_code == 200
    assert (updated.json()["status"], updated.json()["notes"]) == (
        "pilot",
        "Weekly review on Friday",
    )
    async with platform.sessions() as session:
        action = await session.scalar(
            select(AdminAction).where(AdminAction.action == "pilot_updated")
        )
    assert action is not None
    assert action.detail == "Example Agents: pilot"
