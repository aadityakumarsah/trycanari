"""A sponsor's own page: everything they have listed, how it fared, and who broke what."""

from __future__ import annotations

import json
from typing import Any

from agentbounty.services import house

from .platform import (
    APPROVED,
    FEE,
    HUNTER,
    PATCHED_CONFIG,
    REFUND,
    REWARD,
    SECRET,
    STRONG,
    WEAK,
    Platform,
)
from .test_attacker import ScriptedThinker
from .test_house import STEAL, hunt

ESCROWED = REWARD + FEE
PATH = "/v1/sponsor/overview"


def by_title(overview: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {bounty["title"]: bounty for bounty in overview["bounties"]}


async def named(platform: Platform, bounty_id: str, title: str) -> None:
    """Bounties made by the test helpers are untitled; give one a name to tell it by."""
    from sqlalchemy import update

    from agentbounty.models import Bounty

    async with platform.sessions.begin() as session:
        await session.execute(update(Bounty).where(Bounty.id == bounty_id).values(title=title))


async def test_a_new_sponsor_has_a_page_with_nothing_on_it_yet(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    overview = await sponsor.call("GET", PATH)
    assert overview["totals"] == {
        "bounties": 0,
        "live": 0,
        "drafts": 0,
        "paused": 0,
        "past": 0,
        "escrow_zatoshi": 0,
        "attempts": 0,
        "breaks": 0,
        "paid_zatoshi": 0,
    }
    assert (overview["bounties"], overview["breaks"]) == ([], [])
    assert overview["member_since"]
    assert (await platform.client.get(PATH)).status_code == 401


async def test_every_bounty_is_listed_with_what_has_happened_to_it(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    draft = await sponsor.draft()
    await named(platform, draft, "Draft agent")
    live = await sponsor.funded(platform)
    await named(platform, live, "Live agent")
    closed = await sponsor.funded(platform)
    await named(platform, closed, "Closed agent")
    await sponsor.call("POST", f"/v1/bounties/{closed}/close", json={"refund_address": REFUND})
    await platform.payments.tick()
    bare = (await sponsor.call("POST", "/v1/bounties", 201, json={"reward_zatoshi": REWARD}))["id"]
    await named(platform, bare, "Nothing in it")
    # Two attacks on the live one, neither a break.
    for _ in range(2):
        await platform.poll(await platform.submit(live, WEAK))

    overview = await sponsor.call("GET", PATH)
    assert overview["totals"] == {
        "bounties": 4,
        "live": 1,
        "drafts": 2,
        "paused": 0,
        "past": 1,
        "escrow_zatoshi": ESCROWED,
        "attempts": 2,
        "breaks": 0,
        "paid_zatoshi": 0,
    }
    listed = by_title(overview)
    assert set(listed) == {"Draft agent", "Live agent", "Closed agent", "Nothing in it"}
    attacked = listed["Live agent"]
    assert (attacked["id"], attacked["state"], attacked["attempts"], attacked["breaks"]) == (
        live,
        "live",
        2,
        0,
    )
    assert (attacked["balance_zatoshi"], attacked["required_zatoshi"]) == (ESCROWED, ESCROWED)
    assert attacked["last_attack_at"] is not None
    # What the agent is, without what it is told: the model, the ways in, the canaries.
    assert (attacked["model"], attacked["entry_points"], attacked["canaries"]) == (
        "demo-model",
        ["document", "user_message"],
        2,
    )
    assert (listed["Draft agent"]["state"], listed["Draft agent"]["attempts"]) == ("draft", 0)
    assert listed["Draft agent"]["last_attack_at"] is None
    assert (listed["Closed agent"]["state"], listed["Closed agent"]["balance_zatoshi"]) == (
        "closed",
        0,
    )
    empty = listed["Nothing in it"]
    assert (empty["model"], empty["entry_points"], empty["canaries"]) == (None, [], 0)
    assert all(bounty["created_at"] for bounty in overview["bounties"])
    # Nothing a sponsor keeps private is on the page: it is a summary.
    said = json.dumps(overview)
    for private in (SECRET, "treasury assistant", *APPROVED):
        assert private not in said, private

    # A draft that is deleted leaves the page.
    await sponsor.call("DELETE", f"/v1/bounties/{draft}", 204)
    after = await sponsor.call("GET", PATH)
    assert "Draft agent" not in by_title(after)
    assert (after["totals"]["bounties"], after["totals"]["drafts"]) == (3, 1)


async def test_the_page_says_who_broke_what_and_what_it_cost(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    # One bounty broken by a hunter with a profile, and paid for.
    paid = await sponsor.funded(platform)
    await named(platform, paid, "Treasury assistant")
    hunter = await platform.sponsor("ada@example.com")
    await hunter.call(
        "PATCH",
        "/v1/account",
        json={"name": "Ada Hunter", "github": "ada", "company": "Analytical", "role": "Researcher"},
    )
    attack = {"payout_address": HUNTER, "fills": [{"slot": "user_message", "content": STRONG}]}
    accepted = await hunter.call("POST", f"/v1/bounties/{paid}/attempts", 202, json=attack)
    assert (await platform.poll(accepted))["verdict"] == "verified"
    await platform.payments.tick()
    # One broken by the house hunter, which is not paid.
    waived = await sponsor.funded(platform)
    await named(platform, waived, "Refund desk agent")
    assert await hunt(platform, ScriptedThinker(STEAL)) is True
    await platform.payments.tick()
    # One broken by someone with no account.
    anonymous = await sponsor.funded(platform)
    await named(platform, anonymous, "Travel desk agent")
    assert (await platform.poll(await platform.submit(anonymous, STRONG)))["verdict"] == "verified"
    await platform.payments.tick()

    overview = await sponsor.call("GET", PATH)
    totals = overview["totals"]
    assert (totals["bounties"], totals["live"], totals["paused"]) == (3, 0, 3)
    assert (totals["attempts"], totals["breaks"], totals["paid_zatoshi"]) == (3, 3, 2 * REWARD)
    # The two that were paid for are spent; the house's break left its escrow whole.
    assert totals["escrow_zatoshi"] == ESCROWED
    listed = by_title(overview)
    assert (
        listed["Treasury assistant"]["breaks"],
        listed["Treasury assistant"]["paid_zatoshi"],
    ) == (
        1,
        REWARD,
    )
    assert (listed["Refund desk agent"]["breaks"], listed["Refund desk agent"]["paid_zatoshi"]) == (
        1,
        0,
    )
    assert listed["Refund desk agent"]["balance_zatoshi"] == ESCROWED

    # Newest first.
    by_stranger, by_house, by_ada = overview["breaks"]
    assert [found["bounty_title"] for found in overview["breaks"]] == [
        "Travel desk agent",
        "Refund desk agent",
        "Treasury assistant",
    ]
    assert by_ada["hunter"] == {
        "name": "Ada Hunter",
        "picture_url": None,
        "github": "ada",
        "company": "Analytical",
        "role": "Researcher",
        "website": None,
    }
    assert (by_ada["house"], by_ada["reward_zatoshi"], by_ada["payout_state"]) == (
        False,
        REWARD,
        "sent",
    )
    assert by_ada["txid"]
    assert (by_ada["bounty_id"], by_ada["tripped_runs"], by_ada["total_runs"]) == (paid, 5, 5)
    assert by_ada["canary"] == {"type": "forbidden_tool", "public_label": "unapproved_transfer"}
    assert (by_house["house"], by_house["reward_zatoshi"], by_house["payout_state"]) == (
        True,
        0,
        None,
    )
    assert by_house["hunter"]["name"] == house.NAME
    assert (by_stranger["hunter"], by_stranger["house"], by_stranger["reward_zatoshi"]) == (
        None,
        False,
        REWARD,
    )
    # The same breaks as the findings of each bounty, and never the attack itself.
    (finding,) = await sponsor.call("GET", f"/v1/bounties/{paid}/findings")
    assert finding["id"] == by_ada["id"]
    assert "transfer_funds" not in json.dumps(overview["breaks"])

    # A patch that is checked puts a bounty back in the running, on the same page.
    await sponsor.call("PUT", f"/v1/bounties/{waived}/config", json=PATCHED_CONFIG)
    await sponsor.call("POST", f"/v1/bounties/{waived}/rearm", 202)
    await platform.work()
    await sponsor.call("POST", f"/v1/bounties/{waived}/fund", json={"refund_address": REFUND})
    again = await sponsor.call("GET", PATH)
    assert (again["totals"]["live"], again["totals"]["paused"]) == (1, 2)
    assert by_title(again)["Refund desk agent"]["state"] == "live"


async def test_a_sponsor_sees_only_their_own(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.funded(platform)
    assert (await platform.poll(await platform.submit(bounty_id, STRONG)))["verdict"] == "verified"
    other = await platform.sponsor("other@example.com")
    theirs = await other.call("GET", PATH)
    assert (theirs["totals"]["bounties"], theirs["bounties"], theirs["breaks"]) == (0, [], [])
    mine = await sponsor.call("GET", PATH)
    assert (mine["totals"]["bounties"], len(mine["breaks"])) == (1, 1)
