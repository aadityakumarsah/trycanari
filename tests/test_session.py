"""Browser sessions for the single-page app, and how the app itself is served."""

from __future__ import annotations

import re
import time
from pathlib import Path

import httpx
import pytest

from agentbounty.api import session as sessions
from agentbounty.api import spa
from agentbounty.app import create_app
from agentbounty.settings import Settings

from .platform import DEMO_CONFIG, Platform

SAME_ORIGIN = {"Origin": "http://test", "Sec-Fetch-Site": "same-origin"}


def browser(platform: Platform) -> httpx.AsyncClient:
    """A browser: it keeps cookies and has no API key of its own."""
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=platform.app), base_url="http://test"
    )


async def sign_up(client: httpx.AsyncClient, email: str = "team@example.com") -> str:
    created = await client.post("/v1/sponsors", json={"email": email})
    key: str = created.json()["api_key"]
    signed_in = await client.post("/v1/session", json={"api_key": key}, headers=SAME_ORIGIN)
    assert signed_in.status_code == 200, signed_in.text
    return key


async def test_signing_in_exchanges_the_key_for_an_opaque_cookie(platform: Platform) -> None:
    client = browser(platform)
    assert (await client.get("/v1/session")).json() == {"signed_in": False, "email": None}
    assert (await client.get("/v1/sponsor/bounties")).status_code == 401

    created = await client.post("/v1/sponsors", json={"email": "team@example.com"})
    key = created.json()["api_key"]
    wrong = await client.post("/v1/session", json={"api_key": "sk_wrong"}, headers=SAME_ORIGIN)
    assert (wrong.status_code, wrong.json()) == (401, {"detail": "that API key was not recognised"})
    assert "set-cookie" not in wrong.headers

    signed_in = await client.post("/v1/session", json={"api_key": f"  {key} "}, headers=SAME_ORIGIN)
    assert signed_in.json() == {"signed_in": True, "email": "team@example.com"}
    cookie = signed_in.headers["set-cookie"]
    # Script cannot read it, other sites cannot send it, and the key is not legible in it.
    assert "HttpOnly" in cookie
    assert "SameSite=strict" in cookie
    assert key not in cookie

    # The cookie now authenticates the sponsor API, with no key in the request.
    assert (await client.get("/v1/session")).json() == {
        "signed_in": True,
        "email": "team@example.com",
    }
    assert (await client.get("/v1/sponsor/bounties")).json() == []
    made = await client.post("/v1/bounties", json={"reward_zatoshi": 5}, headers=SAME_ORIGIN)
    assert made.status_code == 201

    signed_out = await client.delete("/v1/session", headers=SAME_ORIGIN)
    assert signed_out.json() == {"signed_in": False, "email": None}
    assert (await client.get("/v1/sponsor/bounties")).status_code == 401


async def test_a_cookie_only_counts_on_requests_from_this_site(platform: Platform) -> None:
    client = browser(platform)
    await sign_up(client)
    body = {"reward_zatoshi": 5}

    refused = [
        {"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
        {"Origin": "https://evil.example"},
        {"Sec-Fetch-Site": "cross-site"},
        {"Sec-Fetch-Site": "same-site"},
        # A sandboxed page posts with a null origin.
        {"Origin": "null"},
        # No evidence at all of where the request came from.
        {},
    ]
    for headers in refused:
        response = await client.post("/v1/bounties", json=body, headers=headers)
        assert (response.status_code, response.json()) == (
            403,
            {"detail": "cross-site request refused"},
        ), headers
    assert (await client.get("/v1/sponsor/bounties")).json() == []

    # Reading is allowed: another site can make the browser send a GET but cannot read the answer.
    assert (
        await client.get("/v1/sponsor/bounties", headers={"Sec-Fetch-Site": "cross-site"})
    ).status_code == 200
    # Another site cannot sign a visitor in as someone else, or sign them out, either.
    for method, payload in (("POST", {"api_key": "sk_x"}), ("DELETE", None)):
        forged = await client.request(
            method, "/v1/session", json=payload, headers={"Origin": "https://evil.example"}
        )
        assert forged.status_code == 403
    assert (await client.get("/v1/session")).json()["signed_in"] is True


async def test_a_bearer_key_needs_no_origin_and_wins_over_a_cookie(platform: Platform) -> None:
    client = browser(platform)
    await sign_up(client)
    other = await platform.sponsor("other@example.com")
    bounty_id = await other.draft()

    # A script sends its key and no Origin; the cookie in the jar is not consulted.
    mine = await client.get(f"/v1/sponsor/bounties/{bounty_id}", headers=other.headers)
    assert mine.status_code == 200
    # Without the key it is the cookie's sponsor asking, and the bounty is not theirs.
    assert (await client.get(f"/v1/sponsor/bounties/{bounty_id}")).status_code == 404
    put = await client.put(
        f"/v1/bounties/{bounty_id}/config", json=DEMO_CONFIG, headers=other.headers
    )
    assert put.status_code == 200
    bad = await client.get("/v1/sponsor/bounties", headers={"Authorization": "Bearer sk_wrong"})
    assert bad.status_code == 401


async def test_a_forged_or_expired_cookie_is_worth_nothing(
    platform: Platform, monkeypatch: pytest.MonkeyPatch
) -> None:
    client = browser(platform)
    key = await sign_up(client)
    genuine = client.cookies.get("ab_session")
    assert genuine
    assert sessions.unseal(platform.cipher, genuine) == key

    for forged in ("", "c2stZm9yZ2Vk", genuine[:-4] + "AAAA", "not base64 !!"):
        assert sessions.unseal(platform.cipher, forged) is None
    client.cookies.set("ab_session", genuine[:-4] + "AAAA")
    assert (await client.get("/v1/sponsor/bounties")).status_code == 401

    # Twelve hours on, the same cookie no longer opens anything.
    later = time.time() + sessions.MAX_AGE_SECONDS + 1
    monkeypatch.setattr("agentbounty.api.session.time.time", lambda: later)
    assert sessions.unseal(platform.cipher, genuine) is None


# --- serving the app ------------------------------------------------------------------------


@pytest.fixture
def built_app(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A stand-in for the output of `bun run build`."""
    (tmp_path / "index.html").write_text(
        '<!doctype html><html><head><script type="module" src="/app/chunk-abc.js"></script></head>'
        '<body><div id="root"></div></body></html>'
    )
    (tmp_path / "chunk-abc.js").write_text("console.log('app')")
    monkeypatch.setattr(spa, "APP", tmp_path)
    return tmp_path


async def test_every_page_route_gets_the_app_shell(
    service_settings: Settings, built_app: Path
) -> None:
    app = create_app(service_settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        for path in ("/", "/login", "/dashboard", "/b/1234", "/dashboard/b/1234", "/anything/else"):
            page = await client.get(path)
            assert page.status_code == 200, path
            assert '<div id="root">' in page.text
            assert page.headers["content-type"].startswith("text/html")
            assert page.headers["content-security-policy"].startswith("default-src 'self'")
            assert page.headers["cache-control"] == "no-cache"
            assert page.headers["x-content-type-options"] == "nosniff"
        # The shell has no inline script: the policy would not run one.
        assert re.findall(r"<script[^>]*>[^<]+</script>", page.text) == []

        asset = await client.get("/app/chunk-abc.js")
        assert asset.status_code == 200
        assert "javascript" in asset.headers["content-type"]
        assert asset.headers["cache-control"] == "public, max-age=31536000, immutable"

        # The API is untouched: unknown API paths and missing assets are real 404s, in JSON.
        for missing in ("/v1/nope", "/v1", "/app/missing.js", "/docs/x"):
            assert (await client.get(missing)).status_code == 404, missing
        assert (await client.get("/health")).json() == {"status": "ok"}
        assert (await client.get("/v1/bounties")).json() == []
        assert (await client.post("/login")).status_code == 405
    await app.state.engine.dispose()


async def test_without_a_build_the_page_says_how_to_make_one(
    service_settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(spa, "APP", tmp_path / "not-built")
    app = create_app(service_settings)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        page = await client.get("/")
        assert page.status_code == 503
        assert "make web-build" in page.text
        assert (await client.get("/v1/bounties")).status_code == 200
    await app.state.engine.dispose()
