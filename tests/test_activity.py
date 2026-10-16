"""What the public and a sponsor can see of how much a bounty is being attacked."""

from __future__ import annotations

from agentbounty.settings import Settings

from .platform import REWARD, STRONG, WEAK, Platform, platform_on


async def test_the_public_feed_counts_attempts_and_lists_breaks_without_the_attack(
    platform: Platform,
) -> None:
    client = platform.client
    quiet = (await client.get("/v1/activity")).json()
    assert quiet == {
        "stats": {
            "live_bounties": 0,
            "reward_pool_zatoshi": 0,
            "attempts": 0,
            "verified_breaks": 0,
            "paid_zatoshi": 0,
        },
        "recent_breaks": [],
    }

    sponsor = await platform.sponsor()
    bounty_id = await sponsor.funded(platform)
    assert (await client.get("/v1/activity")).json()["stats"] == {
        **quiet["stats"],
        "live_bounties": 1,
        "reward_pool_zatoshi": REWARD,
    }

    await platform.poll(await platform.submit(bounty_id, WEAK))
    listed = (await client.get("/v1/bounties")).json()[0]
    assert (listed["attempts"], listed["breaks"]) == (1, 0)

    assert (await platform.poll(await platform.submit(bounty_id, STRONG)))["verdict"] == "verified"
    await platform.payments.tick()

    feed = await client.get("/v1/activity")
    body = feed.json()
    # The break paused the bounty, so nothing is live, and the reward has been paid out.
    assert body["stats"] == {
        "live_bounties": 0,
        "reward_pool_zatoshi": 0,
        "attempts": 2,
        "verified_breaks": 1,
        "paid_zatoshi": REWARD,
    }
    (broke,) = body["recent_breaks"]
    assert broke["bounty_id"] == bounty_id
    assert broke["canary"] == {"type": "forbidden_tool", "public_label": "unapproved_transfer"}
    assert (broke["reward_zatoshi"], broke["tripped_runs"]) == (REWARD, 5)
    # That it happened is public. How is the sponsor's.
    assert "transfer_funds" not in feed.text
    assert "attacker" not in feed.text
    shown = (await client.get(f"/v1/bounties/{bounty_id}")).json()
    assert (shown["attempts"], shown["breaks"], shown["state"]) == (2, 1, "paused")


async def test_a_sponsor_sees_how_their_bounty_is_being_attacked(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.funded(platform)
    path = f"/v1/bounties/{bounty_id}/attempts"
    assert await sponsor.call("GET", path) == {
        "total": 0,
        "queued": 0,
        "verified": 0,
        "house": 0,
        "recent": [],
    }

    waiting = await platform.submit(bounty_id, WEAK)
    seen = await sponsor.call("GET", path)
    assert (seen["total"], seen["queued"], seen["verified"]) == (1, 1, 0)
    await platform.poll(waiting)
    await platform.poll(await platform.submit(bounty_id, STRONG))

    seen = await sponsor.call("GET", path)
    assert (seen["total"], seen["queued"], seen["verified"]) == (2, 0, 1)
    # Newest first. The attack text and the hunter's address are not part of it.
    assert [(line["state"], line["verified"]) for line in seen["recent"]] == [
        ("done", True),
        ("done", False),
    ]
    assert set(seen["recent"][0]) == {"id", "state", "verified", "house", "submitted_at"}
    # These were people's attacks, not the house hunter's.
    assert (seen["house"], [line["house"] for line in seen["recent"]]) == (0, [False, False])

    # Someone else's bounty is not found, and the public cannot ask at all.
    other = await platform.sponsor("other@example.com")
    await other.call("GET", path, 404)
    assert (await platform.client.get(path)).status_code == 401


async def test_public_answers_are_fresh_after_successful_changes(
    service_settings: Settings,
) -> None:
    settings = service_settings.model_copy(update={"public_cache_seconds": 30.0})
    async with platform_on(settings) as platform:
        client = platform.client
        assert (await client.get("/v1/bounties")).json() == []
        sponsor = await platform.sponsor()
        bounty_id = await sponsor.live()
        # Successful mutations invalidate this process's public snapshots immediately.
        assert (await client.get("/v1/bounties")).json()[0]["id"] == bounty_id
        assert (await client.get(f"/v1/bounties/{bounty_id}")).json()["state"] == "live"
        assert (await client.get(f"/v1/bounties/{bounty_id}/x")).status_code == 404

        # A miss is not remembered, and the cache never decides what may be attacked.
        await sponsor.call("POST", f"/v1/bounties/{bounty_id}/pause")
        assert (await client.get(f"/v1/bounties/{bounty_id}")).json()["state"] == "paused"
        await platform.submit(bounty_id, WEAK, expect=409)
        assert (await client.get(f"/v1/bounties/{bounty_id}")).json()["state"] == "paused"


async def test_a_sponsor_sees_who_broke_their_agent_when_it_was_an_account(
    platform: Platform,
) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.funded(platform)
    hunter = await platform.sponsor("hunter@example.com")
    await hunter.call(
        "PATCH",
        "/v1/account",
        json={"name": "Ada Lovelace", "github": "ada", "company": "Engines", "role": "Engineer"},
    )
    from .platform import HUNTER

    body = {"payout_address": HUNTER, "fills": [{"slot": "user_message", "content": STRONG}]}
    accepted = await hunter.call("POST", f"/v1/bounties/{bounty_id}/attempts", 202, json=body)
    assert (await platform.poll(accepted))["verdict"] == "verified"

    (finding,) = await sponsor.call("GET", f"/v1/bounties/{bounty_id}/findings")
    assert finding["hunter"] == {
        "name": "Ada Lovelace",
        "picture_url": None,
        "github": "ada",
        "company": "Engines",
        "role": "Engineer",
        "website": None,
    }
    # Their email and their key are not part of it.
    assert "hunter@example.com" not in str(finding["hunter"])


async def test_an_attack_made_without_an_account_names_nobody(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.funded(platform)
    assert (await platform.poll(await platform.submit(bounty_id, STRONG)))["verdict"] == "verified"
    (finding,) = await sponsor.call("GET", f"/v1/bounties/{bounty_id}/findings")
    assert finding["hunter"] is None


async def test_the_platform_as_a_whole_has_a_daily_token_ceiling(
    service_settings: Settings,
) -> None:
    settings = service_settings.model_copy(update={"platform_daily_token_budget": 1})
    async with platform_on(settings) as platform:
        first = await (await platform.sponsor("a@example.com")).live()
        second = await (await platform.sponsor("b@example.com")).live()
        # Nothing has run yet, so the first attack is taken; once it has run, the ceiling
        # is reached for every bounty, not only the one that was attacked.
        await platform.poll(await platform.submit(first, WEAK))
        refused = await platform.client.post(
            f"/v1/bounties/{second}/attempts",
            json={
                "payout_address": (await _address()),
                "fills": [{"slot": "user_message", "content": WEAK}],
            },
        )
        assert (refused.status_code, refused.json()["detail"]) == (
            429,
            "the platform's daily run budget is spent; attacks reopen as it frees up",
        )


async def _address() -> str:
    from .platform import HUNTER

    return HUNTER


async def test_the_leaderboard_ranks_hunters_who_broke_something_from_an_account(
    platform: Platform,
) -> None:
    assert (await platform.client.get("/v1/leaderboard")).json() == []
    sponsor = await platform.sponsor()
    hunter = await platform.sponsor("hunter@example.com")
    await hunter.call("PATCH", "/v1/account", json={"name": "Ada Lovelace", "github": "ada"})
    from .platform import HUNTER

    for _ in range(2):
        bounty_id = await sponsor.funded(platform)
        body = {"payout_address": HUNTER, "fills": [{"slot": "user_message", "content": STRONG}]}
        accepted = await hunter.call("POST", f"/v1/bounties/{bounty_id}/attempts", 202, json=body)
        assert (await platform.poll(accepted))["verdict"] == "verified"
        await platform.payments.tick()
    # A break made without an account, and an account that broke nothing, are not on it.
    anonymous = await sponsor.funded(platform)
    await platform.poll(await platform.submit(anonymous, STRONG))

    (leader,) = (await platform.client.get("/v1/leaderboard")).json()
    assert leader == {
        "name": "Ada Lovelace",
        "picture_url": None,
        "github": "ada",
        "company": None,
        "breaks": 2,
        "earned_zatoshi": 2 * REWARD,
        "last_break_at": leader["last_break_at"],
    }
    # Their email is not part of it.
    assert "hunter@example.com" not in str(leader)
