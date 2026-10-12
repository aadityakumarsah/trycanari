"""Deleting a bounty, and the public list of bounties that are over."""

from __future__ import annotations

from sqlalchemy import select

from agentbounty.models import Bounty, BountyState, Finding, LedgerEntry

from .platform import REWARD, STRONG, WEAK, Platform, Sponsor


async def mine(sponsor: Sponsor) -> list[str]:
    listed = await sponsor.call("GET", "/v1/sponsor/bounties")
    return [bounty["id"] for bounty in listed]


async def test_a_draft_is_deleted_and_then_exists_for_no_one(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    kept = await sponsor.draft()
    gone = await sponsor.draft()

    await sponsor.call("DELETE", f"/v1/bounties/{gone}", 204)

    assert await mine(sponsor) == [kept]
    await sponsor.call("GET", f"/v1/sponsor/bounties/{gone}", 404)
    # Nothing can be done to it any more, a second delete included.
    await sponsor.call("POST", f"/v1/bounties/{gone}/activate", 404)
    await sponsor.call("DELETE", f"/v1/bounties/{gone}", 404)
    # The row itself stays: records may point at it.
    async with platform.sessions() as session:
        row = await session.get_one(Bounty, gone)
    assert row.deleted_at is not None


async def test_only_the_owner_can_delete(platform: Platform) -> None:
    owner = await platform.sponsor()
    bounty = await owner.draft()
    stranger = await platform.sponsor("other@example.com")
    await stranger.call("DELETE", f"/v1/bounties/{bounty}", 404)
    assert (await platform.client.delete(f"/v1/bounties/{bounty}")).status_code == 401
    assert await mine(owner) == [bounty]


async def test_a_bounty_with_something_still_to_happen_cannot_be_deleted(
    platform: Platform,
) -> None:
    sponsor = await platform.sponsor()
    live = await sponsor.funded(platform)
    refused = await sponsor.call("DELETE", f"/v1/bounties/{live}", 409)
    assert "close it first" in str(refused)

    await sponsor.call("POST", f"/v1/bounties/{live}/pause")
    assert "close it first" in str(await sponsor.call("DELETE", f"/v1/bounties/{live}", 409))

    waiting = await sponsor.draft()
    await sponsor.fund(waiting)
    assert "close it first" in str(await sponsor.call("DELETE", f"/v1/bounties/{waiting}", 409))

    # Asked to close but not closed yet: the refund is still to come.
    await sponsor.call("POST", f"/v1/bounties/{live}/close", json={})
    assert "closing" in str(await sponsor.call("DELETE", f"/v1/bounties/{live}", 409))
    assert sorted(await mine(sponsor)) == sorted([live, waiting])


async def test_a_closed_bounty_is_deleted_and_its_money_records_stay(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty = await sponsor.funded(platform)
    attempt = await platform.submit(bounty, STRONG)
    await platform.work()
    await platform.payments.tick()
    await platform.payments.tick()
    await sponsor.call("POST", f"/v1/bounties/{bounty}/close", json={})
    await platform.payments.tick()
    assert (await platform.status(attempt))["verdict"] == "verified"

    await sponsor.call("DELETE", f"/v1/bounties/{bounty}", 204)

    assert await mine(sponsor) == []
    assert (await platform.client.get(f"/v1/bounties/{bounty}")).status_code == 404
    assert (await platform.client.get("/v1/bounties", params={"show": "completed"})).json() == []
    async with platform.sessions() as session:
        row = await session.get_one(Bounty, bounty)
        entries = (
            await session.scalars(select(LedgerEntry).where(LedgerEntry.bounty_id == row.id))
        ).all()
        findings = (await session.scalars(select(Finding))).all()
    assert row.state is BountyState.CLOSED
    assert len(entries) >= 2
    assert len(findings) == 1
    # The hunter can still read their result and what they were paid.
    assert (await platform.status(attempt))["payout"]["amount_zatoshi"] == REWARD


async def test_a_deposit_for_a_deleted_draft_brings_it_back(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    bounty = await sponsor.draft()
    code = (await sponsor.call("GET", f"/v1/sponsor/bounties/{bounty}"))["deposit_code"]
    await sponsor.call("DELETE", f"/v1/bounties/{bounty}", 204)

    platform.wallet.simulate_deposit(REWARD // 2, code, confirmations=3)
    await platform.payments.tick()

    # The money is not lost in a bounty nobody can see.
    assert await mine(sponsor) == [bounty]
    held = await sponsor.call("GET", f"/v1/sponsor/bounties/{bounty}")
    assert held["balance_zatoshi"] == REWARD // 2
    # And a draft that holds money is closed for its refund, not deleted.
    refused = await sponsor.call("DELETE", f"/v1/bounties/{bounty}", 409)
    assert "holds escrow" in str(refused)


async def test_the_completed_list_shows_what_is_over_and_nothing_private(
    platform: Platform,
) -> None:
    sponsor = await platform.sponsor()
    client = platform.client

    async def completed() -> list[dict[str, object]]:
        answer = await client.get("/v1/bounties", params={"show": "completed"})
        assert answer.status_code == 200, answer.text
        listed: list[dict[str, object]] = answer.json()
        return listed

    held = await sponsor.funded(platform)
    broken = await sponsor.funded(platform)
    await sponsor.draft()
    # Closed before anyone attacked it: nothing was completed, so it is not listed.
    unattacked = await sponsor.funded(platform)
    await sponsor.call("POST", f"/v1/bounties/{unattacked}/close", json={})
    await platform.payments.tick()
    assert await completed() == []

    # One is attacked without success and closed; the other is broken, which pauses it.
    await platform.submit(held, WEAK)
    await platform.submit(broken, STRONG)
    await platform.work()
    await sponsor.call("POST", f"/v1/bounties/{held}/close", json={})
    await platform.payments.tick()
    await platform.payments.tick()

    over = {str(bounty["id"]): bounty for bounty in await completed()}
    assert set(over) == {held, broken}
    assert (over[held]["state"], over[held]["attempts"], over[held]["breaks"]) == ("closed", 1, 0)
    assert (over[broken]["state"], over[broken]["breaks"]) == ("paused", 1)
    assert all(bounty["accepting_attempts"] is False for bounty in over.values())
    assert "treasury assistant" not in str(over)
    # The live list is unchanged by any of this, and a paused bounty that was never
    # broken is not "completed": it may simply be resumed.
    assert (await client.get("/v1/bounties")).json() == []
    resting = await sponsor.funded(platform)
    await sponsor.call("POST", f"/v1/bounties/{resting}/pause")
    assert set(str(bounty["id"]) for bounty in await completed()) == {held, broken}
    assert (await client.get("/v1/bounties", params={"show": "everything"})).status_code == 422
