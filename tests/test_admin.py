"""Admins: who runs the platform, and what they can do that nobody else can."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import update

from agentbounty.models import Sponsor
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
        listed = (await browser.get("/v1/admin/people")).json()
        mine = next(person for person in listed if person["email"] == OWNER)
        assert (mine["is_admin"], mine["is_owner"]) == (True, True)


async def test_an_admin_makes_ambassadors_and_admins_and_grants_access(club: Platform) -> None:
    boss = await account(club, "boss@example.com")
    await promote(club, "boss@example.com")
    friend = await account(club, "friend@example.com")
    await club.client.patch("/v1/account", headers=friend, json={"name": "Grace Hopper"})

    people = (await club.client.get("/v1/admin/people", headers=boss)).json()
    assert [person["email"] for person in people] == ["friend@example.com", "boss@example.com"]
    found = (await club.client.get("/v1/admin/people", headers=boss, params={"q": "grace"})).json()
    (grace,) = found
    assert (grace["is_ambassador"], grace["is_admin"], grace["can_hunt"]) == (False, False, False)
    assert (
        await club.client.get("/v1/admin/people", headers=boss, params={"q": "%"})
    ).json() == people

    async def change(person: dict[str, Any], **values: Any) -> httpx.Response:
        return await club.client.patch(
            f"/v1/admin/people/{person['id']}", headers=boss, json=values
        )

    # Ambassador: they can now make codes of their own.
    assert (await change(grace, is_ambassador=True)).json()["is_ambassador"] is True
    made = await club.client.post("/v1/referral-codes", headers=friend, json={"max_uses": 2})
    assert made.status_code == 201
    assert (await change(grace, is_ambassador=False)).json()["is_ambassador"] is False
    assert (
        await club.client.post("/v1/referral-codes", headers=friend, json={"max_uses": 2})
    ).status_code == 403

    # Access to the bounties, with no referral code.
    assert (await change(grace, can_hunt=True)).json()["can_hunt"] is True
    assert (await club.client.get("/v1/account", headers=friend)).json()["can_hunt"] is True
    assert (await change(grace, can_hunt=False)).status_code == 422

    # Admin: they can now use this API themselves.
    assert (await change(grace, is_admin=True)).json()["is_admin"] is True
    assert (await club.client.get("/v1/admin/people", headers=friend)).status_code == 200
    assert (await change(grace, is_admin=False)).json()["is_admin"] is False
    assert (await club.client.get("/v1/admin/people", headers=friend)).status_code == 404

    # An admin cannot lock themselves out, and a missing account is said to be missing.
    me = next(person for person in people if person["email"] == "boss@example.com")
    locked = await change(me, is_admin=False)
    assert (locked.status_code, locked.json()["detail"]) == (
        409,
        "you cannot remove your own admin access",
    )
    gone = await change({"id": "00000000-0000-0000-0000-000000000000"}, is_admin=True)
    assert gone.status_code == 404
    assert (await change(grace, anything="else")).status_code == 422


async def test_an_admin_makes_codes_of_any_size_and_an_invite_link_grants_access(
    club: Platform,
) -> None:
    boss = await account(club, "boss@example.com")
    await promote(club, "boss@example.com")
    await club.client.patch("/v1/account", headers=boss, json={"name": "The Boss"})

    made = await club.client.post(
        "/v1/admin/referral-codes", headers=boss, json={"max_uses": 500, "code": "launch-day"}
    )
    assert made.status_code == 201
    assert made.json() == {
        "code": "LAUNCH-DAY",
        "max_uses": 500,
        "used": 0,
        "owner_email": "boss@example.com",
        "created_at": made.json()["created_at"],
    }
    again = await club.client.post(
        "/v1/admin/referral-codes", headers=boss, json={"max_uses": 1, "code": "LAUNCH-DAY"}
    )
    assert again.status_code == 409
    generated = await club.client.post("/v1/admin/referral-codes", headers=boss, json={})
    assert (generated.json()["max_uses"], len(generated.json()["code"])) == (10, 9)
    for bad in ({"max_uses": 0}, {"max_uses": 10_001}, {"code": "has spaces"}, {"code": "abc"}):
        refused = await club.client.post("/v1/admin/referral-codes", headers=boss, json=bad)
        assert refused.status_code == 422, bad

    # What the link shows to whoever follows it, signed in or not.
    shown = await club.client.get("/v1/invites/launch-day")
    assert shown.json() == {"code": "LAUNCH-DAY", "remaining": 500, "invited_by": "The Boss"}
    assert (await club.client.get("/v1/invites/NOPE-NOPE")).status_code == 404

    # Someone follows it and joins: they can hunt at once.
    newcomer = await join(club, "new@example.com", "LAUNCH-DAY")
    headers = {"Authorization": f"Bearer {newcomer.json()['api_key']}"}
    assert (await club.client.get("/v1/account", headers=headers)).json()["can_hunt"] is True
    # Someone who already had an account follows it and accepts.
    old = await account(club, "old@example.com")
    accepted = await club.client.post(
        "/v1/account/referral", headers=old, json={"code": "LAUNCH-DAY"}
    )
    assert accepted.json()["can_hunt"] is True

    listed = (await club.client.get("/v1/admin/referral-codes", headers=boss)).json()
    assert {(item["code"], item["used"]) for item in listed} == {
        ("LAUNCH-DAY", 2),
        (generated.json()["code"], 0),
    }
    assert (await club.client.get("/v1/invites/LAUNCH-DAY")).json()["remaining"] == 498


def test_helpers_are_importable() -> None:
    assert callable(attack)
