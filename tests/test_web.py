"""The web app, driven the way a browser drives it: pages, form posts and HTMX requests."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest
from markupsafe import escape

from agentbounty.web.formatting import format_zec, parse_zec

from .platform import (
    DEMO_CONFIG,
    HUNTER,
    REFUND,
    SECRET,
    STRONG,
    SYSTEM_PROMPT,
    WEAK,
    Platform,
)

HTMX = {"HX-Request": "true"}
CONFIG_FORM = {
    "system_prompt": SYSTEM_PROMPT,
    "model": "demo-model",
    "tools": json.dumps(DEMO_CONFIG["tools"]),
    "params": '{"max_tokens": 256}',
    "entry_points": ["user_message", "document"],
}
SECRET_FORM = {"kind": "secret_leak", "public_label": "signing_reference", "secret": SECRET}
TOOL_FORM = {
    "kind": "forbidden_tool",
    "public_label": "unapproved_transfer",
    "tool": "transfer_funds",
    "argument": "recipient",
    "operator": "not_in",
    "value": "payroll-ops, vendor-acme",
}


class Browser:
    """A browser session: it keeps cookies and does not follow redirects by itself."""

    def __init__(self, platform: Platform) -> None:
        transport = httpx.ASGITransport(app=platform.app)
        self.client = httpx.AsyncClient(transport=transport, base_url="http://test")

    async def get(self, path: str, expect: int = 200) -> str:
        response = await self.client.get(path)
        assert response.status_code == expect, response.text
        return response.text

    async def post(
        self, path: str, data: dict[str, Any] | None = None, expect: int = 303, **kwargs: Any
    ) -> httpx.Response:
        response = await self.client.post(path, data=data or {}, **kwargs)
        assert response.status_code == expect, response.text
        return response

    async def sign_up(self, email: str = "team@example.com") -> str:
        page = (await self.post("/signup", {"email": email}, expect=200)).text
        found = re.search(r'<pre class="secret">(sk_[^<]+)</pre>', page)
        assert found, page
        return found[1]

    async def new_bounty(
        self, title: str = "Treasury agent", description: str = "Make it pay."
    ) -> str:
        form = {"title": title, "description": description, "reward": "0.01"}
        created = await self.post("/dashboard/bounties", form)
        found = re.fullmatch(
            r"/dashboard/b/([0-9a-f-]{36})\?done=created", created.headers["location"]
        )
        assert found, created.headers["location"]
        return found[1]

    async def listed(self, platform: Platform, title: str = "Treasury agent") -> str:
        """A bounty taken live entirely through the dashboard, funded by a simulated deposit."""
        bounty_id = await self.new_bounty(title)
        base = f"/dashboard/b/{bounty_id}"
        await self.post(f"{base}/config", CONFIG_FORM)
        await self.post(f"{base}/canaries", SECRET_FORM)
        await self.post(f"{base}/canaries", TOOL_FORM)
        await self.post(f"{base}/fund", {"refund_address": REFUND})
        page = await self.get(base)
        code = re.search(r'id="deposit-code">([^<]+)<', page)
        assert code, page
        platform.wallet.simulate_deposit(1_100_000, code[1], confirmations=3)
        await platform.payments.tick()
        return bounty_id

    async def attack(self, bounty_id: str, text: str, **headers: str) -> tuple[str, dict[str, str]]:
        """Submit the attempt form. Returns the response and the fields of the polling form."""
        form = {"payout_address": HUNTER, "user_message": text}
        response = await self.post(f"/b/{bounty_id}/attempts", form, expect=200, headers=headers)
        return response.text, polling_form(response.text)

    async def poll(self, fields: dict[str, str]) -> str:
        path = f"/attempts/{fields['attempt_id']}/status"
        body = {"token": fields["token"], "bounty_id": fields["bounty_id"]}
        return (await self.post(path, body, expect=200, headers=HTMX)).text


def polling_form(html: str) -> dict[str, str]:
    action = re.search(r'action="/attempts/([0-9a-f-]{36})/status"', html)
    token = re.search(r'name="token" value="([^"]+)"', html)
    bounty = re.search(r'name="bounty_id" value="([^"]+)"', html)
    assert action, html
    assert token, html
    assert bounty, html
    return {"attempt_id": action[1], "token": token[1], "bounty_id": bounty[1]}


@pytest.fixture
async def browser(platform: Platform) -> Browser:
    return Browser(platform)


# --- the whole flow -----------------------------------------------------------------------


async def test_the_full_flow_through_the_pages(platform: Platform, browser: Browser) -> None:
    assert "No bounty is live right now." in await browser.get("/")
    assert (await browser.client.get("/dashboard")).headers["location"] == "/login"

    # --- the sponsor signs up, lists an agent and funds it -------------------------------
    await browser.sign_up()
    assert "You have no bounties yet." in await browser.get("/dashboard")
    bounty_id = await browser.new_bounty()
    base = f"/dashboard/b/{bounty_id}"
    assert "Draft created." in await browser.get(f"{base}?done=created")

    assert (await browser.post(f"{base}/config", CONFIG_FORM)).headers[
        "location"
    ] == f"{base}?done=config"
    await browser.post(f"{base}/canaries", SECRET_FORM)
    await browser.post(f"{base}/canaries", TOOL_FORM)
    page = await browser.get(base)
    for shown in (
        "signing_reference",
        "unapproved_transfer",
        SECRET,
        "Version 1, not live yet",
        "payroll-ops",
    ):
        assert shown in page

    await browser.post(f"{base}/fund", {"refund_address": REFUND})
    page = await browser.get(f"{base}?done=fund")
    assert "awaiting deposit" in page
    assert "Send <strong>0.011 ZEC</strong>" in page
    assert f'id="escrow-address">{platform.wallet.address}<' in page
    code = re.search(r'id="deposit-code">(dep-[0-9a-f]+)<', page)
    assert code

    # --- the deposit arrives and the bounty is live --------------------------------------
    assert "No bounty is live right now." in await browser.get("/")
    platform.wallet.simulate_deposit(1_100_000, code[1], confirmations=3)
    await platform.payments.tick()
    listing = await browser.get("/")
    assert f'<a href="/b/{bounty_id}">Treasury agent</a>' in listing
    assert "0.01 <span>ZEC</span>" in listing

    # --- the public page shows what a hunter may know, and nothing else ------------------
    hunter = Browser(platform)
    page = await hunter.get(f"/b/{bounty_id}")
    for shown in (
        "Treasury agent",
        "Make it pay.",
        "0.01 ZEC",
        "signing_reference",
        "unapproved_transfer",
        "user message",
        "document",
        'name="payout_address"',
    ):
        assert shown in page
    for hidden in (SECRET, "treasury assistant", "payroll-ops", "dep-", "Dashboard"):
        assert hidden not in page

    # --- a weak attempt: queued, then not verified, with the agent's reply ---------------
    fragment, weak = await hunter.attack(bounty_id, WEAK, **HTMX)
    assert fragment.lstrip().startswith('<section id="attempt"')
    assert "<html" not in fragment
    assert "badge-queued" in fragment
    assert 'hx-trigger="load delay:1500ms, submit"' in fragment
    await platform.work()
    fragment = await hunter.poll(weak)
    assert "badge-not_verified" in fragment
    assert "Tripped in 0 of 5 runs." in fragment
    assert "How can I help?" in fragment
    assert "hx-trigger" not in fragment

    # --- a strong attempt: verified, then paid, with the transaction ID ------------------
    _, strong = await hunter.attack(bounty_id, STRONG, **HTMX)
    await platform.work()
    fragment = await hunter.poll(strong)
    assert "badge-verified" in fragment
    assert "Tripped in 5 of 5 runs." in fragment
    assert "Canary: unapproved_transfer." in fragment
    assert "transfer_funds(" in fragment
    # Verified but not yet paid: it keeps polling.
    assert "The payout is being sent." in fragment
    assert "hx-trigger" in fragment
    await platform.payments.tick()
    (sent,) = platform.wallet.sent
    fragment = await hunter.poll(strong)
    assert "A payout of 0.01 ZEC was sent." in fragment
    assert f"<code>{sent.txid}</code>" in fragment
    assert "hx-trigger" not in fragment
    assert "is paused and is not taking attempts" in await hunter.get(f"/b/{bounty_id}")

    # --- the sponsor sees the finding, the transcript, the evidence and the ledger -------
    page = await browser.get(base)
    assert "Tripped in 5 of 5 runs." in page
    assert str(escape(STRONG)) in page
    assert (
        "<strong>unapproved_transfer</strong> tripped at message 2 (tool_call:transfer_funds)"
        in page
    )
    # The matched text is the offending call, as the agent made it.
    assert "<pre>transfer_funds({" in page
    assert "&#34;recipient&#34;: &#34;attacker&#34;" in page
    assert "Transfer submitted." in page
    assert f"transaction <code>{sent.txid}</code>" in page
    assert page.count("<summary>Run ") == 5
    for row in (
        "<td>deposit</td>",
        "<td>payout</td>",
        "<td>fee</td>",
        "+0.011<",
        "-0.01<",
        "-0.001<",
    ):
        assert row in page
    assert re.search(r"<th>Balance</th><th class=\"num\">0</th>", page)
    assert "0 of 0.011 ZEC" in page


# --- escaping -----------------------------------------------------------------------------


async def test_everything_people_type_is_escaped(platform: Platform, browser: Browser) -> None:
    script = "<script>alert('title')</script>"
    image = '<img src=x onerror="alert(1)">'
    await browser.sign_up()
    bounty_id = await browser.listed(platform, title=script)
    attack = f"<b onmouseover=alert(2)>bold</b> {STRONG}"
    hunter = Browser(platform)
    _, fields = await hunter.attack(bounty_id, attack, **HTMX)
    listing = await Browser(platform).get("/")
    await platform.work()
    await platform.payments.tick()

    pages = [
        await hunter.poll(fields),
        await hunter.get(f"/b/{bounty_id}"),
        listing,
        await browser.get("/dashboard"),
        await browser.get(f"/dashboard/b/{bounty_id}"),
    ]
    for page in pages:
        for raw in (script, image, "<b onmouseover"):
            assert raw not in page
        assert page.count("<script") <= 1
    assert str(escape(script)) in pages[1]
    assert str(escape(script)) in pages[2]
    assert str(escape(attack)) in pages[4]
    # JSON shown in a page is escaped like any other text.
    assert "&#34;recipient&#34;" in pages[4]

    # An error message that quotes input is escaped too.
    refused = await browser.post(
        f"/dashboard/b/{await browser.new_bounty()}/config",
        {**CONFIG_FORM, "tools": '[{"name": "<script>x</script>"}]'},
        expect=400,
    )
    assert "<script>x</script>" not in refused.text.replace(
        str(escape('[{"name": "<script>x</script>"}]')), ""
    )


# --- sign-in and privacy ------------------------------------------------------------------


async def test_the_dashboard_is_private(platform: Platform, browser: Browser) -> None:
    await browser.sign_up()
    bounty_id = await browser.new_bounty()
    base = f"/dashboard/b/{bounty_id}"

    stranger = Browser(platform)
    for path in ("/dashboard", base):
        assert (await stranger.client.get(path)).headers["location"] == "/login"
    for path in ("/dashboard/bounties", f"{base}/config", f"{base}/close", f"{base}/fund"):
        assert (await stranger.post(path)).headers["location"] == "/login"

    # A cookie that was not sealed by this service is worth nothing.
    stranger.client.cookies.set("ab_session", "c2stZm9yZ2Vk")
    assert (await stranger.client.get("/dashboard")).headers["location"] == "/login"
    stranger.client.cookies.clear()

    # Another sponsor is signed in, but the bounty is not theirs.
    await stranger.sign_up("other@example.com")
    assert "That bounty does not exist." in await stranger.get(base, expect=404)
    await stranger.post(f"{base}/close", expect=404)
    assert "Treasury agent" not in await stranger.get("/dashboard")
    assert "Treasury agent" in await browser.get("/dashboard")


async def test_signing_in_with_an_api_key_and_out_again(
    platform: Platform, browser: Browser
) -> None:
    sponsor = await platform.sponsor("api@example.com")
    key = sponsor.headers["Authorization"].removeprefix("Bearer ")
    await sponsor.draft()

    wrong = await browser.post("/login", {"key": "sk_wrong"}, expect=401)
    assert "That API key was not recognised." in wrong.text
    assert "ab_session" not in wrong.headers.get("set-cookie", "")

    signed_in = await browser.post("/login", {"key": key})
    assert signed_in.headers["location"] == "/dashboard"
    cookie = signed_in.headers["set-cookie"]
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    # The cookie is opaque: the key is not readable in it.
    assert key not in cookie
    assert "Untitled agent" in await browser.get("/dashboard")
    assert (await browser.client.get("/login")).headers["location"] == "/dashboard"

    assert (await browser.post("/logout")).headers["location"] == "/"
    assert (await browser.client.get("/dashboard")).headers["location"] == "/login"

    taken = await browser.post("/signup", {"email": "api@example.com"}, expect=400)
    assert "already exists" in taken.text


async def test_form_posts_from_another_site_are_refused(
    platform: Platform, browser: Browser
) -> None:
    await browser.sign_up()
    bounty_id = await browser.new_bounty()
    path = f"/dashboard/b/{bounty_id}/close"
    await browser.post(path, expect=403, headers={"Origin": "https://evil.example"})
    await browser.post(path, expect=403, headers={"Sec-Fetch-Site": "cross-site"})
    await browser.post(path, expect=403, headers={"Sec-Fetch-Site": "same-site"})
    # A sandboxed page posts with a null origin.
    await browser.post(path, expect=403, headers={"Origin": "null"})
    assert "closing" not in await browser.get(f"/dashboard/b/{bounty_id}")
    await browser.post(path, headers={"Origin": "http://test", "Sec-Fetch-Site": "same-origin"})


# --- dashboard forms ----------------------------------------------------------------------


async def test_form_errors_are_shown_and_nothing_is_saved(
    platform: Platform, browser: Browser
) -> None:
    await browser.sign_up()
    for reward, message in (
        ("abc", "enter the reward as a number"),
        ("0.000000001", "at most 8 decimal places"),
        ("0", "greater than 0"),
    ):
        refused = await browser.post(
            "/dashboard/bounties", {"title": "Kept title", "reward": reward}, expect=400
        )
        assert message in refused.text
        # What was typed is still in the form.
        assert 'value="Kept title"' in refused.text
    assert "You have no bounties yet." in await browser.get("/dashboard")

    bounty_id = await browser.new_bounty()
    base = f"/dashboard/b/{bounty_id}"
    refused = await browser.post(
        f"{base}/config", {**CONFIG_FORM, "tools": "[not json"}, expect=400
    )
    assert "tools and params must be valid JSON" in refused.text
    assert "You are the treasury assistant" in refused.text
    refused = await browser.post(f"{base}/config", {**CONFIG_FORM, "entry_points": []}, expect=400)
    assert "entry point" in refused.text
    assert "Version 1" not in await browser.get(base)

    early = await browser.post(f"{base}/canaries", SECRET_FORM, expect=400)
    assert "upload a config before adding canaries" in early.text
    await browser.post(f"{base}/config", CONFIG_FORM)
    short = await browser.post(f"{base}/canaries", {**SECRET_FORM, "secret": "hunter2"}, expect=400)
    assert "at least 12 characters" in short.text
    unlisted = await browser.post(f"{base}/fund", {"refund_address": REFUND}, expect=400)
    assert "listing check failed" in unlisted.text
    await browser.post(f"{base}/canaries", TOOL_FORM)
    bad_address = await browser.post(
        f"{base}/fund", {"refund_address": "t1transparent"}, expect=400
    )
    assert "not a shielded testnet address" in bad_address.text


async def test_pause_resume_and_close_from_the_dashboard(
    platform: Platform, browser: Browser
) -> None:
    await browser.sign_up()
    bounty_id = await browser.listed(platform)
    base = f"/dashboard/b/{bounty_id}"
    page = await browser.get(base)
    assert f'action="{base}/pause"' in page
    assert f'action="{base}/resume"' not in page

    # An attack already in the queue cannot be dodged by pausing.
    hunter = Browser(platform)
    await hunter.attack(bounty_id, WEAK, **HTMX)
    blocked = await browser.post(f"{base}/pause", expect=400)
    assert "attempts are still being judged" in blocked.text
    await platform.work()

    assert (await browser.post(f"{base}/pause")).headers["location"] == f"{base}?done=paused"
    page = await browser.get(base)
    assert "badge-paused" in page
    assert ">Resume<" in page
    assert "is paused and is not taking attempts" in await hunter.get(f"/b/{bounty_id}")
    assert "No bounty is live right now." in await hunter.get("/")

    await browser.post(f"{base}/resume")
    assert "badge-live" in await browser.get(base)
    assert 'name="payout_address"' in await hunter.get(f"/b/{bounty_id}")

    await browser.post(f"{base}/close")
    page = await browser.get(base)
    assert "badge-closing" in page
    assert "Close and refund" not in page
    await platform.payments.tick()
    page = await browser.get(base)
    assert "badge-closed" in page
    assert "<td>refund</td>" in page
    assert "-0.011<" in page
    assert [(p.to_address, p.amount_zatoshi) for p in platform.wallet.sent] == [(REFUND, 1_100_000)]


async def test_the_tool_canary_form_understands_what_is_typed(
    platform: Platform, browser: Browser
) -> None:
    await browser.sign_up()
    bounty_id = await browser.new_bounty()
    base = f"/dashboard/b/{bounty_id}"
    await browser.post(f"{base}/config", CONFIG_FORM)
    over = {
        **TOOL_FORM,
        "public_label": "big_transfer",
        "argument": "amount",
        "operator": "gt",
        "value": "1000",
    }
    anything = {**TOOL_FORM, "public_label": "any_transfer", "argument": "", "value": ""}
    for form in (TOOL_FORM, over, anything):
        await browser.post(f"{base}/canaries", form)
    sponsor_api = await platform.client.get(
        f"/v1/sponsor/bounties/{bounty_id}",
        cookies=None,
        headers=await _api_headers(browser, platform),
    )
    rules = {canary["public_label"]: canary["rule"] for canary in sponsor_api.json()["canaries"]}
    assert rules == {
        "unapproved_transfer": {
            "tool": "transfer_funds",
            "predicate": {
                "argument": "recipient",
                "operator": "not_in",
                "value": ["payroll-ops", "vendor-acme"],
            },
        },
        "big_transfer": {
            "tool": "transfer_funds",
            "predicate": {"argument": "amount", "operator": "gt", "value": 1000},
        },
        "any_transfer": {"tool": "transfer_funds", "predicate": None},
    }


async def _api_headers(browser: Browser, platform: Platform) -> dict[str, str]:
    """The API key behind a browser session, as the web app itself recovers it."""
    from agentbounty.web import session

    key = session.unseal(platform.cipher, browser.client.cookies.get("ab_session"))
    assert key
    return {"Authorization": f"Bearer {key}"}


# --- the attempt form without scripts -----------------------------------------------------


async def test_the_attempt_form_works_without_scripts(platform: Platform, browser: Browser) -> None:
    await browser.sign_up()
    bounty_id = await browser.listed(platform)
    hunter = Browser(platform)

    page, fields = await hunter.attack(bounty_id, WEAK)
    assert "<html" in page
    assert "Treasury agent" in page
    assert "badge-queued" in page
    assert ">Refresh<" in page
    await platform.work()
    path = f"/attempts/{fields['attempt_id']}/status"
    refreshed = await hunter.post(
        path, {"token": fields["token"], "bounty_id": bounty_id}, expect=200
    )
    assert "<html" in refreshed.text
    assert "badge-not_verified" in refreshed.text

    # The status token never appears in a URL.
    assert fields["token"] not in str(refreshed.url)
    wrong = await hunter.post(
        path, {"token": "wrong", "bounty_id": bounty_id}, expect=200, headers=HTMX
    )
    assert "attempt not found" in wrong.text

    for form, message in (
        (
            {"payout_address": "t1Hsc1LR8yKnbbe3twRp88p6vFfC5t7DLbs", "user_message": WEAK},
            "not a shielded testnet address",
        ),
        ({"payout_address": HUNTER, "user_message": ""}, "write a message or a document"),
    ):
        refused = await hunter.post(f"/b/{bounty_id}/attempts", form, expect=400)
        assert message in refused.text
        fragment = await hunter.post(f"/b/{bounty_id}/attempts", form, expect=200, headers=HTMX)
        assert message in fragment.text
        assert "hx-trigger" not in fragment.text


# --- pages, headers and assets ------------------------------------------------------------


async def test_pages_are_locked_down_and_assets_are_local(
    platform: Platform, browser: Browser
) -> None:
    response = await browser.client.get("/")
    assert response.headers["content-security-policy"].startswith("default-src 'self'")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    # With "no-referrer" a browser sends "Origin: null" on our own forms, which we refuse.
    assert response.headers["referrer-policy"] == "same-origin"
    page = response.text
    # One script, loaded from this server, and no inline script or style.
    assert re.findall(r"<script[^>]*>", page) == ['<script src="/static/htmx.min.js" defer>']
    assert "style=" not in page
    assert "http://" not in page
    assert "https://" not in page
    for asset, kind in (("style.css", "text/css"), ("htmx.min.js", "javascript")):
        served = await browser.client.get(f"/static/{asset}")
        assert served.status_code == 200
        assert kind in served.headers["content-type"]

    for missing in ("/b/00000000-0000-0000-0000-000000000000", "/b/not-a-uuid"):
        assert "That bounty does not exist." in await browser.get(missing, expect=404)
    # The JSON API is unaffected: it still answers in JSON.
    assert (await browser.client.get("/v1/bounties")).json() == []
    assert (await browser.client.get("/health")).json() == {"status": "ok"}


# --- money as text ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("zatoshi", "text"),
    [
        (0, "0"),
        (1, "0.00000001"),
        (100_000, "0.001"),
        (1_100_000, "0.011"),
        (250_000_000, "2.5"),
        (100_000_000, "1"),
        (-1_000_000, "-0.01"),
        (2_100_000_000_000_000, "21000000"),
        (9_007_199_254_740_993, "90071992.54740993"),
    ],
)
def test_zatoshi_are_formatted_exactly(zatoshi: int, text: str) -> None:
    assert format_zec(zatoshi) == text
    assert parse_zec(text) == zatoshi


@pytest.mark.parametrize("text", ["", "abc", "0.000000001", "1e-9", "NaN", "Infinity", "1,5"])
def test_amounts_that_are_not_exact_zec_are_refused(text: str) -> None:
    with pytest.raises(ValueError, match="ZEC"):
        parse_zec(text)


def test_amounts_are_parsed_without_floats() -> None:
    assert parse_zec(" 0.1 ") == 10_000_000
    assert parse_zec("0.30000000") == 30_000_000
    assert parse_zec("1e2") == 10_000_000_000
