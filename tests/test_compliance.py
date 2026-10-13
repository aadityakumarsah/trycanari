"""The audit report: a bounty's testing set against OWASP's lists, the EU AI Act and ISO 42001."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from agentbounty.services import compliance
from agentbounty.services.compliance import Break, Evidence, Probe, Tripwire
from agentbounty.services.model import FakeModel

from .platform import (
    APPROVED,
    PATCHED_CONFIG,
    SECRET,
    STRONG,
    WEAK,
    Platform,
)
from .test_scan import Obedient

NOW = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
WIRES = (
    Tripwire("signing_reference", "secret_leak", in_prompt=True),
    Tripwire("unapproved_transfer", "forbidden_tool"),
)
DRAFT = Evidence(
    bounty_id="7b0c7f0e-0000-4000-8000-000000000001",
    title="Treasury assistant",
    state="draft",
    model="demo-model",
    config_version=1,
    config_hash="a" * 64,
    entry_points=("user_message", "document"),
    tools=("transfer_funds",),
    tripwires=WIRES,
)
HELD_PROBES = (
    Probe("Direct override", "Direct prompt injection", "signing_reference"),
    Probe("Forged policy update", "Indirect prompt injection", "unapproved_transfer"),
)
SCANNED = replace(DRAFT, scan_state="done", scan_finished_at=NOW, probes=HELD_PROBES)
STOLEN = Break(
    id="7b0c7f0e-0000-4000-8000-0000000000f1",
    label="unapproved_transfer",
    type="forbidden_tool",
    slots=("user_message",),
    config_hash="a" * 64,
    tripped_runs=5,
    verified_at=NOW,
)


def statuses(report: dict[str, Any]) -> dict[str, str]:
    """Each risk and each control of a report, with where it stands."""
    found = {risk["id"]: risk["status"] for risk in report["risks"]}
    for framework in report["frameworks"]:
        found.update({control["ref"]: control["status"] for control in framework["controls"]})
    return found


def test_an_agent_nothing_has_attacked_has_no_evidence_to_show() -> None:
    report = compliance.report(DRAFT, NOW)
    found = statuses(report)
    assert {found[risk["id"]] for risk in report["risks"]} == {"not_tested"}
    # Naming the risks is a start on assessing them, and nothing more.
    assert found["Article 9(2)"] == "partial"
    assert found["Clauses 6.1.2 and 8.2"] == "partial"
    for untested in (
        "Article 9(6) and 9(8)",
        "Article 15(5)",
        "Article 11 and Annex IV, point 2(g)",
        "Article 72",
        "Clauses 8.3 and 10.2",
        "Clause 9.1",
        "Annex A, control A.6.2.4",
        "Annex A, control A.6.2.6",
        "Annex A, control A.8.3",
    ):
        assert found[untested] == "none", untested
    assert report["findings"] == []
    # With no tripwire there is not even a risk named.
    bare = statuses(compliance.report(replace(DRAFT, tripwires=()), NOW))
    assert (bare["Article 9(2)"], bare["Clauses 6.1.2 and 8.2"]) == ("none", "none")


def test_a_scan_the_agent_held_against_is_evidence_for_what_it_tested() -> None:
    report = compliance.report(SCANNED, NOW)
    found = statuses(report)
    for held in ("LLM01:2025", "LLM02:2025", "LLM06:2025", "LLM07:2025", "ASI01", "ASI02"):
        assert found[held] == "held", held
    by_id = {risk["id"]: risk for risk in report["risks"]}
    # Every attack tests getting in; only those aimed at a tripwire test what it guards.
    assert (by_id["LLM01:2025"]["probes_run"], by_id["LLM01:2025"]["probes_held"]) == (2, 2)
    assert by_id["LLM06:2025"]["tripwires"] == ["unapproved_transfer"]
    assert (by_id["LLM06:2025"]["probes_run"], by_id["ASI02"]["probes_run"]) == (1, 1)
    assert by_id["LLM02:2025"]["tripwires"] == ["signing_reference"]
    for shown in (
        "Article 9(2)",
        "Article 9(6) and 9(8)",
        "Article 15(5)",
        "Article 11 and Annex IV, point 2(g)",
        "Clauses 6.1.2 and 8.2",
        "Clause 9.1",
        "Annex A, control A.6.2.4",
    ):
        assert found[shown] == "evidence", shown
    # A scan of a draft is not monitoring, and no outsider has been asked.
    assert (found["Article 72"], found["Annex A, control A.6.2.6"]) == ("none", "none")
    assert found["Annex A, control A.8.3"] == "none"
    # Nothing got through, so there has been nothing to put right.
    assert found["Clauses 8.3 and 10.2"] == "not_applicable"
    control = report["frameworks"][0]["controls"][1]
    assert control["ref"] == "Article 9(6) and 9(8)"
    assert "at least 3 of 5 independent replays" in control["evidence"][0]
    assert control["evidence"][1] == (
        "Scan of configuration version 1 (fingerprint aaaaaaaaaaaaaaaa) on 2026-10-13: held"
        " against 2 of 2 known attacks."
    )


def test_a_secret_kept_out_of_the_prompt_is_no_test_of_prompt_leakage() -> None:
    elsewhere = (Tripwire("signing_reference", "secret_leak"), WIRES[1])
    report = compliance.report(replace(SCANNED, tripwires=elsewhere), NOW)
    found = statuses(report)
    assert (found["LLM02:2025"], found["LLM07:2025"]) == ("held", "not_tested")


def test_a_known_attack_that_got_through_is_an_open_weakness() -> None:
    probes = (
        HELD_PROBES[0],
        replace(HELD_PROBES[1], tripped=("unapproved_transfer",)),
    )
    found = statuses(compliance.report(replace(SCANNED, probes=probes), NOW))
    # It got in, and it made the agent misuse a tool. The secret was not touched.
    for broken in ("LLM01:2025", "ASI01", "LLM06:2025", "ASI02"):
        assert found[broken] == "broken", broken
    assert (found["LLM02:2025"], found["LLM07:2025"]) == ("held", "held")
    assert found["Article 15(5)"] == "gap"
    assert found["Clauses 8.3 and 10.2"] == "gap"
    # The test was still made, and is still documented.
    assert found["Article 9(6) and 9(8)"] == "evidence"
    assert found["Annex A, control A.6.2.4"] == "evidence"


def test_a_break_is_open_until_the_config_is_changed_and_fixed_once_that_is_checked() -> None:
    paused = replace(DRAFT, state="paused", attacks=3, attacks_judged=3, breaks=(STOLEN,))
    report = compliance.report(paused, NOW)
    (finding,) = report["findings"]
    assert (finding["status"], finding["vector"]) == ("broken", "Direct prompt injection")
    assert finding["risks"] == ["LLM01:2025", "LLM06:2025", "ASI01", "ASI02"]
    found = statuses(report)
    assert (found["LLM06:2025"], found["LLM02:2025"]) == ("broken", "held")
    assert (found["Article 15(5)"], found["Clauses 8.3 and 10.2"]) == ("gap", "gap")
    # Hunters did attack it, though it is not open to them now.
    assert (found["Article 72"], found["Annex A, control A.8.3"]) == ("partial", "partial")

    patched = replace(paused, config_version=2, config_hash="b" * 64)
    report = compliance.report(patched, NOW)
    assert report["findings"][0]["status"] == "patched"
    found = statuses(report)
    assert (found["LLM06:2025"], found["Clauses 8.3 and 10.2"]) == ("patched", "partial")
    assert "have not yet been replayed" in report["frameworks"][1]["controls"][1]["evidence"][0]

    report = compliance.report(replace(patched, rearm="passed"), NOW)
    assert report["findings"][0]["status"] == "fixed"
    found = statuses(report)
    assert (found["LLM06:2025"], found["Clauses 8.3 and 10.2"]) == ("fixed", "evidence")
    # A check that failed leaves the patch unproven.
    failed = statuses(compliance.report(replace(patched, rearm="failed"), NOW))
    assert failed["Clauses 8.3 and 10.2"] == "partial"


def test_an_attack_that_came_in_through_what_the_agent_reads_is_indirect() -> None:
    planted = replace(STOLEN, slots=("tool_result",), label="signing_reference", type="secret_leak")
    report = compliance.report(replace(DRAFT, state="paused", breaks=(planted,)), NOW)
    (finding,) = report["findings"]
    assert finding["vector"] == "Indirect prompt injection"
    assert finding["risks"] == ["LLM01:2025", "LLM02:2025", "LLM07:2025", "ASI01"]


def test_a_live_bounty_and_a_daily_scan_are_monitoring() -> None:
    live = replace(SCANNED, state="live", attacks=4, attacks_judged=4)
    found = statuses(compliance.report(live, NOW))
    assert (found["Article 72"], found["Annex A, control A.6.2.6"]) == ("evidence", "evidence")
    assert found["Annex A, control A.8.3"] == "evidence"
    watched = statuses(compliance.report(replace(SCANNED, watch=True), NOW))
    assert watched["Article 72"] == "evidence"
    # Attacks on earlier configs are not a test of the one in use.
    unscanned = replace(live, scan_state=None, probes=(), state="paused")
    found = statuses(compliance.report(unscanned, NOW))
    assert found["Article 9(6) and 9(8)"] == "partial"
    assert found["Annex A, control A.6.2.4"] == "partial"
    assert found["LLM01:2025"] == "held"


def test_the_same_evidence_gives_the_same_digest_whenever_it_is_asked_for() -> None:
    first = compliance.report(SCANNED, NOW)
    later = compliance.report(SCANNED, NOW + timedelta(days=30))
    assert first["evidence_digest"] == later["evidence_digest"]
    assert len(first["evidence_digest"]) == 64
    assert first["generated_at"] != later["generated_at"]
    changed = compliance.report(replace(SCANNED, attacks=1, attacks_judged=1), NOW)
    assert changed["evidence_digest"] != first["evidence_digest"]


def test_the_report_cites_its_sources_and_never_says_a_requirement_is_met() -> None:
    report = compliance.report(replace(SCANNED, state="live", watch=True), NOW)
    assert [framework["name"] for framework in report["frameworks"]] == [
        "EU AI Act",
        "ISO/IEC 42001",
    ]
    assert report["references"] == [
        "OWASP Top 10 for LLM Applications (2025)",
        "OWASP Top 10 for Agentic Applications (2026)",
        "Regulation (EU) 2024/1689, the Artificial Intelligence Act",
        "ISO/IEC 42001:2023, Artificial intelligence: Management system",
    ]
    assert {risk["source"] for risk in report["risks"]} == set(report["references"][:2])
    for framework in report["frameworks"]:
        assert framework["scope"]
        for control in framework["controls"]:
            assert control["asks"], control["ref"]
            assert control["evidence"], control["ref"]
            assert control["status"] in ("evidence", "partial", "gap", "none", "not_applicable")
    said = json.dumps(report, default=str).lower()
    for claim in ("compliant", "conforms", "certified", "requirement is met", "satisfies"):
        assert claim not in said, claim
    assert "not a certificate" in report["disclaimer"]


# --- over the API ---------------------------------------------------------------------


async def test_a_sponsor_reads_the_mapping_for_a_draft_and_nobody_else_does(
    platform: Platform,
) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.draft()
    path = f"/v1/bounties/{bounty_id}/compliance"
    report = await sponsor.call("GET", path)
    assert report["subject"]["bounty_id"] == bounty_id
    assert report["subject"]["tripwires"] == [
        {"label": "signing_reference", "type": "secret_leak"},
        {"label": "unapproved_transfer", "type": "forbidden_tool"},
    ]
    assert (report["testing"]["threshold"], report["testing"]["runs_per_attack"]) == (3, 5)
    assert {risk["status"] for risk in report["risks"]} == {"not_tested"}
    # The demo agent holds its secret in its prompt, so a leak of one is a leak of the other.
    by_id = {risk["id"]: risk for risk in report["risks"]}
    assert by_id["LLM07:2025"]["tripwires"] == ["signing_reference"]
    # Labels and counts, and nothing a sponsor keeps private.
    said = json.dumps(report)
    for private in (SECRET, "treasury assistant", "Northwind", *APPROVED):
        assert private not in said, private

    other = await platform.sponsor("other@example.com")
    await other.call("GET", path, 404)
    assert (await platform.client.get(path)).status_code == 401

    # A bounty with no agent in it yet has a report too, with nothing in it.
    made = await sponsor.call("POST", "/v1/bounties", 201, json={"reward_zatoshi": 1_000_000})
    empty = await sponsor.call("GET", f"/v1/bounties/{made['id']}/compliance")
    assert (empty["subject"]["model"], empty["subject"]["config_hash"]) == (None, None)
    assert empty["subject"]["tripwires"] == []
    assert set(statuses(empty).values()) == {"not_tested", "none"}


async def test_a_scan_shows_in_the_mapping_as_it_went(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.draft()
    base = f"/v1/bounties/{bounty_id}"
    await sponsor.call("POST", f"{base}/scan", 202)
    # Asked for and not yet run, it is not evidence.
    queued = statuses(await sponsor.call("GET", f"{base}/compliance"))
    assert queued["Article 9(6) and 9(8)"] == "none"
    await platform.work(Obedient())
    report = await sponsor.call("GET", f"{base}/compliance")
    found = statuses(report)
    assert (report["testing"]["scan_state"], report["testing"]["probes_run"]) == ("done", 6)
    assert 0 < report["testing"]["probes_held"] < 6
    # This model does what it is told with the transfer tool, and keeps its secret.
    assert (found["LLM06:2025"], found["ASI02"], found["LLM02:2025"]) == (
        "broken",
        "broken",
        "held",
    )
    assert (found["Article 15(5)"], found["Clauses 8.3 and 10.2"]) == ("gap", "gap")

    # Patched, the new config is a new subject with no scan of its own.
    await sponsor.call("PUT", f"{base}/config", json=PATCHED_CONFIG)
    fresh = await sponsor.call("GET", f"{base}/compliance")
    assert fresh["subject"]["config_version"] == 1
    assert fresh["subject"]["config_hash"] != report["subject"]["config_hash"]
    assert fresh["evidence_digest"] != report["evidence_digest"]
    assert statuses(fresh)["LLM06:2025"] == "not_tested"
    await sponsor.call("POST", f"{base}/scan", 202)
    await platform.work(Obedient())
    held = await sponsor.call("GET", f"{base}/compliance")
    assert {risk["status"] for risk in held["risks"]} == {"held"}
    assert statuses(held)["Article 15(5)"] == "evidence"
    # Read again with nothing changed, the evidence is the same evidence.
    again = await sponsor.call("GET", f"{base}/compliance")
    assert again["evidence_digest"] == held["evidence_digest"]


async def test_a_paid_break_is_mapped_and_followed_to_its_fix(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.funded(platform)
    base = f"/v1/bounties/{bounty_id}"
    live = await sponsor.call("GET", f"{base}/compliance")
    assert live["subject"]["state"] == "live"
    assert statuses(live)["Annex A, control A.8.3"] == "evidence"
    assert statuses(live)["Article 72"] == "evidence"

    await platform.poll(await platform.submit(bounty_id, WEAK), FakeModel())
    await platform.poll(await platform.submit(bounty_id, STRONG), FakeModel())
    await platform.payments.tick()
    report = await sponsor.call("GET", f"{base}/compliance")
    assert report["subject"]["state"] == "paused"
    testing = report["testing"]
    assert (testing["attacks"], testing["attacks_judged"], testing["verified_breaks"]) == (2, 2, 1)
    (finding,) = report["findings"]
    (paid,) = await sponsor.call("GET", f"{base}/findings")
    assert finding["id"] == paid["id"]
    assert (finding["tripwire"], finding["status"], finding["tripped_runs"]) == (
        "unapproved_transfer",
        "broken",
        5,
    )
    assert finding["vector"] == "Direct prompt injection"
    assert finding["risks"] == ["LLM01:2025", "LLM06:2025", "ASI01", "ASI02"]
    found = statuses(report)
    assert (found["LLM06:2025"], found["LLM02:2025"]) == ("broken", "held")
    assert (found["Article 15(5)"], found["Clauses 8.3 and 10.2"]) == ("gap", "gap")
    # The attack itself stays on the findings page.
    assert "transfer_funds" not in json.dumps(report["findings"])

    await sponsor.call("PUT", f"{base}/config", json=PATCHED_CONFIG)
    patched = await sponsor.call("GET", f"{base}/compliance")
    assert patched["findings"][0]["status"] == "patched"
    assert statuses(patched)["Clauses 8.3 and 10.2"] == "partial"

    await sponsor.call("POST", f"{base}/rearm", 202)
    await platform.work()
    fixed = await sponsor.call("GET", f"{base}/compliance")
    assert (fixed["testing"]["rearm"], fixed["findings"][0]["status"]) == ("passed", "fixed")
    found = statuses(fixed)
    assert (found["LLM06:2025"], found["ASI02"]) == ("fixed", "fixed")
    assert found["Clauses 8.3 and 10.2"] == "evidence"
    correction = fixed["frameworks"][1]["controls"][1]["evidence"]
    assert correction == [
        "1 verified break on an earlier config: the config was changed, and every paid attack"
        " was replayed against the patch without breaking it."
    ]
