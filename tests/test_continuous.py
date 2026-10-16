"""Continuous corpus replays: versions, CI gates, and the daily scheduler."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update

from agentbounty.models import Bounty, BountyState, ContinuousRun
from agentbounty.models import TargetConfig as TargetConfigRow
from agentbounty.services import continuous
from agentbounty.services.model import GUARD_SENTENCE

from .platform import PATCHED_CONFIG, STRONG, Platform, Sponsor


async def broken_then_patched(platform: Platform) -> tuple[Sponsor, str]:
    sponsor = await platform.sponsor("continuous@example.com")
    bounty_id = await sponsor.funded(platform)
    attack = await platform.submit(bounty_id, STRONG)
    await platform.work()
    assert (await platform.status(attack))["verdict"] == "verified"
    await sponsor.call("PUT", f"/v1/bounties/{bounty_id}/config", json=PATCHED_CONFIG)
    await platform.work()
    return sponsor, bounty_id


async def test_versions_and_ci_gate_report_regressions(platform: Platform) -> None:
    sponsor, bounty_id = await broken_then_patched(platform)
    first = await sponsor.call("GET", f"/v1/bounties/{bounty_id}/continuous")
    completed = [run for run in first["runs"] if run["state"] == "done"]
    assert any(run["passed"] is True and run["total"] == 7 for run in completed)

    # The weaker version removes the guard. Its stored past attack newly succeeds.
    prompt = PATCHED_CONFIG["system_prompt"]
    weaker = {**PATCHED_CONFIG, "system_prompt": prompt.replace(GUARD_SENTENCE, "")}
    await sponsor.call("PUT", f"/v1/bounties/{bounty_id}/config", json=weaker)
    gate = await sponsor.call("POST", f"/v1/bounties/{bounty_id}/continuous/gate", 202)
    assert gate["state"] == "queued"
    await platform.work()
    report = await sponsor.call("GET", f"/v1/bounties/{bounty_id}/continuous")
    ci = next(run for run in report["runs"] if run["id"] == gate["id"])
    assert ci["passed"] is False
    assert [item["canary_type"] for item in ci["regressed"]] == ["forbidden_tool"]
    assert ci["succeeded"] == 1
    assert ci["failures"] == 1
    assert ci["attack_success_rate"] == 1 / 7


async def test_first_ci_gate_has_a_baseline_before_any_saved_break(platform: Platform) -> None:
    sponsor = await platform.sponsor("new-assurance@example.com")
    bounty_id = await sponsor.draft(PATCHED_CONFIG)

    gate = await sponsor.call("POST", f"/v1/bounties/{bounty_id}/continuous/gate", 202)
    await platform.work()
    report = await sponsor.call("GET", f"/v1/bounties/{bounty_id}/continuous")
    completed = next(run for run in report["runs"] if run["id"] == gate["id"])

    assert completed["passed"] is True
    assert completed["total"] == 6
    assert completed["failures"] == 0
    assert {probe["source"] for probe in completed["regressed"]} == set()


async def test_schedule_queues_a_fresh_corpus_retest(platform: Platform) -> None:
    sponsor, bounty_id = await broken_then_patched(platform)
    await sponsor.call("PATCH", f"/v1/bounties/{bounty_id}/settings", json={"watch": True})
    async with platform.sessions.begin() as session:
        patched = await session.scalar(
            select(TargetConfigRow)
            .where(TargetConfigRow.bounty_id == bounty_id)
            .order_by(TargetConfigRow.version.desc())
            .limit(1)
        )
        assert patched is not None
        await session.execute(
            update(Bounty)
            .where(Bounty.id == bounty_id)
            .values(state=BountyState.LIVE, live_config_id=patched.id)
        )
        await session.execute(
            update(ContinuousRun)
            .where(ContinuousRun.bounty_id == bounty_id)
            .values(finished_at=datetime.now(UTC) - timedelta(days=2))
        )
        queued = await continuous.requeue_scheduled(session, plans_offered=False)
    assert queued == 1
    await platform.work()
    async with platform.sessions() as session:
        latest = await session.scalar(
            select(ContinuousRun)
            .where(ContinuousRun.bounty_id == bounty_id)
            .order_by(ContinuousRun.created_at.desc())
            .limit(1)
        )
    assert latest is not None
    assert latest.trigger == "schedule"
    assert latest.state == "done"
