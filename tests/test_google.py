"""Sign in with Google: the code flow, with Google itself replaced by a stand-in."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from agentbounty.api import google
from agentbounty.settings import Settings

from .platform import Platform, platform_on

CLIENT_ID = "1234-test.apps.googleusercontent.com"
CLIENT_SECRET = "GOCSPX-test-secret"
SAME_ORIGIN = {"Origin": "http://test", "Sec-Fetch-Site": "same-origin"}


def id_token(claims: dict[str, Any]) -> str:
    def part(value: dict[str, Any]) -> str:
        return base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()

    return f"{part({'alg': 'RS256'})}.{part(claims)}.signature"


class Google:
    """Google's token endpoint. It remembers what the browser was sent off with."""

    def __init__(self) -> None:
        self.account: dict[str, Any] = {
            "sub": "google-user-1",
            "email": "Ada@Example.com",
            "email_verified": True,
        }
        self.claims: dict[str, Any] = {}
        self.status = 200
        self.challenge = ""
        self.nonce = ""
        self.requests: list[dict[str, str]] = []

    def handle(self, request: httpx.Request) -> httpx.Response:
        assert str(request.url) == google.TOKEN_URL
        form = {key: values[0] for key, values in parse_qs(request.content.decode()).items()}
        self.requests.append(form)
        digest = hashlib.sha256(form["code_verifier"].encode()).digest()
        proves = base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == self.challenge
        if self.status != 200 or not proves or form["client_secret"] != CLIENT_SECRET:
            return httpx.Response(400 if self.status == 200 else self.status, json={})
        claims = {
            "iss": "https://accounts.google.com",
            "aud": CLIENT_ID,
            "exp": int(time.time()) + 300,
            "nonce": self.nonce,
            **self.account,
            **self.claims,
        }
        return httpx.Response(200, json={"id_token": id_token(claims)})


@pytest.fixture
async def site(service_settings: Settings) -> AsyncIterator[tuple[Platform, Google]]:
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
        yield platform, stand_in


def browser(platform: Platform) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=platform.app), base_url="http://test"
    )


async def leave_for_google(client: httpx.AsyncClient, stand_in: Google) -> dict[str, str]:
    """Press the button: the query the browser carries to Google."""
    sent = await client.get("/v1/auth/google/start")
    assert sent.status_code == 302, sent.text
    target = urlsplit(sent.headers["location"])
    assert f"{target.scheme}://{target.netloc}{target.path}" == google.AUTHORIZE_URL
    query = {key: values[0] for key, values in parse_qs(target.query).items()}
    stand_in.challenge, stand_in.nonce = query["code_challenge"], query["nonce"]
    return query


async def come_back(client: httpx.AsyncClient, **query: str) -> httpx.Response:
    returned = await client.get("/v1/auth/google/callback", params=query)
    assert returned.status_code == 303, returned.text
    return returned


async def sign_in(client: httpx.AsyncClient, stand_in: Google) -> httpx.Response:
    query = await leave_for_google(client, stand_in)
    return await come_back(client, code="one-time-code", state=query["state"])


async def test_it_is_off_unless_configured(platform: Platform) -> None:
    offered = (await platform.client.get("/v1/auth/providers")).json()
    assert (offered["google"], offered["invite_only"]) == (False, False)
    assert (await platform.client.get("/v1/auth/google/start")).status_code == 404
    assert (await platform.client.get("/v1/auth/google/callback?code=x")).status_code == 404


async def test_signing_in_with_google_creates_a_sponsor_and_a_session(
    site: tuple[Platform, Google],
) -> None:
    platform, stand_in = site
    client = browser(platform)
    assert (await client.get("/v1/auth/providers")).json()["google"] is True

    query = await leave_for_google(client, stand_in)
    assert query["client_id"] == CLIENT_ID
    assert query["redirect_uri"] == "http://test/v1/auth/google/callback"
    assert (query["response_type"], query["scope"]) == ("code", "openid email profile")
    assert query["code_challenge_method"] == "S256"
    assert CLIENT_SECRET not in str(query)

    returned = await come_back(client, code="one-time-code", state=query["state"])
    assert returned.headers["location"] == "http://test/dashboard"
    cookies = returned.headers.get_list("set-cookie")
    session_cookie = next(c for c in cookies if c.startswith("ab_session="))
    assert "HttpOnly" in session_cookie
    assert "SameSite=strict" in session_cookie
    # The one-time state is spent.
    assert any(c.startswith('ab_oauth="";') or c.startswith("ab_oauth=;") for c in cookies)
    (exchange,) = stand_in.requests
    assert exchange["code"] == "one-time-code"
    assert exchange["redirect_uri"] == "http://test/v1/auth/google/callback"

    # The email is Google's, lower-cased. The cookie works like any other session.
    assert (await client.get("/v1/session")).json() == {
        "signed_in": True,
        "email": "ada@example.com",
    }
    made = await client.post("/v1/bounties", json={"reward_zatoshi": 5}, headers=SAME_ORIGIN)
    assert made.status_code == 201
    # ...including the rule that a cookie only counts on requests from this site.
    foreign = await client.post("/v1/bounties", json={"reward_zatoshi": 5})
    assert foreign.status_code == 403


async def test_the_same_google_account_is_the_same_sponsor(site: tuple[Platform, Google]) -> None:
    platform, stand_in = site
    first = browser(platform)
    await sign_in(first, stand_in)
    await first.post("/v1/bounties", json={"reward_zatoshi": 5}, headers=SAME_ORIGIN)

    # Another browser, and the account's email has changed since. Still the same sponsor.
    second = browser(platform)
    stand_in.account["email"] = "ada.lovelace@example.com"
    await sign_in(second, stand_in)
    assert len((await second.get("/v1/sponsor/bounties")).json()) == 1

    # A different Google account is a different sponsor.
    third = browser(platform)
    stand_in.account = {"sub": "google-user-2", "email": "bob@example.com", "email_verified": True}
    await sign_in(third, stand_in)
    assert (await third.get("/v1/session")).json()["email"] == "bob@example.com"
    assert (await third.get("/v1/sponsor/bounties")).json() == []


async def refused(client: httpx.AsyncClient, returned: httpx.Response, code: str) -> None:
    assert returned.headers["location"] == f"http://test/login?error={code}"
    assert not any(c.startswith("ab_session=") for c in returned.headers.get_list("set-cookie"))
    assert (await client.get("/v1/session")).json()["signed_in"] is False


async def test_an_answer_this_browser_did_not_ask_for_is_refused(
    site: tuple[Platform, Google],
) -> None:
    platform, stand_in = site
    client = browser(platform)
    # No sign-in was started at all.
    await refused(client, await come_back(client, code="c", state="anything"), "google_state")
    assert stand_in.requests == []

    # A sign-in was started, but the answer carries someone else's state.
    await leave_for_google(client, stand_in)
    await refused(client, await come_back(client, code="c", state="not-mine"), "google_state")
    # The state cookie is spent by the failure, so the right state no longer works either.
    assert stand_in.requests == []

    # A state started in one browser does not work in another.
    query = await leave_for_google(client, stand_in)
    other = browser(platform)
    await refused(other, await come_back(other, code="c", state=query["state"]), "google_state")


async def test_the_person_can_say_no_at_google(site: tuple[Platform, Google]) -> None:
    platform, stand_in = site
    client = browser(platform)
    query = await leave_for_google(client, stand_in)
    returned = await come_back(client, error="access_denied", state=query["state"])
    await refused(client, returned, "google_denied")


@pytest.mark.parametrize(
    ("claims", "code"),
    [
        ({"aud": "another-client"}, "google_failed"),
        ({"iss": "https://evil.example"}, "google_failed"),
        ({"exp": 1}, "google_failed"),
        ({"nonce": "from-another-sign-in"}, "google_failed"),
        ({"sub": ""}, "google_failed"),
        ({"email_verified": False}, "google_unverified"),
        ({"email_verified": "true"}, "google_unverified"),
        ({"email": None}, "google_unverified"),
    ],
)
async def test_an_id_token_is_believed_only_if_every_claim_holds(
    site: tuple[Platform, Google], claims: dict[str, Any], code: str
) -> None:
    platform, stand_in = site
    client = browser(platform)
    stand_in.claims = claims
    await refused(client, await sign_in(client, stand_in), code)


async def test_google_failing_is_a_refusal_not_a_crash(site: tuple[Platform, Google]) -> None:
    platform, stand_in = site
    client = browser(platform)
    stand_in.status = 500
    await refused(client, await sign_in(client, stand_in), "google_failed")

    def unreachable(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    platform.app.state.google_transport = httpx.MockTransport(unreachable)
    await refused(client, await sign_in(client, stand_in), "google_failed")

    platform.app.state.google_transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"id_token": "not-a-token"})
    )
    await refused(client, await sign_in(client, stand_in), "google_failed")


async def test_google_reclaims_an_address_someone_signed_up_with_and_revokes_their_key(
    site: tuple[Platform, Google],
) -> None:
    """Sign-up by email proves nothing; Google proves who owns the address. The account
    passes to the Google identity, and the key its maker holds stops working, so a
    squatter is locked out rather than left with a key to the owner's work."""
    platform, stand_in = site
    squatter = await platform.sponsor("ada@example.com")
    await squatter.call("POST", "/v1/bounties", 201, json={"reward_zatoshi": 5})

    client = browser(platform)
    returned = await sign_in(client, stand_in)
    assert returned.headers["location"] == "http://test/dashboard"
    assert (await client.get("/v1/session")).json() == {
        "signed_in": True,
        "email": "ada@example.com",
    }
    # The owner has the account, with what was in it. The old key is dead.
    assert len((await client.get("/v1/sponsor/bounties")).json()) == 1
    await squatter.call("GET", "/v1/sponsor/bounties", 401)
    await squatter.call("GET", "/v1/account", 401)

    # A different Google identity claiming the same address does not get in.
    stand_in.account = {"sub": "someone-else", "email": "ada@example.com", "email_verified": True}
    await refused(
        browser(platform), await sign_in(browser(platform), stand_in), "google_email_taken"
    )


async def test_an_account_can_mint_a_new_api_key_which_replaces_the_old(
    site: tuple[Platform, Google],
) -> None:
    platform, stand_in = site
    # Someone who signed in with Google has no key until they ask for one.
    client = browser(platform)
    await sign_in(client, stand_in)
    minted = await client.post("/v1/account/api-key", headers=SAME_ORIGIN)
    assert minted.status_code == 200
    first = minted.json()["api_key"]
    assert first.startswith("sk_")
    by_key = {"Authorization": f"Bearer {first}"}
    assert (await platform.client.get("/v1/account", headers=by_key)).status_code == 200
    # Asking again replaces it, and the browser stays signed in throughout.
    second = (await client.post("/v1/account/api-key", headers=SAME_ORIGIN)).json()["api_key"]
    assert second != first
    assert (await platform.client.get("/v1/account", headers=by_key)).status_code == 401
    assert (await client.get("/v1/session")).json()["signed_in"] is True
    # It cannot be done to someone from another site.
    assert (await client.post("/v1/account/api-key")).status_code == 403

    # A browser signed in with a key is not signed out by changing that key.
    sponsor = await platform.sponsor("keyed@example.com")
    keyed = browser(platform)
    key = sponsor.headers["Authorization"].removeprefix("Bearer ")
    await keyed.post("/v1/session", json={"api_key": key}, headers=SAME_ORIGIN)
    assert (await keyed.post("/v1/account/api-key", headers=SAME_ORIGIN)).status_code == 200
    assert (await keyed.get("/v1/session")).json()["signed_in"] is True
    await sponsor.call("GET", "/v1/account", 401)


async def test_starting_on_another_host_moves_to_the_public_one(
    site: tuple[Platform, Google],
) -> None:
    platform, _ = site
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=platform.app), base_url="http://127.0.0.1:3000"
    )
    moved = await client.get("/v1/auth/google/start")
    assert moved.status_code == 307
    assert moved.headers["location"] == "http://test/v1/auth/google/start"
    assert "set-cookie" not in moved.headers


def settings_with(**values: object) -> Settings:
    base: dict[str, Any] = {
        "app_env": "development",
        "database_url": "postgresql+asyncpg://u@localhost/d",
        "secret_encryption_key": base64.b64encode(bytes(32)).decode(),
        "model_backend": "fake",
        # Whatever the developer's own environment holds must not leak in.
        "google_client_id": "",
        "google_client_secret": "",
        "public_url": "",
    }
    return Settings(**{**base, **values})


def test_settings_for_google_sign_in() -> None:
    assert settings_with().google_sign_in is False
    assert settings_with(google_client_id="", google_client_secret="").google_sign_in is False
    ready = settings_with(
        google_client_id=CLIENT_ID,
        google_client_secret=CLIENT_SECRET,
        public_url="http://localhost:3000/",
    )
    assert ready.google_sign_in is True
    assert google.redirect_uri(ready) == "http://localhost:3000/v1/auth/google/callback"
    assert CLIENT_SECRET not in repr(ready)

    for broken, message in [
        ({"google_client_id": CLIENT_ID}, "must be set together"),
        ({"google_client_secret": CLIENT_SECRET}, "must be set together"),
        ({"google_client_id": CLIENT_ID, "google_client_secret": CLIENT_SECRET}, "PUBLIC_URL"),
        ({"public_url": "localhost:3000"}, "http or https"),
        ({"public_url": "http://localhost:3000/app"}, "must not have a path"),
    ]:
        with pytest.raises(ValidationError, match=message):
            settings_with(**broken)
