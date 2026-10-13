"""The build gate: a sponsor's script that scans the agent and fails when it gives way."""

from __future__ import annotations

import itertools
from typing import Any

import httpx

from agentbounty import check
from agentbounty.services.model import FakeModel
from agentbounty.workers import replay

from .platform import PATCHED_CONFIG, Platform
from .test_scan import Obedient

TEAMS = itertools.count()


class Broken:
    async def complete(self, *_: Any) -> Any:
        raise RuntimeError("the model is down")


async def run(platform: Platform, model: Any, config: dict[str, Any] | None) -> check.Verdict:
    """Run the gate as a sponsor's build would, with the worker doing its part in between."""
    sponsor = await platform.sponsor(f"team-{next(TEAMS)}@example.com")
    bounty_id = await sponsor.draft()

    async def work() -> None:
        await replay.work_once(platform.sessions, platform.cipher, model, max_tries=3)

    transport = httpx.ASGITransport(app=platform.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=sponsor.headers
    ) as http:
        return await check.check(http, bounty_id, config, wait=work)


async def test_the_gate_passes_an_agent_that_holds_and_fails_one_that_gives_way(
    platform: Platform,
) -> None:
    held = await run(platform, FakeModel(), None)
    assert held.code == check.HELD
    assert held.lines[-1] == "The agent held against all 6 probes."
    assert all(line.startswith("held") for line in held.lines[:-1])


async def test_the_gate_fails_the_build_when_a_canary_trips(platform: Platform) -> None:
    broken = await run(platform, Obedient(), None)
    assert broken.code == check.TRIPPED
    assert "gave way to" in broken.lines[-1]
    tripped = [line for line in broken.lines if line.startswith("TRIPPED")]
    assert tripped
    assert all("unapproved_transfer" in line for line in tripped)


async def test_the_gate_sends_the_agent_as_it_now_is_before_scanning_it(
    platform: Platform,
) -> None:
    # The bounty holds a config that gives way; the build sends a patched one, and it is
    # the patched one that is scanned.
    assert (await run(platform, Obedient(), None)).code == check.TRIPPED
    assert (await run(platform, Obedient(), PATCHED_CONFIG)).code == check.HELD
    refused = await run(platform, FakeModel(), {"system_prompt": "no model named"})
    assert refused.code == check.FAILED
    assert refused.lines[0].startswith("The config was refused")


async def test_the_gate_says_so_when_the_check_could_not_be_carried_out(
    platform: Platform,
) -> None:
    failed = await run(platform, Broken(), None)
    assert (failed.code, failed.lines) == (
        check.FAILED,
        ("The platform could not run the scan. Run the check again.",),
    )
    # Someone else's bounty is not there to be scanned.
    sponsor = await platform.sponsor("first@example.com")
    bounty_id = await sponsor.draft()
    other = await platform.sponsor("second@example.com")
    transport = httpx.ASGITransport(app=platform.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=other.headers
    ) as http:
        verdict = await check.check(http, bounty_id)
    assert verdict.code == check.FAILED
    assert verdict.lines[0].startswith("The scan could not be started")


async def test_the_gate_can_run_a_finished_scan_afresh(platform: Platform) -> None:
    sponsor = await platform.sponsor(f"team-{next(TEAMS)}@example.com")
    bounty_id = await sponsor.draft()
    models: list[Any] = [FakeModel(), Obedient()]

    async def work() -> None:
        await replay.work_once(platform.sessions, platform.cipher, models.pop(0), max_tries=3)

    transport = httpx.ASGITransport(app=platform.app)
    async with httpx.AsyncClient(
        transport=transport, base_url="http://test", headers=sponsor.headers
    ) as http:
        assert (await check.check(http, bounty_id, wait=work)).code == check.HELD
        # Asked the plain way again, the answer is the scan there already is.
        assert (await check.check(http, bounty_id, wait=work)).code == check.HELD
        assert len(models) == 1
        # Asked afresh, it is run again, and this time the agent gives way.
        assert (await check.check(http, bounty_id, again=True, wait=work)).code == check.TRIPPED
