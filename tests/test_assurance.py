"""Managed assurance scope, privacy, lifecycle, and operator delivery controls."""

from __future__ import annotations

from sqlalchemy import select, update

from agentbounty.models import AdminAction, AssuranceEngagement, AssuranceInvoice
from agentbounty.models import Sponsor as SponsorRow
from agentbounty.services import billing

from .platform import PATCHED_CONFIG, Platform, Sponsor


def scope() -> dict[str, object]:
    return {
        "service": "continuous",
        "stage": "production",
        "deployment": "private_cloud",
        "untrusted_inputs": ["user_message", "document", "document"],
        "sensitive_data": ["financial", "credentials"],
        "consequential_actions": ["read", "move_money"],
        "workflow": (
            "The treasury agent reads supplier invoices and proposes transfers from the "
            "company operating account."
        ),
        "feared_failure": (
            "An untrusted invoice makes the agent disclose a signing reference or send "
            "money to an unapproved recipient."
        ),
        "identity_controls": (
            "The agent uses a dedicated workload identity with a transfer-only role."
        ),
        "human_approval": (
            "A treasury operator approves every transfer before the payment tool executes."
        ),
    }


async def make_admin(platform: Platform) -> dict[str, str]:
    operator = await platform.sponsor("assurance-operator@example.com")
    async with platform.sessions.begin() as session:
        await session.execute(
            update(SponsorRow)
            .where(SponsorRow.email == "assurance-operator@example.com")
            .values(is_admin=True)
        )
    return operator.headers


async def test_sponsor_saves_encrypted_scope_and_requests_delivery(platform: Platform) -> None:
    sponsor = await platform.sponsor("assurance-customer@example.com")
    bounty_id = await sponsor.draft()

    empty = await sponsor.call("GET", f"/v1/bounties/{bounty_id}/assurance")
    assert empty == {"engagement": None}

    saved = await sponsor.call("PUT", f"/v1/bounties/{bounty_id}/assurance", json=scope())
    assert saved["status"] == "draft"
    assert saved["untrusted_inputs"] == ["user_message", "document"]
    assert saved["readiness"] == {
        "agent_configured": True,
        "boundaries_defined": True,
        "attack_surface_declared": True,
        "consequence_documented": True,
        "identity_documented": True,
        "approval_documented": True,
        "ready": True,
    }

    requested = await sponsor.call("POST", f"/v1/bounties/{bounty_id}/assurance/request")
    assert requested["status"] == "requested"
    assert requested["requested_at"] is not None

    async with platform.sessions() as session:
        stored = await session.scalar(select(AssuranceEngagement))
        assert stored is not None
        plaintext = str(scope()["workflow"]).encode()
        assert plaintext not in stored.workflow_enc
        assert stored.workflow_enc != plaintext


async def test_scope_validation_and_bounty_ownership(platform: Platform) -> None:
    sponsor = await platform.sponsor("scope-owner@example.com")
    bounty_id = await sponsor.draft()
    invalid = scope()
    invalid.update(
        {
            "untrusted_inputs": [],
            "consequential_actions": [],
            "workflow": "too short",
            "identity_controls": "none",
        }
    )
    await sponsor.call("PUT", f"/v1/bounties/{bounty_id}/assurance", 422, json=invalid)

    stranger = await platform.sponsor("scope-stranger@example.com")
    await stranger.call("GET", f"/v1/bounties/{bounty_id}/assurance", 404)


async def test_operator_controls_lifecycle_without_exposing_private_notes(
    platform: Platform,
) -> None:
    sponsor = await platform.sponsor("managed-customer@example.com")
    bounty_id = await sponsor.draft()
    engagement = await sponsor.call("PUT", f"/v1/bounties/{bounty_id}/assurance", json=scope())
    await sponsor.call("POST", f"/v1/bounties/{bounty_id}/assurance/request")
    admin = await make_admin(platform)

    queue = await platform.client.get("/v1/admin/assessments", headers=admin)
    assert queue.status_code == 200
    assert queue.json()["total"] == 1
    assert queue.json()["items"][0]["sponsor_email"] == "managed-customer@example.com"

    scoping = await platform.client.patch(
        f"/v1/admin/assessments/{engagement['id']}",
        headers=admin,
        json={"status": "scoping"},
    )
    assert scoping.status_code == 200
    await sponsor.call("POST", f"/v1/bounties/{bounty_id}/assurance/request", 409)

    changed = await platform.client.patch(
        f"/v1/admin/assessments/{engagement['id']}",
        headers=admin,
        json={
            "status": "testing",
            "quoted_price_cents": 250_000,
            "operator_notes": "Reproduce against the private staging runner on Tuesday.",
            "limitations": "Email delivery is mocked; production payment execution is disabled.",
        },
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["status"] == "testing"
    assert body["started_at"] is not None
    assert body["tested_config_hash"] is not None
    assert body["tested_config_version"] == 1
    assert body["operator_notes"].startswith("Reproduce")

    customer = await sponsor.call("GET", f"/v1/bounties/{bounty_id}/assurance")
    visible = customer["engagement"]
    assert visible["quoted_price_cents"] == 250_000
    assert visible["limitations"].startswith("Email delivery")
    assert "operator_notes" not in visible
    await sponsor.call("PUT", f"/v1/bounties/{bounty_id}/assurance", 409, json=scope())

    # Saving delivery notes does not silently move the evidence to a newer config.
    tested_hash = body["tested_config_hash"]
    await sponsor.call("PUT", f"/v1/bounties/{bounty_id}/config", json=PATCHED_CONFIG)
    notes_only = await platform.client.patch(
        f"/v1/admin/assessments/{engagement['id']}",
        headers=admin,
        json={"status": "testing", "operator_notes": "Testing is still in progress."},
    )
    assert notes_only.status_code == 200
    assert notes_only.json()["tested_config_hash"] == tested_hash

    completed = await platform.client.patch(
        f"/v1/admin/assessments/{engagement['id']}",
        headers=admin,
        json={"status": "complete"},
    )
    assert completed.status_code == 200
    assert completed.json()["completed_at"] is not None
    reopened = await platform.client.patch(
        f"/v1/admin/assessments/{engagement['id']}",
        headers=admin,
        json={"status": "testing"},
    )
    assert reopened.status_code == 409
    async with platform.sessions() as session:
        action = await session.scalar(
            select(AdminAction).where(AdminAction.action == "assurance_updated")
        )
        assert action is not None


async def test_request_requires_a_saved_scope(platform: Platform) -> None:
    sponsor = await platform.sponsor("no-scope@example.com")
    bounty_id = await sponsor.draft()
    await sponsor.call("POST", f"/v1/bounties/{bounty_id}/assurance/request", 409)


# --- fiat invoices for a managed assessment ----------------------------------------------


async def quoted(
    platform: Platform, cents: int = 250_000
) -> tuple[Sponsor, str, str, dict[str, str]]:
    """A requested assessment an operator has put a price on."""
    sponsor = await platform.sponsor("billed-customer@example.com")
    bounty_id = await sponsor.draft()
    engagement = await sponsor.call("PUT", f"/v1/bounties/{bounty_id}/assurance", json=scope())
    await sponsor.call("POST", f"/v1/bounties/{bounty_id}/assurance/request")
    admin = await make_admin(platform)
    priced = await platform.client.patch(
        f"/v1/admin/assessments/{engagement['id']}",
        headers=admin,
        json={"quoted_price_cents": cents},
    )
    assert priced.status_code == 200, priced.text
    return sponsor, bounty_id, engagement["id"], admin


async def test_an_assessment_is_invoiced_once_and_the_sponsor_sees_where_to_pay(
    platform: Platform,
) -> None:
    fake = billing.FakeBilling()
    platform.app.state.biller = fake
    sponsor, bounty_id, engagement_id, admin = await quoted(platform)

    # Nothing is billed until an operator says so.
    before = await sponsor.call("GET", f"/v1/bounties/{bounty_id}/assurance")
    assert before["engagement"]["invoice"] is None

    made = await platform.client.post(
        f"/v1/admin/assessments/{engagement_id}/invoice", headers=admin
    )
    assert made.status_code == 201, made.text
    assert (made.json()["invoice"]["amount_cents"], made.json()["invoice"]["state"]) == (
        250_000,
        "open",
    )
    # The sponsor was billed at their own address, for the quoted price.
    (sent,) = fake.issued
    assert sent.amount_cents == 250_000

    seen = (await sponsor.call("GET", f"/v1/bounties/{bounty_id}/assurance"))["engagement"]
    assert seen["invoice"]["state"] == "open"
    assert seen["invoice"]["hosted_url"]
    assert seen["invoice"]["paid_at"] is None

    # Billing twice would charge twice.
    again = await platform.client.post(
        f"/v1/admin/assessments/{engagement_id}/invoice", headers=admin
    )
    assert again.status_code == 409
    assert len(fake.issued) == 1


async def test_an_assessment_with_no_price_is_not_invoiced(platform: Platform) -> None:
    fake = billing.FakeBilling()
    platform.app.state.biller = fake
    _, _, engagement_id, admin = await quoted(platform, cents=0)
    refused = await platform.client.post(
        f"/v1/admin/assessments/{engagement_id}/invoice", headers=admin
    )
    # Billing a zero is a mistake, not a free assessment.
    assert refused.status_code == 409
    assert fake.issued == []


async def test_only_an_operator_can_invoice_an_assessment(platform: Platform) -> None:
    platform.app.state.biller = billing.FakeBilling()
    sponsor, _, engagement_id, _ = await quoted(platform)
    # The admin area answers 404 to anyone else: nobody invoices themselves.
    mine = await platform.client.post(
        f"/v1/admin/assessments/{engagement_id}/invoice", headers=sponsor.headers
    )
    assert mine.status_code == 404
    assert (
        await platform.client.post(f"/v1/admin/assessments/{engagement_id}/invoice")
    ).status_code in (401, 404)


async def test_a_provider_that_fails_leaves_no_invoice_behind(platform: Platform) -> None:
    fake = billing.FakeBilling(fail_with=billing.BillingError("Stripe is down", permanent=False))
    platform.app.state.biller = fake
    _, _, engagement_id, admin = await quoted(platform)
    failed = await platform.client.post(
        f"/v1/admin/assessments/{engagement_id}/invoice", headers=admin
    )
    # 502, not 400: it may work on the next try.
    assert failed.status_code == 502
    async with platform.sessions.begin() as session:
        assert (await session.scalars(select(AssuranceInvoice))).all() == []
