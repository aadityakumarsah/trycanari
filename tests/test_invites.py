"""Invite-only mode: referral codes to join, and credits to attack."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select, update

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


async def operator_code(platform: Platform, uses: int = 5, code: str | None = None) -> str:
    async with platform.sessions.begin() as session:
        return (await credits.create_code(session, None, uses, code)).code


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


async def test_joining_needs_a_referral_code_with_a_use_left(club: Platform) -> None:
    assert (await club.client.get("/v1/auth/providers")).json()["invite_only"] is True
    refused = await join(club, "a@example.com", None)
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "a referral code is needed to join",
    )
    wrong = await join(club, "a@example.com", "NOPE-NOPE")
    assert (wrong.status_code, wrong.json()["detail"]) == (403, "that referral code is not valid")

    code = await operator_code(club, uses=2)
    # Typed in lower case with spaces around it, it is still the same code.
    assert (await join(club, "a@example.com", f"  {code.lower()} ")).status_code == 201
    assert (await join(club, "b@example.com", code)).status_code == 201
    spent = await join(club, "c@example.com", code)
    assert (spent.status_code, spent.json()["detail"]) == (
        403,
        "that referral code has been used up",
    )
    # A sign-up that fails for another reason does not use the code up.
    other = await operator_code(club, uses=1)
    assert (await join(club, "a@example.com", other)).status_code == 409
    assert (await join(club, "d@example.com", other)).status_code == 201


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
    sponsor = await member(club, "sponsor@example.com", code)
    hunter = await member(club, "hunter@example.com", code)
    from .platform import Sponsor as SponsorClient

    bounty_id = await SponsorClient(club.client, sponsor).live()

    stranger = await attack(club, bounty_id, None)
    assert (stranger.status_code, stranger.json()["detail"]) == (
        401,
        "sign in to attack: this platform is invite-only",
    )

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
    code = await operator_code(club)
    hunter = await member(club, "hunter@example.com", code)
    from .platform import Sponsor as SponsorClient

    bounty_id = await SponsorClient(
        club.client, await member(club, "sponsor@example.com", code)
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
    code = await operator_code(club)
    hunter = await member(club, "hunter@example.com", code)
    from .platform import Sponsor as SponsorClient

    sponsor = SponsorClient(club.client, await member(club, "sponsor@example.com", code))
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


async def test_only_ambassadors_make_referral_codes(club: Platform) -> None:
    code = await operator_code(club)
    someone = await member(club, "someone@example.com", code)
    assert (await club.client.get("/v1/referral-codes", headers=someone)).json() == []
    refused = await club.client.post("/v1/referral-codes", json={"max_uses": 2}, headers=someone)
    assert (refused.status_code, refused.json()["detail"]) == (
        403,
        "only ambassadors can make referral codes",
    )
    assert (await club.client.get("/v1/referral-codes")).status_code == 401

    async with club.sessions.begin() as session:
        await session.execute(
            update(Sponsor).where(Sponsor.email == "someone@example.com").values(is_ambassador=True)
        )
    made = await club.client.post("/v1/referral-codes", json={"max_uses": 2}, headers=someone)
    assert made.status_code == 201
    theirs = made.json()
    assert (theirs["max_uses"], theirs["used"], len(theirs["code"])) == (2, 0, 9)
    too_many = await club.client.post("/v1/referral-codes", json={"max_uses": 26}, headers=someone)
    assert too_many.status_code == 422

    # The code works, and its owner sees it being used. They see only their own codes.
    await member(club, "friend@example.com", theirs["code"])
    listed = (await club.client.get("/v1/referral-codes", headers=someone)).json()
    assert [(item["code"], item["used"]) for item in listed] == [(theirs["code"], 1)]


async def test_google_sign_in_needs_the_code_only_for_someone_new(
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
        code = await operator_code(platform, uses=1)

        def browser() -> httpx.AsyncClient:
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=platform.app), base_url="http://test"
            )

        async def sign_in(client: httpx.AsyncClient, referral: str | None) -> str:
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
            location: str = returned.headers["location"]
            return location

        # New, and no code: refused, and no account is made.
        assert await sign_in(browser(), None) == "http://test/login?error=google_invite"
        assert await sign_in(browser(), "WRONG") == "http://test/login?error=google_invite"
        # New, with a code: in.
        assert await sign_in(browser(), code) == "http://test/dashboard"
        # Back again later, with no code at all: still in, and the code was used once.
        assert await sign_in(browser(), None) == "http://test/dashboard"
        async with platform.sessions() as session:
            assert await credits.uses_of(session, code) == 1
        # The code is spent, so the next stranger is refused.
        stand_in.account = {"sub": "another", "email": "b@example.com", "email_verified": True}
        assert await sign_in(browser(), code) == "http://test/login?error=google_invite"


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
