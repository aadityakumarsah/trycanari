"""Admins: who runs the platform, and what they can do that nobody else can."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError

from agentbounty.models import AdminAction, Sponsor
from agentbounty.settings import Settings

from .platform import Platform, platform_on
from .test_google import CLIENT_ID, CLIENT_SECRET, Google, come_back, leave_for_google
from .test_invites import attack, join

OWNER = "owner@example.com"


@pytest.fixture
async def club(service_settings: Settings) -> AsyncIterator[Platform]:
    settings = service_settings.model_copy(update={"invite_only": True, "admin_emails": OWNER})
    async with platform_on(settings) as platform:
        yield platform


async def account(platform: Platform, email: str) -> dict[str, str]:
    joined = await join(platform, email, None)
    assert joined.status_code == 201, joined.text
    return {"Authorization": f"Bearer {joined.json()['api_key']}"}


async def promote(platform: Platform, email: str) -> None:
    async with platform.sessions.begin() as session:
        await session.execute(update(Sponsor).where(Sponsor.email == email).values(is_admin=True))


async def owner(platform: Platform) -> dict[str, str]:
    """The owner: the address named in the settings, as an account Google vouches for."""
    headers = await account(platform, OWNER)
    async with platform.sessions.begin() as session:
        await session.execute(
            update(Sponsor).where(Sponsor.email == OWNER).values(google_sub="google-owner")
        )
    return headers


async def test_the_admin_api_does_not_exist_for_anyone_else(club: Platform) -> None:
    someone = await account(club, "someone@example.com")
    for method, path in (
        ("GET", "/v1/admin/people"),
        ("GET", "/v1/admin/referral-codes"),
        ("POST", "/v1/admin/referral-codes"),
        ("PATCH", "/v1/admin/people/00000000-0000-0000-0000-000000000000"),
    ):
        refused = await club.client.request(method, path, headers=someone, json={})
        assert refused.status_code == 404, path
        assert (await club.client.request(method, path, json={})).status_code == 401, path
    assert (await club.client.get("/v1/account", headers=someone)).json()["is_admin"] is False


async def test_an_owner_is_an_admin_only_when_google_vouches_for_the_address(
    service_settings: Settings,
) -> None:
    settings = service_settings.model_copy(
        update={
            "admin_emails": f" {OWNER.upper()} , second@example.com",
            "google_client_id": CLIENT_ID,
            "google_client_secret": SecretStr(CLIENT_SECRET),
            "public_url": "http://test",
        }
    )
    assert settings.owners == {OWNER, "second@example.com"}
    async with platform_on(settings) as platform:
        # Signing up with the owner's address proves nothing, so it grants nothing.
        squatter = await account(platform, "second@example.com")
        assert (await platform.client.get("/v1/account", headers=squatter)).json()[
            "is_admin"
        ] is False
        assert (await platform.client.get("/v1/admin/people", headers=squatter)).status_code == 404

        stand_in = Google()
        stand_in.account = {"sub": "owner-1", "email": OWNER, "email_verified": True}
        platform.app.state.google_transport = httpx.MockTransport(stand_in.handle)
        browser = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=platform.app), base_url="http://test"
        )
        query = await leave_for_google(browser, stand_in)
        await come_back(browser, code="one-time", state=query["state"])
        assert (await browser.get("/v1/account")).json()["is_admin"] is True
        listed = (await browser.get("/v1/admin/people")).json()["items"]
        mine = next(person for person in listed if person["email"] == OWNER)
        assert (mine["is_admin"], mine["is_owner"]) == (True, True)


async def test_the_owner_makes_ambassadors_and_admins_and_grants_access(club: Platform) -> None:
    boss = await owner(club)
    friend = await account(club, "friend@example.com")
    await club.client.patch("/v1/account", headers=friend, json={"name": "Grace Hopper"})

    async def listed(**params: Any) -> dict[str, Any]:
        page: dict[str, Any] = (
            await club.client.get("/v1/admin/people", headers=boss, params=params)
        ).json()
        return page

    everyone = await listed()
    assert everyone["total"] == 2
    assert [person["email"] for person in everyone["items"]] == ["friend@example.com", OWNER]
    (grace,) = (await listed(q="grace"))["items"]
    assert (grace["is_ambassador"], grace["is_admin"], grace["can_hunt"]) == (False, False, False)
    # Search text is only ever text: a wildcard matches nothing by itself.
    assert (await listed(q="%"))["total"] == 0
    assert (await listed(q="_"))["total"] == 0
    # Pages.
    first = await listed(limit=1)
    second = await listed(limit=1, offset=1)
    assert (first["total"], len(first["items"]), second["items"][0]["email"]) == (2, 1, OWNER)
    for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}):
        refused = await club.client.get("/v1/admin/people", headers=boss, params=bad)
        assert refused.status_code == 422, bad

    async def change(person: dict[str, Any], **values: Any) -> httpx.Response:
        return await club.client.patch(
            f"/v1/admin/people/{person['id']}", headers=boss, json=values
        )

    # Ambassador: they can now make codes of their own.
    assert (await change(grace, is_ambassador=True)).json()["is_ambassador"] is True
    made = await club.client.post("/v1/referral-codes", headers=friend, json={"count": 2})
    assert made.status_code == 201
    assert (await change(grace, is_ambassador=False)).json()["is_ambassador"] is False
    assert (
        await club.client.post("/v1/referral-codes", headers=friend, json={"count": 1})
    ).status_code == 403

    # Access to the bounties with no referral code, which can be taken back.
    granted = (await change(grace, hunting_granted=True)).json()
    assert (granted["can_hunt"], granted["hunting_granted"], granted["referral_code"]) == (
        True,
        True,
        None,
    )
    assert (await club.client.get("/v1/account", headers=friend)).json()["can_hunt"] is True
    assert (await change(grace, hunting_granted=False)).json()["can_hunt"] is False

    # Admin: they can now use this API themselves.
    assert (await change(grace, is_admin=True)).json()["is_admin"] is True
    assert (await club.client.get("/v1/admin/people", headers=friend)).status_code == 200
    assert (await change(grace, is_admin=False)).json()["is_admin"] is False
    assert (await club.client.get("/v1/admin/people", headers=friend)).status_code == 404

    # The owner cannot be unmade from here: asking changes nothing.
    me = next(person for person in everyone["items"] if person["email"] == OWNER)
    assert (me["is_owner"], me["is_admin"]) == (True, True)
    still = (await change(me, is_admin=False)).json()
    assert (still["is_owner"], still["is_admin"]) == (True, True)
    gone = await change({"id": "00000000-0000-0000-0000-000000000000"}, is_admin=True)
    assert gone.status_code == 404
    assert (await change(grace, anything="else")).status_code == 422

    # Every change that happened is in the log, once, and the log cannot be rewritten.
    async with club.sessions() as session:
        actions = list(
            await session.scalars(select(AdminAction.action).order_by(AdminAction.created_at))
        )
    assert actions == [
        "is_ambassador=true",
        "is_ambassador=false",
        "hunting_granted=true",
        "hunting_granted=false",
        "is_admin=true",
        "is_admin=false",
    ]
    for statement in ("UPDATE admin_actions SET action = 'x'", "DELETE FROM admin_actions"):
        with pytest.raises(DBAPIError, match="append-only"):
            async with club.sessions.begin() as session:
                await session.execute(text(statement))

    # The owner can read the log back, newest first, with who did what to whom.
    log = (await club.client.get("/v1/admin/actions", headers=boss, params={"limit": 2})).json()
    assert log["total"] == 6
    assert log["items"][0] == {
        "admin_email": OWNER,
        "action": "is_admin=false",
        "target_email": "friend@example.com",
        "detail": "friend@example.com",
        "created_at": log["items"][0]["created_at"],
    }
    assert (await club.client.get("/v1/admin/actions", headers=friend)).status_code == 404


async def test_only_the_owner_decides_who_is_an_admin(club: Platform) -> None:
    boss = await owner(club)
    deputy = await account(club, "deputy@example.com")
    await account(club, "other@example.com")
    people = (await club.client.get("/v1/admin/people", headers=boss)).json()["items"]
    ids = {person["email"]: person["id"] for person in people}

    async def change(by: dict[str, str], email: str, **values: Any) -> httpx.Response:
        return await club.client.patch(f"/v1/admin/people/{ids[email]}", headers=by, json=values)

    assert (await change(boss, "deputy@example.com", is_admin=True)).json()["is_admin"] is True
    mine = (await club.client.get("/v1/account", headers=deputy)).json()
    assert (mine["is_admin"], mine["is_owner"]) == (True, False)
    assert (await club.client.get("/v1/account", headers=boss)).json()["is_owner"] is True

    # An admin does everything else, but not this: not for others, the owner, or themselves.
    assert (await change(deputy, "other@example.com", is_ambassador=True)).status_code == 200
    assert (await change(deputy, "other@example.com", hunting_granted=True)).status_code == 200
    for email, value in (
        ("other@example.com", True),
        ("deputy@example.com", False),
        (OWNER, True),
    ):
        refused = await change(deputy, email, is_admin=value)
        assert (refused.status_code, refused.json()["detail"]) == (
            403,
            "only the owner can make or remove admins",
        ), email
    # Asking for what is already so is not a change, so it is not refused.
    assert (await change(deputy, "other@example.com", is_admin=False)).status_code == 200
    # Nothing an admin sent alongside a refused change was applied.
    mixed = await change(deputy, "other@example.com", is_admin=True, is_ambassador=False)
    assert mixed.status_code == 403
    listed = (await club.client.get("/v1/admin/people", headers=boss, params={"q": "other"})).json()
    assert listed["items"][0]["is_ambassador"] is True

    # The owner takes it back, and the deputy is out at once.
    assert (await change(boss, "deputy@example.com", is_admin=False)).json()["is_admin"] is False
    assert (await club.client.get("/v1/admin/people", headers=deputy)).status_code == 404


async def test_an_admin_makes_single_use_codes_and_an_invite_link_grants_access(
    club: Platform,
) -> None:
    boss = await account(club, "boss@example.com")
    await promote(club, "boss@example.com")
    await club.client.patch("/v1/account", headers=boss, json={"name": "The Boss"})

    async def make(**body: Any) -> httpx.Response:
        return await club.client.post("/v1/admin/referral-codes", headers=boss, json=body)

    named = await make(code="launch-day")
    assert named.status_code == 201
    assert named.json() == [
        {
            "code": "LAUNCH-DAY",
            "kind": "hunter",
            "used_by": None,
            "max_uses": 1,
            "uses": 0,
            "users": [],
            "owner_email": "boss@example.com",
            "created_at": named.json()[0]["created_at"],
        }
    ]
    assert (await make(code="LAUNCH-DAY")).status_code == 409
    # An admin is not held to an ambassador's limit.
    batch = await make(count=40)
    assert batch.status_code == 201
    assert len({item["code"] for item in batch.json()}) == 40
    for bad in ({"count": 0}, {"count": 501}, {"code": "has spaces"}, {"code": "abc"}):
        assert (await make(**bad)).status_code == 422, bad
    assert (await make(count=2, code="TWO-AT-ONCE")).status_code == 422

    # What the link shows to whoever follows it, signed in or not.
    shown = await club.client.get("/v1/invites/launch-day")
    assert shown.json() == {
        "code": "LAUNCH-DAY",
        "kind": "hunter",
        "available": True,
        "invited_by": "The Boss",
    }
    assert (await club.client.get("/v1/invites/NOPE-NOPE")).status_code == 404

    # Someone follows it and joins: they can hunt at once, and the link is spent.
    newcomer = await join(club, "new@example.com", "LAUNCH-DAY")
    headers = {"Authorization": f"Bearer {newcomer.json()['api_key']}"}
    assert (await club.client.get("/v1/account", headers=headers)).json()["can_hunt"] is True
    assert (await club.client.get("/v1/invites/LAUNCH-DAY")).json()["available"] is False
    old = await account(club, "old@example.com")
    late = await club.client.post("/v1/account/referral", headers=old, json={"code": "LAUNCH-DAY"})
    assert (late.status_code, late.json()["detail"]) == (
        403,
        "that referral code has already been used",
    )
    # Someone who already had an account accepts a fresh one.
    fresh = batch.json()[0]["code"]
    accepted = await club.client.post("/v1/account/referral", headers=old, json={"code": fresh})
    assert accepted.json()["can_hunt"] is True

    page = (
        await club.client.get("/v1/admin/referral-codes", headers=boss, params={"limit": 100})
    ).json()
    assert page["total"] == 41
    used = {item["code"]: item["used_by"] for item in page["items"] if item["used_by"]}
    assert used == {"LAUNCH-DAY": "new@example.com", fresh: "old@example.com"}
    small = (
        await club.client.get(
            "/v1/admin/referral-codes", headers=boss, params={"limit": 5, "offset": 40}
        )
    ).json()
    assert (small["total"], len(small["items"])) == (41, 1)


async def test_the_admin_api_is_left_out_of_the_published_description(club: Platform) -> None:
    described = (await club.client.get("/openapi.json")).json()
    assert not [path for path in described["paths"] if "admin" in path]
    assert "/v1/account" in described["paths"]


async def test_every_answer_carries_the_security_headers(club: Platform) -> None:
    for path in ("/v1/bounties", "/v1/session", "/health", "/v1/admin/people"):
        answer = await club.client.get(path)
        assert answer.headers["x-content-type-options"] == "nosniff", path
        assert answer.headers["x-frame-options"] == "DENY", path
        assert answer.headers["referrer-policy"] == "same-origin", path
    private = await club.client.get("/v1/session")
    assert private.headers["cache-control"] == "no-store"
    assert private.headers["x-robots-tag"] == "noindex"
    # Only over HTTPS, which development is not.
    assert "strict-transport-security" not in private.headers


async def test_cookies_are_marked_secure_wherever_the_site_is_served_over_https(
    service_settings: Settings,
) -> None:
    for public_url, expected in (("https://example.com", True), (None, False)):
        settings = service_settings.model_copy(update={"public_url": public_url})
        assert settings.secure_cookies is expected
        async with platform_on(settings) as platform:
            sponsor = await platform.sponsor(f"secure-{expected}@example.com".lower())
            key = sponsor.headers["Authorization"].removeprefix("Bearer ")
            signed_in = await platform.client.post(
                "/v1/session",
                json={"api_key": key},
                headers={"Origin": "http://test", "Sec-Fetch-Site": "same-origin"},
            )
            assert ("Secure" in signed_in.headers["set-cookie"]) is expected


def test_helpers_are_importable() -> None:
    assert callable(attack)


async def test_an_admin_shares_one_code_with_several_people_and_sees_who_used_it(
    club: Platform,
) -> None:
    boss = await account(club, "boss@example.com")
    await promote(club, "boss@example.com")
    made = await club.client.post(
        "/v1/admin/referral-codes", headers=boss, json={"code": "MEETUP-10", "max_uses": 2}
    )
    assert made.status_code == 201
    assert (made.json()[0]["max_uses"], made.json()[0]["uses"]) == (2, 0)
    for bad in (0, 10_001):
        refused = await club.client.post(
            "/v1/admin/referral-codes", headers=boss, json={"max_uses": bad}
        )
        assert refused.status_code == 422

    first = await join(club, "first@example.com", "MEETUP-10")
    assert first.status_code == 201
    assert (await club.client.get("/v1/invites/MEETUP-10")).json()["available"] is True
    second = await account(club, "second@example.com")
    taken = await club.client.post(
        "/v1/account/referral", headers=second, json={"code": "meetup-10"}
    )
    assert taken.json()["can_hunt"] is True
    assert (await club.client.get("/v1/invites/MEETUP-10")).json()["available"] is False
    assert (await join(club, "third@example.com", "MEETUP-10")).status_code == 403

    (line,) = (await club.client.get("/v1/admin/referral-codes", headers=boss)).json()["items"]
    assert (line["max_uses"], line["uses"], line["used_by"]) == (2, 2, "first@example.com")
    assert sorted(user["email"] for user in line["users"]) == [
        "first@example.com",
        "second@example.com",
    ]


async def test_an_admin_deletes_codes_and_whoever_joined_keeps_their_access(
    club: Platform,
) -> None:
    boss = await account(club, "boss@example.com")
    await promote(club, "boss@example.com")

    async def make(**body: Any) -> list[str]:
        made = await club.client.post("/v1/admin/referral-codes", headers=boss, json=body)
        return [item["code"] for item in made.json()]

    async def left() -> set[str]:
        page = await club.client.get(
            "/v1/admin/referral-codes", headers=boss, params={"limit": 100}
        )
        return {item["code"] for item in page.json()["items"]}

    (spent,) = await make(code="SPENT-ONE")
    (shared,) = await make(code="SHARED-ONE", max_uses=5)
    (sponsoring,) = await make(code="SPONSOR-ONE", kind="sponsor")
    spare = await make(count=3)
    hunter = await join(club, "hunter@example.com", spent)
    sharer = await join(club, "sharer@example.com", shared)
    lister = await join(club, "lister@example.com", sponsoring)

    # Not for anyone else, and not for a code that is not there.
    nobody = await account(club, "nobody@example.com")
    assert (
        await club.client.delete(f"/v1/admin/referral-codes/{spent}", headers=nobody)
    ).status_code == 404
    assert (
        await club.client.delete("/v1/admin/referral-codes/NOT-THERE", headers=boss)
    ).status_code == 404
    assert (
        await club.client.delete("/v1/admin/referral-codes", headers=boss, params={"which": "all"})
    ).status_code == 422

    # One at a time: a code part-way through its uses goes, and its link with it.
    gone = await club.client.delete(f"/v1/admin/referral-codes/{shared}", headers=boss)
    assert gone.json() == {"deleted": 1}
    assert (await club.client.get(f"/v1/invites/{shared}")).status_code == 404
    # Every used-up code at once: the unused ones stay.
    cleared = await club.client.delete(
        "/v1/admin/referral-codes", headers=boss, params={"which": "used_up"}
    )
    assert cleared.json() == {"deleted": 2}
    assert await left() == set(spare)
    # And every unused one.
    cleared = await club.client.delete(
        "/v1/admin/referral-codes", headers=boss, params={"which": "unused"}
    )
    assert cleared.json() == {"deleted": 3}
    assert await left() == set()

    # Whoever joined with a deleted code can still do what it let them do.
    for joined, sponsors in ((hunter, False), (sharer, False), (lister, True)):
        headers = {"Authorization": f"Bearer {joined.json()['api_key']}"}
        me = (await club.client.get("/v1/account", headers=headers)).json()
        assert (me["can_hunt"], me["can_sponsor"]) == (True, sponsors)

    log = (await club.client.get("/v1/admin/actions", headers=boss)).json()["items"]
    assert [item["detail"] for item in log if item["action"] == "codes_deleted"] == [
        "3 unused code(s)",
        "2 used-up code(s)",
        f"the code {shared}",
    ]


async def test_an_admin_bans_an_account_and_sets_and_refills_its_tokens(club: Platform) -> None:
    master = await owner(club)
    boss = await account(club, "boss@example.com")
    await promote(club, "boss@example.com")
    await club.client.post("/v1/admin/referral-codes", headers=boss, json={"code": "COME-IN"})
    joined = await join(club, "hunter@example.com", "COME-IN")
    hunter = {"Authorization": f"Bearer {joined.json()['api_key']}"}
    bounty = await (await club.sponsor()).live()

    async def person(email: str) -> dict[str, Any]:
        page = await club.client.get("/v1/admin/people", headers=boss, params={"q": email})
        (found,) = page.json()["items"]
        return found  # type: ignore[no-any-return]

    async def change(email: str, **body: Any) -> httpx.Response:
        who = await person(email)
        return await club.client.patch(f"/v1/admin/people/{who['id']}", headers=boss, json=body)

    async def me() -> dict[str, Any]:
        return (await club.client.get("/v1/account", headers=hunter)).json()  # type: ignore[no-any-return]

    # A new account starts on the tier's allowance, which is small.
    starter = club.app.state.settings.starter_credits
    assert starter == 5
    seen = await person("hunter@example.com")
    assert (seen["banned"], seen["credit_limit"]) == (False, None)
    assert (seen["credits_balance"], seen["credits_cap"]) == (starter, starter)

    # A limit of its own, in place of the tier's.
    limited = await change("hunter@example.com", credit_limit=1)
    assert (limited.json()["credit_limit"], limited.json()["credits_cap"]) == (1, 1)
    credits_now = (await me())["credits"]
    assert (credits_now["cap"], credits_now["balance"], credits_now["next_cap"]) == (1, 1, None)
    assert (await change("hunter@example.com", credit_limit=-1)).status_code == 422
    # With a limit below what an attack costs, there is nothing to attack with.
    assert (await attack(club, bounty, hunter)).status_code == 402
    # Null puts it back on the tier's.
    back = await change("hunter@example.com", credit_limit=None)
    assert (back.json()["credit_limit"], back.json()["credits_cap"]) == (None, starter)

    # It spends some, and a refill forgets what it spent.
    assert (await attack(club, bounty, hunter)).status_code == 202
    await club.work()
    spent = (await me())["credits"]["balance"]
    assert spent < starter
    refilled = await change("hunter@example.com", reset_credits=True)
    assert refilled.json()["credits_balance"] == starter
    assert (await me())["credits"]["balance"] == starter

    # Banned: it can neither attack nor list agents, and a code does not help.
    banned = await change("hunter@example.com", banned=True)
    assert (banned.json()["banned"], banned.json()["can_hunt"]) == (True, False)
    mine = await me()
    assert (mine["banned"], mine["can_hunt"], mine["can_sponsor"]) == (True, False, False)
    refused = await attack(club, bounty, hunter)
    assert (refused.status_code, refused.json()["detail"]) == (403, "this account has been banned")
    # The ban is lifted and it is as it was.
    assert (await change("hunter@example.com", banned=False)).json()["can_hunt"] is True
    assert (await attack(club, bounty, hunter)).status_code == 202

    # Nobody who runs the platform is locked out of it from here.
    assert (await change("boss@example.com", banned=True)).status_code == 403
    assert (await change(OWNER, banned=True)).status_code == 403
    await club.client.patch(
        f"/v1/admin/people/{(await person('boss@example.com'))['id']}",
        headers=master,
        json={"is_admin": False},
    )

    log = (await club.client.get("/v1/admin/actions", headers=master)).json()["items"]
    done = [(item["action"], item["detail"]) for item in log if item["target_email"]]
    assert ("banned=true", "hunter@example.com") in done
    assert ("banned=false", "hunter@example.com") in done
    assert ("credit_limit", "1") in done
    assert ("credit_limit", "the tier's") in done
    assert ("credits_reset", "hunter@example.com") in done
