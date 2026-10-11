"""Invite-only mode: referral codes to join, and credits to attack."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from agentbounty.models import Attempt, AttemptState, Sponsor
from agentbounty.services import credits
from agentbounty.services.harness import EntryPoint, SlotFill
from agentbounty.settings import Settings

from .platform import HUNTER, STRONG, WEAK, Platform, platform_on
from .test_google import CLIENT_ID, CLIENT_SECRET, Google, come_back, leave_for_google


@pytest.fixture
async def club(service_settings: Settings) -> AsyncIterator[Platform]:
    """The platform in invite-only mode, with a small allowance."""
    settings = service_settings.model_copy(
        update={"invite_only": True, "starter_credits": 3, "proven_credits": 10}
    )
    async with platform_on(settings) as platform:
        yield platform


async def operator_code(platform: Platform, code: str | None = None) -> str:
    """A fresh single-use code, as the operator would make one."""
    async with platform.sessions.begin() as session:
        (made,) = await credits.create_codes(session, None, 1, code)
        return made.code


async def join(platform: Platform, email: str, code: str | None) -> httpx.Response:
    body: dict[str, Any] = {"email": email}
    if code is not None:
        body["referral_code"] = code
    return await platform.client.post("/v1/sponsors", json=body)


async def member(platform: Platform, email: str, code: str) -> dict[str, str]:
    joined = await join(platform, email, code)
    assert joined.status_code == 201, joined.text
    return {"Authorization": f"Bearer {joined.json()['api_key']}"}


async def attack(
    platform: Platform, bounty_id: str, headers: dict[str, str] | None, content: str = WEAK
) -> httpx.Response:
    body = {"payout_address": HUNTER, "fills": [{"slot": "user_message", "content": content}]}
    return await platform.client.post(
        f"/v1/bounties/{bounty_id}/attempts", json=body, headers=headers or {}
    )


def test_what_an_attack_costs_grows_with_its_size() -> None:
    def cost(*sizes: int) -> int:
        return credits.cost_of([SlotFill(EntryPoint.USER_MESSAGE, "x" * size) for size in sizes])

    assert [cost(1), cost(2_000), cost(2_001), cost(8_000), cost(8_001), cost(40_000)] == [
        1,
        1,
        2,
        2,
        4,
        4,
    ]
    # Both entry points count together.
    assert cost(1_500, 1_500) == 2


async def test_anyone_may_join_and_a_referral_code_lets_exactly_one_of_them_hunt(
    club: Platform,
) -> None:
    assert (await club.client.get("/v1/auth/providers")).json()["invite_only"] is True
    # No code: an account all the same. It just cannot take part in bounties yet.
    joined = await join(club, "a@example.com", None)
    assert joined.status_code == 201
    a = {"Authorization": f"Bearer {joined.json()['api_key']}"}
    assert (await club.client.get("/v1/account", headers=a)).json()["can_hunt"] is False

    async def redeem(headers: dict[str, str], code: str) -> httpx.Response:
        return await club.client.post("/v1/account/referral", json={"code": code}, headers=headers)

    wrong = await redeem(a, "NOPE-NOPE")
    assert (wrong.status_code, wrong.json()["detail"]) == (403, "that referral code is not valid")
    code = await operator_code(club)
    # Typed in lower case with spaces around it, it is still the same code.
    redeemed = await redeem(a, f"  {code.lower()} ")
    assert (redeemed.status_code, redeemed.json()["can_hunt"]) == (200, True)
    again = await redeem(a, await operator_code(club))
    assert (again.status_code, again.json()["detail"]) == (
        409,
        "you already have access to the bounties",
    )

    # The code is spent: nobody else can use it, at sign-up or afterwards.
    late = await join(club, "b@example.com", None)
    b = {"Authorization": f"Bearer {late.json()['api_key']}"}
    spent = await redeem(b, code)
    assert (spent.status_code, spent.json()["detail"]) == (
        403,
        "that referral code has already been used",
    )
    assert (await join(club, "c@example.com", code)).status_code == 403
    assert (await join(club, "d@example.com", "NOPE-NOPE")).status_code == 403
    assert (await club.client.post("/v1/account/referral", json={"code": code})).status_code == 401
    # A code given at sign-up is redeemed there and then.
    e = await member(club, "e@example.com", await operator_code(club))
    assert (await club.client.get("/v1/account", headers=e)).json()["can_hunt"] is True


async def test_two_people_cannot_share_a_code_even_at_the_same_instant(club: Platform) -> None:
    code = await operator_code(club)
    people = []
    for name in ("x", "y", "z"):
        joined = await join(club, f"{name}@example.com", None)
        people.append({"Authorization": f"Bearer {joined.json()['api_key']}"})

    import asyncio

    answers = await asyncio.gather(
        *(
            club.client.post("/v1/account/referral", json={"code": code}, headers=headers)
            for headers in people
        )
    )
    assert sorted(answer.status_code for answer in answers) == [200, 403, 403]
    async with club.sessions() as session:
        holders = await session.scalar(
            select(func.count()).select_from(Sponsor).where(Sponsor.referral_code == code)
        )
    assert holders == 1
    # And the database itself would refuse a second holder, whatever the code above did.
    with pytest.raises(IntegrityError):
        async with club.sessions.begin() as session:
            await session.execute(
                update(Sponsor).where(Sponsor.email == "y@example.com").values(referral_code=code)
            )


async def test_without_invite_only_nothing_changes(platform: Platform) -> None:
    assert (await join(platform, "open@example.com", None)).status_code == 201
    sponsor = await platform.sponsor()
    bounty_id = await sponsor.live()
    # No account, no credits, as before.
    anonymous = await attack(platform, bounty_id, None)
    assert anonymous.status_code == 202
    # An account that attacks is remembered against the attempt, but is not charged.
    signed = await attack(platform, bounty_id, sponsor.headers)
    assert signed.status_code == 202
    async with platform.sessions() as session:
        rows = (await session.execute(select(Attempt.account_id, Attempt.cost_credits))).all()
    assert sorted(cost for _, cost in rows) == [0, 0]
    assert sum(1 for account, _ in rows if account is not None) == 1


async def test_an_attack_needs_an_account_and_spends_its_credits(club: Platform) -> None:
    code = await operator_code(club)
    sponsor = await member(club, "sponsor@example.com", await operator_code(club))
    hunter = await member(club, "hunter@example.com", await operator_code(club))
    from .platform import Sponsor as SponsorClient

    bounty_id = await SponsorClient(club.client, sponsor).live()

    stranger = await attack(club, bounty_id, None)
    assert (stranger.status_code, stranger.json()["detail"]) == (
        401,
        "sign in to attack: this platform is invite-only",
    )
    # An account is not enough: it has to have redeemed a referral code.
    uninvited = await join(club, "uninvited@example.com", None)
    outsider = {"Authorization": f"Bearer {uninvited.json()['api_key']}"}
    refused = await attack(club, bounty_id, outsider)
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "enter a referral code to take part in bounties",
    )
    await club.client.post("/v1/account/referral", json={"code": code}, headers=outsider)
    assert (await attack(club, bounty_id, outsider)).status_code == 202

    account = (await club.client.get("/v1/account", headers=hunter)).json()
    assert account["credits"] == {
        "balance": 3,
        "cap": 3,
        "window_hours": 24,
        "tier": "Starter",
        "next_tier": "Proven",
        "next_cap": 10,
        "costs": [
            {"up_to_characters": 2_000, "credits": 1},
            {"up_to_characters": 8_000, "credits": 2},
            {"up_to_characters": 40_000, "credits": 4},
        ],
    }
    assert (account["invite_only"], account["is_ambassador"], account["verified_breaks"]) == (
        True,
        False,
        0,
    )

    # A long attack costs two, a short one costs one, and then there is nothing left.
    assert (await attack(club, bounty_id, hunter, "x" * 3_000)).status_code == 202
    assert (await attack(club, bounty_id, hunter)).status_code == 202
    broke = await attack(club, bounty_id, hunter)
    assert broke.status_code == 402
    assert broke.json()["detail"].startswith("this attack costs 1 credits and you have 0.")
    assert (await club.client.get("/v1/account", headers=hunter)).json()["credits"]["balance"] == 0
    # Someone else's allowance is their own.
    assert (await attack(club, bounty_id, sponsor)).status_code == 202

    # An attack that is refused for another reason costs nothing.
    async with club.sessions.begin() as session:
        await session.execute(update(Attempt).values(cost_credits=0))
    bad = {"payout_address": "t1transparent", "fills": [{"slot": "user_message", "content": "x"}]}
    refused = await club.client.post(f"/v1/bounties/{bounty_id}/attempts", json=bad, headers=hunter)
    assert refused.status_code == 422
    assert (await club.client.get("/v1/account", headers=hunter)).json()["credits"]["balance"] == 3


async def test_the_allowance_refills_and_forgives_what_the_platform_failed(
    club: Platform,
) -> None:
    hunter = await member(club, "hunter@example.com", await operator_code(club))
    from .platform import Sponsor as SponsorClient

    bounty_id = await SponsorClient(
        club.client, await member(club, "sponsor@example.com", await operator_code(club))
    ).live()
    for _ in range(3):
        assert (await attack(club, bounty_id, hunter)).status_code == 202

    async def balance() -> int:
        account = await club.client.get("/v1/account", headers=hunter)
        left: int = account.json()["credits"]["balance"]
        return left

    assert await balance() == 0
    ids = []
    async with club.sessions() as session:
        ids = list(await session.scalars(select(Attempt.id).order_by(Attempt.submitted_at)))
    # One replay could not be completed: that is on the platform, so it is not counted.
    async with club.sessions.begin() as session:
        await session.execute(
            update(Attempt).where(Attempt.id == ids[0]).values(state=AttemptState.ERROR)
        )
    assert await balance() == 1
    # One attempt is now more than a day old: it has aged out.
    async with club.sessions.begin() as session:
        await session.execute(
            update(Attempt)
            .where(Attempt.id == ids[1])
            .values(submitted_at=Attempt.submitted_at - credits.WINDOW)
        )
    assert await balance() == 2


async def test_a_verified_break_raises_the_allowance(club: Platform) -> None:
    hunter = await member(club, "hunter@example.com", await operator_code(club))
    from .platform import Sponsor as SponsorClient

    sponsor = SponsorClient(
        club.client, await member(club, "sponsor@example.com", await operator_code(club))
    )
    bounty_id = await sponsor.funded(club)
    accepted = await attack(club, bounty_id, hunter, STRONG)
    assert accepted.status_code == 202
    assert (await club.poll(accepted.json()))["verdict"] == "verified"

    account = (await club.client.get("/v1/account", headers=hunter)).json()
    assert account["verified_breaks"] == 1
    assert account["credits"] == {
        **account["credits"],
        "tier": "Proven",
        "cap": 10,
        "balance": 9,
        "next_tier": None,
        "next_cap": None,
    }


async def test_only_ambassadors_make_referral_codes_and_only_so_many(club: Platform) -> None:
    someone = await member(club, "someone@example.com", await operator_code(club))
    assert (await club.client.get("/v1/referral-codes", headers=someone)).json() == []
    refused = await club.client.post("/v1/referral-codes", json={"count": 2}, headers=someone)
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "only ambassadors can make referral codes",
    )
    assert (await club.client.get("/v1/referral-codes")).status_code == 401

    async with club.sessions.begin() as session:
        await session.execute(
            update(Sponsor).where(Sponsor.email == "someone@example.com").values(is_ambassador=True)
        )
    made = await club.client.post("/v1/referral-codes", json={"count": 3}, headers=someone)
    assert made.status_code == 201
    codes = [item["code"] for item in made.json()]
    assert len(set(codes)) == 3
    assert all(
        len(code) == 9 and item["used"] is False
        for code, item in zip(codes, made.json(), strict=True)
    )
    for bad in ({"count": 0}, {"count": 11}, {"max_uses": 5}):
        assert (
            await club.client.post("/v1/referral-codes", json=bad, headers=someone)
        ).status_code == 422

    # Each works once, and its owner sees which have gone. They see only their own codes.
    await member(club, "friend@example.com", codes[0])
    assert (await join(club, "second@example.com", codes[0])).status_code == 403
    listed = (await club.client.get("/v1/referral-codes", headers=someone)).json()
    assert {(item["code"], item["used"]) for item in listed} == {
        (codes[0], True),
        (codes[1], False),
        (codes[2], False),
    }

    # At most 25 unused at a time: two are out, so 23 more fit and a 24th does not.
    for size in (10, 10, 3):
        assert (
            await club.client.post("/v1/referral-codes", json={"count": size}, headers=someone)
        ).status_code == 201
    full = await club.client.post("/v1/referral-codes", json={"count": 1}, headers=someone)
    assert full.status_code == 409
    assert full.json()["detail"].startswith("you can hold 25 unused codes at a time; you have 25.")
    # Using one up makes room for one more.
    await member(club, "third@example.com", codes[1])
    assert (
        await club.client.post("/v1/referral-codes", json={"count": 1}, headers=someone)
    ).status_code == 201


async def test_google_sign_in_lets_anyone_join_and_redeems_a_code_they_arrived_with(
    service_settings: Settings,
) -> None:
    settings = service_settings.model_copy(
        update={
            "invite_only": True,
            "google_client_id": CLIENT_ID,
            "google_client_secret": SecretStr(CLIENT_SECRET),
            "public_url": "http://test",
        }
    )
    async with platform_on(settings) as platform:
        stand_in = Google()
        platform.app.state.google_transport = httpx.MockTransport(stand_in.handle)
        code = await operator_code(platform)

        async def sign_in(referral: str | None) -> dict[str, Any]:
            client = httpx.AsyncClient(
                transport=httpx.ASGITransport(app=platform.app), base_url="http://test"
            )
            sent = await client.get(
                "/v1/auth/google/start", params={"code": referral} if referral else {}
            )
            assert sent.status_code == 302
            from urllib.parse import parse_qs, urlsplit

            query = parse_qs(urlsplit(sent.headers["location"]).query)
            stand_in.challenge, stand_in.nonce = query["code_challenge"][0], query["nonce"][0]
            # The referral code never travels to Google.
            assert code not in sent.headers["location"]
            returned = await come_back(client, code="one-time", state=query["state"][0])
            assert returned.headers["location"] == "http://test/dashboard"
            account: dict[str, Any] = (await client.get("/v1/account")).json()
            return account

        # New, no code: in, with a profile, but not hunting yet.
        assert (await sign_in(None))["can_hunt"] is False
        # Someone else arrives with a code that does not exist: in all the same.
        stand_in.account = {"sub": "two", "email": "two@example.com", "email_verified": True}
        assert (await sign_in("WRONG"))["can_hunt"] is False
        # A third arrives through an invite link: in, and hunting.
        stand_in.account = {"sub": "three", "email": "three@example.com", "email_verified": True}
        assert (await sign_in(code))["can_hunt"] is True
        assert (await platform.client.get(f"/v1/invites/{code}")).json()["available"] is False


async def test_a_profile_is_filled_in_a_field_at_a_time(platform: Platform) -> None:
    sponsor = await platform.sponsor()
    empty = await sponsor.call("GET", "/v1/account")
    fields = ("name", "github", "company", "role", "location", "website", "bio")
    assert [empty[field] for field in fields] == [None] * 7
    # Off an invite-only platform everyone can hunt.
    assert empty["can_hunt"] is True

    saved = await sponsor.call(
        "PATCH",
        "/v1/account",
        json={
            "name": "  Ada   Lovelace ",
            "github": "https://github.com/ada-lovelace/",
            "company": "Analytical Engines",
            "website": "https://example.com/ada",
            "bio": "  First programmer.\nStill debugging.  ",
        },
    )
    assert [saved[field] for field in fields] == [
        "Ada Lovelace",
        "ada-lovelace",
        "Analytical Engines",
        None,
        None,
        "https://example.com/ada",
        "First programmer.\nStill debugging.",
    ]
    # Only what is sent changes, and an empty string clears a field.
    changed = await sponsor.call(
        "PATCH", "/v1/account", json={"github": "@ada", "company": "", "role": "Engineer"}
    )
    assert (changed["name"], changed["github"], changed["company"], changed["role"]) == (
        "Ada Lovelace",
        "ada",
        None,
        "Engineer",
    )

    for bad in (
        {"github": "not a username"},
        {"github": "-leading"},
        {"github": "a" * 40},
        {"website": "example.com"},
        {"website": "javascript:alert(1)"},
        {"name": ""},
        {"bio": "x" * 401},
        {"unknown": "field"},
    ):
        await sponsor.call("PATCH", "/v1/account", 422, json=bad)
    assert (await sponsor.call("GET", "/v1/account"))["github"] == "ada"


def test_unused_helper_is_importable() -> None:
    assert callable(leave_for_google)


async def test_an_account_has_a_name_and_a_picture(service_settings: Settings) -> None:
    settings = service_settings.model_copy(
        update={
            "google_client_id": CLIENT_ID,
            "google_client_secret": SecretStr(CLIENT_SECRET),
            "public_url": "http://test",
        }
    )
    async with platform_on(settings) as platform:
        stand_in = Google()
        platform.app.state.google_transport = httpx.MockTransport(stand_in.handle)
        picture = "https://lh3.googleusercontent.com/a/abc=s96-c"
        stand_in.claims = {"name": "  Ada Lovelace ", "picture": picture}
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=platform.app), base_url="http://test"
        )

        async def sign_in() -> dict[str, Any]:
            query = await leave_for_google(client, stand_in)
            await come_back(client, code="one-time", state=query["state"])
            account: dict[str, Any] = (await client.get("/v1/account")).json()
            return account

        account = await sign_in()
        assert (account["name"], account["picture_url"]) == ("Ada Lovelace", picture)
        assert account["google_linked"] is True

        # A picture from anywhere but Google is not kept: the page would not load it.
        stand_in.claims = {"name": "Ada L.", "picture": "https://evil.example/x.png"}
        account = await sign_in()
        assert account["picture_url"] is None
        # ...and Google's name does not replace one already on the account.
        assert account["name"] == "Ada Lovelace"

        same_origin = {"Origin": "http://test", "Sec-Fetch-Site": "same-origin"}
        renamed = await client.patch(
            "/v1/account", json={"name": "  Countess   Ada "}, headers=same_origin
        )
        assert renamed.json()["name"] == "Countess Ada"
        blank = await client.patch("/v1/account", json={"name": "   "}, headers=same_origin)
        assert blank.status_code == 422
        assert (await sign_in())["name"] == "Countess Ada"

    # An account made with an email starts with neither.
    async with platform_on(service_settings) as platform:
        sponsor = await platform.sponsor()
        account = await sponsor.call("GET", "/v1/account")
        assert (account["name"], account["picture_url"]) == (None, None)
        assert account["google_linked"] is False
