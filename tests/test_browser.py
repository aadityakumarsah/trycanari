"""The browser the MCP server drives over the sponsor's own site."""

from __future__ import annotations

import asyncio
import functools
import http.server
import re
import threading
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from agentbounty import browser
from agentbounty.browser import BrowserError, Site, irreversible, origin_of

playwright = pytest.importorskip(
    "playwright.async_api", reason="the browser extra is not installed"
)

HOME = """<!doctype html>
<html><head><title>trycanari</title></head><body>
  <h1>Live bounties</h1>
  <p id="lede">Break the agent, get paid.</p>
  <ul class="bounties">
    <li class="bounty"><b>Treasury agent</b><span class="reward">0.1 SOL</span>
      <a href="/bounty.html" title="b1">Attack it</a></li>
    <li class="bounty"><b>Refund desk</b><span class="reward">0.2 SOL</span>
      <a href="/bounty.html" title="b2">Attack it</a></li>
  </ul>
  <a href="/bounty.html">Open a bounty</a>
  <nav class="tabs" role="tablist">
    <button type="button" role="tab" id="tab">Breaks <span>1</span></button>
  </nav>
  <a href="/bounty.html" id="card">Treasury agent, 0 breaks so far</a>
  <p id="tab-said">no tab</p>
  <button type="button" id="close">Close and refund</button>
  <form><input id="attack" name="attack" /></form>
  <p id="said">nothing yet</p>
  <script>
    document.getElementById("close").addEventListener("click", () => {
      document.getElementById("said").textContent = "the escrow went back";
    });
    document.getElementById("tab").addEventListener("click", () => {
      document.getElementById("tab-said").textContent = "the breaks tab";
    });
  </script>
</body></html>
"""
BOUNTY = """<!doctype html>
<html><head><title>Treasury agent</title></head><body>
  <h1>Treasury agent</h1><p>Two canaries, 0.1 SOL.</p>
</body></html>
"""


@pytest.fixture(scope="module")
def served() -> Iterator[str]:
    """A tiny site of two pages, on a port of its own."""
    root = Path(__file__).parent / "_served"
    root.mkdir(exist_ok=True)
    (root / "index.html").write_text(HOME)
    (root / "bounty.html").write_text(BOUNTY)
    quiet = type(
        "Quiet",
        (http.server.SimpleHTTPRequestHandler,),
        {"log_message": lambda *_, **__: None},
    )
    handler = functools.partial(quiet, directory=str(root))
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        for page in root.iterdir():
            page.unlink()
        root.rmdir()


@pytest.fixture
async def site(served: str) -> AsyncIterator[Site]:
    opened = Site(served)
    try:
        yield opened
    finally:
        await opened.aclose()


# --- what needs no browser --------------------------------------------------------------


def test_it_opens_its_own_site_and_nowhere_else() -> None:
    only = Site("https://trycanari.example/")
    assert only.origin == "https://trycanari.example"
    assert only.resolve("/docs") == "https://trycanari.example/docs"
    assert only.resolve("docs#house") == "https://trycanari.example/docs#house"
    # A whole address on the site is itself.
    assert only.resolve("https://trycanari.example/b/1") == "https://trycanari.example/b/1"
    for elsewhere in (
        "https://example.com/",
        "//example.com/",
        "http://trycanari.example/docs",
        "https://trycanari.example.attacker.test/",
        "file:///etc/passwd",
    ):
        with pytest.raises(BrowserError, match=re.escape("not on https://trycanari.example")):
            only.resolve(elsewhere)


def test_a_control_that_cannot_be_taken_back_is_known_by_its_words() -> None:
    assert irreversible("Close and refund") == "close"
    assert irreversible("Delete draft") == "delete"
    assert irreversible("SIGN OUT") == "sign out"
    assert irreversible("Revoke the key") == "revoke"
    for safe in ("Run the scan", "Create draft", "Open the audit report", "Resume", "Save"):
        assert irreversible(safe) is None, safe


def test_the_origin_of_an_address() -> None:
    assert origin_of("https://a.example:8443/x?y#z") == "https://a.example:8443"


# --- with a real browser ------------------------------------------------------------------


async def test_it_opens_a_page_and_says_what_is_on_it(site: Site) -> None:
    shown = await site.open("/")
    assert shown.title == "trycanari"
    assert shown.url.endswith("/")
    assert "Live bounties" in shown.text
    assert "Break the agent, get paid." in shown.text
    # One part of it, on its own.
    only = await site.shown("#lede")
    assert only.text == "Break the agent, get paid."
    with pytest.raises(BrowserError, match="nothing on the page matches"):
        await site.shown("#nothing-here")


async def test_a_page_somewhere_else_is_refused_before_the_browser_goes(site: Site) -> None:
    with pytest.raises(BrowserError, match=re.escape("not on http://127.0.0.1")):
        await site.open("https://example.com/")


async def test_clicking_follows_the_page_it_leads_to(site: Site) -> None:
    await site.open("/")
    after = await site.click(text="Open a bounty", selector=None, allow=False)
    assert after.title == "Treasury agent"
    assert "Two canaries" in after.text
    # And the page it left behind is the one the next call works on.
    assert (await site.shown()).url.endswith("/bounty.html")
    with pytest.raises(BrowserError, match="nothing to click matches"):
        await site.click(text="Not on this page", selector=None, allow=False)
    with pytest.raises(BrowserError, match="not both and not neither"):
        await site.click(text="a", selector="b", allow=False)


async def test_a_control_that_cannot_be_taken_back_is_not_pressed_by_accident(
    site: Site,
) -> None:
    await site.open("/")
    with pytest.raises(BrowserError, match="cannot be taken back"):
        await site.click(text="Close and refund", selector=None, allow=False)
    # Nothing happened: it was refused before the press, not after it.
    assert "nothing yet" in (await site.shown("#said")).text
    # Said plainly, it is pressed.
    after = await site.click(text="Close and refund", selector=None, allow=True)
    assert "the escrow went back" in after.text


async def test_a_word_presses_what_is_called_that_and_not_what_merely_says_it(
    site: Site,
) -> None:
    await site.open("/")
    # A tab called "Breaks 1", beside a card that says "breaks" somewhere lower down:
    # the one whose name starts with the word is the one that is meant. A tab is a thing
    # a person presses, though it is not a button as far as the page is concerned.
    after = await site.click(text="Breaks", selector=None, allow=False)
    assert "the breaks tab" in after.text
    assert after.url.endswith("/")
    # The whole of a name still wins outright.
    exactly = await site.click(text="Open a bounty", selector=None, allow=False)
    assert exactly.title == "Treasury agent"


async def test_a_word_that_fits_several_things_names_them_rather_than_guessing(
    site: Site,
) -> None:
    await site.open("/")
    with pytest.raises(BrowserError, match="2 things on the page are called") as refused:
        await site.click(text="Attack it", selector=None, allow=False)
    assert "'Attack it'" in str(refused.value)
    assert "give a selector" in str(refused.value)
    # Said precisely, one of them is pressed.
    after = await site.click(text=None, selector="a[title='b2']", allow=False)
    assert after.title == "Treasury agent"


async def test_it_types_into_a_box(site: Site) -> None:
    await site.open("/")
    await site.fill("#attack", "[[reveal]]")
    page = await site.page()
    assert await page.input_value("#attack") == "[[reveal]]"
    with pytest.raises(BrowserError, match="no box on the page matches"):
        await site.fill("#not-a-box", "x")


async def test_it_scrapes_rows_of_a_listing(site: Site) -> None:
    await site.open("/")
    rows = await site.scrape(".bounty", {"title": "b", "reward": ".reward", "id": "a@title"})
    assert rows == [
        {"title": "Treasury agent", "reward": "0.1 SOL", "id": "b1"},
        {"title": "Refund desk", "reward": "0.2 SOL", "id": "b2"},
    ]
    # A field nothing matches is empty, not an error.
    assert (await site.scrape(".bounty", {"none": ".missing"}))[0] == {"none": None}
    # With no fields, each row is its text.
    plain = await site.scrape(".bounty", {})
    assert "Treasury agent" in plain[0]
    assert browser.as_json(rows).startswith("[\n")
    with pytest.raises(BrowserError, match="nothing on the page matches"):
        await site.scrape(".no-such-row", {})


async def test_it_photographs_the_page(site: Site) -> None:
    await site.open("/")
    shot = await site.screenshot(full_page=False)
    assert shot.startswith(b"\x89PNG\r\n")
    whole = await site.screenshot(full_page=True)
    assert whole.startswith(b"\x89PNG\r\n")


async def test_the_browser_is_started_once_and_closes_cleanly(site: Site) -> None:
    first = await site.page()
    assert await site.page() is first
    await site.aclose()
    # Closing twice is not an error, and it starts again when it is next wanted.
    await site.aclose()
    assert await site.page() is not first


async def test_without_playwright_it_says_how_to_install_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import builtins

    real = builtins.__import__

    def missing(name: str, *rest: object, **kw: object) -> object:
        if name == "playwright.async_api":
            raise ImportError("no playwright")
        return real(name, *rest, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "__import__", missing)
    bare = Site("https://trycanari.example")
    with pytest.raises(BrowserError, match="uv sync --extra browser"):
        await bare.page()


def test_asyncio_is_imported_for_the_type_checker_only() -> None:
    # The module must not need playwright to be imported at all.
    assert asyncio is not None
    assert browser.NO_PLAYWRIGHT.startswith("This needs Playwright")
