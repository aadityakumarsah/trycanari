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


async def test_an_admin_makes_ambassadors_and_admins_and_grants_access(club: Platform) -> None:
    boss = await account(club, "boss@example.com")
    await promote(club, "boss@example.com")
    friend = await account(club, "friend@example.com")
    await club.client.patch("/v1/account", headers=friend, json={"name": "Grace Hopper"})

    async def listed(**params: Any) -> dict[str, Any]:
        page: dict[str, Any] = (
            await club.client.get("/v1/admin/people", headers=boss, params=params)
        ).json()
        return page

    everyone = await listed()
    assert everyone["total"] == 2
    assert [person["email"] for person in everyone["items"]] == [
        "friend@example.com",
        "boss@example.com",
    ]
    (grace,) = (await listed(q="grace"))["items"]
    assert (grace["is_ambassador"], grace["is_admin"], grace["can_hunt"]) == (False, False, False)
    # Search text is only ever text: a wildcard matches nothing by itself.
    assert (await listed(q="%"))["total"] == 0
    assert (await listed(q="_"))["total"] == 0
    # Pages.
    first = await listed(limit=1)
    second = await listed(limit=1, offset=1)
    assert (first["total"], len(first["items"]), second["items"][0]["email"]) == (
        2,
        1,
        "boss@example.com",
    )
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

    # An admin cannot lock themselves out, and a missing account is said to be missing.
    me = next(person for person in everyone["items"] if person["email"] == "boss@example.com")
    locked = await change(me, is_admin=False)
    assert (locked.status_code, locked.json()["detail"]) == (
        409,
        "you cannot remove your own admin access",
    )
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
            "used_by": None,
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
    assert shown.json() == {"code": "LAUNCH-DAY", "available": True, "invited_by": "The Boss"}
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


def test_helpers_are_importable() -> None:
    assert callable(attack)
