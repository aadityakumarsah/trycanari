"""First-five-minutes sponsor onboarding and its free pre-funding scan."""

from __future__ import annotations

from .platform import DEMO_CONFIG, Platform, Sponsor


async def test_guided_listing_queues_a_free_baseline_before_funding(platform: Platform) -> None:
    sponsor: Sponsor = await platform.sponsor("guided@example.com")
    bounty_id = await sponsor.draft(DEMO_CONFIG)
    # The guided flow starts this fixed first scan before any escrow is requested.
    await sponsor.call("POST", f"/v1/bounties/{bounty_id}/scan", 202)
    await platform.work()
    scan = await sponsor.call("GET", f"/v1/bounties/{bounty_id}/scan")
    assert scan["state"] == "done"
    assert scan["total"] > 0
    detail = await sponsor.call("GET", f"/v1/sponsor/bounties/{bounty_id}")
    assert detail["state"] == "draft"
    assert detail["balance_zatoshi"] == 0


async def test_template_makes_a_listable_draft_and_free_report(platform: Platform) -> None:
    sponsor = await platform.sponsor("template@example.com")
    templates = await sponsor.call("GET", "/v1/onboarding/templates")
    assert {template["key"] for template in templates} == {
        "support_refunds",
        "rag_documents",
        "email_assistant",
    }
    made = await sponsor.call(
        "POST",
        "/v1/bounties/templates/support_refunds",
        201,
        json={"title": "Refund helper", "description": "", "reward_zatoshi": 1_000_000},
    )
    bounty_id = made["id"]
    await platform.work()
    report = await sponsor.call("GET", f"/v1/bounties/{bounty_id}/scan")
    assert report["state"] == "done"
    assert report["total"] > 0
    # Funding makes the template pass the ordinary listing check and go live.
    funding = await sponsor.fund(bounty_id)
    platform.wallet.simulate_deposit(
        funding["amount_due_zatoshi"], funding["deposit_code"], confirmations=3
    )
    await platform.payments.tick()
    listed = await sponsor.call("GET", f"/v1/sponsor/bounties/{bounty_id}")
    assert listed["state"] == "live"
